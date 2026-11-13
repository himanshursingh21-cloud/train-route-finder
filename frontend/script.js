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
const form            = document.getElementById('search-form');
const sourceInput     = document.getElementById('source');
const destInput       = document.getElementById('destination');
const swapBtn         = document.getElementById('swap-btn');
const classSelect     = document.getElementById('travel-class');
const dateInput       = document.getElementById('travel-date');
const searchBtn       = document.getElementById('search-btn');
const resultsSection  = document.getElementById('results-section');
const loadingSpinner  = document.getElementById('loading-spinner');
const errorBanner     = document.getElementById('error-banner');
const errorMessage    = document.getElementById('error-message');
const resultsHeader   = document.getElementById('results-header');
const resultsMeta     = document.getElementById('results-meta');
const routeCardsEl    = document.getElementById('route-cards');
const mapWrapper      = document.getElementById('map-wrapper');

// Unified autocomplete datalist ID
const DATALIST_ID = 'stations-list';

/* ════════════ STATE ════════════ */
let leafletMap      = null;
let mapMarkers      = [];
let mapPolylines    = [];   // one Leaflet Polyline per route leg
let activeRouteId   = null;
let _stationsCache  = [];   // flat [{code,name,lat,lon}] — populated once; never bulk-injected into datalist

/* ════════════ INIT ════════════ */
(function init() {
  populateDateDefault();
  loadStations();           // async — fetches /api/stations and fills datalist
  attachEventListeners();
})();

/* ════════════ SETUP HELPERS ════════════ */

function populateDateDefault() {
  const today = new Date();
  const yyyy  = today.getFullYear();
  const mm    = String(today.getMonth() + 1).padStart(2, '0');
  const dd    = String(today.getDate()).padStart(2, '0');
  dateInput.value = `${yyyy}-${mm}-${dd}`;
  dateInput.min   = `${yyyy}-${mm}-${dd}`;
}

/**
 * Fetch all routable stations from /api/stations once at startup.
 * Stores results in:
 *   _stationsCache – flat array used by injectStationSuggestions()
 *   stationIndex   – O(1) code→{name,lat,lon} map used by drawRouteLegs()
 *
 * The datalist is intentionally left empty here; <option> tags are
 * injected lazily in injectStationSuggestions() as the user types.
 */
async function loadStations() {
  try {
    const res = await fetch(`${API_BASE_URL}/api/stations`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const stations = await res.json(); // [{code, name, lat, lon}, ...]

    _stationsCache = stations;          // keep flat array for search

    for (const s of stations) {
      stationIndex[s.code] = { name: s.name, lat: s.lat, lon: s.lon };
    }
  } catch (err) {
    console.warn('Could not load station list from API:', err.message);
  }
}

function attachEventListeners() {
  form.addEventListener('submit', onSearchSubmit);
  swapBtn.addEventListener('click', swapStations);

  // Lazy autocomplete: only filter + inject when ≥ 2 chars are typed
  const datalist = document.getElementById(DATALIST_ID);
  [sourceInput, destInput].forEach(input => {
    input.addEventListener('input', () =>
      injectStationSuggestions(input.value, datalist)
    );
  });
}

/**
 * Filter _stationsCache and inject the top 15 matches into the shared
 * <datalist id="station-options"> element.
 *
 * Matching order (most specific first):
 *   1. Station code  starts-with  query  (e.g. "ND"  → NDLS)
 *   2. Station name  starts-with  query  (e.g. "New" → New Delhi)
 *   3. Station name  contains     query  (e.g. "elh" → Delhi)
 *
 * @param {string}              rawQuery  Current value of the input field
 * @param {HTMLDataListElement} datalist  The shared <datalist> element
 */
function injectStationSuggestions(rawQuery, datalist) {
  datalist.innerHTML = '';                 // clear previous suggestions

  const q = rawQuery.trim().toUpperCase();
  if (q.length < 2) return;               // wait for at least 2 characters

  const LIMIT       = 15;
  const codePrefix  = [];
  const namePrefix  = [];
  const nameContain = [];

  for (const s of _stationsCache) {
    const code   = s.code.toUpperCase();
    const nameUC = s.name.toUpperCase();

    if (code.startsWith(q))        { codePrefix.push(s);  }
    else if (nameUC.startsWith(q)) { namePrefix.push(s);  }
    else if (nameUC.includes(q))   { nameContain.push(s); }

    // Early-exit once we have far more than LIMIT candidates
    if (codePrefix.length + namePrefix.length + nameContain.length >= LIMIT * 3) break;
  }

  // Merge in priority order, slice to top 15
  const matches  = [...codePrefix, ...namePrefix, ...nameContain].slice(0, LIMIT);
  const fragment = document.createDocumentFragment();

  for (const s of matches) {
    const opt   = document.createElement('option');
    opt.value   = `${s.code} - ${s.name}`;
    fragment.appendChild(opt);
  }

  datalist.appendChild(fragment);
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

  const mode = document.querySelector('input[name="search_mode"]:checked').value;
  const maxLayovers = document.getElementById('max-layovers').value;

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
    const data = await fetchRoutes({ source, destination, mode, max_layover: maxLayovers });
    // Pass raw strings for display; bare codes live inside route_legs from backend
    renderBackendResults(data, { source: sourceRaw, destination: destRaw, mode });
  } catch (err) {
    showError(err.message || 'Could not fetch routes. Is the backend running?');
  } finally {
    setLoadingState(false);
  }
}

