/**
 * TrainRoute Finder — script.js
 * ──────────────────────────────
 * Backend: Flask on http://localhost:5000
 *   GET /api/stations → [{code, name, lat, lon}, …]   (818 routable stations)
 *   GET /api/route    → {total_cost, total_time, route_legs[]}  (with per-leg coords)
 *
 * Map: Leaflet.js  — solid blue polylines for IN_TRAIN legs,
 *                    dashed gray polylines for TRANSFER legs.
 */

'use strict';

/* ════════════ CONFIG ════════════ */

const API_BASE_URL = 'http://localhost:5000'; // Flask backend

/* ════════════ STATION INDEX ════════════
   Populated at page load from GET /api/stations.
   stationIndex[code] = { name, lat, lon }
*/
const stationIndex = {};

/**
 * Extract a bare station code from either a raw code ("JBP")
 * or the datalist format ("JBP - Jabalpur").
 */
function parseStationCode(inputValue) {
  return inputValue.trim().split(/\s*-\s*/)[0].trim().toUpperCase();
}

/* ════════════ DOM REFS ════════════ */
const form          = document.getElementById('search-form');
const sourceInput   = document.getElementById('source');
const destInput     = document.getElementById('destination');
const swapBtn       = document.getElementById('swap-btn');
const classSelect   = document.getElementById('travel-class');
const dateInput     = document.getElementById('travel-date');
const searchBtn     = document.getElementById('search-btn');
const resultsSection  = document.getElementById('results-section');
const loadingSpinner  = document.getElementById('loading-spinner');
const errorBanner     = document.getElementById('error-banner');
const errorMessage    = document.getElementById('error-message');
const resultsHeader   = document.getElementById('results-header');
const resultsMeta     = document.getElementById('results-meta');
const routeCardsEl    = document.getElementById('route-cards');
const mapWrapper      = document.getElementById('map-wrapper');

/* ════════════ STATE ════════════ */
let map;
let routeLayer;
let leafletMap    = null;
let mapMarkers    = [];
let mapPolylines  = [];   // one Leaflet Polyline per route leg
let activeRouteId = null;

/* ════════════ INIT ════════════ */
(function init() {
  populateDateDefault();
  initApp();
  attachEventListeners();
})();

/**
 * Initialize the Leaflet map, the route layer group, and the station
 * autocomplete datalist by fetching stations from the backend.
 */
async function initApp() {
  // Initialize Leaflet map centered on India (only once)
  if (!map) {
    map = L.map('map').setView([20.59, 78.96], 5);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© OpenStreetMap contributors',
      maxZoom: 18,
    }).addTo(map);

    // Layer group to hold future route lines
    routeLayer = L.layerGroup().addTo(map);
  }

  // Load stations and populate the autocomplete datalist
  try {
    const res = await fetch('http://localhost:5000/api/stations');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const stations = await res.json(); // [{code, name, lat, lon}, ...]

    const datalist = document.getElementById('station-options');
    datalist.innerHTML = '';

    stations.forEach((s) => {
      const opt = document.createElement('option');
      opt.value = s.code;
      opt.textContent = s.name;
      datalist.appendChild(opt);
    });
  } catch (err) {
    console.warn('Could not load stations from API:', err.message);
  }
}

/* ════════════ SETUP HELPERS ════════════ */

function populateDateDefault() {
  const today = new Date();
  const yyyy  = today.getFullYear();
  const mm    = String(today.getMonth() + 1).padStart(2, '0');
  const dd    = String(today.getDate()).padStart(2, '0');
  dateInput.value = `${yyyy}-${mm}-${dd}`;
  dateInput.min   = `${yyyy}-${mm}-${dd}`;
}

function attachEventListeners() {
  form.addEventListener('submit', onSearchSubmit);
  swapBtn.addEventListener('click', swapStations);
}

/* ════════════ SWAP STATIONS ════════════ */
function swapStations() {
  [sourceInput.value, destInput.value] = [destInput.value, sourceInput.value];
  sourceInput.focus();
}

