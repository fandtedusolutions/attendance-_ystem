# Hikvision Attendance System: Complete Working Workflow

This document provides a comprehensive overview of the design, data pipelines, and workflow of the Natdemy Hikvision Live Attendance Monitoring System.

---

## 1. System Architecture

```mermaid
graph TD
    subgraph Hikvision Device (192.168.0.101)
        A[Biometric / Card Log Database]
    end

    subgraph Local Server (Django Dashboard)
        B[Background Monitor Loop]
        C[Django App Core / Database]
        D[Channels WebSocket Server]
    end

    subgraph Clients
        E[Connected Web Dashboards]
    end

    subgraph ERP System
        F[External ERP Database]
    end

    A -->|1. Polled via HTTPS POST| B
    B -->|2. Saves Punch Event| C
    C -->|3. Triggers DB Signals| B
    B -->|4. Broadcasts WebSocket| D
    D -->|5. Updates Screen Live| E
    C -->|6. Asynchronous Webhook POST| F
```

---

## 2. Component Directory & Responsibilities

| Component | File Path | Responsibility |
| :--- | :--- | :--- |
| **Settings** | `attendance_system/settings.py` | Stores Hikvision IP/credentials, and the ERP Webhook URL configurations. |
| **Database Schema** | `monitor/models.py` | Holds `Employee` (device key mappings), `PunchEvent` (punches log), and `SystemStatus` (errors log). |
| **Monitor Daemon** | `monitor/management/commands/monitor_device.py` | High-performance background polling command listening to device logs. |
| **CLI Sync Tool** | `monitor/management/commands/fetch_events.py` | Manual recovery command allowing date-specific fetches. |
| **Real-time Forwarder**| `monitor/signals.py` | Intercepts new punch saves and sends them asynchronously to the ERP. |
| **WebSockets** | `monitor/consumers.py` & `routing.py` | Establishes full-duplex pipelines with web browsers. |
| **Dashboard UI** | `monitor/templates/monitor/dashboard.html` | Modern interface displaying today's attendance stats, active feed, and manual sync buttons. |
| **Day Report UI** | `monitor/templates/monitor/day_report.html` | Interactive calendar and day-wise attendance report showing in/out times and hours. |
| **Reports API** | `monitor/views.py` | Calculates employee in/out times dynamically via aggregates (`Min`/`Max` punch times). |

---

## 3. Step-by-Step Data Workflow

### Step 1: Employee Registry Synchronization
* The local server polls `/ISAPI/AccessControl/UserInfo/Search?format=json` using a digest-authenticated session.
* User mappings are stored locally in the `Employee` model, matching the device's `employee_id`.

### Step 2: Background Live Monitoring (`monitor_device`)
* The daemon runs continuously (`while True`) with a **5-second polling interval** to prevent device webserver crashes.
* **Smart Back-Scan Algorithm (O(Missed Events))**:
  1. Requests the **total number** of event logs on the device.
  2. Queries the device's log **backwards** starting from the end, using a page size of **30** (enforced by Hikvision API constraints).
  3. Inspects the `serialNo` of the logs.
  4. Continues scanning backwards until it encounters an event with a `serialNo` equal to or less than the **last recorded serial number** in the local database.
  5. This guarantees that only new events are fetched and no duplicate checks occur.

### Step 3: Local Persistence & Formatting
* Events are parsed, times are standardized to **IST (Asia/Kolkata)**, and saved into the `PunchEvent` model.
* Statistics (Total present count, absent count) are re-calculated based on explicit IST timezone boundaries.

### Step 4: WebSockets Broadcast
* The monitor server makes an HTTP-POST request internally to `/api/broadcast/`.
* The broadcast view dispatches the updated attendance feed card and updated header stats to `monitor_status` and `attendance_punch` WebSocket channels.
* Connected browsers dynamically update their DOM instantly without a page reload.

### Step 5: Asynchronous ERP Integration (Webhook)
* The creation of a new `PunchEvent` fires a Django `post_save` signal receiver (`monitor/signals.py`).
* If `ERP_WEBHOOK_URL` is configured in `settings.py`, the signal constructs a JSON payload:
  ```json
  {
    "serial_no": 87429,
    "employee_id": "2220675785",
    "name": "bilal",
    "time": "2026-06-11T14:18:00+05:30",
    "verify_mode": "Card"
  }
  ```
* Spawns a separate, non-blocking **background thread** using Python's `threading.Thread` to send the payload to the ERP. This ensures that slow or offline ERP servers do not block local server operations.

---

## 4. Key Management Commands

### Start the Background Live Monitor
Run this process continuously (usually managed via systemd in production) to enable live syncing:
```bash
python manage.py monitor_device
```

### Manually Recover Logs for a Date
In case of server downtime or network failure, you can catch up on logs for any specific date using:
```bash
python manage.py fetch_events --date YYYY-MM-DD
```
*(Example: `python manage.py fetch_events --date 2026-06-11`)*

---

## 5. UI Control Options
* **Sync Device Employees**: Triggers immediate user import from the Hikvision device to the local system database.
* **Recover Today**: Sends an HTTP request that scans today's logs backwards on the device to fetch any events missed while the server/monitor process was down.
