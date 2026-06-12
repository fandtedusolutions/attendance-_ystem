import requests
from requests.auth import HTTPDigestAuth

ip = "192.168.0.101"
username = "admin"
password = "Nisa@408"

url = f"http://{ip}/ISAPI/AccessControl/AcsEvent?format=json"
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
    print("Testing http connection with digest auth...")
    r = requests.post(url, json=payload, auth=HTTPDigestAuth(username, password), timeout=5)
    print(f"Status Code: {r.status_code}")
    print(f"Final URL: {r.url}")
    print(f"Redirect history: {r.history}")
    print(f"Response: {r.text[:500]}")
except Exception as e:
    print(f"Failed: {e}")
