import datetime
import json
from django.shortcuts import render
from django.http import JsonResponse
from django.utils import timezone
from django.conf import settings
from django.db.models import Exists, OuterRef
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie

from monitor.models import Employee, PunchEvent, SystemStatus
from monitor.utils import sync_employees_from_device

def get_monitor_status():
    heartbeat = SystemStatus.objects.filter(key="monitor_heartbeat").first()
    if not heartbeat:
        return "Offline"
    try:
        hb_time = timezone.datetime.fromisoformat(heartbeat.value)
        # If last heartbeat was within 10 seconds, it's running
        if timezone.is_naive(hb_time):
            hb_time = timezone.make_aware(hb_time)
        delta = timezone.now() - hb_time
        if delta.total_seconds() < 10:
            return "Active"
    except Exception:
        pass
    return "Offline"

def get_dashboard_data():
    tz = timezone.get_current_timezone()
    today_local = timezone.localtime(timezone.now()).date()

    # IST day boundaries converted to UTC-aware datetimes for correct SQLite comparison
    today_start = timezone.make_aware(datetime.datetime.combine(today_local, datetime.time.min), tz)
    today_end   = timezone.make_aware(datetime.datetime.combine(today_local, datetime.time.max), tz)
    
    # 1. Total employees
    total_employees = Employee.objects.count()
    
    # 2. Present today (unique employee count from events today, using IST day range)
    today_events = PunchEvent.objects.filter(time__gte=today_start, time__lte=today_end)
    present_today = today_events.values('employee_id_str').distinct().count()
    absent_today = max(0, total_employees - present_today)
    
    # 3. Last punch details
    last_punch = PunchEvent.objects.order_by('-time').first()
    last_punch_name = last_punch.name if last_punch else "No records"
    
    if last_punch:
        local_time = timezone.localtime(last_punch.time)
        last_punch_time = local_time.strftime("%d %b %Y, %I:%M:%S %p")
    else:
        last_punch_time = "N/A"
        
    # 4. Status
    status = get_monitor_status()
    
    # 5. Events list (last 15 events)
    recent_events = PunchEvent.objects.select_related('employee').order_by('-time')[:15]
    events_list = []
    for e in recent_events:
        local_e_time = timezone.localtime(e.time)
        events_list.append({
            "serial_no": e.serial_no,
            "employee_id": e.employee_id_str,
            "name": e.name or (e.employee.name if e.employee else "Unknown"),
            "time": local_e_time.strftime("%d %b %Y, %I:%M:%S %p"),
            "verify_mode": e.verify_mode or "Card/Face",
            "gender": e.employee.gender if e.employee else "Unknown",
            "user_type": e.employee.user_type if e.employee else "Unknown",
            "face_url": e.employee.face_url if (e.employee and e.employee.face_url) else ""
        })
        
    # 6. Employees list annotated with today's presence (using IST day range)
    today_punches = PunchEvent.objects.filter(
        employee_id_str=OuterRef('employee_id'),
        time__gte=today_start,
        time__lte=today_end
    )
    employees = Employee.objects.annotate(
        is_present_today=Exists(today_punches)
    ).order_by('name')
    
    employees_list = []
    for emp in employees:
        last_punch_str = "Never"
        if emp.last_punch_time:
            last_punch_str = timezone.localtime(emp.last_punch_time).strftime("%d %b %Y, %I:%M:%S %p")
            
        employees_list.append({
            "employee_id": emp.employee_id,
            "name": emp.name or "Unnamed",
            "gender": emp.gender,
            "user_type": emp.user_type,
            "num_fp": emp.num_fp,
            "num_face": emp.num_face,
            "is_present_today": emp.is_present_today,
            "last_punch": last_punch_str,
            "face_url": emp.face_url
        })
        
    return {
        "status": status,
        "stats": {
            "total_employees": total_employees,
            "present_today": present_today,
            "absent_today": absent_today,
            "last_punch_name": last_punch_name,
            "last_punch_time": last_punch_time
        },
        "events": events_list,
        "employees": employees_list
    }

