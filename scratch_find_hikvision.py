import socket
import threading
import requests
import urllib3
urllib3.disable_warnings()

ips = [
    "192.168.0.100", "192.168.0.102", "192.168.0.106", "192.168.0.107", 
    "192.168.0.108", "192.168.0.109", "192.168.0.110", "192.168.0.111", 
    "192.168.0.113", "192.168.0.115", "192.168.0.118", "192.168.0.120", 
    "192.168.0.121", "192.168.0.122", "192.168.0.124", "192.168.0.127", 
    "192.168.0.128", "192.168.0.131", "192.168.0.133", "192.168.0.134", 
    "192.168.0.135", "192.168.0.144", "192.168.0.145", "192.168.0.146", 
    "192.168.0.150"
]

found_ips = []

def scan_ip(ip):
    # Try port 443 first
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    result = s.connect_ex((ip, 443))
    s.close()
    
    if result == 0:
        print(f"IP {ip}: Port 443 is OPEN. Checking if it is Hikvision...")
        try:
            url = f"https://{ip}/ISAPI/System/deviceInfo"
            r = requests.get(url, timeout=2, verify=False)
            # Hikvision usually returns 401 with digest headers or 200 with XML/JSON
            if "Hikvision" in r.headers.get("Server", "") or "WWW-Authenticate" in r.headers:
                print(f"🔥 SUCCESS: Found Hikvision device on {ip} (port 443)!")
                found_ips.append((ip, 443))
        except Exception as e:
            print(f"IP {ip} check error: {e}")
            
    # Also scan port 80 just in case
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.settimeout(0.5)
    result2 = s2.connect_ex((ip, 80))
    s2.close()
    
    if result2 == 0:
        try:
            url = f"http://{ip}/ISAPI/System/deviceInfo"
            r = requests.get(url, timeout=2)
            if "Hikvision" in r.headers.get("Server", "") or "WWW-Authenticate" in r.headers:
                print(f"🔥 SUCCESS: Found Hikvision device on {ip} (port 80)!")
                found_ips.append((ip, 80))
        except Exception:
            pass

threads = []
for ip in ips:
    t = threading.Thread(target=scan_ip, args=(ip,))
    threads.append(t)
    t.start()

for t in threads:
    t.join()

print("\nScan complete. Found IPs:", found_ips)
