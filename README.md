# GLS Locations Map

[![GitHub Repo](https://img.shields.io/badge/GitHub-cvinther1411%2Fcubivue--gls--frontend-181717?logo=github)](https://github.com/cvinther1411/cubivue-gls-frontend)

Interactive map (Leaflet) showing locations from the GLS `within-area`
internal API. Locations load automatically from the current map bounds as
you pan/zoom, but only once you're zoomed in enough that the request
returns a reasonable number of points — this dataset is dense (individual
delivery addresses, not just depots), so a wide view can mean tens of
thousands of results.

## Why there's a small backend

The GLS API needs an HMAC-SHA256 request signature computed from a client
secret. That secret can't live in browser JS (anyone could read it from
page source), so `server.py` signs each request server-side and proxies
just the bounding box query to GLS. It also strips each result down to the
fields the map needs and enforces two safety limits:

- a maximum bounding-box area (`MAX_BBOX_AREA_DEG2` in `server.py`) — if
  the visible map area is larger than this, the client is told to zoom in
  further without ever calling GLS
- a maximum result count (`MAX_RESULTS`) — if GLS still returns more than
  this for the given box, the client is told to zoom in rather than being
  sent thousands of markers

## Setup

Copy `.env.example` to `.env` and fill in the GLS QA client id/secret.

## Run

```bash
py server.py
```

Then open http://127.0.0.1:8787/

No pip installs needed — the server only uses the Python standard library.
