const CLIENT_MIN_ZOOM = 15; // below this we don't even try to fetch
const ROAD_SNAP_MIN_ZOOM = 19; // below this, road-snap lines/points are hidden
const DEBOUNCE_MS = 400;

const map = L.map("map", { minZoom: 3, maxZoom: 20 }).setView([48.8195, 10.149], 17);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 20,
  maxNativeZoom: 19, // OSM tiles stop at 19; Leaflet upscales them for zoom 20
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

const clusterGroup = L.markerClusterGroup({
  maxClusterRadius: 50,
  disableClusteringAtZoom: 20,
});
map.addLayer(clusterGroup);

const roadSnapLayer = L.layerGroup().addTo(map);
const voronoiLayer = L.layerGroup(); // not added to the map until the toggle is checked

const statusText = document.getElementById("status-text");
const locationsSpinner = document.getElementById("locations-spinner");
const zoomOverlay = document.getElementById("zoom-overlay");
const zoomOverlayDetail = document.getElementById("zoom-overlay-detail");
const voronoiToggle = document.getElementById("voronoi-toggle");

voronoiToggle.addEventListener("change", () => {
  if (voronoiToggle.checked) {
    map.addLayer(voronoiLayer);
  } else {
    map.removeLayer(voronoiLayer);
  }
});

let debounceTimer = null;
let currentAbort = null;
let requestSeq = 0;

function setStatus(text) {
  statusText.textContent = text;
}

function showZoomOverlay(show, detail) {
  zoomOverlay.classList.toggle("hidden", !show);
  if (detail) zoomOverlayDetail.textContent = detail;
}

