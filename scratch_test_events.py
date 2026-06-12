import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
import urllib3
urllib3.disable_warnings()

username = "admin"
password = "Nisa@408"

devices = [
    ("192.168.0.106", 443, "https"),
    ("192.168.0.120", 80, "http"),
    ("192.168.0.122", 80, "http")
]

for ip, port, scheme in devices:
    url = f"{scheme}://{ip}:{port}/ISAPI/AccessControl/AcsEvent?format=json"
    try:
        # Send raw request without auth to see headers
        r_raw = requests.post(url, json={}, timeout=2, verify=False)
        print(f"\nDevice: {scheme}://{ip}:{port} (No Auth Request)")
        print(f"Status: {r_raw.status_code}")
        print(f"Server header: {r_raw.headers.get('Server')}")
        print(f"WWW-Authenticate: {r_raw.headers.get('WWW-Authenticate')}")
        
        # Test basic auth
        r_basic = requests.post(url, json={}, auth=HTTPBasicAuth(username, password), timeout=2, verify=False)
        print(f"Basic Auth Status: {r_basic.status_code}")
        
        # Test digest auth
        r_digest = requests.post(url, json={}, auth=HTTPDigestAuth(username, password), timeout=2, verify=False)
        print(f"Digest Auth Status: {r_digest.status_code}")
        
    except Exception as e:
        print(f"Device: {scheme}://{ip}:{port} error: {e}")
