"""
Management command to manually fetch all missed attendance events from the Hikvision device.

Usage:
    python manage.py fetch_events                  # Fetch all events newer than last DB serial
    python manage.py fetch_events --date 2026-06-11  # Fetch all events for a specific date
    python manage.py fetch_events --all            # Fetch the last 200 events unconditionally
"""
import datetime
import time

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from monitor.models import Employee, PunchEvent
from monitor.utils import get_device_session, reset_device_session

import urllib3
urllib3.disable_warnings()


class Command(BaseCommand):
    help = "Manually fetch missed attendance events from the Hikvision device"

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            default=None,
            help='Fetch events for a specific date (YYYY-MM-DD). Defaults to today.'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            default=False,
            help='Fetch all events newer than the last saved serial (ignore date filter).'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=500,
            help='Maximum number of events to fetch (default: 500).'
        )

    def handle(self, *args, **options):
        ip = settings.HIKVISION_IP
        protocol = getattr(settings, 'HIKVISION_PROTOCOL', 'https')
        url = f"{protocol}://{ip}/ISAPI/AccessControl/AcsEvent?format=json"

        self.stdout.write(self.style.SUCCESS(f"Connecting to Hikvision device at {ip}..."))

        # Determine last serial to resume from
        last_db_event = PunchEvent.objects.order_by('-serial_no').first()
        last_serial = last_db_event.serial_no if last_db_event else 0
        self.stdout.write(f"Last serial in database: {last_serial}")

        # Determine date filter
        filter_date = None
        if not options['all']:
            date_str = options['date']
            if date_str:
                try:
                    filter_date = datetime.date.fromisoformat(date_str)
                except ValueError:
                    raise CommandError(f"Invalid date format: '{date_str}'. Use YYYY-MM-DD.")
            else:
                filter_date = timezone.localtime(timezone.now()).date()
            self.stdout.write(f"Fetching events for date: {filter_date} (IST)")
        else:
            self.stdout.write("Fetching all events newer than last DB serial...")

        try:
            total = self._get_total(url)
        except Exception as e:
            raise CommandError(f"Cannot connect to device: {e}")

        self.stdout.write(f"Device has {total} total event records.")

        # Back-scan from end of device log to find new events
        page_size = 30
        new_events = []
        scan_position = max(0, total - page_size)
        found_boundary = False
        fetched_pages = 0
        max_pages = (options['limit'] // page_size) + 2

        while not found_boundary and fetched_pages < max_pages:
            try:
                page_data = self._get_events(url, scan_position, page_size)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Page fetch error: {e}. Retrying..."))
                reset_device_session()
                time.sleep(3)
                continue

            page_events = page_data.get("AcsEvent", {}).get("InfoList", [])
            fetched_pages += 1

            if not page_events:
                break

            for ev in page_events:
                if ev.get("serialNo", 0) > last_serial:
                    new_events.append(ev)

            if any(ev.get("serialNo", 0) <= last_serial for ev in page_events):
                found_boundary = True
                break

            if scan_position == 0:
                break
            scan_position = max(0, scan_position - page_size)

        if not new_events:
            self.stdout.write(self.style.SUCCESS("No new events found on the device. Database is up to date."))
            return

        # Sort oldest-first
        new_events.sort(key=lambda x: x.get("serialNo", 0))
        self.stdout.write(f"Found {len(new_events)} new events on device. Processing...")

        tz = timezone.get_current_timezone()
        saved = 0
        skipped_date = 0

        for event in new_events:
            serial = event.get("serialNo", 0)
            emp_id = str(event.get("employeeNoString", ""))
            if not emp_id:
                continue

            # Parse time
            time_str = event.get("time", "")
            dt = parse_datetime(time_str)
            if not dt:
                try:
                    dt = datetime.datetime.fromisoformat(time_str)
                except Exception:
                    dt = timezone.now()

            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)

            # Apply date filter if not --all
            if filter_date:
                event_local_date = timezone.localtime(dt, tz).date()
                if event_local_date != filter_date:
                    skipped_date += 1
                    continue

            # Get or create employee
            employee, _ = Employee.objects.get_or_create(
                employee_id=emp_id,
                defaults={
                    'name': event.get('name', ''),
                    'gender': 'Unknown',
                    'user_type': 'Unknown',
                }
            )
            if not employee.name and event.get('name'):
                employee.name = event.get('name')
                employee.save()

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
            local_dt = timezone.localtime(dt, tz)
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ [{serial}] {employee.name or emp_id} → {local_dt.strftime('%d %b %Y, %I:%M:%S %p')}"
                )
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Done. Saved {saved} events."))
        if skipped_date > 0:
            self.stdout.write(f"  ({skipped_date} events skipped — not on {filter_date})")
        self.stdout.write("Refresh the dashboard to see the updated attendance.")

    def _get_total(self, url):
        payload = {
            "AcsEventCond": {
                "searchID": "1",
                "searchResultPosition": 0,
                "maxResults": 1,
                "major": 5,
                "minor": 38
            }
        }
        session = get_device_session()
        r = session.post(url, json=payload, timeout=45)
        r.raise_for_status()
        return r.json()["AcsEvent"]["totalMatches"]

    def _get_events(self, url, position, max_results):
        payload = {
            "AcsEventCond": {
                "searchID": "1",
                "searchResultPosition": position,
                "maxResults": max_results,
                "major": 5,
                "minor": 38
            }
        }
        session = get_device_session()
        r = session.post(url, json=payload, timeout=45)
        r.raise_for_status()
        return r.json()
