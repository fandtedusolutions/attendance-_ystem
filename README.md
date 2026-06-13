# Natdemy Attendance System

## Overview
This repository contains a **Docker‑Compose** based deployment of a Django attendance tracking system that receives punch events from a Hikvision device, broadcasts them through **Redis** + **Django Channels**, and displays a live dashboard with WebSocket updates.

## Quick Start on a New Machine
The following steps assume a fresh **Linux** host (Ubuntu/Debian, Fedora, Raspberry Pi, etc.) with **sudo** access.

### 1. Install prerequisites
```bash
# Docker Engine (latest)
curl -fsSL https://get.docker.com | sh
# Docker Compose plugin (Docker 20.10+ includes the `docker compose` sub‑command)
sudo apt-get install -y docker-compose-plugin
# Optional: add your user to the docker group (avoid sudo for every docker command)
sudo usermod -aG docker $USER && newgrp docker
# Git (to clone this repo)
sudo apt-get install -y git
```

### 2. Clone the repository
```bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/<YOUR_USERNAME>/Natdemy.git Attendance   # replace with the actual URL
cd Attendance
```

### 3. Configure environment variables
Create a `.env` file in the project root (next to `docker‑compose.yml`).
```bash
cat > .env <<EOF
# Hikvision device credentials
HIKVISION_IP=192.168.1.50
HIKVISION_USERNAME=admin
HIKVISION_PASSWORD=your_password

# Optional ERP webhook (if you use it)
ERP_WEBHOOK_URL=https://my‑erp.example.com/webhook
ERP_WEBHOOK_TOKEN=abcd1234
EOF
```
> **Note:** The Docker‑Compose file already loads this file for the `web` and `monitor` services.

### 4. Build and start the stack
```bash
docker compose up -d --build
```
Verify everything is running:
```bash
docker compose ps
```
You should see `web`, `monitor`, `redis` (and optionally a database) listed as **Up**.

# Monitor container logs
```bash
# Follow the web container logs
docker compose logs -f web

# In another terminal, you can also watch the monitor logs
docker compose logs -f monitor
```

### 5. Access the dashboard
Open a browser and navigate to:
```
http://<HOST_IP>:8000/monitor/
```
Replace `<HOST_IP>` with the IP address of the machine running the containers.

### 6. (Optional) Run as a systemd service
If you want the stack to start automatically after a reboot:
1. Copy the provided service file:
```bash
sudo cp attendance-stack.service /etc/systemd/system/
# Edit the WorkingDirectory line if you placed the repo elsewhere
sudo nano /etc/systemd/system/attendance-stack.service
```
2. Enable and start it:
```bash
sudo systemctl daemon-reload
sudo systemctl enable attendance-stack.service
sudo systemctl start attendance-stack.service
```
3. Check status:
```bash
sudo systemctl status attendance-stack.service
```

### 7. Verify real‑time updates
1. Open the developer console (F12 → Console) on the dashboard page. You should see:
   - `Connecting to WebSocket: ws://<HOST>:8000/ws/attendance/`
   - `WebSocket connection established!`
2. Trigger a punch (real device or via the API endpoint `/api/broadcast/`).
3. You should see:
   - A toast notification like `John Doe checked in!`
   - An optional chime sound
   - The new entry appear instantly in the live feed
   - Stats cards update automatically

### 8. Common troubleshooting
| Symptom | Fix |
|---|---|
| **WebSocket refuses to connect** | Ensure the host can reach port 8000 (open firewall: `sudo ufw allow 8000/tcp`). Verify `monitor.routing.websocket_urlpatterns` contains `ws/attendance/`. |
| **No punch events appear** | Check `docker compose logs monitor` for `attendance_punch` logs. Verify `CHANNEL_LAYERS` points to the Redis container (`redis://redis:6379/0`). |
| **Docker compose fails to build** | Make sure you have internet access for the base images. If the target machine is offline, export the images from another host (`docker save … > images.tar`) and load them (`docker load -i images.tar`). |

## Repository structure
```
Attendance/
├─ attendance_system/           # Django project (settings, wsgi, asgi)
├─ monitor/                     # Monitor app (models, consumers, signals)
│   ├─ templates/monitor/      # HTML dashboard
│   └─ routing.py              # WebSocket routing
├─ docker-compose.yml           # Docker Compose definition
├─ .gitignore                  # Ignored files (added by this repo)
├─ README.md                   # You are reading it!
└─ attendance-stack.service    # Systemd unit (optional)
```

---
*Feel free to open an issue or PR if you run into any problems.*
