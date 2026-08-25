import json
import logging
import threading
import time
import os
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Flask, jsonify, render_template
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
LATEST_FILE = ROOT / "latest_boosts.json"

DEFAULT_CONFIG = {
    "interval": 1,
    "timeout": 2.5,
    "min_multiplier": 1.01,
    "brand_id": "2186087039504621568",
    "api_hosts": [
        "api-g-1289c5be-1336.sptpub.com",
        "api-g-eab80791-1316.sptpub.com",
        "api-g-c7818b61-607.sptpub.com"
    ],
    # This is the endpoint actually used by the Chips promo carousel.
    # It returns home_top/event_page/live_page/operator_page* and the
    # line_banner records with view=boosted_odds.
    "promo_path": "/api/v2/promo/banners/brand/{brand_id}/en",
    "event_paths": [
        "/api/v4/prematch/brand/{brand_id}/event/en/{event_id}",
        "/api/v4/prematch/brand/{brand_id}/event/{event_id}/en"
    ],
    "event_url_template": "https://chips.gg/sports/event/{event_id}",
    "event_cache_ttl": 120,
    "exclude_started": True,
    "clock_skew_seconds": 3,
    "max_event_workers": 6
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

session = requests.Session()
session.mount("https://", HTTPAdapter(pool_connections=12, pool_maxsize=12, max_retries=0))
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://chips.gg/",
    "Origin": "https://chips.gg"
})

state = {
    "boosts": [],
    "last_scan": None,
    "last_success": None,
    "last_change": None,
    "ok": False,
    "error": None,
    "scan_count": 0,
    "consecutive_errors": 0,
    "api_host": None
}
lock = threading.Lock()
stop_event = threading.Event()
event_cache = {}
host_state = {"host": None, "checked": 0.0}


def load_config():
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(saved)
    return cfg


def build_url(host, path, **kwargs):
    values = {"brand_id": kwargs.get("brand_id"), "event_id": kwargs.get("event_id")}
    return "https://" + host + path.format(**values)


def fetch_json(url, timeout):
    # Deliberately NO HTTP retry/backoff. One bad upstream request must not
    # create a queue of retries and freeze the one-second scanner.
    r = session.get(url, timeout=(1.5, float(timeout)))
    r.raise_for_status()
    return r.json()


def candidate_hosts(cfg):
    hosts = []
    preferred = host_state.get("host")
    if preferred:
        hosts.append(preferred)
    for h in cfg.get("api_hosts", []):
        if h and h not in hosts:
            hosts.append(h)
    return hosts


def fetch_promo(cfg):
    brand = str(cfg["brand_id"])
    path = str(cfg["promo_path"]).format(brand_id=brand)
    last_error = None

    for host in candidate_hosts(cfg):
        try:
            data = fetch_json(build_url(host, path, brand_id=brand), cfg["timeout"])
            if isinstance(data, (dict, list)):
                host_state["host"] = host
                host_state["checked"] = time.monotonic()
                return data, host
        except Exception as exc:
            last_error = exc
            logging.warning("promo host %s unavailable: %s", host, type(exc).__name__)

    raise RuntimeError(f"Promo API недоступен: {last_error}")


