# GLS Locations Map

[![GitHub Repo](https://img.shields.io/badge/GitHub-cvinther1411%2Fcubivue--gls--frontend-181717?logo=github)](https://github.com/cvinther1411/cubivue-gls-frontend)

Interactive map (Leaflet) showing locations from the GLS Locations
internal API. Locations load automatically from the current map bounds as
you pan/zoom, but only once you're zoomed in enough that the request
returns a reasonable number of points — this dataset is dense (individual
delivery addresses, not just depots), so a wide view can mean tens of
thousands of results.

## Why there's a small backend

The GLS API needs an HMAC-SHA256 request signature computed from a client
secret. That secret can't live in browser JS (anyone could read it from
page source), so `server.py` signs each request server-side. It also
strips each result down to the fields the map needs, and calls two GLS
endpoints per request:

- `Locations/count` first — a cheap call that returns just a number, no
  location data. If that's more than `MAX_RESULTS` (in `server.py`), the
  client is told to zoom in further and GLS is never asked for the actual
  data.
- `Locations/within-bbox` — only once `count` is within budget, this
  fetches the actual locations for the box.

GLS's Swagger docs (`/swagger/v2/swagger.json` on the API host) list a
fair bit more than these two — `geocode`/`reverse-geocode`, `get-by-ids`,
`address-wash/*` — not wired into this app, but worth knowing about if you
extend it.

## Setup

Copy `.env.example` to `.env` and fill in the GLS QA client id/secret.

## Run

```bash
py server.py
```

Then open http://127.0.0.1:8787/

No pip installs needed — the server only uses the Python standard library.
