"""Local dev server for the GLS locations map.

Serves the static frontend from public/ and proxies bounding-box queries to
the GLS Locations API, signing each request with HMAC-SHA256 server-side so
the client secret never reaches the browser. Each request first calls
Locations/count (cheap — no data payload) to decide whether the box is
small enough to fetch, then Locations/within-bbox for the actual data.

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

# Before fetching any data, we ask GLS's Locations/count endpoint how many
# locations are in the requested box. If that's more than this, we tell the
# caller to zoom in further instead of fetching (and shipping to the
# browser) a payload full of markers nobody can usefully look at.
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

# Each configured environment (qa, prod, ...) gets its own client
# id/secret/base URL/include list — GLS treats them as entirely separate
# deployments with separate data. An environment is only enabled if all
# three of CLIENT_ID/CLIENT_SECRET/BASE_URL are set for it.
ENVIRONMENTS: dict[str, dict] = {}
for env_name in ("qa", "prod"):
    prefix = f"GLS_{env_name.upper()}_"
    client_id = ENV.get(f"{prefix}CLIENT_ID", "")
    client_secret = ENV.get(f"{prefix}CLIENT_SECRET", "")
    base_url = ENV.get(f"{prefix}BASE_URL", "").rstrip("/")
    if client_id and client_secret and base_url:
        ENVIRONMENTS[env_name] = {
            "client_id": client_id,
            "client_secret": client_secret,
            "base_url": base_url,
            "include": ENV.get(f"{prefix}INCLUDE", "a,ac,rt,gs,da,dac"),
        }

if not ENVIRONMENTS:
    raise SystemExit(
        "No GLS environment configured. Copy .env.example to .env and fill "
        "in at least the GLS_QA_* values."
    )

DEFAULT_ENVIRONMENT = "qa" if "qa" in ENVIRONMENTS else next(iter(ENVIRONMENTS))


def sign_request(client_id: str, client_secret: str) -> tuple[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(
        client_secret.encode("utf-8"), timestamp.encode("utf-8"), hashlib.sha256
    ).digest()
    hash_value = f"{client_id}:{base64.b64encode(digest).decode('ascii')}"
    return timestamp, hash_value


def _post(env: dict, path: str, body: dict, timeout: int = 20) -> dict:
    timestamp, hash_value = sign_request(env["client_id"], env["client_secret"])
    req = urllib.request.Request(
        f"{env['base_url']}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Timestamp": timestamp,
            "Hash": hash_value,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def count_locations(env: dict, min_lon, min_lat, max_lon, max_lat) -> int:
    # Locations/count only takes an area polygon (no bbox variant), but it
    # returns just a number — no location payload — so it's cheap even for
    # a huge box.
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
    raw = _post(env, "/internal/v2/Locations/count", polygon)
    return raw.get("data", {}).get("locationCount", 0)


def fetch_locations(env: dict, min_lon, min_lat, max_lon, max_lat) -> dict:
    body = {
        "minLatitude": min_lat,
        "minLongitude": min_lon,
        "maxLatitude": max_lat,
        "maxLongitude": max_lon,
    }
    return _post(env, f"/internal/v2/Locations/within-bbox?include={env['include']}", body)


def simplify(item: dict) -> dict:
    loc = item.get("location") or {}
    comp = item.get("addressComponents") or {}
    localizations = comp.get("localizations") or []
    city = localizations[0].get("city") if localizations else None
    street = localizations[0].get("street") if localizations else None
    road_snaps = []
    for snap in item.get("roadSnaps") or []:
        snap_loc = snap.get("snapLocation") or {}
        road_snaps.append(
            {
                "mode": snap.get("transportationMode"),
                "lat": snap_loc.get("latitude"),
                "lon": snap_loc.get("longitude"),
                "distanceMeter": snap.get("distanceInMeter"),
                "nodes": snap.get("nodes") or [],
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
        "street": street,
        "houseNumber": comp.get("houseNumber"),
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
        if parsed.path == "/api/environments":
            self.send_json(
                200, {"environments": list(ENVIRONMENTS.keys()), "default": DEFAULT_ENVIRONMENT}
            )
            return
        self.serve_static(parsed.path)

    def handle_locations(self, query: dict):
        env_name = query.get("env", [DEFAULT_ENVIRONMENT])[0]
        env = ENVIRONMENTS.get(env_name)
        if env is None:
            self.send_json(
                400,
                {"error": f"unknown environment '{env_name}' — available: {', '.join(ENVIRONMENTS)}"},
            )
            return

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

        try:
            count = count_locations(env, min_lon, min_lat, max_lon, max_lat)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                count = 0  # GLS returns 404 for an area with no locations at all
            else:
                detail = exc.read().decode("utf-8", errors="replace")
                self.send_json(502, {"error": f"GLS API error {exc.code}", "detail": detail[:500]})
                return
        except urllib.error.URLError as exc:
            self.send_json(502, {"error": f"Could not reach GLS API: {exc.reason}"})
            return

        if count > MAX_RESULTS:
            self.send_json(
                200,
                {
                    "status": "too_many_results",
                    "message": f"{count} locations in view — zoom in further to load them.",
                    "count": count,
                    "locations": [],
                },
            )
            return

        if count == 0:
            self.send_json(200, {"status": "ok", "count": 0, "locations": []})
            return

        try:
            raw = fetch_locations(env, min_lon, min_lat, max_lon, max_lat)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.send_json(200, {"status": "ok", "count": 0, "locations": []})
                return
            detail = exc.read().decode("utf-8", errors="replace")
            self.send_json(502, {"error": f"GLS API error {exc.code}", "detail": detail[:500]})
            return
        except urllib.error.URLError as exc:
            self.send_json(502, {"error": f"Could not reach GLS API: {exc.reason}"})
            return

        data = raw.get("data", [])
        self.send_json(
            200,
            {
                "status": "ok",
                "count": len(data),
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