def as_seconds(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if x > 10_000_000_000:
        x /= 1000.0
    return x


def find_event_object(data, event_id):
    target = str(event_id)
    if not isinstance(data, dict):
        return None
    events = data.get("events")
    if isinstance(events, dict) and isinstance(events.get(target), dict):
        return events[target]
    if isinstance(data.get(target), dict):
        return data[target]

    def walk(obj, depth=0):
        if depth > 7:
            return None
        if isinstance(obj, dict):
            for k, v in obj.items():
                if str(k) == target and isinstance(v, dict):
                    return v
                hit = walk(v, depth + 1)
                if hit is not None:
                    return hit
        elif isinstance(obj, list):
            for v in obj[:800]:
                hit = walk(v, depth + 1)
                if hit is not None:
                    return hit
        return None
    return walk(data)


def lookup_name(table, key):
    if not isinstance(table, dict) or key is None:
        return ""
    obj = table.get(str(key), {})
    return str(obj.get("name") or obj.get("title") or obj.get("label") or "").strip() if isinstance(obj, dict) else ""


def parse_event_metadata(data, event_id):
    ev = find_event_object(data, event_id)
    if not isinstance(ev, dict):
        return None
    desc = ev.get("desc") if isinstance(ev.get("desc"), dict) else ev
    scheduled = as_seconds(desc.get("scheduled") or desc.get("start") or desc.get("start_time") or ev.get("scheduled") or ev.get("start"))
    if scheduled is None:
        return None

    competitors = desc.get("competitors") or desc.get("participants") or []
    names = []
    if isinstance(competitors, list):
        for item in competitors:
            if isinstance(item, dict):
                n = item.get("name") or item.get("title") or item.get("label")
            else:
                n = item
            if n:
                names.append(str(n).strip())
    if len(names) < 2:
        home = desc.get("home") or desc.get("home_team") or desc.get("homeTeam")
        away = desc.get("away") or desc.get("away_team") or desc.get("awayTeam")
        if isinstance(home, dict): home = home.get("name")
        if isinstance(away, dict): away = away.get("name")
        if home and away:
            names = [str(home).strip(), str(away).strip()]

    name = f"{names[0]} vs {names[1]}" if len(names) >= 2 else (names[0] if names else str(desc.get("name") or desc.get("title") or ev.get("name") or "").strip())
    return {
        "scheduled": scheduled,
        "name": name,
        "sport": lookup_name(data.get("sports", {}), desc.get("sport")),
        "category": lookup_name(data.get("categories", {}), desc.get("category")),
        "tournament": lookup_name(data.get("tournaments", {}), desc.get("tournament")),
        "slug": str(desc.get("slug") or "").strip(),
    }


def event_info(event_id, cfg):
    key = str(event_id)
    now = time.time()
    cached = event_cache.get(key)
    ttl = max(15, int(cfg.get("event_cache_ttl", 120)))

    if cached and cached.get("scheduled", 0) > now and time.monotonic() - cached.get("checked", 0) < ttl:
        return cached

    brand = str(cfg["brand_id"])
    templates = cfg.get("event_paths") or DEFAULT_CONFIG["event_paths"]
    last_error = None

    for host in candidate_hosts(cfg):
        for path in templates:
            url = build_url(host, str(path), brand_id=brand, event_id=key)
            try:
                data = fetch_json(url, cfg["timeout"])
                info = parse_event_metadata(data, key)
                if info:
                    info["checked"] = time.monotonic()
                    event_cache[key] = info
                    host_state["host"] = host
                    return info
            except Exception as exc:
                last_error = exc

    # Keep a still-future cached record during a short upstream failure.
    if cached and cached.get("scheduled", 0) > now:
        return cached
    return None


def iter_boosted_banners(data):
    """
    Walk the whole promo response.  Chips can expose Boost carousels under
    home_top as well as event/operator pages.  We only accept the semantic
    Boost marker (view=boosted_odds), not arbitrary green UI elements.
    """
    seen = set()

    def walk(obj, page=""):
        if isinstance(obj, dict):
            if obj.get("type") == "line_banner" and obj.get("view") == "boosted_odds":
                bid = str(obj.get("id") or "")
                # The same banner can be present in more than one page.
                dedup = bid or (page, json.dumps(obj.get("details", {}), sort_keys=True))
                if dedup not in seen:
                    seen.add(dedup)
                    yield obj
            for key, value in obj.items():
                child_page = page
                if key in ("home_top", "event_page", "live_page", "operator_page2", "operator_page4"):
                    child_page = key
                yield from walk(value, child_page)
        elif isinstance(obj, list):
            for value in obj:
                yield from walk(value, page)

    yield from walk(data)


def extract_boosts(data, cfg):
    grouped = {}
    min_multiplier = float(cfg.get("min_multiplier", 1.01))

    for banner in iter_boosted_banners(data):
        if banner.get("type") not in (None, "line_banner"):
            continue
        platforms = banner.get("display_platform") or []
        if platforms and "desktop" not in platforms:
            continue
        details = banner.get("details")
        if not isinstance(details, dict):
            continue

        event_id = details.get("event_id")
        if not event_id:
            continue
        try:
            multiplier = float(details.get("multiplier"))
        except (TypeError, ValueError):
            continue
        if multiplier < min_multiplier:
            continue

        eid = str(event_id)
        item = {
            "event_id": eid,
            "multiplier": multiplier,
            "boost_percent": round((multiplier - 1.0) * 100, 2),
            "event_url": str(cfg["event_url_template"]).format(event_id=eid)
        }
        old = grouped.get(eid)
        if old is None or multiplier > old["multiplier"]:
            grouped[eid] = item

    if not grouped:
        return []

    verified = []
    workers = min(int(cfg.get("max_event_workers", 6)), len(grouped))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(event_info, eid, cfg): item for eid, item in grouped.items()}
        now = time.time()
        skew = max(0, int(cfg.get("clock_skew_seconds", 3)))

        for future in as_completed(futures):
            item = futures[future]
            try:
                info = future.result()
            except Exception:
                info = None
            if not info:
                continue
            scheduled = info.get("scheduled")
            if not scheduled:
                continue
            if cfg.get("exclude_started", True) and scheduled <= now + skew:
                continue
            item.update({
                "scheduled": scheduled,
                "event_name": info.get("name") or "Предстоящее событие",
                "sport": info.get("sport") or "",
                "category": info.get("category") or "",
                "tournament": info.get("tournament") or "",
                "slug": info.get("slug") or ""
            })
            verified.append(item)

    return sorted(verified, key=lambda x: (-x["multiplier"], x["scheduled"], x["event_id"]))


