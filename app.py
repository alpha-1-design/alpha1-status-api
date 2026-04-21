"""
Alpha-1 Studio — Status API Backend
Runs on Render (free tier). Fetches real data from GitHub API, Vercel API,
and pings all services directly.

ENV VARS REQUIRED (set in Render dashboard):
  GITHUB_TOKEN      — Personal access token (repo scope)
  VERCEL_TOKEN      — Vercel API token
  DISCORD_WEBHOOK   — Discord webhook URL for alerts (optional)
  ALERT_COOLDOWN    — Seconds between alerts (default 300 = 5 min)
"""

import os, time, asyncio, httpx, json
from datetime import datetime, timezone
from flask import Flask, jsonify
from flask_cors import CORS
from functools import lru_cache
import threading

app = Flask(__name__)
CORS(app)

GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
VERCEL_TOKEN   = os.environ.get("VERCEL_TOKEN", "")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
ALERT_COOLDOWN = int(os.environ.get("ALERT_COOLDOWN", 300))
GITHUB_USER    = "alpha-1-design"

_previous_state = {}
_last_alert_time = {}

# ── All services to monitor ──────────────────────────────────────────────────
SERVICES = [
    {
        "id":      "aether",
        "name":    "AETHER AI",
        "url":     "https://aether-pwa.vercel.app",
        "backend": "https://aether-pwa.onrender.com",
        "group":   "flagship",
        "repo":    "aether-pwa",
        "host":    "vercel+render",
        "stack":   ["Flask", "Python", "OpenRouter", "Claude API", "PWA", "Twi NLP"],
        "desc":    "Ghana-tuned AI PWA — multi-model, Twi support, low-data mobile-first"
    },
    {
        "id":      "gracemanager",
        "name":    "GRACEMANAGER",
        "url":     "https://gracemanager.onrender.com",
        "backend": None,
        "group":   "flagship",
        "repo":    "gracemanager",
        "host":    "render",
        "stack":   ["Node.js", "WebSocket", "Three.js", "USGS", "GDELT", "Reddit"],
        "desc":    "Real-time global situational awareness — 3D globe, WebSocket streaming"
    },
    {
        "id":      "familygamenight",
        "name":    "FAMILYGAMENIGHT",
        "url":     "https://github.com/alpha-1-design/FamilyGameNight",
        "backend": None,
        "group":   "flagship",
        "repo":    "FamilyGameNight",
        "host":    "android",
        "stack":   ["TypeScript", "Capacitor 6", "Vite", "Web Audio API"],
        "desc":    "Android app — 10 game engines, 330+ games"
    },
    {
        "id":      "toolbox",
        "name":    "UNIVERSAL TOOLBOX",
        "url":     "https://alpha1studio.vercel.app",
        "backend": None,
        "group":   "studio",
        "repo":    "universal-toolbox",
        "host":    "vercel",
        "stack":   ["Static", "Vercel", "No Auth", "700+ Tools"],
        "desc":    "700+ tools, 54 categories — updated weekly"
    },
    {
        "id":      "privacy",
        "name":    "PRIVACY TOOLKIT",
        "url":     "https://privacy-toolkit-ten.vercel.app",
        "backend": None,
        "group":   "studio",
        "repo":    "privacy-toolkit",
        "host":    "vercel",
        "stack":   ["Zero-Server", "PWA", "VirusTotal v3", "Heroicons"],
        "desc":    "13 browser-based security tools — fully local, no data collection"
    },
    {
        "id":      "rehoboth",
        "name":    "REHOBOTH KITCHEN",
        "url":     "https://rehoboth-kitchen-app.vercel.app",
        "backend": None,
        "group":   "studio",
        "repo":    "rehoboth-kitchen-app",
        "host":    "vercel+railway",
        "stack":   ["React", "MongoDB", "Cloudinary", "Express", "Web Push"],
        "desc":    "Client PWA — kitchen appliances store with push notifications"
    },
]

# ── In-memory cache ──────────────────────────────────────────────────────────
_cache = {"data": None, "ts": 0}
CACHE_TTL = 60  # seconds

# ── Metrics tracking (init after SERVICES defined) ────────────────────────
_metrics_history = {}
_incident_log = []