/* ════════════ DATA FETCHING ════════════ */
async function fetchRoutes({ source, destination, mode, max_layover }) {
  const abortCtrl = new AbortController();
  const timeoutMs = 10000;

  // Automatically abort after timeoutMs
  const timeoutId = setTimeout(() => abortCtrl.abort(), timeoutMs);

  try {
    const url = new URL(`${API_BASE_URL}/api/route`);
    url.searchParams.set('source', source);
    url.searchParams.set('destination', destination);
    url.searchParams.set('preference', mode);
    if (max_layover && max_layover !== 'any') {
      url.searchParams.set('max_layovers', max_layover);
    }

    const response = await fetch(url.toString(), {
      method: 'GET',
      headers: { 'Accept': 'application/json' },
      signal: abortCtrl.signal,
    });

    // Clear the timeout since the request completed
    clearTimeout(timeoutId);

    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error || `Server error ${response.status}`);
    }

    return response.json(); // { total_cost, total_time, route_legs }
  } catch (err) {
    // Re-throw AbortError with a specific message for the caller
    if (err.name === 'AbortError') {
      throw new Error('Search timeout: The backend took too long to respond');
    }
    throw err;
  }
}

/* ════════════ BACKEND RESULTS RENDERER ════════════ */

function renderBackendResults(data, { source, destination, mode }) {
  routeCardsEl.innerHTML = '';

  // New API structure: { cheapest: {...}, fastest: {...}, balanced: {...}, direct_baseline: {...} | null }
  const { cheapest, fastest, balanced, direct_baseline } = data;

  // Helper to render a single preference option
  function renderOption(optionData, preferenceName) {
    if (!optionData || optionData.error) {
      const errCard = document.createElement('div');
      errCard.className = 'route-card error-card';
      errCard.setAttribute('role', 'listitem');
      errCard.textContent = optionData?.error || `No ${preferenceName} route found`;
      routeCardsEl.appendChild(errCard);
      return;
    }

    const { total_cost, total_time, route_legs } = optionData;

    // Render route legs
    route_legs.forEach((leg, index) => {
      const card = buildLegCard(leg, index, preferenceName);
      routeCardsEl.appendChild(card);
    });

    // Summary line for this option
    const hrs  = Math.floor(total_time);
    const mins = Math.round((total_time - hrs) * 60);
    let timeDisplay = `${hrs}h`;
    if (mins > 0) timeDisplay += ` ${String(mins).padStart(2, '0')}m`;

    const savingsInfo = optionData.savings_vs_direct !== undefined
      ? ` (saves ₹${optionData.savings_vs_direct} vs direct)`
      : '';

    const optionHeader = document.createElement('div');
    optionHeader.className = 'option-header';
    optionHeader.innerHTML = `
      <strong>${preferenceName} Route</strong>
      <span class="fare ₹${total_cost.toFixed(2)} ${timeDisplay}${savingsInfo}</span>
    `;
    routeCardsEl.appendChild(optionHeader);
  }

  // Render each preference option
  renderOption(cheapest, 'Cheapest');
  renderOption(fastest,  'Fastest');
  renderOption(balanced, 'Balanced');

  // ── If a direct train exists, render it first as a comparison card ──
  if (direct_baseline) {
    const directCard = buildDirectBaselineCard(direct_baseline);
    routeCardsEl.insertBefore(directCard, routeCardsEl.firstChild);
  }

  // Summary header - show meta for the cheapest route as primary
  resultsHeader.removeAttribute('hidden');
  if (cheapest && !cheapest.error) {
    const hrs  = Math.floor(cheapest.total_time);
    const mins = Math.round((cheapest.total_time - hrs) * 60);
    let timeDisplay = `${hrs}h`;
    if (mins > 0) timeDisplay += ` ${String(mins).padStart(2, '0')}m`;
    resultsMeta.textContent =
      `${source} → ${destination} · ₹${cheapest.total_cost.toFixed(2)} total · ${timeDisplay} · ${capitalize(mode)}`;
  } else if (fastest && !fastest.error) {
    const hrs  = Math.floor(fastest.total_time);
    const mins = Math.round((fastest.total_time - hrs) * 60);
    let timeDisplay = `${hrs}h`;
    if (mins > 0) timeDisplay += ` ${String(mins).padStart(2, '0')}m`;
    resultsMeta.textContent =
      `${source} → ${destination} · ₹${fastest.total_cost.toFixed(2)} total · ${timeDisplay} · ${capitalize(mode)}`;
  } else {
    resultsMeta.textContent = `${source} → ${destination} — No routes found`;
  }

  // Show map only when at least one leg carries coordinate data
  // Collect all route legs from all options
  const allRouteLegs = [];
  [cheapest, fastest, balanced].forEach(opt => {
    if (opt && opt.route_legs) {
      opt.route_legs.forEach(leg => {
        if (leg.edge_type !== 'TRANSFER') { // only IN_TRAIN legs have coords typically
          allRouteLegs.push(leg);
        }
      });
    }
  });

  const hasCoords = allRouteLegs.some(
    leg => leg.from_lat != null && leg.to_lat != null
  );
  if (hasCoords) {
    mapWrapper.removeAttribute('hidden');
    initOrResetMap();
    drawRouteLegs(allRouteLegs);
  } else {
    mapWrapper.setAttribute('hidden', '');
  }

  resultsSection.removeAttribute('hidden');
}