/* ════════════ SEARCH HANDLER ════════════ */
async function onSearchSubmit(e) {
  e.preventDefault();

  // Raw display values (may be "JBP - Jabalpur" or just "JBP")
  const sourceRaw = sourceInput.value.trim();
  const destRaw   = destInput.value.trim();

  // Bare station codes sent to the API
  const source      = parseStationCode(sourceRaw);
  const destination = parseStationCode(destRaw);

    const travelClass = classSelect.value;
    const date        = dateInput.value;
    const mode        = document.querySelector('input[name="search_mode"]:checked').value;

    /* Basic validation */
    if (!sourceRaw) { highlightError(sourceInput, 'Please enter a source station.'); return; }
    if (!destRaw)   { highlightError(destInput,   'Please enter a destination station.'); return; }
    if (source === destination) {
      highlightError(destInput, 'Source and destination must be different.');
      return;
    }

    clearHighlights();
    setLoadingState(true);
    showResultsSection(true);

    try {
      const data = await fetchRoutes({ source, destination, mode, travelClass });
    // Pass raw strings for display, bare codes are embedded in route_legs from backend
    renderBackendResults(data, { source: sourceRaw, destination: destRaw, mode });
  } catch (err) {
    showError(err.message || 'Could not fetch routes. Is the backend running?');
  } finally {
    setLoadingState(false);
  }
}

/* ════════════ DATA FETCHING ════════════ */
async function fetchRoutes({ source, destination, mode, travelClass }) {
  const url = new URL(`${API_BASE_URL}/api/route`);
  url.searchParams.set('source', source);
  url.searchParams.set('destination', destination);
  url.searchParams.set('preference', mode);
  url.searchParams.set('travel_class', travelClass);

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `Server error ${response.status}`);
  }

  return response.json(); // { total_cost, total_time, route_legs }
}

/* ════════════ RENDER ════════════ */

function renderResults(routes, { source, destination, travelClass, mode }) {
  routeCardsEl.innerHTML = '';

  if (!routes || routes.length === 0) {
    showError('No routes found for this search. Try different stations or travel class.');
    return;
  }

  // Results header
  resultsHeader.removeAttribute('hidden');
  resultsMeta.textContent =
    `${routes.length} route${routes.length > 1 ? 's' : ''} found · ${source.split('(')[0].trim()} → ${destination.split('(')[0].trim()} · ${travelClass} · ${capitalize(mode)}`;

  routes.forEach((route, index) => {
    const card = buildRouteCard(route, index === 0 && mode === 'cheapest');
    routeCardsEl.appendChild(card);
  });

  // Auto-show first route on map (mock-data path, kept for reference)
  if (routes[0] && routes[0].mapCoords) {
    mapWrapper.removeAttribute('hidden');
  }

  resultsSection.removeAttribute('hidden');
}

