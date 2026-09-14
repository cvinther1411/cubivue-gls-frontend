# GLS Locations Map

[![GitHub Repo](https://img.shields.io/badge/GitHub-cvinther1411%2Fcubivue--gls--frontend-181717?logo=github)](https://github.com/cvinther1411/cubivue-gls-frontend)

Interactive map (Leaflet) showing locations from the GLS Locations
internal API. Locations load automatically from the current map bounds as
you pan/zoom, but only once you're zoomed in enough that the request
returns a reasonable number of points — this dataset is dense (individual
delivery addresses, not just depots), so a wide view can mean tens of
thousands of results.

## Features

- **Auto-loading locations** — fetches on pan/zoom (debounced), gated by a
  client-side minimum zoom (15) plus a server-side check against the real
  location count for the visible area (see below).
- **Clustering** — markers cluster at zoom 17 and below; zoom 18+ always
  shows individual markers.
- **Address/city search** — powered by OSM Nominatim; pick a result to fly
  the map to it (bounded to zoom 18 max, so a city search doesn't zoom in
  absurdly far).
- **Road-snap points** (zoom 19+, toggleable) — each location's nearest
  point on the road network per transport mode, drawn as a dashed line
  with a small marker.
- **Voronoi areas** (toggleable, off by default) — each location's
  approximate delivery catchment polygon, outline only.
- **Location detail panel** — click any marker to open a panel on the
  right with everything the API returns for it: full address, exact
  coordinates/ID, every road-snap (mode, distance, snap point, OSM node
  IDs), and Voronoi polygon info.
- **QA / Prod toggle** — top-left buttons switch which GLS environment
  every request targets (Prod highlighted red as a visual "this is real
  data" warning). Switching clears the map and re-fetches for the current
  view immediately. The Prod button only appears if Prod credentials are
  configured (see below).

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
extend it. The only mutating endpoint in the whole API is
`TemplateManagement/AddTemplate` (v1, unrelated to location data); the
rest — everything this app uses — is read-only.

## Setup

Copy `.env.example` to `.env` and fill in `GLS_QA_CLIENT_ID` /
`GLS_QA_CLIENT_SECRET`. Also filling in the `GLS_PROD_*` values enables the
Prod button in the UI; leaving them blank runs QA-only. QA and Prod are
separate GLS deployments with separate credentials, base URLs, and even a
different `include` query param (GLS's own Postman collections for each
show that) — the server keeps them as fully independent configs, never
mixing a client secret with the other environment's host.

## Run

```bash
py server.py
```

Then open http://127.0.0.1:8787/

No pip installs needed — the server only uses the Python standard library.