@ensure_csrf_cookie
def dashboard(request):
    data = get_dashboard_data()
    return render(request, "monitor/dashboard.html", {"initial_data": data})

def api_events(request):
    data = get_dashboard_data()
    return JsonResponse(data)

@require_POST
def api_recover_today(request):
    """
    Fetches all missed events from the Hikvision device for today (IST).
    Called from the dashboard "Recover Today's Data" button.
    """
    from monitor.utils import get_device_session, reset_device_session
    from monitor.models import PunchEvent, Employee
    from django.utils.dateparse import parse_datetime

    ip = settings.HIKVISION_IP if hasattr(settings, 'HIKVISION_IP') else None
    if not ip:
        return JsonResponse({"success": False, "message": "Device IP not configured."}, status=500)

    protocol = getattr(settings, 'HIKVISION_PROTOCOL', 'https')
    url = f"{protocol}://{ip}/ISAPI/AccessControl/AcsEvent?format=json"
    tz = timezone.get_current_timezone()
    today_local = timezone.localtime(timezone.now()).date()
    today_start = timezone.make_aware(datetime.datetime.combine(today_local, datetime.time.min), tz)
    today_end   = timezone.make_aware(datetime.datetime.combine(today_local, datetime.time.max), tz)

    last_db = PunchEvent.objects.order_by('-serial_no').first()
    last_serial = last_db.serial_no if last_db else 0

    try:
        payload = {"AcsEventCond": {"searchID": "1", "searchResultPosition": 0, "maxResults": 1, "major": 5, "minor": 38}}
        session = get_device_session()
        r = session.post(url, json=payload, timeout=8)
        r.raise_for_status()
        if not r.text.strip():
            total = 0
        else:
            total = r.json().get("AcsEvent", {}).get("totalMatches", 0)
    except Exception as e:
        try:
            fallback_url = f"{url}&searchResultPosition=0&maxResults=1"
            r = session.get(fallback_url, timeout=8)
            r.raise_for_status()
            if not r.text.strip():
                total = 0
            else:
                total = r.json().get("AcsEvent", {}).get("totalMatches", 0)
        except Exception as e2:
            reset_device_session()
            return JsonResponse({"success": False, "message": f"Cannot reach device: POST failed ({e}), GET failed ({e2})"}, status=503)

    # Back-scan from end to collect missed events
    page_size = 30
    new_events = []
    scan_pos = max(0, total - page_size)
    found_boundary = False
    max_pages = 20

    for _ in range(max_pages):
        try:
            payload = {"AcsEventCond": {"searchID": "1", "searchResultPosition": scan_pos, "maxResults": page_size, "major": 5, "minor": 38}}
            r = session.post(url, json=payload, timeout=10)
            r.raise_for_status()
            if not r.text.strip():
                page_events = []
            else:
                page_events = r.json().get("AcsEvent", {}).get("InfoList", [])
        except Exception as e:
            try:
                fallback_url = f"{url}&searchResultPosition={scan_pos}&maxResults={page_size}"
                r = session.get(fallback_url, timeout=10)
                r.raise_for_status()
                if not r.text.strip():
                    page_events = []
                else:
                    page_events = r.json().get("AcsEvent", {}).get("InfoList", [])
            except Exception as e2:
                reset_device_session()
                break

        if not page_events:
            break

        for ev in page_events:
            if ev.get("serialNo", 0) > last_serial:
                new_events.append(ev)

        if any(ev.get("serialNo", 0) <= last_serial for ev in page_events):
            found_boundary = True
            break

        if scan_pos == 0:
            break
        scan_pos = max(0, scan_pos - page_size)

    new_events.sort(key=lambda x: x.get("serialNo", 0))
    saved = 0

    for event in new_events:
        serial = event.get("serialNo", 0)
        emp_id = str(event.get("employeeNoString", ""))
        if not emp_id:
            continue

        time_str = event.get("time", "")
        dt = parse_datetime(time_str)
        if not dt:
            try:
                dt = datetime.datetime.fromisoformat(time_str)
            except Exception:
                dt = timezone.now()
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)

        # Only save today's events
        if not (today_start <= dt <= today_end):
            continue

        employee, _ = Employee.objects.get_or_create(
            employee_id=emp_id,
            defaults={'name': event.get('name', ''), 'gender': 'Unknown', 'user_type': 'Unknown'}
        )
        if not employee.name and event.get('name'):
            employee.name = event.get('name')
        employee.last_punch_time = dt
        employee.save()

        PunchEvent.objects.update_or_create(
            serial_no=serial,
            defaults={
                'employee': employee,
                'employee_id_str': emp_id,
                'name': event.get('name', employee.name),
                'time': dt,
                'verify_mode': event.get('currentVerifyMode', 'Unknown'),
            }
        )
        saved += 1

    return JsonResponse({
        "success": True,
        "message": f"Recovered {saved} event(s) for today ({today_local}).",
        "saved": saved
    })


