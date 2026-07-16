import requests
from requests.auth import HTTPDigestAuth
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import urllib3
import logging
import concurrent.futures
import socket
from django.conf import settings
from monitor.models import Employee, SystemStatus

urllib3.disable_warnings()
logger = logging.getLogger('monitor')

_session = None
_discovered_ip = None  # Cache for auto-discovered device IP


def discover_device_ip(subnet_prefix=None):
    """Scan the local /24 subnet to find the Hikvision access control device.
    
    Probes each IP with a quick POST to the ISAPI AccessControl endpoint.
    A real Hikvision access control terminal responds with 401 (digest auth challenge).
    Devices that redirect (302) or don't respond are skipped.
    
    Returns the discovered IP string or None.
    """
    if subnet_prefix is None:
        # Derive subnet from configured IP
        configured_ip = getattr(settings, 'HIKVISION_IP', '192.168.0.1')
        parts = configured_ip.split('.')
        subnet_prefix = '.'.join(parts[:3])

    protocol = getattr(settings, 'HIKVISION_PROTOCOL', 'http')
    test_payload = {
        "AcsEventCond": {
            "searchID": "1",
            "searchResultPosition": 0,
            "maxResults": 1,
            "major": 5,
            "minor": 38,
        }
    }

    username = getattr(settings, 'HIKVISION_USERNAME', 'admin')
    password = getattr(settings, 'HIKVISION_PASSWORD', '')

    def probe_ip(host_num):
        ip = f"{subnet_prefix}.{host_num}"
        url = f"{protocol}://{ip}/ISAPI/AccessControl/AcsEvent?format=json"
        try:
            # Quick TCP port check first to skip offline hosts fast
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex((ip, 80 if protocol == 'http' else 443))
            sock.close()
            if result != 0:
                return None

            # Try authenticating with our credentials — must return 200 to be our device
            r = requests.post(
                url,
                json=test_payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                auth=HTTPDigestAuth(username, password),
                timeout=3,
                verify=False,
                allow_redirects=False,
            )
            if r.status_code == 200:
                return ip
        except Exception:
            pass
        return None

    logger.info(f"Auto-discovering Hikvision device on {subnet_prefix}.0/24 ...")

    found_ips = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        results = executor.map(probe_ip, range(1, 255))
        for ip in results:
            if ip is not None:
                found_ips.append(ip)

    if found_ips:
        ip = found_ips[0]
        logger.info(f"Auto-discovered Hikvision device at {ip}")
        return ip

    logger.warning("Auto-discovery: No Hikvision access control device found on subnet")
    return None


def get_device_ip():
    """Get the current working Hikvision device IP."""
    global _discovered_ip
    if _discovered_ip is not None:
        return _discovered_ip
    return settings.HIKVISION_IP


def rediscover_device_ip():
    """Force a network scan to find the device and update cached IP."""
    global _discovered_ip
    new_ip = discover_device_ip()
    if new_ip:
        _discovered_ip = new_ip
        SystemStatus.objects.update_or_create(
            key="device_ip",
            defaults={"value": new_ip}
        )
        logger.info(f"Device IP updated to {new_ip}")
        reset_device_session()
    return new_ip


def get_device_session():
    global _session
    if _session is None:
        _session = requests.Session()
        # Ensure we request JSON format from the device
        _session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
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
    ip = get_device_ip()
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
