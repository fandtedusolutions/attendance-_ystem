import datetime
import time
import json
import requests
import urllib3
import logging

logger = logging.getLogger('monitor')

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from monitor.models import Employee, PunchEvent, SystemStatus
from monitor.utils import sync_employees_from_device, get_device_session, reset_device_session, get_device_ip, rediscover_device_ip

urllib3.disable_warnings()

BROADCAST_URL = "http://127.0.0.1:8000/api/broadcast/"

def broadcast_ws(msg_type, data):
    """Send an event to all WebSocket clients via the web server's broadcast endpoint."""
    try:
        requests.post(
            BROADCAST_URL,
            json={"type": msg_type, "data": data},
            timeout=2
        )
    except Exception as e:
        pass  # Silently fail — the fallback AJAX polling on the frontend will pick up the change

class Command(BaseCommand):
    help = "Monitors Hikvision Access Control device for live punch events"

    def handle(self, *args, **options):
        protocol = getattr(settings, 'HIKVISION_PROTOCOL', 'http')
        ip = get_device_ip()
        self.stdout.write(self.style.SUCCESS(f"Starting Hikvision Monitor for IP: {ip} ({protocol})"))

        consecutive_errors = 0  # Track consecutive errors for auto-discovery

        def build_attendance_url():
            return f"{protocol}://{get_device_ip()}/ISAPI/AccessControl/AcsEvent?format=json"

        attendance_url = build_attendance_url()

        # 1. Sync users from device to DB on start
        try:
            self.stdout.write("Running initial user synchronization...")
            success, count, err = sync_employees_from_device()
            if success:
                self.stdout.write(self.style.SUCCESS(f"Initial sync complete. Synced {count} employees."))
            else:
                self.stdout.write(self.style.WARNING(f"Initial sync completed with errors: {err}"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Initial sync failed: {e}"))

        # 2. Resume from the last serial we have in DB (never lose events during downtime)
        last_db_event = PunchEvent.objects.order_by('-serial_no').first()
        if last_db_event:
            last_serial = last_db_event.serial_no
            self.stdout.write(self.style.SUCCESS(f"Resuming from last DB serial: {last_serial}"))
        else:
            # Fresh DB — start from the last 30 events on the device
            try:
                init_search_id = str(int(time.time()))
                total = self.get_total_matches(attendance_url, init_search_id)
                start_position = max(0, total - 30)
                data = self.get_events(attendance_url, start_position, 30, init_search_id)
                events = data.get("AcsEvent", {}).get("InfoList", [])
                last_serial = max((e["serialNo"] for e in events), default=0)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Failed to get initial events: {e}. Starting from serial 0."))
                last_serial = 0

        self.stdout.write(self.style.SUCCESS(f"Monitor started. Last serial: {last_serial}"))
        SystemStatus.objects.update_or_create(
            key="monitor_status",
            defaults={"value": "running"}
        )

        while True:
            try:
                # Update heartbeat
                SystemStatus.objects.update_or_create(
                    key="monitor_heartbeat",
                    defaults={"value": timezone.now().isoformat()}
                )

                # Broadcast active status to connected browser clients
                broadcast_ws("monitor_status_change", {"status": "Active", "error": None})

                # Rebuild the URL each iteration in case the IP was re-discovered
                attendance_url = build_attendance_url()

                search_id = str(int(time.time()))
                total = self.get_total_matches(attendance_url, search_id)

                # Smart back-scan: start from the END of the device log and page
                # backwards until we find events we've already saved (serial <= last_serial).
                # This is O(missed events) — fast even when the device has 100k+ total records.
                page_size = 30
                new_events_batch = []
                scan_position = max(0, total - page_size)
                found_boundary = False

                while not found_boundary:
                    page_data = self.get_events(attendance_url, scan_position, page_size, search_id)
                    page_events = page_data.get("AcsEvent", {}).get("InfoList", [])

                    if not page_events:
                        break

                    for ev in page_events:
                        if ev.get("serialNo", 0) > last_serial:
                            new_events_batch.append(ev)

                    # If this page contains any already-seen serial, we've reached the boundary
                    if any(ev.get("serialNo", 0) <= last_serial for ev in page_events):
                        found_boundary = True
                        break

                    # This entire page is new — go back one more page
                    if scan_position == 0:
                        break
                    scan_position = max(0, scan_position - page_size)

                # Sort oldest-first so we save them in chronological order
                new_events_batch.sort(key=lambda x: x.get("serialNo", 0))
                events = new_events_batch

                new_events_count = 0
                for event in events:
                    serial = event.get("serialNo", 0)
                    if serial <= last_serial:
                        continue

                    emp_id = str(event.get("employeeNoString", ""))
                    if not emp_id:
                        continue

                    # Parse attendance time
                    time_str = event.get("time", "")
                    dt = parse_datetime(time_str)
                    if not dt:
                        try:
                            dt = datetime.datetime.fromisoformat(time_str)
                        except Exception:
                            dt = timezone.now()
                    
                    if timezone.is_naive(dt):
                        dt = timezone.make_aware(dt)

                    # Get or create employee
                    employee, created = Employee.objects.get_or_create(
                        employee_id=emp_id,
                        defaults={
                            'name': event.get('name', ''),
                            'gender': 'Unknown',
                            'user_type': 'Unknown',
                        }
                    )
                    
                    # Update employee name if it was empty
                    if not employee.name and event.get('name'):
                        employee.name = event.get('name')
                        employee.save()
                    
                    employee.last_punch_time = dt
                    employee.save()

                    # Save punch event
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

                    last_serial = serial
                    new_events_count += 1
                    formatted_time = timezone.localtime(dt).strftime('%d %b %Y, %I:%M:%S %p')
                    self.stdout.write(self.style.SUCCESS(f"New punch: {employee.name} ({emp_id}) at {formatted_time}"))
                    logger.info(f"New punch: {employee.name} ({emp_id}) at {formatted_time} (Serial: {serial})")

                    # Recalculate statistics for WS payload (using correct IST day range)
                    tz = timezone.get_current_timezone()
                    today_local = timezone.localtime(timezone.now()).date()
                    today_start = timezone.make_aware(datetime.datetime.combine(today_local, datetime.time.min), tz)
                    today_end   = timezone.make_aware(datetime.datetime.combine(today_local, datetime.time.max), tz)

                    total_employees = Employee.objects.count()
                    today_events = PunchEvent.objects.filter(time__gte=today_start, time__lte=today_end)
                    present_today = today_events.values('employee_id_str').distinct().count()
                    absent_today = max(0, total_employees - present_today)

                    # Broadcast new punch event to all connected browsers via web server
                    broadcast_ws("attendance_punch", {
                        "event": {
                            "serial_no": serial,
                            "employee_id": emp_id,
                            "name": employee.name or "Unknown",
                            "time": formatted_time,
                            "verify_mode": event.get('currentVerifyMode', 'Card/Face'),
                            "gender": employee.gender,
                            "user_type": employee.user_type,
                            "face_url": employee.face_url or ""
                        },
                        "stats": {
                            "total_employees": total_employees,
                            "present_today": present_today,
                            "absent_today": absent_today,
                            "last_punch_name": employee.name or "Unknown",
                            "last_punch_time": formatted_time
                        }
                    })

                if new_events_count > 0:
                    self.stdout.write(f"Processed {new_events_count} new events.")

                # Clear previous monitor error if successful
                SystemStatus.objects.filter(key="monitor_error").delete()
                consecutive_errors = 0  # Reset error counter on success

                time.sleep(5)

            except Exception as e:
                err_str = str(e)
                consecutive_errors += 1
                self.stdout.write(self.style.ERROR(f"Error in monitor loop (attempt {consecutive_errors}): {err_str}"))
                logger.error(f"Error in monitor loop (attempt {consecutive_errors}): {err_str}", exc_info=True)
                SystemStatus.objects.update_or_create(
                    key="monitor_error",
                    defaults={"value": err_str}
                )

                # Reset the HTTP session so the next attempt creates a fresh connection
                reset_device_session()

                # After 3 consecutive errors, the device IP may have changed — auto-discover
                if consecutive_errors >= 3:
                    old_ip = get_device_ip()
                    self.stdout.write(self.style.WARNING(
                        f"Device unreachable at {old_ip} after {consecutive_errors} attempts. "
                        f"Scanning network for device..."
                    ))
                    logger.warning(f"Device unreachable at {old_ip}, triggering auto-discovery...")
                    new_ip = rediscover_device_ip()
                    if new_ip and new_ip != old_ip:
                        self.stdout.write(self.style.SUCCESS(
                            f"Device found at new IP: {new_ip} (was {old_ip})"
                        ))
                        logger.info(f"Device IP changed: {old_ip} -> {new_ip}")
                        attendance_url = build_attendance_url()
                        consecutive_errors = 0  # Reset so we try the new IP immediately
                    elif new_ip:
                        self.stdout.write(self.style.WARNING(
                            f"Device still at {new_ip} but not responding properly. Will retry..."
                        ))
                    else:
                        self.stdout.write(self.style.ERROR(
                            "No Hikvision device found on network. Will retry in 30 seconds..."
                        ))
                        time.sleep(25)  # Extra wait when device is completely gone

                # Notify connected browsers that monitor is offline
                broadcast_ws("monitor_status_change", {"status": "Offline", "error": err_str})

                time.sleep(5)

    def get_total_matches(self, url, search_id):
        """Fetch total number of matches using a POST request as required by Hikvision API.
        Raises exception on error.
        """
        payload = {
            "AcsEventCond": {
                "searchID": search_id,
                "searchResultPosition": 0,
                "maxResults": 1,
                "major": 5,
                "minor": 38,
            }
        }
        session = get_device_session()
        try:
            r = session.post(url, json=payload, timeout=15)
            logger.debug(f"POST total matches {url} status {r.status_code}")
            r.raise_for_status()
            if not r.text.strip():
                raise ValueError("Empty response for total matches from device")
            data = r.json()
            return data.get("AcsEvent", {}).get("totalMatches", 0)
        except Exception as e:
            logger.warning(f"POST total matches failed ({e}), attempting GET fallback")
            fallback_url = f"{url}&searchResultPosition=0&maxResults=1&searchID={search_id}"
            try:
                r = session.get(fallback_url, timeout=15)
                logger.debug(f"GET fallback total matches {fallback_url} status {r.status_code}")
                r.raise_for_status()
                if not r.text.strip():
                    raise ValueError("Empty response for total matches (fallback)")
                data = r.json()
                return data.get("AcsEvent", {}).get("totalMatches", 0)
            except Exception as e2:
                logger.error(f"Failed to get total matches via both POST and GET: {e2}")
                raise e2

    def get_events(self, url, position, max_results, search_id):
        """Retrieve a page of events using a POST request as required by Hikvision API.
        Raises exception on error.
        """
        payload = {
            "AcsEventCond": {
                "searchID": search_id,
                "searchResultPosition": position,
                "maxResults": max_results,
                "major": 5,
                "minor": 38,
            }
        }
        session = get_device_session()
        try:
            r = session.post(url, json=payload, timeout=15)
            logger.debug(f"POST events {url} position {position} max {max_results} status {r.status_code}")
            r.raise_for_status()
            if not r.text.strip():
                raise ValueError(f"Empty response for events at position {position}")
            return r.json()
        except Exception as e:
            logger.warning(f"POST events failed ({e}), attempting GET fallback")
            fallback_url = f"{url}&searchResultPosition={position}&maxResults={max_results}&searchID={search_id}"
            try:
                r = session.get(fallback_url, timeout=15)
                logger.debug(f"GET fallback events {fallback_url} status {r.status_code}")
                r.raise_for_status()
                if not r.text.strip():
                    raise ValueError(f"Empty response for events (fallback) at position {position}")
                return r.json()
            except Exception as e2:
                logger.error(f"Failed to get events via both POST and GET: {e2}")
                raise e2