def api_sync_users(request):
    success, count, err = sync_employees_from_device()
    if success:
        return JsonResponse({
            "success": True,
            "message": f"Successfully synced {count} employees."
        })
    else:
        return JsonResponse({
            "success": False,
            "message": f"Sync failed: {err}"
        }, status=500)

from django.views.decorators.csrf import csrf_exempt
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

@csrf_exempt
@require_POST
def api_broadcast(request):
    try:
        body = json.loads(request.body)
        msg_type = body.get("type")
        msg_data = body.get("data")
        
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                "attendance_events",
                {
                    "type": msg_type,
                    "data": msg_data
                }
            )
            return JsonResponse({"success": True})
        return JsonResponse({"success": False, "error": "No channel layer configured"}, status=500)
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)

def day_report_page(request):
    return render(request, "monitor/day_report.html")

def api_day_report(request):
    from django.db.models import Min, Max
    date_str = request.GET.get('date')
    if not date_str:
        date_str = timezone.localtime(timezone.now()).strftime('%Y-%m-%d')
    
    try:
        target_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({"success": False, "message": "Invalid date format. Use YYYY-MM-DD."}, status=400)
    
    tz = timezone.get_current_timezone()
    start_time = timezone.make_aware(datetime.datetime.combine(target_date, datetime.time.min), tz)
    end_time = timezone.make_aware(datetime.datetime.combine(target_date, datetime.time.max), tz)
    
    # 1. Fetch all punches for this day ordered by time
    all_punches = PunchEvent.objects.filter(time__gte=start_time, time__lte=end_time).order_by('time')
    
    # Group punches by employee ID
    emp_punches_map = {}
    for p in all_punches:
        emp_id = p.employee_id_str
        if emp_id not in emp_punches_map:
            emp_punches_map[emp_id] = []
        emp_punches_map[emp_id].append(p)
 
    # 2. Fetch all registered employees
    all_employees = Employee.objects.all().order_by('name')
    
    # Late entry limit: 09:00 AM local time
    late_limit = datetime.time(9, 0, 0)
    # Early exit limit: 06:00 PM (18:00) local time
    early_limit = datetime.time(18, 0, 0)
    
    report_data = []
    total_present = 0
    total_absent = 0
    
    for emp in all_employees:
        emp_id = str(emp.employee_id)
        p_events = emp_punches_map.get(emp_id, [])
        
        is_late = False
        is_early = False
        punches_list = []
        
        if p_events:
            total_present += 1
            first_punch = p_events[0]
            last_punch = p_events[-1]
            
            in_local = timezone.localtime(first_punch.time)
            out_local = timezone.localtime(last_punch.time)
            
            in_str = in_local.strftime('%I:%M:%S %p')
            
            # Late Entry Check: If First Punch is after 09:00:00 AM
            if in_local.time() > late_limit:
                is_late = True
            
            if len(p_events) == 1:
                out_str = "-"
                duration_str = "-"
                status = "Single Punch"
            else:
                out_str = out_local.strftime('%I:%M:%S %p')
                duration = last_punch.time - first_punch.time
                hours, remainder = divmod(duration.total_seconds(), 3600)
                minutes, _ = divmod(remainder, 60)
                duration_str = f"{int(hours)}h {int(minutes)}m"
                status = "Checked Out"
                
                # Early Exit Check: If Checked Out and Out Time is before 06:00:00 PM
                if out_local.time() < early_limit:
                    is_early = True
            
            for p_ev in p_events:
                p_local = timezone.localtime(p_ev.time)
                punches_list.append({
                    "time": p_local.strftime('%I:%M:%S %p'),
                    "verify_mode": p_ev.verify_mode or "Card/Face",
                    "serial_no": p_ev.serial_no,
                    "shared_to_erp": p_ev.shared_to_erp,
                })
            
            report_data.append({
                "employee_id": emp_id,
                "name": emp.name or "Unnamed",
                "in_time": in_str,
                "out_time": out_str,
                "duration": duration_str,
                "status": status,
                "is_late": is_late,
                "is_early": is_early,
                "punches": punches_list
            })
        else:
            total_absent += 1
            report_data.append({
                "employee_id": emp_id,
                "name": emp.name or "Unnamed",
                "in_time": "-",
                "out_time": "-",
                "duration": "-",
                "status": "Absent",
                "is_late": False,
                "is_early": False,
                "punches": []
            })
            
    return JsonResponse({
        "success": True,
        "date": date_str,
        "total_employees": all_employees.count(),
        "total_present": total_present,
        "total_absent": total_absent,
        "data": report_data
    })