/** Render a Direct Train baseline card (single‑train edge) */
function buildDirectBaselineCard(leg) {
  const card = document.createElement('article');
  card.className = 'route-card direct-baseline';
  card.setAttribute('role', 'listitem');
  card.setAttribute('aria-label', `Direct train ${leg.train_name} (${leg.train_number}) from ${leg.from_station_name} to ${leg.to_station_name}`);

  card.innerHTML = `
    <div class="card-top" style="border-left: 4px solid #002147; background: #e8f4fd;">
      <div>
        <div class="train-name" style="color: #002147; font-weight: 700;">${leg.train_name}</div>
        <div class="train-number" style="color: #002147;">Train ${leg.train_number}</div>
      </div>
      <div class="fare-block" style="text-align: left;">
        <div class="fare" style="color: #002147;">₹${Number(leg.cost).toLocaleString('en-IN')}</div>
        <div class="fare-class" style="color: #4a4a4a;">Baseline</div>
      </div>
    </div>

    <div class="route-timeline" role="group" aria-label="Leg timeline">
      <div class="timeline-station">
        <span class="station-time">${(leg.depart_time).toFixed(2)}h</span>
        <span class="station-name">${escHtml(leg.from_station_name)}</span>
      </div>
      <div class="timeline-line">
        <span class="duration-label" aria-hidden="true" style="color: #002147;">direct</span>
      </div>
      <div class="timeline-station">
        <span class="station-time">${(leg.arrive_time).toFixed(2)}h</span>
        <span class="station-name">${escHtml(leg.to_station_name)}</span>
      </div>
    </div>

    <div class="layover-warning">
      <strong>Direct train available</strong><br>
      Single‑train journey: ${leg.from_station_name} → ${leg.to_station_name}
    </div>
  `;

  return card;
}

