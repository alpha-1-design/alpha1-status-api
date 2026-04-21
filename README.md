# Alpha-1 Studio Status Monitor

Production monitoring dashboard for Alpha-1 Studio services.

## Architecture

```
alpha1-status/
├── backend/           # Flask API (Render)
│   ├── app.py       # Main API with all endpoints
│   └── render.yaml  # Render deployment config
└── frontend/        # Dashboard (Vercel)
    ├── index.html  # Main dashboard
    ├── manifest.json
    ├── sw.js
    └── icon.svg
```

## Services Monitored

| Service | Host | Stack | URL |
|---------|------|-------|-----|
| AETHER AI | Vercel+Render | Flask, Python, OpenRouter, Claude | https://aether-pwa.vercel.app |
| GRACEMANAGER | Render | Node.js, WebSocket, Three.js, USGS, GDELT | https://gracemanager.onrender.com |
| FAMILYGAMENIGHT | Android | TypeScript, Capacitor 6, Vite | GitHub Repo |
| UNIVERSAL TOOLBOX | Vercel | Static, 700+ Tools | https://alpha1studio.vercel.app |
| PRIVACY TOOLKIT | Vercel | PWA, VirusTotal v3 | https://privacy-toolkit-ten.vercel.app |
| REHOBOTH KITCHEN | Vercel+Railway | React, MongoDB, Cloudinary | https://rehoboth-kitchen-app.vercel.app |

## API Endpoints

| Endpoint | Description |
|---------|------------|
| `/api/status` | Full status JSON (all services) |
| `/api/ping` | Quick health check |
| `/api/repos` | All GitHub repos |
| `/api/metrics` | Uptime/latency metrics |
| `/api/incidents` | Incident log |
| `/api/refresh` | Manual refresh (POST) |
| `/api/service/<id>` | Single service detail |

## Environment Variables (Render)

```
GITHUB_TOKEN      — GitHub personal access token (repo scope)
VERCEL_TOKEN     — Vercel API token
DISCORD_WEBHOOK — Discord webhook URL (optional)
ALERT_COOLDOWN   — Seconds between alerts (default 300)
```

## Local Development

```bash
# Backend
cd backend
pip install -r requirements.txt
python app.py

# Frontend
# Open index.html in browser, or serve with:
cd frontend
python -m http.server 8000
```

## Keyboard Shortcuts

- `r` — Refresh data
- `s` — Open settings
- `1-6` — Jump to service

## Deployment

- Backend: Render (free tier) — auto-deploys from GitHub
- Frontend: Vercel — auto-deploys from GitHub

## Features

- Real-time service status monitoring
- Latency tracking per service
- Discord alerts on status changes
- GitHub integration (commits, repos)
- PWA support (offline capable)
- Multiple views: Main, Metrics, Tree, Digest, Connect