import csv
from django.http import HttpResponse

def export_attendance(request):
    period = request.GET.get('period', 'day')
    emp_id = request.GET.get('employee_id', None)
    date_str = request.GET.get('date', None)
    
    tz = timezone.get_current_timezone()
    today_local = timezone.localtime(timezone.now()).date()
    
    if date_str:
        try:
            ref_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            ref_date = today_local
    else:
        ref_date = today_local
        
    if period == 'week':
        start_date = ref_date - datetime.timedelta(days=ref_date.weekday())
        end_date = start_date + datetime.timedelta(days=6)
    elif period == 'month':
        start_date = ref_date.replace(day=1)
        next_month = start_date.replace(day=28) + datetime.timedelta(days=4)
        end_date = next_month - datetime.timedelta(days=next_month.day)
    else: # day
        start_date = ref_date
        end_date = ref_date
        
    start_time = timezone.make_aware(datetime.datetime.combine(start_date, datetime.time.min), tz)
    end_time = timezone.make_aware(datetime.datetime.combine(end_date, datetime.time.max), tz)
    
    punches = PunchEvent.objects.filter(time__gte=start_time, time__lte=end_time).order_by('time')
    
    from django.db.models import Q
    if emp_id:
        # emp_id is acting as a search term for name or ID
        punches = punches.filter(Q(employee_id_str__icontains=emp_id) | Q(name__icontains=emp_id))
        
    from collections import defaultdict
    grouped = defaultdict(list)
    for p in punches:
        p_date = timezone.localtime(p.time).date()
        grouped[(p_date, p.employee_id_str)].append(p)
        
    response = HttpResponse(content_type='text/csv')
    filename = f"attendance_{period}_{start_date.strftime('%Y%m%d')}"
    if emp_id:
        filename += f"_filtered"
    response['Content-Disposition'] = f'attachment; filename="{filename}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Date', 'Employee ID', 'Name', 'Check In', 'Check Out', 'Working Hours', 'Status'])
    
    late_limit = datetime.time(9, 0, 0)
    early_limit = datetime.time(18, 0, 0)
    
    employees = Employee.objects.all().order_by('name')
    if emp_id:
        employees = employees.filter(Q(employee_id__icontains=emp_id) | Q(name__icontains=emp_id))
        
    emp_dict = {str(e.employee_id): e.name for e in employees}
    
    current_date = start_date
    while current_date <= end_date:
        for eid, name in emp_dict.items():
            daily_punches = grouped.get((current_date, eid), [])
            if not daily_punches:
                writer.writerow([current_date.strftime('%Y-%m-%d'), eid, name, '-', '-', '-', 'Absent'])
                continue
                
            first = daily_punches[0]
            last = daily_punches[-1]
            
            in_local = timezone.localtime(first.time)
            out_local = timezone.localtime(last.time)
            
            in_str = in_local.strftime('%I:%M:%S %p')
            
            if len(daily_punches) == 1:
                out_str = "-"
                duration_str = "-"
                status = "Single Punch"
            else:
                out_str = out_local.strftime('%I:%M:%S %p')
                duration = last.time - first.time
                hours, remainder = divmod(duration.total_seconds(), 3600)
                minutes, _ = divmod(remainder, 60)
                duration_str = f"{int(hours)}h {int(minutes)}m"
                status = "Present"
                
            if in_local.time() > late_limit:
                status += " (Late)"
            if out_str != "-" and out_local.time() < early_limit:
                status += " (Early Exit)"
                
            writer.writerow([current_date.strftime('%Y-%m-%d'), eid, name, in_str, out_str, duration_str, status])
            
        current_date += datetime.timedelta(days=1)
        
    return response


