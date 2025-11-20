# Connectivity Monitor Dashboard

Lightweight Docker-based network probe + web dashboard for tracking:

- RTT (latency)
- Packet loss
- Uptime history
- Public IP changes
- Multi-target probing
- Daily summaries
- CSV exports
- Home Assistant integration

Designed for ISP troubleshooting and long-term monitoring.

---

## ⭐ Features

### **Probe Engine**
- Runs every N seconds
- Logs to JSONL (`/logs/connectivity.log`)
- Supports named multi-target probing  
  Example: `GoogleDNS=8.8.8.8,Cloudflare=1.1.1.1`

### **Dashboard Features**
- Live charts (RTT + uptime)
- Raw log table (sortable)
- Daily roll-up summaries
- Per-day detail pages
- CSV export for:
  - Full daily summary
  - Individual daily logs
- Editable targets + interval from UI

### **API Endpoints**
| Endpoint | Description |
|---------|-------------|
| `/data` | Latest N records (JSON) |
| `/daily` | All daily summaries (JSON) |
| `/day?date=YYYY-MM-DD` | Full day detail page |
| `/config` | Update targets + interval (POST) |

---

## 🚀 Quick Start (Docker Compose)

```bash
git clone https://github.com/YOURNAME/connectivity-monitor.git
cd connectivity-monitor
docker compose up -d --build
```

Dashboard will be available at:  
**http://localhost:8080**  
(or behind your reverse proxy)

---

## 🔐 Secure Deployment

Recommend placing behind a reverse proxy:

- nginx  
- SWAG  
- Nginx Proxy Manager  
- Traefik  
- Caddy  

Add **basic auth** and TLS for secure access.

---

## 🏠 Home Assistant Integration

Use the included:

```
homeassistant/connectivity_monitor.yaml
```

This provides:

- RTT sensor
- Loss sensor
- Public IP sensor
- Source IP sensor
- “Internet Up” binary sensor
- Sidebar dashboard (HTTPS-safe)

Before loading the package, add secrets for your hostname and credentials so you avoid storing personal data in version control:

```
# secrets.yaml
connectivity_host: https://connectivity.example.com/data
connectivity_username: your_username
connectivity_password: your_password
```

---

## 📁 Logs

All logs are stored at:

```
./logs/connectivity.log
```

They persist across container restarts.

---

## 🧪 CSV Export

The UI supports:

- Daily summary CSV export
- Per-day CSV export
- Full JSON log usable for analysis

---

## 🛠 Development

Run the web UI locally:

```bash
cd app
python3 webserver.py
```

### Running Tests

Install dev dependencies and execute the automated suite:

```bash
python -m pip install -r requirements-test.txt
pytest
```

---

## 📝 License

MIT License (recommended)