for svc in SERVICES:
    _metrics_history[svc["id"]] = {
        "latencies": [],
        "uptime_seconds": 0,
        "downtime_seconds": 0,
        "last_check": None,
        "last_status": "online"
    }
CACHE_TTL = 60  # seconds

def ping_url(url, timeout=8, max_retries=2):
    """Return (status, latency_ms) for a URL with retry logic."""
    if not url or "github.com" in url:
        return "github", None

    last_status = "offline"
    last_latency = None
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            t0 = time.time()
            r = httpx.get(url, timeout=timeout, follow_redirects=True)
            ms = round((time.time() - t0) * 1000)

            if r.status_code < 400:
                return "online", ms
            elif r.status_code < 500:
                return "degraded", ms
            else:
                last_status = "offline"
                last_latency = ms
                break

        except httpx.TimeoutException:
            last_status = "timeout"
            last_error = "timeout"
        except httpx.ConnectError as e:
            last_status = "offline"
            last_error = f"connection error: {e}"
        except httpx.DNSError as e:
            last_status = "offline"
            last_error = f"DNS error: {e}"
        except Exception as e:
            last_status = "offline"
            last_error = str(e)

        if attempt < max_retries:
            wait_time = (attempt + 1) * 2
            time.sleep(wait_time)

    return last_status, last_latency

def github_headers():
    h = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h

def fetch_github_commits(repo, n=5):
    """Fetch last n commits from a repo."""
    if not repo:
        return []
    try:
        url = f"https://api.github.com/repos/{GITHUB_USER}/{repo}/commits?per_page={n}"
        r = httpx.get(url, headers=github_headers(), timeout=10)
        if r.status_code != 200:
            return []
        commits = []
        for c in r.json():
            commits.append({
                "sha":     c["sha"][:7],
                "message": c["commit"]["message"].split("\n")[0][:80],
                "author":  c["commit"]["author"]["name"],
                "date":    c["commit"]["author"]["date"],
            })
        return commits
    except Exception:
        return []

def fetch_all_github_repos():
    """Fetch ALL repos for the GitHub user."""
    if not GITHUB_TOKEN:
        return []
    try:
        url = f"https://api.github.com/users/{GITHUB_USER}/repos?per_page=100&sort=pushed"
        r = httpx.get(url, headers=github_headers(), timeout=15)
        if r.status_code != 200:
            return []
        repos = []
        for d in r.json():
            repos.append({
                "name": d.get("name"),
                "full_name": d.get("full_name"),
                "description": d.get("description"),
                "url": d.get("html_url"),
                "stars": d.get("stargazers_count", 0),
                "forks": d.get("forks_count", 0),
                "language": d.get("language"),
                "pushed_at": d.get("pushed_at"),
                "created_at": d.get("created_at"),
                "default_branch": d.get("default_branch"),
                "size_kb": d.get("size"),
            })
        return repos
    except Exception as e:
        print(f"[REPOS ERROR] {e}")
        return []

def fetch_github_repo_meta(repo):
    """Fetch repo metadata — stars, last push, open issues."""
    if not repo:
        return {}
    try:
        url = f"https://api.github.com/repos/{GITHUB_USER}/{repo}"
        r = httpx.get(url, headers=github_headers(), timeout=10)
        if r.status_code != 200:
            return {}
        d = r.json()
        return {
            "stars":      d.get("stargazers_count", 0),
            "forks":      d.get("forks_count", 0),
            "open_issues": d.get("open_issues_count", 0),
            "pushed_at":  d.get("pushed_at"),
            "default_branch": d.get("default_branch", "main"),
        }
    except Exception:
        return {}

def fetch_vercel_deployments(project_name):
    """Fetch latest Vercel deployments for a project."""
    if not VERCEL_TOKEN or not project_name:
        return []
    try:
        url = f"https://api.vercel.com/v6/deployments?app={project_name}&limit=3"
        r = httpx.get(url, headers={"Authorization": f"Bearer {VERCEL_TOKEN}"}, timeout=10)
        if r.status_code != 200:
            return []
        deploys = []
        for d in r.json().get("deployments", []):
            deploys.append({
                "uid":     d.get("uid", "")[:8],
                "state":   d.get("state", ""),
                "created": d.get("createdAt"),
                "url":     d.get("url", ""),
            })
        return deploys
    except Exception:
        return []