function popupHtml(loc) {
  const parts = [loc.zipCode, loc.city, loc.countryCode].filter(Boolean).join(", ");
  return `<div class="gls-popup"><span class="label">${escapeHtml(loc.label || "")}</span>${escapeHtml(parts)}</div>`;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function renderLocations(locations) {
  clusterGroup.clearLayers();
  const markers = locations
    .filter((loc) => typeof loc.lat === "number" && typeof loc.lon === "number")
    .map((loc) => L.marker([loc.lat, loc.lon]).bindPopup(popupHtml(loc)));
  clusterGroup.addLayers(markers);
  renderRoadSnaps(locations);
  renderVoronoi(locations);
}

function renderVoronoi(locations) {
  voronoiLayer.clearLayers();
  for (const loc of locations) {
    if (!loc.voronoi) continue;
    L.geoJSON(loc.voronoi, {
      style: { color: "#6a51a3", weight: 1, fillColor: "#9e9ac8", fillOpacity: 0.25 },
    })
      .bindTooltip(escapeHtml(loc.label || ""))
      .addTo(voronoiLayer);
  }
}

function renderRoadSnaps(locations) {
  roadSnapLayer.clearLayers();
  if (map.getZoom() < ROAD_SNAP_MIN_ZOOM) return;

  for (const loc of locations) {
    if (typeof loc.lat !== "number" || typeof loc.lon !== "number") continue;
    for (const snap of loc.roadSnaps || []) {
      if (typeof snap.lat !== "number" || typeof snap.lon !== "number") continue;

      L.polyline(
        [
          [loc.lat, loc.lon],
          [snap.lat, snap.lon],
        ],
        { color: "#e6550d", weight: 2, dashArray: "4,4" }
      ).addTo(roadSnapLayer);

      const distance = typeof snap.distanceMeter === "number" ? `${Math.round(snap.distanceMeter)} m` : "unknown distance";
      L.circleMarker([snap.lat, snap.lon], {
        radius: 4,
        color: "#e6550d",
        weight: 1,
        fillColor: "#e6550d",
        fillOpacity: 1,
      })
        .bindTooltip(`${escapeHtml(snap.mode || "road")} snap — ${distance}`)
        .addTo(roadSnapLayer);
    }
  }
}

async function loadLocations() {
  const zoom = map.getZoom();

  if (currentAbort) currentAbort.abort();

  if (zoom < CLIENT_MIN_ZOOM) {
    showZoomOverlay(true, `Current zoom ${zoom} — zoom to at least ${CLIENT_MIN_ZOOM}`);
    clusterGroup.clearLayers();
    roadSnapLayer.clearLayers();
    voronoiLayer.clearLayers();
    setStatus("Zoom in to load GLS locations.");
    locationsSpinner.classList.add("hidden");
    return;
  }
  showZoomOverlay(false);

  const bounds = map.getBounds();
  const params = new URLSearchParams({
    minLon: bounds.getWest(),
    minLat: bounds.getSouth(),
    maxLon: bounds.getEast(),
    maxLat: bounds.getNorth(),
  });

  const abort = new AbortController();
  currentAbort = abort;
  const seq = ++requestSeq;

  setStatus("Loading locations…");
  locationsSpinner.classList.remove("hidden");

  try {
    const resp = await fetch(`/api/locations?${params.toString()}`, { signal: abort.signal });
    const data = await resp.json();
    if (seq !== requestSeq) return; // superseded by a newer request

    if (!resp.ok) {
      setStatus(data.error || "Failed to load locations.");
      clusterGroup.clearLayers();
      roadSnapLayer.clearLayers();
      voronoiLayer.clearLayers();
      return;
    }

    if (data.status === "area_too_large") {
      clusterGroup.clearLayers();
      roadSnapLayer.clearLayers();
      voronoiLayer.clearLayers();
      showZoomOverlay(true, "Zoom in further — visible area is too large");
      setStatus(data.message);
      return;
    }

    if (data.status === "too_many_results") {
      clusterGroup.clearLayers();
      roadSnapLayer.clearLayers();
      voronoiLayer.clearLayers();
      showZoomOverlay(true, `${data.count} locations in view — zoom in further`);
      setStatus(data.message);
      return;
    }

    renderLocations(data.locations);
    setStatus(`${data.count} location${data.count === 1 ? "" : "s"} in view (zoom ${zoom}).`);
  } catch (err) {
    if (err.name === "AbortError") return;
    setStatus("Error loading locations: " + err.message);
  } finally {
    if (seq === requestSeq) locationsSpinner.classList.add("hidden");
  }
}

function scheduleLoad() {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(loadLocations, DEBOUNCE_MS);
}

map.on("moveend zoomend", scheduleLoad);
scheduleLoad();

// --- Address/city search (Nominatim) ---

const searchForm = document.getElementById("search-form");
const searchInput = document.getElementById("search-input");
const searchButton = document.getElementById("search-button");
const searchResults = document.getElementById("search-results");
const searchSpinner = document.getElementById("search-spinner");

function hideSearchResults() {
  searchResults.classList.add("hidden");
  searchResults.innerHTML = "";
}

function renderSearchResults(results) {
  searchResults.innerHTML = "";
  if (results.length === 0) {
    const li = document.createElement("li");
    li.textContent = "No matches found.";
    searchResults.appendChild(li);
  } else {
    for (const result of results) {
      const li = document.createElement("li");
      li.textContent = result.display_name;
      li.addEventListener("click", () => goToSearchResult(result));
      searchResults.appendChild(li);
    }
  }
  searchResults.classList.remove("hidden");
}

function goToSearchResult(result) {
  const [south, north, west, east] = result.boundingbox.map(Number);
  map.fitBounds(
    [
      [south, west],
      [north, east],
    ],
    { maxZoom: 18 }
  );
  hideSearchResults();
}

searchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = searchInput.value.trim();
  if (!query) return;

  searchButton.disabled = true;
  searchSpinner.classList.remove("hidden");
  try {
    const params = new URLSearchParams({ format: "jsonv2", q: query, limit: "5" });
    const resp = await fetch(`https://nominatim.openstreetmap.org/search?${params.toString()}`);
    if (!resp.ok) throw new Error(`Nominatim returned ${resp.status}`);
    const results = await resp.json();
    renderSearchResults(results);
  } catch (err) {
    setStatus("Search failed: " + err.message);
  } finally {
    searchButton.disabled = false;
    searchSpinner.classList.add("hidden");
  }
});

document.addEventListener("click", (e) => {
  if (!searchForm.contains(e.target) && !searchResults.contains(e.target)) {
    hideSearchResults();
  }
});