function buildRouteCard(route, isTopResult) {
  const card = document.createElement('article');
  card.className = `route-card${route.badge === 'best' ? ' best' : route.badge === 'fastest' ? ' fastest' : ''}`;
  card.setAttribute('role', 'listitem');
  card.setAttribute('tabindex', '0');
  card.setAttribute('aria-label', `Route by ${route.trainName}, fare ₹${route.fare}`);
  card.dataset.routeId = route.id;

  card.innerHTML = `
    ${route.badgeLabel ? `<div class="best-badge" aria-label="${route.badgeLabel}">${route.badgeLabel}</div>` : ''}

    <div class="card-top">
      <div>
        <div class="train-name">${escHtml(route.trainName)}</div>
        <div class="train-number">Train #${escHtml(route.trainNumber)}</div>
      </div>
      <div class="fare-block">
        <div class="fare">₹${route.fare.toLocaleString('en-IN')}</div>
        <div class="fare-class">${escHtml(route.class)} class</div>
      </div>
    </div>

    <div class="route-timeline" role="group" aria-label="Journey timeline">
      <div class="timeline-station">
        <span class="station-time">${escHtml(route.departureTime)}</span>
        <span class="station-name" title="${escHtml(route.departureStation)}">${shortName(route.departureStation)}</span>
      </div>
      ${route.via.map(stop => `
        <div class="timeline-line"><span class="duration-label" aria-hidden="true">change</span></div>
        <div class="timeline-station">
          <span class="station-time">–</span>
          <span class="station-name" title="${escHtml(stop)}">${shortName(stop)}</span>
        </div>
      `).join('')}
      <div class="timeline-line">
        <span class="duration-label" aria-hidden="true">${escHtml(route.duration)}</span>
      </div>
      <div class="timeline-station">
        <span class="station-time">${escHtml(route.arrivalTime)}</span>
        <span class="station-name" title="${escHtml(route.arrivalStation)}">${shortName(route.arrivalStation)}</span>
      </div>
    </div>

    <div class="card-stats">
      <span class="stat-chip" aria-label="Duration: ${route.duration}">⏱ ${escHtml(route.duration)}</span>
      <span class="stat-chip" aria-label="${route.transfers === 0 ? 'Direct train' : route.transfers + ' transfer(s)'}">
        ${route.transfers === 0 ? '✅ Direct' : `🔄 ${route.transfers} transfer${route.transfers > 1 ? 's' : ''}`}
      </span>
      ${route.savings > 0
        ? `<span class="savings-badge" aria-label="You save ₹${route.savings.toLocaleString('en-IN')} compared to costliest route">
             💚 You save ₹${route.savings.toLocaleString('en-IN')}
           </span>`
        : ''}
    </div>
  `;

  /* Click / keyboard → scroll to map (live-data path uses drawRouteLegs via renderBackendResults) */
  const activateCard = () => {
    document.querySelectorAll('.route-card').forEach(c => c.classList.remove('active'));
    card.classList.add('active');
    activeRouteId = route.id;
    mapWrapper.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };

  card.addEventListener('click', activateCard);
  card.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activateCard(); }
  });

  return card;
}

/* ════════════ BACKEND RESULTS RENDERER ════════════ */

function renderBackendResults(data, { source, destination, mode }) {
  routeCardsEl.innerHTML = '';

  const { total_cost, total_time, route_legs } = data;

  if (!route_legs || route_legs.length === 0) {
    showError('No route legs returned by the server.');
    return;
  }

  // Summary header
  resultsHeader.removeAttribute('hidden');
  const hrs  = Math.floor(total_time);
  const mins = String(Math.round((total_time - hrs) * 60)).padStart(2, '0');
  resultsMeta.textContent =
    `${source} → ${destination} · ₹${total_cost.toFixed(2)} total · ${hrs}h ${mins}m · ${capitalize(mode)}`;

  route_legs.forEach((leg, index) => {
    routeCardsEl.appendChild(buildLegCard(leg, index));
  });

  // Clear previous route lines from the map
  routeLayer.clearLayers();

  // Collect all coordinates so we can fit the map to the full route
  const latlngs = [];

  route_legs.forEach((leg) => {
    const { from_lat, from_lon, to_lat, to_lon } = leg;

    // Skip legs without geocodable coordinates
    if (from_lat == null || from_lon == null || to_lat == null || to_lon == null) {
      return;
    }

    latlngs.push([from_lat, from_lon]);
    latlngs.push([to_lat, to_lon]);

    const isTransfer = leg.edge_type === 'TRANSFER';
    L.polyline(
      [[from_lat, from_lon], [to_lat, to_lon]],
      {
        color: isTransfer ? 'gray' : 'blue',
        dashArray: isTransfer ? '5, 5' : '',
      }
    ).addTo(routeLayer);
  });

  // Zoom the map to fit the drawn route
  if (latlngs.length > 0) {
    map.fitBounds(L.latLngBounds(latlngs));
  }

  // Show map only when at least one leg carries coordinate data
  const hasCoords = route_legs.some(
    leg => leg.from_lat != null && leg.to_lat != null
  );
  if (hasCoords) {
    mapWrapper.removeAttribute('hidden');
  } else {
    mapWrapper.setAttribute('hidden', '');
  }

  resultsSection.removeAttribute('hidden');
}

