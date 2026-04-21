"""
Alpha-1 Studio — Status API Backend
Runs on Render (free tier). Fetches real data from GitHub API, Vercel API,
and pings all services directly.

ENV VARS REQUIRED (set in Render dashboard):
  GITHUB_TOKEN      — Personal access token (repo scope)
  VERCEL_TOKEN      — Vercel API token
  SECRET_KEY        — Any random string, used for CORS validation
"""

import os, time, asyncio, httpx, json
from datetime import datetime, timezone
from flask import Flask, jsonify
from flask_cors import CORS
from functools import lru_cache
import threading

app = Flask(__name__)
CORS(app)

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
VERCEL_TOKEN  = os.environ.get("VERCEL_TOKEN", "")
GITHUB_USER   = "alpha-1-design"

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

def ping_url(url, timeout=8):
    """Return (status, latency_ms) for a URL."""
    if not url or "github.com" in url:
        return "github", None
    try:
        t0 = time.time()
        r = httpx.get(url, timeout=timeout, follow_redirects=True)
        ms = round((time.time() - t0) * 1000)
        if r.status_code < 400:
            return "online", ms
        elif r.status_code < 500:
            return "degraded", ms
        else:
            return "offline", ms
    except httpx.TimeoutException:
        return "timeout", None
    except Exception:
        return "offline", None

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
    # Sort by date
    all_commits.sort(key=lambda x: x.get("date", ""), reverse=True)
    return all_commits[:15]

def build_payload():
    """Build the full status payload. Called every CACHE_TTL seconds."""
    now = datetime.now(timezone.utc).isoformat()
    services_data = []

    for svc in SERVICES:
        status, latency = ping_url(svc["url"])
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