def signature(boosts):
    return [(b["event_id"], b["multiplier"]) for b in boosts]


def purge_expired(boosts):
    now = time.time()
    return [b for b in boosts if b.get("scheduled", 0) > now + 3]


def scan_once():
    cfg = load_config()
    started = datetime.now(timezone.utc).isoformat()

    try:
        data, host = fetch_promo(cfg)
        boosts = extract_boosts(data, cfg)

        with lock:
            old = state["boosts"]
            changed = signature(boosts) != signature(old)
            state.update({
                "boosts": boosts,
                "last_scan": started,
                "last_success": started,
                "ok": True,
                "error": None,
                "scan_count": state["scan_count"] + 1,
                "consecutive_errors": 0,
                "api_host": host,
                "source": "chips promo carousel /api/v2/promo/banners"
            })
            if changed:
                state["last_change"] = started

        LATEST_FILE.write_text(json.dumps(boosts, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info("scan: %d active boosted events%s via %s", len(boosts), " [CHANGED]" if changed else "", host)

    except Exception as exc:
        with lock:
            state["scan_count"] += 1
            state["consecutive_errors"] += 1
            state["last_scan"] = started
            state["error"] = str(exc)
            state["boosts"] = purge_expired(state["boosts"])
            state["source"] = "chips promo carousel /api/v2/promo/banners"
            # Keep LIVE status only when the last successful scan is recent.
            recent = False
            if state.get("last_success"):
                try:
                    recent = (datetime.now(timezone.utc) - datetime.fromisoformat(state["last_success"])).total_seconds() < 8
                except Exception:
                    pass
            state["ok"] = recent
        logging.warning("scan failed (no retry queue): %s", exc)


def worker():
    while not stop_event.is_set():
        t0 = time.monotonic()
        scan_once()
        cfg = load_config()
        wait = max(0.2, float(cfg.get("interval", 1)) - (time.monotonic() - t0))
        stop_event.wait(wait)


_worker_started = False
_worker_start_lock = threading.Lock()

def start_worker():
    global _worker_started
    with _worker_start_lock:
        if _worker_started:
            return
        _worker_started = True
        threading.Thread(target=worker, name="chips-boost-scanner", daemon=True).start()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def api_state():
    with lock:
        return jsonify(state)


# Start the single background scanner both for local Python execution and
# for WSGI servers such as Gunicorn/Render.
start_worker()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