function buildLegCard(leg, index) {
  const isTransfer = leg.edge_type === 'TRANSFER';

  // Convert decimal hours → HH:MM
  const fmt = (dec) => {
    const h = Math.floor(dec);
    const m = String(Math.round((dec - h) * 60)).padStart(2, '0');
    return `${h}:${m}`;
  };

  const card = document.createElement('article');
  card.className = isTransfer ? 'route-card transfer-leg' : 'route-card';
  card.setAttribute('role', 'listitem');
  card.setAttribute('aria-label',
    isTransfer
      ? `Transfer at ${leg.from_station}`
      : `Leg ${index + 1}: ${leg.from_station} to ${leg.to_station}, ₹${leg.cost}`);

  card.innerHTML = `
    <div class="card-top">
      <div>
        <div class="train-name">${isTransfer ? '🔄 Transfer / Layover' : `🚆 Leg ${index + 1}`}</div>
        <div class="train-number">${escHtml(leg.edge_type)}</div>
      </div>
      <div class="fare-block">
        <div class="fare">${isTransfer ? 'Free' : `₹${Number(leg.cost).toLocaleString('en-IN')}`}</div>
        <div class="fare-class">${isTransfer ? 'layover' : 'fare'}</div>
      </div>
    </div>

    <div class="route-timeline" role="group" aria-label="Leg timeline">
      <div class="timeline-station">
        <span class="station-time">${fmt(leg.depart_time)}</span>
        <span class="station-name">${escHtml(leg.from_station)}</span>
      </div>
      <div class="timeline-line">
        <span class="duration-label" aria-hidden="true">${isTransfer ? 'wait' : 'travel'}</span>
      </div>
      <div class="timeline-station">
        <span class="station-time">${fmt(leg.arrive_time)}</span>
        <span class="station-name">${escHtml(leg.to_station)}</span>
      </div>
    </div>
  `;

  return card;
}

/* ════════════ LEAFLET MAP ════════════ */

/**
 * Create the map (first call) or just clear existing layers (subsequent calls).
 * Centered on India: lat 20.59, lon 78.96, zoom 5.
 */
function initOrResetMap() {
  if (!leafletMap) {
    leafletMap = L.map('map', { zoomControl: true, attributionControl: true })
      .setView([20.59, 78.96], 5);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 18,
    }).addTo(leafletMap);
  } else {
    clearMap();
  }
}

/** Remove all polylines and markers added by the previous route draw. */
function clearMap() {
  mapMarkers.forEach(m => m.remove());
  mapMarkers = [];
  mapPolylines.forEach(p => p.remove());
  mapPolylines = [];
}

/**
 * Draw every route leg as its own styled Leaflet polyline, then add
 * start (A) and end (B) markers and fit the map to the full bounds.
 *
 * Styling:
 *   IN_TRAIN  → solid blue  (#1a73e8), weight 4
 *   TRANSFER  → dashed gray (#9ca3af), weight 2
 */
