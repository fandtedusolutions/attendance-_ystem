import requests
from requests.auth import HTTPDigestAuth
import urllib3
urllib3.disable_warnings()

ip = "192.168.0.101"
username = "admin"
password = "Nisa@408"

def test_endpoint(url):
    payload = {
        "AcsEventCond": {
            "searchID": "1",
            "searchResultPosition": 0,
            "maxResults": 1,
            "major": 5,
            "minor": 38
        }
    }
    try:
        session = requests.Session()
        session.auth = HTTPDigestAuth(username, password)
        session.verify = False
        
        print(f"\n--- Testing URL: {url} ---")
        # Pre-request or normal request
        r = session.post(url, json=payload, timeout=5)
        print(f"Status code: {r.status_code}")
        print(f"Content-Type: {r.headers.get('Content-Type')}")
        print(f"Headers: {dict(r.headers)}")
        print(f"Response snippet: {r.text[:300]}")
    except Exception as e:
        print(f"Error: {e}")

# Test HTTP and HTTPS
test_endpoint(f"http://{ip}/ISAPI/AccessControl/AcsEvent?format=json")
test_endpoint(f"https://{ip}/ISAPI/AccessControl/AcsEvent?format=json")
