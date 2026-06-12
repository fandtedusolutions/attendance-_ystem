import requests
from requests.auth import HTTPDigestAuth
import urllib3
urllib3.disable_warnings()

username = "admin"
password = "Nisa@408"

for ip in ["192.168.0.125", "192.168.0.222"]:
    url = f"http://{ip}/ISAPI/System/deviceInfo"
    try:
        r = requests.get(url, auth=HTTPDigestAuth(username, password), timeout=3)
        print(f"\n--- Device {ip} ---")
        print(f"Status Code: {r.status_code}")
        print(f"Response: {r.text[:600]}")
    except Exception as e:
        print(f"Error on {ip}: {e}")