function drawRouteLegs(routeLegs) {
  if (!leafletMap) return;
  clearMap();

  const allLatLngs = [];

  routeLegs.forEach((leg, index) => {
    const { from_lat, from_lon, to_lat, to_lon, edge_type, from_station, to_station, cost } = leg;

    // Skip legs the backend couldn't geocode
    if (from_lat == null || from_lon == null || to_lat == null || to_lon == null) return;

    const fromLL = [from_lat, from_lon];
    const toLL   = [to_lat,   to_lon];
    const isTransfer = edge_type === 'TRANSFER';

    const poly = L.polyline([fromLL, toLL], {
      color:     isTransfer ? '#9ca3af' : '#1a73e8',
      weight:    isTransfer ? 2 : 4,
      opacity:   isTransfer ? 0.65 : 0.90,
      dashArray: isTransfer ? '6 9' : null,
      lineJoin:  'round',
    }).addTo(leafletMap);

    poly.bindPopup(
      isTransfer
        ? `<strong>🔄 Transfer / Layover</strong><br><span style="color:#6b7280">${escHtml(from_station)}</span>`
        : `<strong>🚆 Leg ${index + 1}</strong><br>
           ${escHtml(from_station)} → ${escHtml(to_station)}<br>
           <span style="color:#1a73e8;font-weight:600">₹${Number(cost).toLocaleString('en-IN')}</span>`
    );

    mapPolylines.push(poly);
    allLatLngs.push(fromLL, toLL);
  });

  if (allLatLngs.length === 0) return;

  // ── Terminus markers ───────────────────────────────────────────────────────
  const makeTerminusIcon = (bgColor, label) => L.divIcon({
    className: '',
    html: `<div style="
      width:22px;height:22px;
      background:${bgColor};
      border:2.5px solid #fff;
      border-radius:50%;
      box-shadow:0 2px 8px rgba(0,0,0,.35);
      display:flex;align-items:center;justify-content:center;
      font-size:10px;font-weight:700;color:#fff;
      font-family:sans-serif;
    ">${label}</div>`,
    iconSize:   [22, 22],
    iconAnchor: [11, 11],
  });

  const firstLeg = routeLegs.find(l => l.from_lat != null);
  const lastLeg  = [...routeLegs].reverse().find(l => l.to_lat != null);

  if (firstLeg) {
    const startMarker = L.marker([firstLeg.from_lat, firstLeg.from_lon],
      { icon: makeTerminusIcon('#1a73e8', 'A') }
    ).bindPopup(`<strong>🚉 Departure</strong><br>${escHtml(firstLeg.from_station)}`);
    startMarker.addTo(leafletMap);
    mapMarkers.push(startMarker);
  }

  if (lastLeg) {
    const endMarker = L.marker([lastLeg.to_lat, lastLeg.to_lon],
      { icon: makeTerminusIcon('#34a853', 'B') }
    ).bindPopup(`<strong>🏁 Arrival</strong><br>${escHtml(lastLeg.to_station)}`);
    endMarker.addTo(leafletMap);
    mapMarkers.push(endMarker);
  }

  // ── Auto-zoom to fit the entire route ──────────────────────────────────────
  leafletMap.fitBounds(L.latLngBounds(allLatLngs), { padding: [44, 44] });

  // Force Leaflet to recalculate tile layout (needed if container was hidden)
  setTimeout(() => leafletMap.invalidateSize(), 120);
}

/* ════════════ UI STATE HELPERS ════════════ */

function setLoadingState(loading) {
  if (loading) {
    loadingSpinner.removeAttribute('hidden');
    errorBanner.setAttribute('hidden', '');
    resultsHeader.setAttribute('hidden', '');
    routeCardsEl.innerHTML = '';
    mapWrapper.setAttribute('hidden', '');
    searchBtn.disabled = true;
    searchBtn.querySelector('.btn-text').textContent = 'Searching…';
  } else {
    loadingSpinner.setAttribute('hidden', '');
    searchBtn.disabled = false;
    searchBtn.querySelector('.btn-text').textContent = 'Search Routes';
  }
}

function showResultsSection(show) {
  if (show) resultsSection.removeAttribute('hidden');
  else resultsSection.setAttribute('hidden', '');
}

function showError(msg) {
  errorBanner.removeAttribute('hidden');
  errorMessage.textContent = msg;
  resultsHeader.setAttribute('hidden', '');
  routeCardsEl.innerHTML = '';
  mapWrapper.setAttribute('hidden', '');
}

function highlightError(input, msg) {
  input.classList.add('input-error');
  input.setAttribute('aria-invalid', 'true');
  input.setCustomValidity(msg);
  input.reportValidity();
  input.focus();
}

function clearHighlights() {
  [sourceInput, destInput].forEach(el => {
    el.classList.remove('input-error');
    el.removeAttribute('aria-invalid');
    el.setCustomValidity('');
  });
}

/* ════════════ UTILITY ════════════ */

const delay = ms => new Promise(r => setTimeout(r, ms));

function parseHours(duration) {
  // "15h 40m" → decimal hours
  const match = duration.match(/(\d+)h\s*(\d+)?m?/);
  if (!match) return 999;
  return parseInt(match[1], 10) + (parseInt(match[2] || 0, 10) / 60);
}

function shortName(stationFull) {
  // "New Delhi (NDLS)" → "New Delhi"
  return stationFull.split('(')[0].trim();
}

function escHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(str)));
  return div.innerHTML;
}

function capitalize(str) {
  return str.charAt(0).toUpperCase() + str.slice(1);
}
