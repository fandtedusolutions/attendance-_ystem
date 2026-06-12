import socket
import threading
import requests
import urllib3
urllib3.disable_warnings()

username = "admin"
password = "Nisa@408"

found_devices = []
lock = threading.Lock()

def scan_ip(ip):
    # Scan port 80 and 443
    for port in [80, 443]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.4)
        result = s.connect_ex((ip, port))
        s.close()
        
        if result == 0:
            scheme = "https" if port == 443 else "http"
            url = f"{scheme}://{ip}:{port}/ISAPI/System/deviceInfo"
            try:
                # First try with credentials
                r = requests.get(url, auth=requests.auth.HTTPDigestAuth(username, password), timeout=1.5, verify=False)
                if r.status_code == 200:
                    with lock:
                        print(f"🔥 FOUND AUTHENTICATED DEVICE: {scheme}://{ip}:{port}")
                        found_devices.append((ip, port, scheme, True))
                    return
                elif r.status_code == 401:
                    # Device is there but wrong credentials or just challenges us
                    auth_header = r.headers.get("WWW-Authenticate", "")
                    with lock:
                        print(f"Device at {scheme}://{ip}:{port} returned 401. Auth header: {auth_header[:100]}")
                        found_devices.append((ip, port, scheme, False))
            except Exception:
                pass

threads = []
print("Starting full subnet scan (192.168.0.2 to 254)...")
for i in range(2, 255):
    ip = f"192.168.0.{i}"
    if ip == "192.168.0.176":  # skip ourselves
        continue
    t = threading.Thread(target=scan_ip, args=(ip,))
    threads.append(t)
    t.start()

for t in threads:
    t.join()

print("\nScan complete. Authenticated devices found:")
for ip, port, scheme, auth_ok in found_devices:
    if auth_ok:
        print(f"- {scheme}://{ip}:{port}")