def fetch_all_commits():
    """Fetch recent commits across all repos — aggregated feed."""
    all_commits = []
    repos = [s["repo"] for s in SERVICES if s["repo"]]
    for repo in repos:
        commits = fetch_github_commits(repo, n=3)
        for c in commits:
            c["repo"] = repo
            all_commits.append(c)
    all_commits.sort(key=lambda x: x.get("date", ""), reverse=True)
    return all_commits[:15]

def send_discord_alert(service_name, old_status, new_status, url):
    """Send alert to Discord webhook when service status changes."""
    global _last_alert_time

    if not DISCORD_WEBHOOK:
        return

    alert_key = f"{service_name}_{new_status}"
    now = time.time()

    if _last_alert_time.get(alert_key, 0) > now - ALERT_COOLDOWN:
        return

    _last_alert_time[alert_key] = now

    if new_status == "offline":
        color = 16711680
        emoji = "🔴"
        title = f"SERVICE DOWN"
        description = f"**{service_name}** is now **OFFLINE**"
    elif new_status == "online" and old_status in ("offline", "degraded"):
        color = 65280
        emoji = "🟢"
        title = "SERVICE RECOVERED"
        description = f"**{service_name}** is back **ONLINE**"
    elif new_status == "degraded":
        color = 16776960
        emoji = "🟡"
        title = "SERVICE DEGRADED"
        description = f"**{service_name}** is running **DEGRADED**"
    else:
        return

    payload = {
        "embeds": [{
            "title": f"{emoji} {title}",
            "description": description,
            "color": color,
            "fields": [
                {"name": "URL", "value": url, "inline": True},
                {"name": "Previous", "value": old_status.title(), "inline": True},
                {"name": "Current", "value": new_status.title(), "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Alpha-1 Status Monitor"}
        }]
    }

    try:
        httpx.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        print(f"[ALERT] {service_name}: {old_status} → {new_status}")
    except Exception as e:
        print(f"[ALERT ERROR] Failed to send Discord alert: {e}")

def check_and_alert(svc_id, old_status, new_status, url):
    """Check if status changed and send alert."""
    global _previous_state, _incident_log

    current_stored = _previous_state.get(svc_id, "online")

    if new_status != current_stored:
        send_discord_alert(svc_id, current_stored, new_status, url)
        _previous_state[svc_id] = new_status

        _incident_log.append({
            "service_id": svc_id,
            "old_status": current_stored,
            "new_status": new_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "url": url
        })
        if len(_incident_log) > 100:
            _incident_log[:] = _incident_log[-100:]

def build_payload():
    """Build the full status payload. Called every CACHE_TTL seconds."""
    now = datetime.now(timezone.utc).isoformat()
    services_data = []

    for svc in SERVICES:
        status, latency = ping_url(svc["url"])

        check_and_alert(svc["id"], _previous_state.get(svc["id"], "online"), status, svc["url"])

        repo_meta       = fetch_github_repo_meta(svc["repo"])
        commits         = fetch_github_commits(svc["repo"], n=3)
        vercel_deploys  = []

        if "vercel" in svc["host"]:
            vercel_deploys = fetch_vercel_deployments(svc["repo"])

        services_data.append({
            **svc,
            "status":         status,
            "latency_ms":     latency,
            "repo_meta":      repo_meta,
            "commits":        commits,
            "vercel_deploys": vercel_deploys,
            "checked_at":     now,
        })

        if svc["id"] in _metrics_history:
            m = _metrics_history[svc["id"]]
            if m["last_status"] in ("online", "degraded"):
                m["uptime_seconds"] += CACHE_TTL
            elif m["last_status"] == "offline":
                m["downtime_seconds"] += CACHE_TTL
            m["last_status"] = status
            m["last_check"] = now
            if latency:
                m["latencies"].append(latency)
                if len(m["latencies"]) > 100:
                    m["latencies"] = m["latencies"][-100:]

    all_commits = fetch_all_commits()

    # Summary stats
    online_count   = sum(1 for s in services_data if s["status"] == "online")
    offline_count  = sum(1 for s in services_data if s["status"] == "offline")
    degraded_count = sum(1 for s in services_data if s["status"] == "degraded")
    latencies      = [s["latency_ms"] for s in services_data if s["latency_ms"]]
    avg_latency    = round(sum(latencies) / len(latencies)) if latencies else None

    return {
        "generated_at": now,
        "summary": {
            "total":    len(SERVICES),
            "online":   online_count,
            "offline":  offline_count,
            "degraded": degraded_count,
            "avg_latency_ms": avg_latency,
            "health_pct": round((online_count / len(SERVICES)) * 100),
        },
        "services":     services_data,
        "commits_feed": all_commits,
    }

def refresh_cache():
    """Background thread that refreshes the cache every CACHE_TTL seconds."""
    while True:
        try:
            print(f"[{datetime.now().isoformat()}] Refreshing status cache...")
            _cache["data"] = build_payload()
            _cache["ts"]   = time.time()
            print(f"[{datetime.now().isoformat()}] Cache refreshed OK")
        except Exception as e:
            print(f"Cache refresh error: {e}")
        time.sleep(CACHE_TTL)

# Start background refresh thread
threading.Thread(target=refresh_cache, daemon=True).start()

# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "Alpha-1 Studio Status API", "version": "1.0"})

@app.route("/api/status")
def status():
    # If cache is empty (first boot), build synchronously
    if _cache["data"] is None:
        _cache["data"] = build_payload()
        _cache["ts"]   = time.time()
    age = round(time.time() - _cache["ts"])
    data = dict(_cache["data"])
    data["cache_age_seconds"] = age
    return jsonify(data)

@app.route("/api/ping")
def ping():
    """Quick health check for the API itself."""
    return jsonify({"ok": True, "ts": datetime.now(timezone.utc).isoformat()})

@app.route("/api/service/<service_id>")
def service_detail(service_id):
    """Get full detail for one service including more commits."""
    svc = next((s for s in SERVICES if s["id"] == service_id), None)
    if not svc:
        return jsonify({"error": "Not found"}), 404
    status, latency = ping_url(svc["url"])
    commits         = fetch_github_commits(svc["repo"], n=10)
    repo_meta       = fetch_github_repo_meta(svc["repo"])
    vercel_deploys  = fetch_vercel_deployments(svc["repo"]) if "vercel" in svc["host"] else []
    return jsonify({
        **svc,
        "status":         status,
        "latency_ms":     latency,
        "commits":        commits,
        "repo_meta":      repo_meta,
        "vercel_deploys": vercel_deploys,
        "checked_at":     datetime.now(timezone.utc).isoformat(),
    })

@app.route("/api/repos")
def repos():
    """Get ALL GitHub repos for alpha-1-design."""
    repos = fetch_all_github_repos()
    return jsonify({
        "count": len(repos),
        "repos": repos,
        "fetched_at": datetime.now(timezone.utc).isoformat()
    })

@app.route("/api/metrics")
def metrics():
    """Get uptime/latency metrics for all services."""
    now = datetime.now(timezone.utc).isoformat()
    metrics_data = []
    for svc in SERVICES:
        m = _metrics_history.get(svc["id"], {})
        lats = m.get("latencies", [])
        total_up = m.get("uptime_seconds", 0)
        total_down = m.get("downtime_seconds", 0)
        total = total_up + total_down
        uptime_pct = round((total_up / total) * 100) if total > 0 else 100

        metrics_data.append({
            "service_id": svc["id"],
            "service_name": svc["name"],
            "uptime_seconds": total_up,
            "downtime_seconds": total_down,
            "uptime_pct": uptime_pct,
            "latency_ms": {
                "current": lats[-1] if lats else None,
                "avg": round(sum(lats) / len(lats)) if lats else None,
                "min": min(lats) if lats else None,
                "max": max(lats) if lats else None,
                "samples": len(lats)
            },
            "last_check": m.get("last_check"),
            "last_status": m.get("last_status", "online")
        })

    return jsonify({
        "generated_at": now,
        "metrics": metrics_data
    })

@app.route("/api/incidents")
def incidents():
    """Get incident log."""
    return jsonify({
        "incidents": _incident_log[-50:],
        "count": len(_incident_log)
    })

@app.route("/api/refresh", methods=["POST"])
def manual_refresh():
    """Manually trigger a cache refresh."""
    global _cache
    try:
        _cache["data"] = build_payload()
        _cache["ts"] = time.time()
        return jsonify({"ok": True, "refreshed_at": datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
