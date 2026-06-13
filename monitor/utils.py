import requests
from requests.auth import HTTPDigestAuth
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import urllib3
from django.conf import settings
from monitor.models import Employee, SystemStatus

urllib3.disable_warnings()

_session = None

def get_device_session():
    global _session
    if _session is None:
        _session = requests.Session()
        retries = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=5, pool_maxsize=10)
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
        _session.auth = HTTPDigestAuth(settings.HIKVISION_USERNAME, settings.HIKVISION_PASSWORD)
        _session.verify = False
    return _session

def reset_device_session():
    """Tear down the cached session so the next call to get_device_session() starts fresh."""
    global _session
    if _session is not None:
        try:
            _session.close()
        except Exception:
            pass
        _session = None

def sync_employees_from_device():
    ip = settings.HIKVISION_IP
    protocol = getattr(settings, 'HIKVISION_PROTOCOL', 'https')
    user_url = f"{protocol}://{ip}/ISAPI/AccessControl/UserInfo/Search?format=json"
    session = get_device_session()
    
    position = 0
    loaded_count = 0
    errors = []
    
    while True:
        payload = {
            "UserInfoSearchCond": {
                "searchID": "1",
                "searchResultPosition": position,
                "maxResults": 50
            }
        }

        try:
            r = session.post(
                user_url,
                json=payload,
                timeout=(5, 10)
            )
            
            if r.status_code != 200:
                errors.append(f"HTTP {r.status_code} response from device")
                break
                
            data = r.json()
            records = data.get("UserInfoSearch", {}).get("UserInfo", [])
            
            if not records:
                break

            for user in records:
                emp_id = str(user.get("employeeNo", ""))
                if not emp_id:
                    continue

                Employee.objects.update_or_create(
                    employee_id=emp_id,
                    defaults={
                        "name": user.get("name", ""),
                        "gender": user.get("gender", "Unknown"),
                        "user_type": user.get("userType", "Unknown"),
                        "num_fp": user.get("numOfFP", 0),
                        "num_face": user.get("numOfFace", 0),
                        "group_id": user.get("groupId", ""),
                        "face_url": user.get("faceURL", "")
                    }
                )
                loaded_count += 1

            position += len(records)
        except Exception as e:
            errors.append(str(e))
            break
            
    if errors:
        error_msg = "; ".join(errors)
        SystemStatus.objects.update_or_create(
            key="last_sync_error",
            defaults={"value": error_msg}
        )
        return False, loaded_count, error_msg
        
    SystemStatus.objects.update_or_create(
        key="last_sync_status",
        defaults={"value": f"Successfully synced {loaded_count} employees."}
    )
    return True, loaded_count, None
