import os
import django
import requests
import concurrent.futures

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'attendance_system.settings')
django.setup()

from django.conf import settings
from monitor.models import PunchEvent

def send_single_punch(args):
    punch_id, serial_no, employee_id_str, name, time_iso, verify_mode, webhook_url, headers = args
    payload = {
        "serial_no": serial_no,
        "employee_id": employee_id_str,
        "name": name,
        "time": time_iso,
        "verify_mode": verify_mode,
    }
    try:
        r = requests.post(webhook_url, json=payload, headers=headers, timeout=5)
        if 200 <= r.status_code < 300:
            return True, punch_id, serial_no, None
        else:
            return False, punch_id, serial_no, f"HTTP {r.status_code}"
    except Exception as e:
        return False, punch_id, serial_no, str(e)

def main():
    unsent = PunchEvent.objects.filter(shared_to_erp=False).order_by('time')
    total = unsent.count()
    print(f"Found {total} unsent events.")
    
    if total == 0:
        return
        
    webhook_url = settings.ERP_WEBHOOK_URL
    token = getattr(settings, 'ERP_WEBHOOK_TOKEN', None)
    
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Natdemy-Attendance-System/1.0"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
        
    print(f"Sending to: {webhook_url}")
    
    # Prepare arguments
    tasks = []
    for p in unsent:
        tasks.append((
            p.id,
            p.serial_no,
            p.employee_id_str,
            p.name or "Unknown",
            p.time.isoformat(),
            p.verify_mode or "Unknown",
            webhook_url,
            headers
        ))
        
    success_count = 0
    fail_count = 0
    
    # Use ThreadPoolExecutor to send concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        results = executor.map(send_single_punch, tasks)
        
        # Batch update the successful ones to avoid hitting DB too frequently
        success_ids = []
        for success, punch_id, serial_no, err in results:
            if success:
                success_ids.append(punch_id)
                success_count += 1
                if success_count % 100 == 0:
                    print(f"Sent {success_count}/{total} events...")
            else:
                fail_count += 1
                if fail_count <= 20:
                    print(f"Failed to send serial {serial_no}: {err}")
                    
        # Perform batch update in DB
        if success_ids:
            PunchEvent.objects.filter(id__in=success_ids).update(shared_to_erp=True)
            
    print(f"Completed! Successfully sent {success_count} events. Failed: {fail_count}")

if __name__ == "__main__":
    main()