function buildLegCard(leg, index, preferenceName = '') {
  const isTransfer = leg.edge_type === 'TRANSFER';

  // Convert decimal hours → "Xh Ym" format
  const fmt = (dec) => {
    const h = Math.floor(dec);
    const m = Math.round((dec - h) * 60);
    if (m === 0) return `${h}h`;
    return `${h}h ${String(m).padStart(2, '0')}m`;
  };

  // Get train display name: "Train Name (Train Number)"
  const trainDisplay = leg.train_name || leg.train_number || '—';
  const trainCodeDisplay = leg.train_number || '—';

  const card = document.createElement('article');
  card.className = isTransfer ? 'route-card transfer-leg' : 'route-card';
  card.setAttribute('role', 'listitem');
  card.setAttribute('aria-label',
    isTransfer
      ? `Switch trains at ${leg.from_station_name} after ${fmt(leg.arrive_time - leg.depart_time)} wait`
      : `Leg ${index + 1}: ${leg.from_station_name} to ${leg.to_station_name}, ₹${leg.cost} ${preferenceName ? `(${preferenceName})` : ''}`);

  // Build the layover warning HTML for transfer legs
  let layoverWarning = '';
  if (isTransfer && leg.arrive_time && leg.depart_time) {
    const layoverMinutes = Math.round((leg.arrive_time - leg.depart_time) * 60);
    const layoverHours = Math.floor(layoverMinutes / 60);
    const layoverMin = layoverMinutes % 60;
    const layoverDesc = layoverHours > 0
      ? `${layoverHours}h ${String(layoverMin).padStart(2, '0')}m`
      : `${layoverMinutes}min`;
    layoverWarning = `
      <div class="layover-warning">
        <strong>Switch trains at ${leg.from_station_name}</strong><br>
        Layover: ${layoverDesc}
      </div>`;
  }

  card.innerHTML = `
    <div class="card-top">
      <div>
        <div class="train-name">${!isTransfer ? `🚆 ${trainDisplay}` : ''}</div>
        <div class="train-number">${escHtml(trainCodeDisplay)}</div>
      </div>
      <div class="fare-block">
        <div class="fare">${isTransfer ? 'Free' : `₹${Number(leg.cost).toLocaleString('en-IN')}`}</div>
        <div class="fare-class">${isTransfer ? 'layover' : 'fare'}</div>
      </div>
    </div>

    ${layoverWarning}

    <div class="route-timeline" role="group" aria-label="Leg timeline">
      <div class="timeline-station">
        <span class="station-time">${fmt(leg.depart_time)}</span>
        <span class="station-name">${escHtml(leg.from_station_name)}</span>
      </div>
      <div class="timeline-line">
        <span class="duration-label" aria-hidden="true">${isTransfer ? 'wait' : 'travel'}</span>
      </div>
      <div class="timeline-station">
        <span class="station-time">${fmt(leg.arrive_time)}</span>
        <span class="station-name">${escHtml(leg.to_station_name)}</span>
      </div>
    </div>
  `;

  return card;
}

/* ════════════ LEAFLET MAP ════════════ */

/**
 * Create the map on first call (centered on India), or clear existing
 * layers on subsequent calls ready for the next route draw.
 */
function initOrResetMap() {
  if (!leafletMap) {
    leafletMap = L.map('map', { zoomControl: true, attributionControl: true })
      .setView([20.59, 78.96], 5);  // India centre
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 18,
    }).addTo(leafletMap);
  } else {
    clearMap();
  }
}

/** Remove all polylines and markers from the previous route draw. */
function clearMap() {
  mapMarkers.forEach(m => m.remove());
  mapMarkers = [];
  mapPolylines.forEach(p => p.remove());
  mapPolylines = [];
}

/**
 * Draw every route leg as its own styled Leaflet polyline, then place
 * start (A) and end (B) terminus markers and fit the map to full bounds.
 *
 * Visual encoding:
 *   IN_TRAIN  → solid blue  (#1a73e8), weight 4
 *   TRANSFER  → dashed gray (#9ca3af), weight 2
 */
function drawRouteLegs(routeLegs) {
  if (!leafletMap) return;
  clearMap();

  const allLatLngs = [];

  routeLegs.forEach((leg, index) => {
    const { from_lat, from_lon, to_lat, to_lon,
            edge_type, from_station, to_station, cost } = leg;

    // Skip legs the backend could not geocode
    if (from_lat == null || from_lon == null ||
        to_lat   == null || to_lon   == null) return;

    const fromLL     = [from_lat, from_lon];
    const toLL       = [to_lat,   to_lon];
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
        ? `<strong>🔄 Transfer / Layover</strong><br>
           <span style="color:#6b7280">${escHtml(from_station)}</span>`
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
    const startMarker = L.marker(
      [firstLeg.from_lat, firstLeg.from_lon],
      { icon: makeTerminusIcon('#1a73e8', 'A') }
    ).bindPopup(`<strong>🚉 Departure</strong><br>${escHtml(firstLeg.from_station)}`);
    startMarker.addTo(leafletMap);
    mapMarkers.push(startMarker);
  }

  if (lastLeg) {
    const endMarker = L.marker(
      [lastLeg.to_lat, lastLeg.to_lon],
      { icon: makeTerminusIcon('#34a853', 'B') }
    ).bindPopup(`<strong>🏁 Arrival</strong><br>${escHtml(lastLeg.to_station)}`);
    endMarker.addTo(leafletMap);
    mapMarkers.push(endMarker);
  }

  // ── Auto-zoom to frame the entire route ────────────────────────────────────
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

function shortName(stationFull) {
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
