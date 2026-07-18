import datetime
import time
import threading
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
PENDING_RETRY_INTERVAL = 60  # seconds between auto-retry of pending webhooks

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


def retry_pending_webhooks():
    """Attempt to send any PunchEvents that have not yet been delivered to the ERP webhook.
    Only runs when webhook_send_mode is 'auto'. Skips silently in 'manual' mode.
    """
    try:
        # Check mode
        status_obj = SystemStatus.objects.filter(key='webhook_send_mode').first()
        mode = status_obj.value if status_obj else 'auto'
        if mode != 'auto':
            return

        webhook_url = getattr(settings, 'ERP_WEBHOOK_URL', None)
        if not webhook_url:
            return

        pending = PunchEvent.objects.filter(shared_to_erp=False, erp_send_failed=False).order_by('time')[:50]
        if not pending.exists():
            return

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Natdemy-Attendance-System/1.0"
        }
        token = getattr(settings, 'ERP_WEBHOOK_TOKEN', None)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        sent = 0
        failed = 0
        perm_failed = 0
        for punch in pending:
            payload = {
                "serial_no": punch.serial_no,
                "employee_id": punch.employee_id_str,
                "name": punch.name or (punch.employee.name if punch.employee else "Unknown"),
                "time": punch.time.isoformat(),
                "verify_mode": punch.verify_mode or "Unknown",
            }
            try:
                r = requests.post(webhook_url, json=payload, headers=headers, timeout=10)
                if 200 <= r.status_code < 300:
                    punch.shared_to_erp = True
                    punch.save(update_fields=['shared_to_erp'])
                    sent += 1
                    logger.info(f"[Auto-Retry] Sent pending punch serial {punch.serial_no} — HTTP {r.status_code}")
                elif r.status_code == 404:
                    # ERP employee not found — permanent failure, don't retry again
                    punch.erp_send_failed = True
                    punch.save(update_fields=['erp_send_failed'])
                    perm_failed += 1
                    logger.warning(f"[Auto-Retry] Permanent failure (ERP 404 - employee not found) for punch serial {punch.serial_no} (Emp: {punch.employee_id_str}). Will not retry.")
                else:
                    failed += 1
                    logger.warning(f"[Auto-Retry] Failed pending punch serial {punch.serial_no} — HTTP {r.status_code}")
            except Exception as e:
                failed += 1
                logger.warning(f"[Auto-Retry] Exception sending punch serial {punch.serial_no}: {e}")

        if sent > 0 or failed > 0 or perm_failed > 0:
            logger.info(f"[Auto-Retry] Pending retry complete: {sent} sent, {failed} failed, {perm_failed} permanently skipped.")

    except Exception as e:
        logger.error(f"[Auto-Retry] Unexpected error in retry_pending_webhooks: {e}")

# ── Background IP Discovery ───────────────────────────────────────────────────
_discovery_lock = threading.Lock()
_discovery_thread = None  # currently running discovery thread (if any)


def background_discover(old_ip, on_found=None):
    """Run rediscover_device_ip() in a background thread.
    Calls on_found(new_ip) on the main thread's next loop iteration via a shared result box.
    """
    logger.info(f"[Discovery] Background scan started (current IP: {old_ip})")
    try:
        new_ip = rediscover_device_ip()  # updates the _discovered_ip global in utils.py
        if new_ip:
            logger.info(f"[Discovery] Device located at {new_ip} (was {old_ip})")
        else:
            logger.warning("[Discovery] No Hikvision device found on network.")
    except Exception as e:
        logger.error(f"[Discovery] Exception during network scan: {e}")


def start_background_discovery(old_ip):
    """Start a background discovery thread if one is not already running."""
    global _discovery_thread
    with _discovery_lock:
        if _discovery_thread is not None and _discovery_thread.is_alive():
            return  # Already scanning, don't start another
        _discovery_thread = threading.Thread(
            target=background_discover,
            args=(old_ip,),
            daemon=True,
            name="HikvisionDiscovery"
        )
        _discovery_thread.start()


