# Natdemy Attendance System — Setup Guide

> A real-time attendance monitoring system that connects to a **Hikvision Access Control** biometric device and displays live punch events on a web dashboard.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Network Setup](#network-setup)
3. [Finding the Hikvision Device IP](#finding-the-hikvision-device-ip)
4. [Project Setup](#project-setup)
5. [Environment Configuration](#environment-configuration)
6. [Running with Docker (Recommended)](#running-with-docker-recommended)
7. [Running without Docker (Development)](#running-without-docker-development)
8. [Accessing the Dashboard](#accessing-the-dashboard)
9. [Verifying the Connection](#verifying-the-connection)
10. [Troubleshooting](#troubleshooting)
11. [Architecture Overview](#architecture-overview)

---

## Prerequisites

### Hardware
- A **Hikvision Access Control** device (e.g., DS-K1T320 series, DS-K1A802 series, or similar)
- A **local server** (Linux PC/laptop) connected to the same network as the device
- Both the server and the Hikvision device must be on the **same LAN subnet** (e.g., `192.168.0.x`)

### Software
- **Docker** and **Docker Compose** (for production deployment)
- **Python 3.12+** (for development without Docker)
- **Git** (to clone the project)

### Install Docker (Ubuntu/Debian)
```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add your user to docker group (avoids needing sudo)
sudo usermod -aG docker $USER

# Verify installation
docker --version
docker compose version
```

---

## Network Setup

### Server and Device Must Be on the Same Network

```
┌─────────────────────────────────┐
│         Local Network           │
│         192.168.0.0/24          │
│                                 │
│  ┌──────────┐   ┌────────────┐  │
│  │ Hikvision│   │ Local      │  │
│  │ Device   │   │ Server     │  │
│  │ .100     │   │ .176       │  │
│  └──────────┘   └────────────┘  │
│         │              │        │
│         └──────┬───────┘        │
│                │                │
│         ┌──────┴──────┐         │
│         │   Router    │         │
│         │   .1        │         │
│         └─────────────┘         │
└─────────────────────────────────┘
```

### Important: Use a Static IP for the Hikvision Device

Hikvision devices default to DHCP, which means their IP can change after a reboot. **Always set a static IP** to avoid connection issues.

#### Option A: Set Static IP on the Device
1. On the device screen, navigate to: **Menu → Communication → Network**
2. Turn **DHCP OFF**
3. Set a static IP address (e.g., `192.168.0.100`)
4. Set Subnet Mask: `255.255.255.0`
5. Set Default Gateway: `192.168.0.1`
6. Press **OK** to save

#### Option B: Reserve IP in Your Router (DHCP Reservation)
1. Log into your router's admin panel (usually `http://192.168.0.1`)
2. Find the **DHCP Reservation** or **Address Reservation** section
3. Add the Hikvision device's **MAC address** and assign a fixed IP
4. The MAC address can be found on the device label or via network scanning (see below)

---

## Finding the Hikvision Device IP

If you don't know the device's current IP, use one of these methods:

### Method 1: Check the Device Screen
Navigate to **Menu → Communication → Network** on the device display. The IP address will be shown.

### Method 2: Scan the Network
```bash
# View all devices in the ARP table
arp -a

# Or use nmap to scan the subnet
sudo nmap -sn 192.168.0.0/24
```

### Method 3: Look for Hikvision MAC Addresses
Hikvision/Prama Hikvision India devices have MAC prefixes like:
- `24:B1:05:xx:xx:xx` (Prama Hikvision India)
- `BC:29:78:xx:xx:xx` (Prama Hikvision India)
- `C0:56:E3:xx:xx:xx` (Hikvision)
- `44:19:B6:xx:xx:xx` (Hikvision)

```bash
# Check ARP table and match MACs
arp -a | grep -i "24:b1:05\|bc:29:78\|c0:56:e3\|44:19:b6"
```

### Method 4: Test with curl
Once you have a candidate IP, verify it's the Hikvision device:
```bash
curl -s -m 5 --digest -u admin:YOUR_PASSWORD \
  -H "Content-Type: application/json" \
  -X POST \
  -d '{"AcsEventCond":{"searchID":"1","searchResultPosition":0,"maxResults":1,"major":5,"minor":38}}' \
  "http://DEVICE_IP/ISAPI/AccessControl/AcsEvent?format=json"
```

A successful response looks like:
```json
{
  "AcsEvent": {
    "searchID": "1",
    "totalMatches": 32158,
    "responseStatusStrg": "MORE",
    "numOfMatches": 1,
    "InfoList": [{ "name": "EMPLOYEE_NAME", "employeeNoString": "100", ... }]
  }
}
```

---

## Project Setup

### 1. Clone the Repository
```bash
git clone <repository-url> Attendance
cd Attendance
```

### 2. Project Structure
```
Attendance/
├── attendance_system/          # Django project settings
│   ├── settings.py
│   ├── asgi.py
│   └── urls.py
├── monitor/                    # Main application
│   ├── management/commands/
│   │   └── monitor_device.py   # Background monitor service
│   ├── templates/monitor/
│   │   ├── dashboard.html      # Live attendance dashboard
│   │   └── day_report.html     # Daily report page
│   ├── models.py               # Employee & PunchEvent models
│   ├── views.py                # API endpoints & views
│   └── utils.py                # Device session & sync helpers
├── docker-compose.yml          # Docker orchestration
├── Dockerfile                  # Container build instructions
├── requirements.txt            # Python dependencies
├── .env                        # Environment configuration
└── db.sqlite3                  # SQLite database (auto-created)
```

---

## Environment Configuration

### Create the `.env` File
Create a `.env` file in the project root with the following variables:

```env
# Hikvision Device Connection
HIKVISION_IP=192.168.0.100          # IP address of the Hikvision device
HIKVISION_PROTOCOL=http             # http or https (most devices use http)
HIKVISION_USERNAME=admin            # Device admin username
HIKVISION_PASSWORD=YourPassword     # Device admin password

# ERP Webhook (Optional — for forwarding punches to an external system)
ERP_WEBHOOK_URL=                    # Leave empty to disable
```

### Configuration Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `HIKVISION_IP` | ✅ | `192.168.0.100` | IP address of the biometric device |
| `HIKVISION_PROTOCOL` | ❌ | `http` | `http` or `https` — use `http` for most devices |
| `HIKVISION_USERNAME` | ✅ | `admin` | Device admin username |
| `HIKVISION_PASSWORD` | ✅ | — | Device admin password |
| `ERP_WEBHOOK_URL` | ❌ | — | External URL to forward punch events to |
| `ERP_WEBHOOK_TOKEN` | ❌ | — | Bearer token for webhook authentication |

---

## Running with Docker (Recommended)

Docker runs three containers:
- **web** — The Django web server (Daphne ASGI) on port 80
- **monitor** — The background device polling service
- **redis** — Message broker for WebSocket communication

### 1. Configure `docker-compose.yml`

Update the `ports` binding for the `web` service to match your server's IP:

```yaml
services:
  web:
    build: .
    command: daphne -b 0.0.0.0 -p 8000 attendance_system.asgi:application
    ports:
      - "YOUR_SERVER_IP:80:8000"    # e.g., "192.168.0.176:80:8000"
    volumes:
      - /path/to/Attendance:/app
    restart: always
    environment:
      - PYTHONUNBUFFERED=1
      - REDIS_URL=redis://redis:6379/0
    env_file:
      - .env

  monitor:
    build: .
    command: python manage.py monitor_device
    volumes:
      - /path/to/Attendance:/app
    restart: always
    environment:
      - PYTHONUNBUFFERED=1
      - REDIS_URL=redis://redis:6379/0
    env_file:
      - .env

  redis:
    image: redis:7-alpine
    restart: always
    ports:
      - "6379:6379"
```

> **Note:** Replace `YOUR_SERVER_IP` with the actual LAN IP of your server and update the `volumes` path to where the project is located.

### 2. Add `env_file` to Docker Compose

Make sure both `web` and `monitor` services have:
```yaml
env_file:
  - .env
```
This ensures the Hikvision credentials from `.env` are injected into the containers.

### 3. Build and Start
```bash
# Build images and start all containers
docker compose up -d --build

# Verify all containers are running
docker compose ps
```

### 4. Check Logs
```bash
# Monitor service logs (should show "Initial sync complete. Synced X employees.")
docker compose logs -f monitor

# Web server logs
docker compose logs -f web

# All logs
docker compose logs -f
```

### 5. Stop / Restart
```bash
# Stop all containers
docker compose down

# Restart after config changes
docker compose down && docker compose up -d --build
```

---

## Running without Docker (Development)

### 1. Create Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Database Migrations
```bash
python manage.py migrate
```

### 3. Start the Web Server
```bash
# In terminal 1
python manage.py runserver 0.0.0.0:8000
```

### 4. Start the Monitor Service
```bash
# In terminal 2
python manage.py monitor_device
```

> **Important:** Both commands must run simultaneously. The web server serves the dashboard, while the monitor service polls the device for new events.

---

## Accessing the Dashboard

| Interface | URL |
|---|---|
| **Live Dashboard** | `http://YOUR_SERVER_IP/` |
| **Day Report** | `http://YOUR_SERVER_IP/report/` |
| **Django Admin** | `http://YOUR_SERVER_IP/admin/` |

### Dashboard Features
- **Live Attendance Feed** — Real-time punch events via WebSocket
- **Employee Directory** — All registered employees with presence status
- **Statistics Cards** — Total employees, checked in today, absent count
- **Recover Today** — Button to manually fetch any missed punches
- **Sync Device Employees** — Re-sync employee list from the device
- **Day Report** — Detailed daily report with in/out times and durations
- **CSV Export** — Download attendance data as CSV
- **Resend Webhooks** — Retry sending failed/unsent punch events to the external ERP webhook on demand.

---

## ERP Webhook & Resend Mechanism

To prevent data loss caused by network instability or temporary ERP server downtime, the system provides a robust webhook delivery tracking and manual recovery mechanism.

### 1. Delivery Tracking (`shared_to_erp`)
Each `PunchEvent` has a `shared_to_erp` boolean flag in the database:
- **`True`**: Webhook payload was successfully sent and received a `2xx` HTTP response from the ERP server.
- **`False`**: Delivery failed due to timeout, network issue, or invalid ERP response.

### 2. Live Status Badge in Day Report
Inside the **Day Report** page:
1. Select a date.
2. Click on an employee row to expand their detailed punch timeline.
3. Every punch has a colored badge:
   - <span style="color: #10b981; font-weight: bold;">✓ Sent</span>: Webhook delivered successfully.
   - <span style="color: #f43f5e; font-weight: bold;">✗ Pending</span>: Webhook delivery failed or was not triggered yet.

### 3. Triggering Manual Webhook Resends
You can manually trigger resending at any time from two places:
- **Dashboard (Today)**: Click the **Resend Webhooks** button in the header. You will be prompted to choose:
  - **OK**: Resend ONLY failed/pending punches for today.
  - **Cancel**: Resend ALL punches for today.
- **Day Report (Any Date)**: Select a date on the calendar and click the **Resend Webhook** button in the report header. Choose:
  - **OK**: Resend ONLY failed/pending punches for that date.
  - **Cancel**: Resend ALL punches for that date.

---

## Verifying the Connection

### Step 1: Test Device Connectivity
```bash
# Ping the device
ping -c 3 192.168.0.100

# Test the Hikvision API directly
curl -s --digest -u admin:YourPassword \
  -H "Content-Type: application/json" \
  -X POST \
  -d '{"AcsEventCond":{"searchID":"1","searchResultPosition":0,"maxResults":1,"major":5,"minor":38}}' \
  "http://192.168.0.100/ISAPI/AccessControl/AcsEvent?format=json" | python3 -m json.tool
```

### Step 2: Check Monitor Logs
```bash
# Docker
docker compose logs monitor --tail 20

# Local
# Check the terminal running `python manage.py monitor_device`
```

**Expected success output:**
```
Starting Hikvision Monitor for IP: 192.168.0.100 (http)
Running initial user synchronization...
Initial sync complete. Synced 82 employees.
Resuming from last DB serial: 89365
Monitor started. Last serial: 89365
New punch: EMPLOYEE_NAME (ID) at 30 Jun 2026, 10:15:30 AM
```

### Step 3: Verify in Browser
Open `http://YOUR_SERVER_IP/` — the dashboard should show:
- ✅ **MONITOR ACTIVE** badge (green)
- ✅ Employee count in the stats cards
- ✅ Recent punch events in the Live Attendance Feed

---

## Troubleshooting

### "Expecting value: line 1 column 1 (char 0)"
**Cause:** The device returned an empty or HTML response instead of JSON.
**Fix:**
- Verify the IP is correct — open `http://DEVICE_IP` in your browser. You should see the Hikvision login page, NOT a router page.
- If you see a TP-Link/router page, the IP is wrong. Find the correct one using the [scanning methods above](#finding-the-hikvision-device-ip).

### "Connection timed out" or "RemoteDisconnected"
**Cause:** The server cannot reach the device on the network.
**Fix:**
- Ensure both are on the same subnet (`192.168.0.x`)
- Check if the device is powered on and connected via Ethernet
- Try: `ping DEVICE_IP`
- If using Docker, ensure the container has host network access (the default bridge network should work if both are on the same LAN)

### "401 Unauthorized"
**Cause:** Wrong username or password.
**Fix:**
- Verify credentials by logging into the device's web UI at `http://DEVICE_IP`
- Update `HIKVISION_USERNAME` and `HIKVISION_PASSWORD` in `.env`
- Rebuild: `docker compose down && docker compose up -d --build`

### "Service Unavailable: /api/recover/" (503)
**Cause:** The Recover Today API cannot reach the device.
**Fix:** Same as the connection timeout fix above — verify IP and credentials.

### "database is locked" (SQLite)
**Cause:** Multiple processes writing to SQLite simultaneously.
**Fix:** This is usually transient. The monitor will retry automatically. For production, consider migrating to PostgreSQL.

### Device IP Changed (DHCP)
**Cause:** The device is set to DHCP and the router assigned a new IP.
**Fix:**
1. Find the new IP (check device screen or scan the network)
2. Update `HIKVISION_IP` in `.env`
3. Restart: `docker compose down && docker compose up -d --build`
4. **Permanently fix:** Set a static IP on the device or create a DHCP reservation in your router

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Compose Stack                      │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │   Redis      │  │  Web Server  │  │  Monitor Service │   │
│  │  (Pub/Sub)   │◄─│  (Daphne)    │◄─│  (Django Mgmt)   │   │
│  │  Port 6379   │  │  Port 80     │  │  Polls device    │   │
│  └─────────────┘  └──────┬───────┘  └────────┬─────────┘   │
│                          │                    │             │
└──────────────────────────┼────────────────────┼─────────────┘
                           │                    │
                    ┌──────┴───────┐    ┌───────┴──────────┐
                    │   Browser    │    │  Hikvision Device │
                    │  Dashboard   │    │  192.168.0.100    │
                    │  WebSocket   │    │  ISAPI REST API   │
                    └──────────────┘    └──────────────────┘
```

### Data Flow
1. **Monitor Service** polls the Hikvision device every 5 seconds via the ISAPI REST API
2. New punch events are saved to the **SQLite database**
3. The monitor broadcasts events to the **Web Server** via an internal HTTP API
4. The web server pushes events to all connected **Browsers** via **WebSocket**
5. If the WebSocket connection drops, browsers fall back to **AJAX polling**

---

## Quick Start Checklist

- [ ] Hikvision device powered on and connected to LAN
- [ ] Device IP identified and noted
- [ ] Static IP configured (device or router DHCP reservation)
- [ ] Project cloned to local server
- [ ] `.env` file created with correct IP, username, and password
- [ ] `docker-compose.yml` updated with server IP in ports
- [ ] Docker containers built and started (`docker compose up -d --build`)
- [ ] Monitor logs show "Initial sync complete" and employee count
- [ ] Dashboard accessible at `http://SERVER_IP/`
- [ ] Test punch on device appears in live feed

---

*Last updated: June 2026*