@require_POST
def api_resend_webhook(request):
    """
    Resends punch events for a specific date to the ERP webhook.
    Can resend all or only unsent ones.
    """
    import concurrent.futures
    import requests

    webhook_url = getattr(settings, 'ERP_WEBHOOK_URL', None)
    if not webhook_url:
        return JsonResponse({"success": False, "message": "ERP Webhook URL is not configured in settings/.env."}, status=400)

    try:
        if request.content_type == 'application/json':
            body = json.loads(request.body)
            date_str = body.get('date')
            resend_all = body.get('resend_all', False)
        else:
            date_str = request.POST.get('date')
            resend_all = request.POST.get('resend_all') == 'true'
    except Exception:
        date_str = None
        resend_all = False

    if not date_str:
        date_str = timezone.localtime(timezone.now()).strftime('%Y-%m-%d')

    try:
        target_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({"success": False, "message": "Invalid date format. Use YYYY-MM-DD."}, status=400)

    tz = timezone.get_current_timezone()
    start_time = timezone.make_aware(datetime.datetime.combine(target_date, datetime.time.min), tz)
    end_time = timezone.make_aware(datetime.datetime.combine(target_date, datetime.time.max), tz)

    punches = PunchEvent.objects.filter(time__gte=start_time, time__lte=end_time)
    if not resend_all:
        punches = punches.filter(shared_to_erp=False)

    punches = punches.order_by('time')
    total_to_send = punches.count()

    if total_to_send == 0:
        return JsonResponse({
            "success": True,
            "message": f"No {'unsent ' if not resend_all else ''}punch events found for {date_str}.",
            "sent": 0,
            "failed": 0
        })

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Natdemy-Attendance-System/1.0"
    }
    token = getattr(settings, 'ERP_WEBHOOK_TOKEN', None)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def send_single_punch(punch):
        payload = {
            "serial_no": punch.serial_no,
            "employee_id": punch.employee_id_str,
            "name": punch.name or (punch.employee.name if punch.employee else "Unknown"),
            "time": punch.time.isoformat(),
            "verify_mode": punch.verify_mode or "Unknown",
        }
        try:
            r = requests.post(webhook_url, json=payload, headers=headers, timeout=5)
            if 200 <= r.status_code < 300:
                punch.shared_to_erp = True
                punch.save(update_fields=['shared_to_erp'])
                return True, punch.serial_no, None
            else:
                return False, punch.serial_no, f"HTTP {r.status_code}"
        except Exception as e:
            return False, punch.serial_no, str(e)

    success_count = 0
    fail_count = 0
    errors = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = executor.map(send_single_punch, punches)
        for success, serial, err in results:
            if success:
                success_count += 1
            else:
                fail_count += 1
                errors.append(f"Serial {serial}: {err}")

    msg = f"Sent {success_count} punch(es) successfully for {date_str}."
    if fail_count > 0:
        msg += f" Failed to send {fail_count} punch(es)."

    return JsonResponse({
        "success": fail_count == 0,
        "message": msg,
        "sent": success_count,
        "failed": fail_count,
        "errors": errors[:10]
    })