def is_discovery_running():
    """Return True if a background network scan is in progress."""
    global _discovery_thread
    with _discovery_lock:
        return _discovery_thread is not None and _discovery_thread.is_alive()

# ─────────────────────────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = "Monitors Hikvision Access Control device for live punch events"

    def handle(self, *args, **options):
        protocol = getattr(settings, 'HIKVISION_PROTOCOL', 'http')
        ip = get_device_ip()
        self.stdout.write(self.style.SUCCESS(f"Starting Hikvision Monitor for IP: {ip} ({protocol})"))

        consecutive_errors = 0  # Track consecutive errors for auto-discovery
        last_pending_retry = 0  # timestamp of last pending webhook retry
        last_known_ip = get_device_ip()  # track IP changes

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

                # Check if IP changed while we were running (background discovery may have updated it)
                current_ip = get_device_ip()
                if current_ip != last_known_ip:
                    self.stdout.write(self.style.SUCCESS(
                        f"Device IP updated: {last_known_ip} → {current_ip}"
                    ))
                    logger.info(f"Device IP updated by background discovery: {last_known_ip} → {current_ip}")
                    last_known_ip = current_ip
                    attendance_url = build_attendance_url()

                # Auto-retry pending webhooks every PENDING_RETRY_INTERVAL seconds
                now_ts = time.time()
                if now_ts - last_pending_retry >= PENDING_RETRY_INTERVAL:
                    retry_pending_webhooks()
                    last_pending_retry = now_ts

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

                # On the VERY FIRST error, immediately start a background network scan.
                # The main loop keeps retrying every 5s in parallel — zero extra delay.
                # If the scan finds a new IP, get_device_ip() returns it on the next iteration.
                if consecutive_errors == 1:
                    old_ip = get_device_ip()
                    if not is_discovery_running():
                        self.stdout.write(self.style.WARNING(
                            f"Device unreachable at {old_ip} — launching background IP scan..."
                        ))
                        start_background_discovery(old_ip)
                else:
                    # Check if the background scan already found a new IP
                    current_ip = get_device_ip()
                    if current_ip != last_known_ip:
                        self.stdout.write(self.style.SUCCESS(
                            f"Background scan found new IP: {current_ip} (was {last_known_ip}) — switching now!"
                        ))
                        logger.info(f"Switching to new device IP: {last_known_ip} → {current_ip}")
                        last_known_ip = current_ip
                        attendance_url = build_attendance_url()
                        consecutive_errors = 0  # Retry immediately with new IP
                    elif consecutive_errors >= 6 and not is_discovery_running():
                        # Every 30s (6×5s), re-run a fresh scan if still failing
                        old_ip = get_device_ip()
                        self.stdout.write(self.style.WARNING(
                            f"Still unreachable after {consecutive_errors} attempts. Re-scanning..."
                        ))
                        start_background_discovery(old_ip)

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
            r = session.post(url, json=payload, timeout=45)
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
                r = session.get(fallback_url, timeout=45)
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
            r = session.post(url, json=payload, timeout=45)
            logger.debug(f"POST events {url} position {position} max {max_results} status {r.status_code}")
            r.raise_for_status()
            if not r.text.strip():
                raise ValueError(f"Empty response for events at position {position}")
            return r.json()
        except Exception as e:
            logger.warning(f"POST events failed ({e}), attempting GET fallback")
            fallback_url = f"{url}&searchResultPosition={position}&maxResults={max_results}&searchID={search_id}"
            try:
                r = session.get(fallback_url, timeout=45)
                logger.debug(f"GET fallback events {fallback_url} status {r.status_code}")
                r.raise_for_status()
                if not r.text.strip():
                    raise ValueError(f"Empty response for events (fallback) at position {position}")
                return r.json()
            except Exception as e2:
                logger.error(f"Failed to get events via both POST and GET: {e2}")
                raise e2


