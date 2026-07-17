import os
import django
import datetime
import requests
from requests.auth import HTTPDigestAuth

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'attendance_system.settings')
django.setup()

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from monitor.models import Employee, PunchEvent
from monitor.utils import get_device_session

def fetch_range(start_iso, end_iso):
    ip = settings.HIKVISION_IP
    protocol = getattr(settings, 'HIKVISION_PROTOCOL', 'http')
    url = f"{protocol}://{ip}/ISAPI/AccessControl/AcsEvent?format=json"
    
    print(f"Fetching events between {start_iso} and {end_iso} from {ip}...")
    
    # Generate a unique search ID based on current time
    search_id = str(int(timezone.now().timestamp()))
    
    payload = {
        "AcsEventCond": {
            "searchID": search_id,
            "searchResultPosition": 0,
            "maxResults": 100,
            "major": 5,
            "minor": 38,
            "startTime": start_iso,
            "endTime": end_iso
        }
    }
    
    session = get_device_session()
    
    try:
        r = session.post(url, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
        total = data.get("AcsEvent", {}).get("totalMatches", 0)
        print(f"Found {total} total matches in this time range.")
    except Exception as e:
        print(f"Error getting total matches: {e}")
        return
        
    position = 0
    saved_count = 0
    
    while position < total:
        payload["AcsEventCond"]["searchResultPosition"] = position
        payload["AcsEventCond"]["maxResults"] = 100
        
        try:
            r = session.post(url, json=payload, timeout=15)
            r.raise_for_status()
            page_data = r.json()
            events = page_data.get("AcsEvent", {}).get("InfoList", [])
            
            if not events:
                break
                
            for ev in events:
                serial = ev.get("serialNo", 0)
                emp_id = str(ev.get("employeeNoString", ""))
                if not emp_id:
                    continue
                    
                time_str = ev.get("time", "")
                dt = parse_datetime(time_str)
                if not dt:
                    try:
                        dt = datetime.datetime.fromisoformat(time_str)
                    except Exception:
                        dt = timezone.now()
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt)
                    
                # Check if already exists
                exists = PunchEvent.objects.filter(serial_no=serial).exists()
                if not exists:
                    employee, _ = Employee.objects.get_or_create(
                        employee_id=emp_id,
                        defaults={
                            'name': ev.get('name', ''),
                            'gender': 'Unknown',
                            'user_type': 'Unknown'
                        }
                    )
                    if not employee.name and ev.get('name'):
                        employee.name = ev.get('name')
                        employee.save()
                        
                    PunchEvent.objects.create(
                        serial_no=serial,
                        employee=employee,
                        employee_id_str=emp_id,
                        name=ev.get('name', employee.name),
                        time=dt,
                        verify_mode=ev.get('currentVerifyMode', 'Unknown')
                    )
                    saved_count += 1
                    print(f"Saved: {ev.get('name')} (ID: {emp_id}) at {dt} (Serial: {serial})")
                    
            position += len(events)
            print(f"Processed position {position}/{total}")
        except Exception as e:
            print(f"Error fetching page at position {position}: {e}")
            break
            
    print(f"Finished range. Saved {saved_count} missing events.")

if __name__ == "__main__":
    # Fetch yesterday (July 7) and today (July 8)
    # Use IST timezone format since the device is in IST (Asia/Kolkata +05:30)
    fetch_range("2026-07-07T00:00:00+05:30", "2026-07-08T23:59:59+05:30")
