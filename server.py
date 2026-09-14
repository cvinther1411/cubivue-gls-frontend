"""Local dev server for the GLS locations map.

Serves the static frontend from public/ and proxies bounding-box queries to
the GLS within-area API, signing each request with HMAC-SHA256 server-side
so the client secret never reaches the browser.

Run:
    py server.py
Then open http://127.0.0.1:8787/
"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
PORT = 8787

# Maximum bounding-box area (in square degrees) we'll forward to GLS.
# Calibrated against the QA dataset: a 0.01x0.01 deg box returns ~600
# locations there, which is already a lot to render as individual markers.
# Above this the caller is told to zoom in further instead of us shipping a
# multi-MB response to the browser.
MAX_BBOX_AREA_DEG2 = 0.00015

# Even inside an allowed bbox, cap how many features we forward to the
# client so a dense area never sends more than this many markers.
MAX_RESULTS = 1200


def load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


ENV = load_env(ROOT / ".env")
CLIENT_ID = ENV.get("GLS_CLIENT_ID", "")
CLIENT_SECRET = ENV.get("GLS_CLIENT_SECRET", "")
BASE_URL = ENV.get("GLS_BASE_URL", "").rstrip("/")
INCLUDE = ENV.get("GLS_INCLUDE", "a,ac,rt,gs,da,dac")

if not (CLIENT_ID and CLIENT_SECRET and BASE_URL):
    raise SystemExit(
        "Missing GLS_CLIENT_ID / GLS_CLIENT_SECRET / GLS_BASE_URL. "
        "Copy .env.example to .env and fill it in."
    )


def sign_request() -> tuple[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(
        CLIENT_SECRET.encode("utf-8"), timestamp.encode("utf-8"), hashlib.sha256
    ).digest()
    hash_value = f"{CLIENT_ID}:{base64.b64encode(digest).decode('ascii')}"
    return timestamp, hash_value


def fetch_locations(min_lon, min_lat, max_lon, max_lat):
    polygon = {
        "type": "Polygon",
        "coordinates": [
            [
                [min_lon, min_lat],
                [max_lon, min_lat],
                [max_lon, max_lat],
                [min_lon, max_lat],
                [min_lon, min_lat],
            ]
        ],
    }
    timestamp, hash_value = sign_request()
    url = f"{BASE_URL}/internal/v2/locations/within-area/?include={INCLUDE}"
    req = urllib.request.Request(
        url,
        data=json.dumps(polygon).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Timestamp": timestamp,
            "Hash": hash_value,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def simplify(item: dict) -> dict:
    loc = item.get("location") or {}
    comp = item.get("addressComponents") or {}
    localizations = comp.get("localizations") or []
    city = localizations[0].get("city") if localizations else None
    road_snaps = []
    for snap in item.get("roadSnaps") or []:
        snap_loc = snap.get("snapLocation") or {}
        road_snaps.append(
            {
                "mode": snap.get("transportationMode"),
                "lat": snap_loc.get("latitude"),
                "lon": snap_loc.get("longitude"),
                "distanceMeter": snap.get("distanceInMeter"),
            }
        )
    voronoi = None
    for geom in item.get("geometries") or []:
        if geom.get("type") == "voronoi":
            voronoi = geom.get("geometry")
            break
    return {
        "id": item.get("id"),
        "lat": loc.get("latitude"),
        "lon": loc.get("longitude"),
        "label": item.get("label"),
        "zipCode": comp.get("zipCode"),
        "city": city,
        "countryCode": comp.get("countryCode"),
        "roadSnaps": road_snaps,
        "voronoi": voronoi,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "GLSMapDev/1.0"

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/locations":
            self.handle_locations(parse_qs(parsed.query))
            return
        self.serve_static(parsed.path)

    def handle_locations(self, query: dict):
        try:
            min_lon = float(query["minLon"][0])
            min_lat = float(query["minLat"][0])
            max_lon = float(query["maxLon"][0])
            max_lat = float(query["maxLat"][0])
        except (KeyError, ValueError, IndexError):
            self.send_json(400, {"error": "minLon, minLat, maxLon, maxLat are required numbers"})
            return

        if max_lon <= min_lon or max_lat <= min_lat:
            self.send_json(400, {"error": "invalid bounding box"})
            return

        area = (max_lon - min_lon) * (max_lat - min_lat)
        if area > MAX_BBOX_AREA_DEG2:
            self.send_json(
                200,
                {
                    "status": "area_too_large",
                    "message": "Zoom in further — the visible area is too large to load locations.",
                    "locations": [],
                },
            )
            return

        try:
            raw = fetch_locations(min_lon, min_lat, max_lon, max_lat)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # GLS returns 404 for areas with no locations at all (outside its
                # coverage, or just empty) — that's a normal empty result, not a
                # failure.
                self.send_json(200, {"status": "ok", "count": 0, "locations": []})
                return
            detail = exc.read().decode("utf-8", errors="replace")
            self.send_json(502, {"error": f"GLS API error {exc.code}", "detail": detail[:500]})
            return
        except urllib.error.URLError as exc:
            self.send_json(502, {"error": f"Could not reach GLS API: {exc.reason}"})
            return

        data = raw.get("data", [])
        total = len(data)
        if total > MAX_RESULTS:
            self.send_json(
                200,
                {
                    "status": "too_many_results",
                    "message": f"{total} locations in view — zoom in further to load them.",
                    "count": total,
                    "locations": [],
                },
            )
            return

        self.send_json(
            200,
            {
                "status": "ok",
                "count": total,
                "locations": [simplify(item) for item in data],
            },
        )

    def serve_static(self, path: str):
        if path == "/":
            path = "/index.html"
        safe_path = os.path.normpath(path).lstrip("\\/")
        file_path = (PUBLIC_DIR / safe_path).resolve()
        if PUBLIC_DIR not in file_path.parents and file_path != PUBLIC_DIR:
            self.send_error(404)
            return
        if not file_path.is_file():
            self.send_error(404)
            return

        content_types = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json",
        }
        ctype = content_types.get(file_path.suffix, "application/octet-stream")
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"GLS map dev server running at http://127.0.0.1:{PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
