/* AgriClimate Explorer frontend: map selection -> analysis -> options -> results. */
const CFG = window.APP_CONFIG || {};
const API = (CFG.API_BASE || "").replace(/\/$/, "");

const HIST_VARS = {
  precipitation: { label: "Precipitation", firstYear: 1998, about: "NASA GPM IMERG satellite rainfall, about 10 km grid." },
  soil_moisture: { label: "Soil Moisture (top 10 cm)", firstYear: 2000, about: "NASA GLDAS land model, about 25 km grid." },
  air_temp: { label: "Air Temperature", firstYear: 2000, about: "NASA GLDAS (monthly/yearly) and MERRA-2 (daily)." },
  evapotranspiration: { label: "Total Land Evapotranspiration", firstYear: 2000, about: "Water lost from soil and plants. NASA GLDAS, about 25 km grid." },
  min_temp: { label: "Minimum Temperature", firstYear: 1980, about: "Daily lowest temperature from NASA MERRA-2, about 50 km grid." },
  max_temp: { label: "Maximum Temperature", firstYear: 1980, about: "Daily highest temperature from NASA MERRA-2, about 50 km grid." },
};
const FUT_LABELS = { precipitation: "Precipitation", min_temp: "Minimum Temperature", max_temp: "Maximum Temperature" };
const SSP_LABELS = { ssp126: "SSP1-2.6 (low emissions)", ssp245: "SSP2-4.5 (middle of the road)", ssp370: "SSP3-7.0 (high emissions)", ssp585: "SSP5-8.5 (very high emissions)" };
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const THIS_YEAR = new Date().getFullYear();
const SERIES_COLOR = "#2a78d6";

const state = {
  history: ["step-map"],
  mode: "pin",          // "pin" | "draw"
  geometry: null,       // GeoJSON Point or Polygon, [lng, lat] order
  label: "",
  analysis: null,
  lastRequest: null,
  lastResult: null,
  pollTimer: null,
  runId: 0,
};

let map, geocoder, pinMarker, youMarker, farmPolygon, cornerLine, chart;
const $ = (id) => document.getElementById(id);

/* ======================= Google Maps ======================= */

(function loadGoogleMaps() {
  if (!CFG.GOOGLE_MAPS_API_KEY || CFG.GOOGLE_MAPS_API_KEY.startsWith("YOUR_")) {
    setMapStatus("Add your Google Maps API key in config.js to show the map.", "warn");
    return;
  }
  window.initMap = initMap;
  const s = document.createElement("script");
  s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(CFG.GOOGLE_MAPS_API_KEY)}` +
    "&libraries=geometry,marker&loading=async&callback=initMap";
  s.async = true;
  s.onerror = () => setMapStatus("Could not load Google Maps. Check your internet connection and API key.", "warn");
  document.head.appendChild(s);
})();

function initMap() {
  map = new google.maps.Map($("map"), {
    center: CFG.FALLBACK_CENTER || { lat: 0, lng: 20 },
    zoom: CFG.FALLBACK_ZOOM || 3,
    mapId: CFG.MAP_ID || "DEMO_MAP_ID",
    mapTypeId: "hybrid",          // satellite photo with labels: farmers can see their fields
    streetViewControl: false,
    clickableIcons: false,
    gestureHandling: "greedy",
  });
  geocoder = new google.maps.Geocoder();

  // Create the (empty) corner list first. With `paths: []` the polygon has no path at all,
  // so getPath() returns undefined and drawing breaks.
  farmPolygon = new google.maps.Polygon({
    map, editable: true,
    strokeColor: "#ffd400", strokeWeight: 3, fillColor: "#ffd400", fillOpacity: 0.25,
  });
  farmPolygon.setPath(new google.maps.MVCArray());
  const path = farmPolygon.getPath();
  // Uses the same corner list as the polygon, so the first two taps already show a line
  cornerLine = new google.maps.Polyline({ map, path, clickable: false, strokeColor: "#ffd400", strokeWeight: 3 });
  ["insert_at", "set_at", "remove_at"].forEach((evt) => path.addListener(evt, onPolygonChanged));

  map.addListener("click", (e) => onMapClick(e.latLng));
  farmPolygon.addListener("click", (e) => onMapClick(e.latLng));

  locateUser();
}

function locateUser() {
  if (!map) return;
  if (!("geolocation" in navigator) || !window.isSecureContext) {
    return locationUnavailable("Your location isn't available (the site must use https or localhost).");
  }
  setMapStatus("Finding your location…");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const here = { lat: pos.coords.latitude, lng: pos.coords.longitude };
      map.setCenter(here);
      map.setZoom(15);
      if (youMarker) youMarker.map = null;
      const dot = document.createElement("div");
      dot.style.cssText = "width:16px;height:16px;border-radius:50%;background:#1a73e8;border:3px solid #fff;box-shadow:0 0 0 2px rgba(26,115,232,.35)";
      youMarker = new google.maps.marker.AdvancedMarkerElement({ map, position: here, content: dot, title: "You are here" });
      setMapStatus("Showing your current location. Tap your farm to pin it, or choose “Draw farm boundary” to outline your field.", "ok");
    },
    (err) => {
      const why = { 1: "Location permission was denied.", 2: "Your location couldn't be found.", 3: "Finding your location took too long." };
      locationUnavailable(why[err.code] || "Your location isn't available.");
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
  );
}

function locationUnavailable(reason) {
  setMapStatus(`${reason} Search for your town above, or zoom and drag the map to your farm and tap to select it.`, "warn");
}

function setMapStatus(msg, kind = "") {
  const el = $("map-status");
  el.textContent = msg;
  el.className = "map-status " + kind;
}

/* ---------- Selection ---------- */

function setMode(mode) {
  state.mode = mode;
  $("mode-pin").classList.toggle("active", mode === "pin");
  $("mode-draw").classList.toggle("active", mode === "draw");
  $("btn-undo").classList.toggle("hidden", mode !== "draw");
  clearSelection();
  if (map) {
    // Crosshair shows the map is ready for corner taps; stop double-taps from zooming while drawing
    map.setOptions({ draggableCursor: mode === "draw" ? "crosshair" : null, disableDoubleClickZoom: mode === "draw" });
  }
  if (mode === "draw") setMapStatus("Drawing: click once on each corner of your field (don't drag). After 3 corners the shape appears. Drag the white dots to adjust.", "ok");
  else setMapStatus("Tap the map to pin your farm.", "ok");
}

function onMapClick(latLng) {
  if (state.mode === "draw") {
    farmPolygon.getPath().push(latLng);
  } else {
    setPoint(latLng.lat(), latLng.lng());
  }
}

function setPoint(lat, lng, label) {
  if (state.mode !== "pin") setMode("pin");
  farmPolygon.getPath().clear();
  if (pinMarker) pinMarker.map = null;
  pinMarker = new google.maps.marker.AdvancedMarkerElement({ map, position: { lat, lng }, title: "Selected farm" });
  state.geometry = { type: "Point", coordinates: [round(lng, 6), round(lat, 6)] };
  state.label = label || `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
  updateSelectionBar(`📍 ${state.label}`);
  if (!label) reverseGeocode(lat, lng);
}

function onPolygonChanged() {
  const pts = farmPolygon.getPath().getArray();
  showFirstCorner(pts.length > 0 && pts.length < 3 ? pts[0] : null);
  if (pts.length < 3) {
    state.geometry = null;
    updateSelectionBar(pts.length ? `${pts.length} corner${pts.length > 1 ? "s" : ""}: tap at least ${3 - pts.length} more` : "Nothing selected yet");
    return;
  }
  const ring = pts.map((p) => [round(p.lng(), 6), round(p.lat(), 6)]);
  ring.push(ring[0]);
  state.geometry = { type: "Polygon", coordinates: [ring] };
  const ha = google.maps.geometry.spherical.computeArea(pts) / 10000;
  const c = boundsCenter(pts);
  state.label = `Farm field of ${ha.toFixed(2)} ha (${(ha * 2.471).toFixed(2)} acres) near ${c.lat.toFixed(4)}, ${c.lng.toFixed(4)}`;
  updateSelectionBar(`✏️ ${ha.toFixed(2)} ha (${(ha * 2.471).toFixed(2)} acres), ${pts.length} corners`);
}

// A yellow dot on the first corner, so the first tap visibly does something
let firstCornerMarker = null;
function showFirstCorner(latLng) {
  if (!latLng) {
    if (firstCornerMarker) firstCornerMarker.map = null;
    return;
  }
  if (!firstCornerMarker) {
    const dot = document.createElement("div");
    dot.style.cssText = "width:14px;height:14px;border-radius:50%;background:#ffd400;border:2px solid #000;transform:translateY(7px);pointer-events:none";
    firstCornerMarker = new google.maps.marker.AdvancedMarkerElement({ content: dot });
  }
  firstCornerMarker.position = latLng;
  firstCornerMarker.map = map;
}

function clearSelection() {
  if (pinMarker) { pinMarker.map = null; pinMarker = null; }
  if (farmPolygon) farmPolygon.getPath().clear();
  state.geometry = null;
  updateSelectionBar("Nothing selected yet");
}

function updateSelectionBar(text) {
  $("selection-text").textContent = text;
  $("btn-confirm").disabled = !state.geometry;
}

function reverseGeocode(lat, lng) {
  if (!geocoder) return;
  geocoder.geocode({ location: { lat, lng } }).then(({ results }) => {
    const r = results.find((x) => x.types.includes("locality")) || results.find((x) => x.types.includes("administrative_area_level_2")) || results[0];
    const g = state.geometry;
    const same = g && g.type === "Point" && g.coordinates[0] === round(lng, 6) && g.coordinates[1] === round(lat, 6);
    if (r && same) {
      state.label = `${r.formatted_address} (${lat.toFixed(4)}, ${lng.toFixed(4)})`;
      updateSelectionBar(`📍 ${state.label}`);
    }
  }).catch(() => { /* keep coordinates as the label */ });
}

function searchLocation(query) {
  query = query.trim();
  if (!query) return;
  const m = query.match(/^\s*(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (m) {
    const lat = parseFloat(m[1]), lng = parseFloat(m[2]);
    if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return setMapStatus("Coordinates should be latitude, longitude (e.g. 1.3521, 103.8198).", "warn");
    map.setCenter({ lat, lng }); map.setZoom(15);
    return setPoint(lat, lng);
  }
  if (!geocoder) return;
  geocoder.geocode({ address: query }).then(({ results }) => {
    const r = results[0];
    if (r.geometry.viewport) map.fitBounds(r.geometry.viewport); else map.setCenter(r.geometry.location);
    setMapStatus(`Found ${r.formatted_address}. Tap your farm to pin it, or draw its boundary.`, "ok");
  }).catch(() => setMapStatus("Place not found. Try a town name, or type coordinates like 1.3521, 103.8198.", "warn"));
}

/* ======================= Navigation ======================= */

function show(viewId, push = true) {
  document.querySelectorAll(".step-view").forEach((el) => el.classList.toggle("active", el.id === viewId));
  if (push && state.history[state.history.length - 1] !== viewId) state.history.push(viewId);
  $("btn-back").classList.toggle("hidden", viewId === "step-map");
  window.scrollTo(0, 0);
}

function goBack() {
  stopPolling();
  if (state.history.length > 1) state.history.pop();
  show(state.history[state.history.length - 1], false);
}

function goHome() {
  stopPolling();
  state.history = ["step-map"];
  show("step-map", false);
}

function openModal(id) { $(id).classList.remove("hidden"); }
function closeModals() { document.querySelectorAll(".modal").forEach((m) => m.classList.add("hidden")); }

/* ======================= Historical form ======================= */

function initHistoricalForm() {
  const sel = $("hist-variable");
  for (const [k, v] of Object.entries(HIST_VARS)) sel.add(new Option(v.label, k));
  for (const id of ["hist-start-month", "hist-end-month"]) MONTHS.forEach((m, i) => $(id).add(new Option(m, String(i + 1).padStart(2, "0"))));
  sel.addEventListener("change", fillHistYears);
  fillHistYears();
  // Defaults: last 10 full years. Recent months can be missing because NASA products lag 1-3 months.
  $("hist-start-month").value = "01";
  $("hist-start-year").value = String(Math.max(THIS_YEAR - 10, HIST_VARS[sel.value].firstYear));
  $("hist-end-month").value = "12";
  $("hist-end-year").value = String(THIS_YEAR - 1);
}

function fillHistYears() {
  const v = HIST_VARS[$("hist-variable").value];
  $("hist-variable-about").textContent = `${v.about} Data from ${v.firstYear}.`;
  for (const id of ["hist-start-year", "hist-end-year"]) {
    const s = $(id), keep = s.value;
    s.innerHTML = "";
    for (let y = THIS_YEAR; y >= v.firstYear; y--) s.add(new Option(String(y), String(y)));
    s.value = keep && +keep >= v.firstYear ? keep : String(id === "hist-start-year" ? Math.max(v.firstYear, THIS_YEAR - 10) : THIS_YEAR - 1);
  }
}

function readHistoricalForm() {
  const start = `${$("hist-start-year").value}-${$("hist-start-month").value}`;
  const end = `${$("hist-end-year").value}-${$("hist-end-month").value}`;
  const frequency = document.querySelector("input[name=hist-frequency]:checked").value;
  if (end < start) return { error: "The end month must be after the start month." };
  const months = (+end.slice(0, 4) - +start.slice(0, 4)) * 12 + (+end.slice(5) - +start.slice(5)) + 1;
  if (frequency === "daily" && months > 24) return { error: "Daily values are limited to 2 years. Shorten the period, or choose Monthly or Yearly." };
  if (frequency === "yearly" && months < 24) return { error: "Choose at least 2 years for yearly values, or pick Monthly." };
  return { variable: $("hist-variable").value, frequency, start, end };
}

/* ======================= Future form ======================= */

function initFutureForm() {
  const p = $("fut-period");
  FUTURE_PERIODS.forEach((x) => p.add(new Option(periodLabel(x), x)));
  p.value = FUTURE_PERIODS[0];
}

function readFutureForm() {
  return { variable: $("fut-variable").value, period: $("fut-period").value };
}

/* ======================= Requests ======================= */

// A dropped connection (Safari: "Load failed", Chrome: "Failed to fetch") rather than a server error
class NetworkError extends Error {}

async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(API + path, { headers: { "Content-Type": "application/json" }, ...options });
  } catch (e) {
    throw new NetworkError("Lost connection to the server. Check your internet and keep this page open while it loads.");
  }
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    // Render returns these while the free server is waking up
    let detail = null;
    try { detail = (await res.clone().json()).detail; } catch { /* HTML page from the host */ }
    if (!detail) throw new NetworkError("The server is starting up.");
  }
  let body = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const detail = body && body.detail;
    throw new Error(typeof detail === "string" ? detail : (Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : `Server error (${res.status})`));
  }
  return body;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Retry only dropped connections (not real errors). Waits grow: 5 s, 10 s, 20 s…
async function withRetry(fn, { tries, runId, onRetry }) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fn();
    } catch (e) {
      if (!(e instanceof NetworkError) || attempt >= tries || runId !== state.runId) throw e;
      onRetry && onRetry(attempt);
      await sleep(5000 * 2 ** (attempt - 1));
    }
  }
}

async function runHistorical(params) {
  const runId = startResults(params, "historical");
  setProgress("Sending your request to NASA Giovanni…", 2);
  try {
    // Several tries: the first request after the free server sleeps can take ~1 minute.
    // Re-sending is safe: the backend reuses the NASA job for identical requests.
    const { job } = await withRetry(
      () => api("/api/historical", { method: "POST", body: JSON.stringify({ ...params, geometry: state.geometry }) }),
      { tries: 4, runId, onRetry: () => setProgress("Waking up the server, this can take about a minute…", 2) },
    );
    if (runId !== state.runId) return;
    const started = Date.now();
    let misses = 0;  // checks in a row that lost the connection
    const poll = async () => {
      if (runId !== state.runId) return;
      try {
        const r = await api(`/api/historical/${job}`);
        misses = 0;
        if (runId !== state.runId) return;
        if (r.status === "successful") return showResult(r);
        if (r.status === "failed") return showError(r.message || "NASA could not process this request.", true);
        setProgress(`NASA is calculating the average for your area… ${r.progress || 0}%`, Math.max(5, r.progress || 0));
      } catch (e) {
        if (runId !== state.runId) return;
        if (!(e instanceof NetworkError) || ++misses > 8) return showError(e.message, true);
        setProgress("Connection lost, reconnecting… (NASA keeps working on your request)", null);
      }
      if (Date.now() - started > 15 * 60 * 1000) return showError("This is taking too long. Try a shorter time period.", true);
      state.pollTimer = setTimeout(poll, misses ? 6000 : 4000);
    };
    state.pollTimer = setTimeout(poll, 3000);
  } catch (e) {
    if (runId === state.runId) showError(e.message, true);
  }
}

async function runFuture(params) {
  const runId = startResults(params, "future");
  setProgress("Loading NASA climate projections…", null);
  const g = state.geometry;
  const [start, end] = params.period.split("-").map(Number);
  const fetchProjections = () => (g.type === "Point"
    ? api(`/api/projections/point?lat=${g.coordinates[1]}&lon=${g.coordinates[0]}`)
    : api("/api/projections/area", { method: "POST", body: JSON.stringify({ geometry: g }) }));
  const testsRequest = api("/api/future/tests", {
    method: "POST",
    body: JSON.stringify({ variable: params.variable, start_year: start, end_year: end, geometry: g }),
  }).catch((e) => ({ available: false, reason: e.message }));
  const retry = { tries: 3, runId, onRetry: () => setProgress("Connection lost, trying again…", null) };
  try {
    let proj;
    try {
      proj = await withRetry(fetchProjections, retry);
    } catch (e) {
      if (!/outside/i.test(e.message)) throw e;
      if (runId !== state.runId) return;
      setProgress("This farm is outside the region with ready-made projections, so they are being fetched directly from NASA. This can take up to a minute. Please keep this page open…", null);
      proj = await withRetry(() => api("/api/future/nasa", {
        method: "POST",
        body: JSON.stringify({ variable: params.variable, geometry: g }),
      }), retry);
    }
    if (runId !== state.runId) return;
    const result = { kind: "projections", variable: params.variable, period: params.period, proj, tests: null };
    showFutureResult(result);
    const tests = await testsRequest;
    if (runId !== state.runId) return;
    result.tests = tests;
    renderFutureTests(result);
  } catch (e) {
    if (runId === state.runId) showError(e.message, true);
  }
}

function retryLast() {
  const req = state.lastRequest;
  if (!req) return;
  if (req.kind === "historical") runHistorical(req.params); else runFuture(req.params);
}

function startResults(params, kind) {
  stopPolling();
  state.runId += 1;
  state.lastRequest = { kind, params };
  state.lastResult = null;
  const isHist = kind === "historical";
  $("results-title").textContent = isHist
    ? `${HIST_VARS[params.variable].label}: ${capitalize(params.frequency)} history`
    : `${FUTURE_VARS[params.variable].label}: future projection`;
  $("results-subtitle").textContent = isHist
    ? `${state.label} · ${fmtMonth(params.start)} to ${fmtMonth(params.end)}`
    : `${state.label} · ${periodLabel(params.period)} vs ${BASELINE_LABEL} · best case (SSP1-2.6) and worst case (SSP5-8.5)`;
  $("btn-other").textContent = isHist ? "See future scenarios" : "See historical trends";
  $("progress").classList.remove("hidden");
  $("results-body").classList.add("hidden");
  $("results-error").textContent = "";
  show("step-results");
  return state.runId;
}

function stopPolling() {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = null;
  state.runId += 1; // ignore late responses from older runs
}

function setProgress(text, pct) {
  $("progress-text").textContent = text;
  const fill = $("progress-fill");
  fill.classList.toggle("indeterminate", pct == null);
  if (pct != null) fill.style.width = `${pct}%`;
  else fill.style.width = "";
}

function showError(msg, canRetry = false) {
  $("progress").classList.add("hidden");
  const el = $("results-error");
  el.textContent = `Sorry, something went wrong: ${msg} `;
  if (canRetry && state.lastRequest) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "primary-btn";
    b.textContent = "Try again";
    b.onclick = retryLast;
    el.appendChild(b);
  }
}

/* ======================= Results ======================= */

function showResult(r) {
  state.lastResult = r;
  $("progress").classList.add("hidden");
  if (r.kind === "projections") return showFutureResult(r);
  $("scenario-compare").classList.add("hidden");
  if (!r.data || !r.data.length) return showError("No data was found for this place and time period.");
  $("results-body").classList.remove("hidden");
  $("insight").textContent = buildInsight(r);
  renderStats(r);
  renderChart(r);
  renderTable(r);
  renderStatistics(r);
  const note = r.data.some((d) => d.complete === false) ? " Faded bars or hollow points mark periods with incomplete data." : "";
  const band = isFuture(r) && (r.models || []).length > 1 ? " The line is the average of the climate models; the shaded band shows the range between them." : "";
  $("results-source").textContent = `Source: ${r.source}${band}${note}`;
}

function isFuture(r) { return state.lastRequest && state.lastRequest.kind === "future"; }
function kindOf(r) { return r.unit.startsWith("mm") ? "flux" : r.unit === "°C" ? "temp" : "soil"; }
function periodWord(r) { return { yearly: "year", monthly: "month", daily: "day" }[r.frequency]; }

function buildInsight(r) {
  const s = r.summary || {};
  const k = kindOf(r), unit = r.unit.replace(/\/(year|month|day)$/, "");
  const fmt = (v) => `${fmtNum(v)} ${unit}`;
  const name = r.label.toLowerCase();
  const per = periodWord(r);
  const perText = k === "flux" ? ` per ${per}` : "";  // totals are "per year"; temperatures are not
  const parts = [];
  const fut = isFuture(r);
  if (fut) {
    const first = r.data[0], last = r.data[r.data.length - 1];
    parts.push(`Under ${SSP_LABELS[r.scenario]}, climate models expect ${name} at your farm to average ${fmt(s.average)}${perText} from ${first.date} to ${last.date}.`);
  } else {
    parts.push(`On average, ${name} at your farm was ${fmt(s.average)}${perText}.`);
  }
  if (s.highest && s.lowest) {
    const evap = /evapo/i.test(r.label);
    const hi = evap ? "highest" : k === "temp" ? "warmest" : "wettest";
    const lo = evap ? "lowest" : k === "temp" ? "coolest" : "driest";
    const hiTxt = `${s.highest.date} (${fmt(s.highest.value)})`, loTxt = `${s.lowest.date} (${fmt(s.lowest.value)})`;
    parts.push(fut
      ? `The ${hi} year is expected to be ${hiTxt} and the ${lo} ${loTxt}.`
      : r.frequency === "monthly"
        ? `The single ${hi} month on record was ${hiTxt} and the ${lo} was ${loTxt}.`
        : `The ${hi} ${per} was ${hiTxt} and the ${lo} was ${loTxt}.`);
  }
  if (s.trend_per_decade != null && r.frequency !== "monthly") parts.push(trendSentence(r, s.trend_per_decade, unit, k));
  if (r.frequency === "monthly") parts.push("Monthly values go up and down with the seasons; choose Yearly to see the long-term trend.");
  return parts.join(" ");
}

function trendSentence(r, t, unit, k) {
  const avg = Math.abs((r.summary || {}).average || 1);
  const small = k === "temp" ? Math.abs(t) < 0.1 : Math.abs(t) < 0.03 * avg;
  const verb = isFuture(r) ? "is expected to" : "has tended to";
  if (small) return `There is little overall change over this period.`;
  const dir = k === "temp" ? (t > 0 ? "get warmer" : "get cooler") : k === "soil" ? (t > 0 ? "get wetter" : "get drier") : /evapo/i.test(r.label) ? (t > 0 ? "increase" : "decrease") : (t > 0 ? "get wetter" : "get drier");
  return `It ${verb} ${dir}, by about ${fmtNum(Math.abs(t))} ${unit} per decade.`;
}

function renderStats(r) {
  const s = r.summary || {};
  const unit = r.unit;
  const tiles = [
    ["Average", fmtNum(s.average), unit],
    ["Highest", s.highest ? fmtNum(s.highest.value) : "–", s.highest ? s.highest.date : ""],
    ["Lowest", s.lowest ? fmtNum(s.lowest.value) : "–", s.lowest ? s.lowest.date : ""],
    ["Change per decade", s.trend_per_decade == null ? "–" : (s.trend_per_decade > 0 ? "+" : "") + fmtNum(s.trend_per_decade), s.trend_per_decade == null ? "needs 3+ years" : unit.replace(/\/(year|month|day)$/, "")],
  ];
  $("stat-grid").innerHTML = "";
  for (const [label, value, note] of tiles) {
    const d = document.createElement("div");
    d.className = "stat";
    d.innerHTML = `<div class="stat-label"></div><div class="stat-value"></div><div class="stat-note"></div>`;
    d.children[0].textContent = label; d.children[1].textContent = value; d.children[2].textContent = note;
    $("stat-grid").appendChild(d);
  }
}

function renderChart(r) {
  if (chart) chart.destroy();
  const labels = r.data.map((d) => d.date);
  const many = r.data.length > 60;
  const datasets = [];
  if (isFuture(r)) {
    datasets.push(
      { label: "Lowest model", data: r.data.map((d) => d.low), borderWidth: 0, pointRadius: 0, fill: false, backgroundColor: "rgba(42,120,214,.15)" },
      { label: "Highest model", data: r.data.map((d) => d.high), borderWidth: 0, pointRadius: 0, fill: "-1", backgroundColor: "rgba(42,120,214,.15)" },
    );
  }
  datasets.push({
    label: isFuture(r) ? "Average of models" : r.label,
    data: r.data.map((d) => d.value),
    borderColor: SERIES_COLOR,
    backgroundColor: SERIES_COLOR,
    borderWidth: 2,
    tension: 0.2,
    pointRadius: many ? 0 : 4,
    pointHoverRadius: 6,
    pointBackgroundColor: r.data.map((d) => (d.complete === false ? "#fcfcfb" : SERIES_COLOR)),
    pointBorderColor: SERIES_COLOR,
    fill: false,
  });
  // Rain/ET totals read best as bars; everything else as a line
  const type = r.unit.startsWith("mm") && r.frequency !== "daily" && !isFuture(r) ? "bar" : "line";
  if (type === "bar") {
    datasets[datasets.length - 1].backgroundColor = r.data.map((d) => (d.complete === false ? "rgba(42,120,214,.35)" : SERIES_COLOR));
  }
  chart = new Chart($("results-chart"), {
    type,
    data: { labels, datasets },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: (item) => !item.dataset.isTrend && (!isFuture(r) || item.datasetIndex === 2),
          callbacks: {
            label: (ctx) => {
              const d = r.data[ctx.dataIndex];
              let s = ` ${fmtNum(ctx.parsed.y)} ${r.unit}`;
              if (isFuture(r) && d.low != null && d.high != null) s += `  (models: ${fmtNum(d.low)} to ${fmtNum(d.high)})`;
              if (d.complete === false) s += "  (incomplete data)";
              return s;
            },
          },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: "#52514e", maxTicksLimit: 12, autoSkip: true } },
        y: {
          beginAtZero: kindOf(r) !== "temp",
          grid: { color: "#ecebe6" },
          border: { display: false },
          ticks: { color: "#52514e" },
          title: { display: true, text: r.unit, color: "#52514e" },
        },
      },
      datasets: { bar: { borderRadius: 4, categoryPercentage: 0.85, barPercentage: 0.9 } },
    },
  });
  if (isFuture(r)) {
    $("results-chart").setAttribute("aria-label", `${r.label} projection chart. Line shows the average of climate models; shaded band shows the range between models.`);
  }
}

function renderTable(r) {
  const t = $("results-table");
  const fut = isFuture(r);
  const band = fut && r.data.some((d) => d.low != null);
  const head = band ? ["Year", `Average (${r.unit})`, "Lowest model", "Highest model"] : [fut ? "Year" : "Date", `${r.label} (${r.unit})`];
  t.innerHTML = "";
  const thead = t.createTHead().insertRow();
  head.forEach((h) => { const th = document.createElement("th"); th.textContent = h; thead.appendChild(th); });
  const tb = t.createTBody();
  for (const d of r.data) {
    const row = tb.insertRow();
    const cells = band ? [d.date, fmtNum(d.value), fmtNum(d.low), fmtNum(d.high)] : [d.date + (d.complete === false ? " (incomplete)" : ""), fmtNum(d.value)];
    cells.forEach((c) => (row.insertCell().textContent = c));
  }
}

function downloadCsv() {
  const r = state.lastResult;
  if (!r) return;
  if (r.kind === "projections") return downloadFutureCsv(r);
  const fut = isFuture(r);
  const rows = [fut ? ["year", "average", "lowest_model", "highest_model"] : ["date", "value", "complete"]];
  r.data.forEach((d) => rows.push(fut ? [d.date, d.value, d.low, d.high] : [d.date, d.value, d.complete]));
  const meta = [`# ${r.label} (${r.unit})`, `# Location: ${state.label}`, `# Source: ${r.source}`];
  const csv = meta.join("\n") + "\n" + rows.map((x) => x.join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `${r.variable}_${fut ? r.scenario : r.frequency}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}



const BLANK = "___";
const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
let seasonalChart;

function esc(x) { return String(x).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function blank(x) { return x == null || x === "" || Number.isNaN(x); }
function txt(x) { return blank(x) ? BLANK : esc(x); }
function val(x, unit = "") { return blank(x) ? BLANK : `${fmtNum(x)}${unit ? (unit === "%" ? "%" : " " + esc(unit)) : ""}`; }
function signed(x, unit = "") { return blank(x) ? BLANK : (x > 0 ? "+" : "") + val(x, unit); }
function pval(p) { return blank(p) ? BLANK : p < 0.001 ? "&lt; 0.001" : Number(p).toFixed(3); }
function pText(p) { return blank(p) ? `p = ${BLANK}` : p < 0.001 ? "p &lt; 0.001" : `p = ${Number(p).toFixed(3)}`; }
function yrs(y) { return Array.isArray(y) && y.length === 2 ? `${y[0]}–${y[1]}` : BLANK; }

function varGroup(variable) {
  if (variable === "precipitation") return "precip";
  if (variable === "evapotranspiration") return "et";
  if (variable === "soil_moisture") return "soil";
  return "temp";
}

function shortUnit(r) {
  const g = varGroup(r.variable);
  return g === "temp" ? "°C" : g === "soil" ? "%" : "mm";
}

function trendUnit(r) {
  return `${shortUnit(r)} per decade`;
}

function renderStatistics(r) {
  loadDescriptiveStats(r);
  loadSeasonalStats(r);
}

function loadDescriptiveStats(result) {
  const s = result.descriptive_stats || {};
  renderMeanChange(result, s.change_in_mean);
  renderTrendTest(result, s.mann_kendall_sens_slope, s.regression_line);
  renderRegressionLine(chart, s.regression_line);
}

function renderMeanChange(result, m) {
  const unit = result.unit;
  const name = result.label.toLowerCase();
  $("mean-change").innerHTML = `
    <h4>Change in mean ${esc(name)}</h4>
    <p>The average ${esc(name)} was <strong>${val(m?.period_a?.mean, unit)}</strong> in ${yrs(m?.period_a?.years)}
    and <strong>${val(m?.period_b?.mean, unit)}</strong> in ${yrs(m?.period_b?.years)},
    a change of <strong>${signed(m?.change, unit)}</strong> (${signed(m?.percent_change, "%")}).</p>`;
}

function renderTrendTest(result, t, reg) {
  const fut = isFuture(result);
  let verdict = `Trend: ${BLANK}`;
  if (t && typeof t.significant === "boolean") {
    const dir = t.direction || (t.sens_slope_per_decade > 0 ? "increasing" : "decreasing");
    verdict = t.significant
      ? `✅ ${fut ? "Climate models show" : "There is"} a <strong>statistically significant ${esc(dir)}</strong> trend.`
      : `➖ <strong>No statistically significant trend.</strong> The ups and downs could be normal year-to-year variation.`;
  }
  const rows = [
    ["Sen's slope", signed(t?.sens_slope_per_decade, trendUnit(result))],
    ["Mann-Kendall p-value", pval(t?.p)],
    ["Kendall's tau", val(t?.tau)],
    ["Regression slope", signed(reg?.slope_per_decade, trendUnit(result))],
    ["Regression R²", val(reg?.r2)],
  ];
  $("trend-test").innerHTML = `
    <h4>Is the trend real? (Mann-Kendall test + Sen's slope)</h4>
    <p class="verdict">${verdict}</p>
    <div class="stats-table"><table><tbody>
      ${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}
    </tbody></table></div>
    <p class="hint">A trend is statistically significant when p &lt; 0.05. The dashed line on the chart is the regression line.</p>`;
}

function renderRegressionLine(chartObj, reg) {
  if (!chartObj || !reg || !Array.isArray(reg.line) || !reg.line.length) return;
  const labels = chartObj.data.labels;
  const len = String(labels[0]).length;
  const byDate = new Map(reg.line.map((p) => [String(p.date).slice(0, len), p.value]));
  chartObj.data.datasets.push({
    type: "line",
    label: "Regression line",
    data: labels.map((l) => (byDate.has(l) ? byDate.get(l) : null)),
    borderColor: "#52514e",
    borderDash: [6, 4],
    borderWidth: 2,
    pointRadius: 0,
    pointHoverRadius: 0,
    fill: false,
    spanGaps: true,
    isTrend: true,
    order: -1,
  });
  chartObj.update();
}

function loadSeasonalStats(result) {
  const section = $("seasonal-stats");
  if (isFuture(result) && !(Array.isArray(result.monthly) && result.monthly.length)) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  const s = result.seasonal_stats || {};
  const g = varGroup(result.variable);
  const unit = shortUnit(result);
  renderSeasonalChart(result, s.monthly_means);
  renderWetDryMonths(g === "precip" ? s.wettest_driest_months : null, unit, g === "precip");
  renderHotCoolMonths(g === "temp" ? s.hottest_coolest_months : null, unit, g === "temp");
  renderExtremes(g, g === "soil" ? s.soil_moisture_extremes : g === "et" ? s.evapotranspiration_extremes : null, unit);
  renderAnomalies(result, s.anomaly_analysis);
}

function renderSeasonalChart(result, means) {
  if (seasonalChart) seasonalChart.destroy();
  const byMonth = new Map((means || []).map((m) => [String(m.month).slice(0, 3), m.mean]));
  const g = varGroup(result.variable);
  const bars = g === "precip" || g === "et";
  seasonalChart = new Chart($("seasonal-chart"), {
    type: bars ? "bar" : "line",
    data: {
      labels: MONTH_SHORT,
      datasets: [{
        label: `Average ${result.label.toLowerCase()}`,
        data: MONTH_SHORT.map((m) => (byMonth.has(m) ? byMonth.get(m) : null)),
        borderColor: SERIES_COLOR,
        backgroundColor: SERIES_COLOR,
        borderWidth: 2,
        borderRadius: 4,
        pointRadius: 4,
        tension: 0.3,
      }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        title: { display: true, text: "Average for each calendar month (all years)", color: "#52514e", font: { weight: "normal" } },
        tooltip: { callbacks: { label: (ctx) => ` ${fmtNum(ctx.parsed.y)} ${shortUnit(result)}` } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: "#52514e" } },
        y: { beginAtZero: bars, grid: { color: "#ecebe6" }, border: { display: false }, ticks: { color: "#52514e" },
             title: { display: true, text: shortUnit(result) === "mm" ? "mm per month" : shortUnit(result), color: "#52514e" } },
      },
    },
  });
}

function renderWetDryMonths(stats, unit, show) {
  $("wet-dry-months").innerHTML = !show ? "" : `
    <h4>Wettest and driest months</h4>
    <p>Across all years, <strong>${txt(stats?.wettest?.month)}</strong> is usually the wettest month
    (average ${val(stats?.wettest?.mean, unit)}) and <strong>${txt(stats?.driest?.month)}</strong> is usually the driest
    (average ${val(stats?.driest?.mean, unit)}).</p>`;
}

function renderHotCoolMonths(stats, unit, show) {
  $("hot-cool-months").innerHTML = !show ? "" : `
    <h4>Hottest and coolest months</h4>
    <p>Across all years, <strong>${txt(stats?.hottest?.month)}</strong> is usually the hottest month
    (average ${val(stats?.hottest?.mean, unit)}) and <strong>${txt(stats?.coolest?.month)}</strong> is usually the coolest
    (average ${val(stats?.coolest?.mean, unit)}).</p>`;
}

function renderExtremes(group, stats, unit) {
  if (group !== "soil" && group !== "et") {
    $("extremes").innerHTML = "";
    return;
  }
  const title = group === "soil" ? "Soil moisture extremes" : "Evapotranspiration extremes";
  const hi = group === "soil" ? "wettest" : "highest";
  const lo = group === "soil" ? "driest" : "lowest";
  const what = group === "soil" ? "The soil is usually" : "Evapotranspiration is usually";
  $("extremes").innerHTML = `
    <h4>${title}</h4>
    <p>Across all years, ${what} ${hi} in <strong>${txt(stats?.highest?.month)}</strong>
    (average ${val(stats?.highest?.mean, unit)}) and ${lo} in <strong>${txt(stats?.lowest?.month)}</strong>
    (average ${val(stats?.lowest?.mean, unit)}).</p>`;
}

function renderAnomalies(result, a) {
  const per = periodWord(result);
  const basis = result.frequency === "monthly" ? "the normal for that calendar month" : "the long-term average";
  const rows = Array.isArray(a?.unusual) && a.unusual.length ? a.unusual : [{}, {}, {}];
  $("anomalies").innerHTML = `
    <h4>Anomaly analysis</h4>
    <p><strong>${txt(a?.count_beyond_2sd)}</strong> ${per}s were unusually high or low
    (more than 2 standard deviations from ${basis}).</p>
    <div class="stats-table"><table>
      <thead><tr><th>${capitalize(per)}</th><th>Value</th><th>Difference from normal</th><th>z-score</th></tr></thead>
      <tbody>${rows.map((x) => `<tr><td>${txt(x.date)}</td><td>${val(x.value, result.unit)}</td><td>${signed(x.anomaly, result.unit)}</td><td>${signed(x.z)}</td></tr>`).join("")}</tbody>
    </table></div>`;
}

const SCENARIOS = [
  { key: "ssp126", name: "SSP1-2.6", case: "Best case", desc: "low emissions", color: "#2a78d6" },
  { key: "ssp585", name: "SSP5-8.5", case: "Worst case", desc: "very high emissions", color: "#eb6834" },
];
const BASELINE_COLOR = "#7a7974";
const BASELINE_LABEL = "1995–2014";
const FUTURE_PERIODS = ["2026-2040", "2041-2060", "2061-2080", "2081-2100"];
const FUTURE_VARS = {
  precipitation: { label: "Precipitation", annual: "pr_annual", monthly: "pr_monthly", unit: "mm/year", base: "mm", group: "precip", percent: true, noun: "yearly rainfall" },
  min_temp: { label: "Minimum Temperature", annual: "tasmin_annual", monthly: "tasmin_monthly", unit: "°C", base: "°C", group: "temp", noun: "the average daily minimum temperature" },
  max_temp: { label: "Maximum Temperature", annual: "tasmax_annual", monthly: "tasmax_monthly", unit: "°C", base: "°C", group: "temp", noun: "the average daily maximum temperature" },
  rx1day: { label: "Wettest day of the year", annual: "rx1day", monthly: null, unit: "mm/day", base: "mm", group: "precip", percent: true, noun: "rain on the wettest day of the year" },
  hotdays35: { label: "Days above 35 °C", annual: "hotdays35", monthly: null, unit: "days/year", base: "days", group: "count", noun: "the number of days above 35 °C" },
};

function periodLabel(p) { return String(p).replace("-", "–"); }

function setTiles(tiles) {
  $("stat-grid").innerHTML = "";
  for (const [label, value, note] of tiles) {
    const d = document.createElement("div");
    d.className = "stat";
    d.innerHTML = `<div class="stat-label"></div><div class="stat-value"></div><div class="stat-note"></div>`;
    d.children[0].textContent = label;
    d.children[1].textContent = value;
    d.children[2].textContent = note;
    $("stat-grid").appendChild(d);
  }
}

function projValue(r, scenario, period, field) {
  const p = r.proj.projections?.[`${scenario}_${period}`];
  return p ? p[field] : null;
}

function changeText(r, value) {
  const v = FUTURE_VARS[r.variable];
  const base = r.proj.baseline?.[v.annual];
  if (blank(value) || blank(base)) return BLANK;
  const d = value - base;
  let t = `${d > 0 ? "+" : ""}${fmtNum(d)} ${v.unit}`;
  if (v.percent && base) t += ` (${d > 0 ? "+" : ""}${fmtNum((d / base) * 100)}%)`;
  return t;
}

function showFutureResult(r) {
  state.lastResult = r;
  $("progress").classList.add("hidden");
  $("results-error").textContent = "";
  const v = FUTURE_VARS[r.variable];
  const base = r.proj.baseline?.[v.annual];
  if (blank(base)) return showError("No projection data was found for this place.", true);
  $("results-body").classList.remove("hidden");
  const best = projValue(r, "ssp126", r.period, v.annual);
  const worst = projValue(r, "ssp585", r.period, v.annual);
  const P = periodLabel(r.period);
  $("insight").textContent =
    `In ${P}, ${v.noun} at your farm is projected to be ${fmtNum(best)} ${v.unit} in the best case (${changeText(r, best)} compared with ${BASELINE_LABEL}) ` +
    `and ${fmtNum(worst)} ${v.unit} in the worst case (${changeText(r, worst)}). The ${BASELINE_LABEL} average was ${fmtNum(base)} ${v.unit}.`;
  setTiles([
    [`${BASELINE_LABEL} average`, fmtNum(base), v.unit],
    [`Best case, ${P}`, blank(best) ? "–" : fmtNum(best), `${v.unit} · ${changeText(r, best)}`],
    [`Worst case, ${P}`, blank(worst) ? "–" : fmtNum(worst), `${v.unit} · ${changeText(r, worst)}`],
    ["Worst minus best", blank(best) || blank(worst) ? "–" : (worst - best > 0 ? "+" : "") + fmtNum(worst - best), v.unit],
  ]);
  renderProjectionChart(r);
  renderProjectionTable(r);
  renderProjectionChanges(r);
  renderProjectionSeasonal(r);
  $("trend-test").innerHTML = `<h4>Is the trend real? (Mann-Kendall test + Sen's slope)</h4><p class="hint">Loading statistical tests… this can take up to a minute when the yearly values come from NASA.</p>`;
  $("scenario-compare").classList.remove("hidden");
  $("scenario-compare-result").innerHTML = `<p class="hint">Loading…</p>`;
  const where = r.proj.mode === "nasa"
    ? `Fetched directly from NASA NCCS for the grid cell at your farm's centre (${r.proj.lat}, ${r.proj.lon}); each period is estimated from ${r.proj.sample_years} sample years.`
    : r.proj.mode === "area"
    ? `Averaged over ${r.proj.baseline.n_cells} grid cells inside your boundary.`
    : r.proj.mode === "centroid"
      ? "Your field is smaller than one grid cell, so values are for the cell at its centre."
      : "Values for the grid cell at your pin.";
  $("results-source").textContent = `Source: NASA NEX-GDDP-CMIP6 downscaled climate projections (about 25 km), GFDL-ESM4 model. Period averages. ${where}`;
}

function renderProjectionChart(r) {
  if (chart) chart.destroy();
  const v = FUTURE_VARS[r.variable];
  const base = r.proj.baseline[v.annual];
  const labels = [[BASELINE_LABEL, "(baseline)"], ...FUTURE_PERIODS.map(periodLabel)];
  const sel = FUTURE_PERIODS.indexOf(r.period) + 1;
  const datasets = SCENARIOS.map((sc) => ({
    label: `${sc.case} (${sc.name})`,
    data: [base, ...FUTURE_PERIODS.map((p) => projValue(r, sc.key, p, v.annual))],
    borderColor: sc.color,
    backgroundColor: sc.color,
    borderWidth: 2,
    pointRadius: labels.map((_, i) => (i === sel ? 7 : 4)),
    pointHoverRadius: 8,
    pointBorderColor: "#fcfcfb",
    pointBorderWidth: 2,
    tension: 0,
    spanGaps: true,
  }));
  datasets.push({
    label: `${BASELINE_LABEL} average`,
    data: labels.map(() => base),
    borderColor: BASELINE_COLOR,
    borderDash: [5, 5],
    borderWidth: 1.5,
    pointRadius: 0,
    pointHoverRadius: 0,
    isTrend: true,
  });
  chart = new Chart($("results-chart"), {
    type: "line",
    data: { labels, datasets },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: true, position: "top", align: "start", labels: { color: "#0b0b0b", boxWidth: 14, boxHeight: 3 } },
        tooltip: {
          filter: (item) => !item.dataset.isTrend,
          callbacks: {
            title: (items) => [].concat(items[0].label).join(" "),
            label: (ctx) => ` ${ctx.dataset.label}: ${fmtNum(ctx.parsed.y)} ${v.unit}` + (ctx.dataIndex ? `  (${changeText(r, ctx.parsed.y)})` : ""),
          },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: "#52514e" } },
        y: {
          beginAtZero: v.group !== "temp",
          grid: { color: "#ecebe6" },
          border: { display: false },
          ticks: { color: "#52514e" },
          title: { display: true, text: v.unit, color: "#52514e" },
        },
      },
    },
  });
  $("results-chart").setAttribute("aria-label", `${v.label}: ${BASELINE_LABEL} baseline and each future period, best case and worst case.`);
}

function renderProjectionTable(r) {
  const v = FUTURE_VARS[r.variable];
  const t = $("results-table");
  t.innerHTML = "";
  const head = t.createTHead().insertRow();
  ["Period", ...SCENARIOS.map((sc) => `${sc.case}, ${sc.name} (${v.unit})`)].forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    head.appendChild(th);
  });
  const body = t.createTBody();
  const base = r.proj.baseline[v.annual];
  const row0 = body.insertRow();
  [`${BASELINE_LABEL} (baseline)`, fmtNum(base), fmtNum(base)].forEach((c) => (row0.insertCell().textContent = c));
  for (const p of FUTURE_PERIODS) {
    const row = body.insertRow();
    [periodLabel(p), ...SCENARIOS.map((sc) => fmtNum(projValue(r, sc.key, p, v.annual)))].forEach((c) => (row.insertCell().textContent = c));
  }
}

function downloadFutureCsv(r) {
  const v = FUTURE_VARS[r.variable];
  const base = r.proj.baseline[v.annual];
  const rows = [["period", "best_case_ssp126", "worst_case_ssp585"], [`${BASELINE_LABEL} baseline`, base, base],
    ...FUTURE_PERIODS.map((p) => [p, ...SCENARIOS.map((sc) => projValue(r, sc.key, p, v.annual) ?? "")])];
  const meta = [`# ${v.label} (${v.unit})`, `# Location: ${state.label}`, "# Source: NASA NEX-GDDP-CMIP6, GFDL-ESM4, period averages"];
  const csv = meta.join("\n") + "\n" + rows.map((x) => x.join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `${r.variable}_best_vs_worst_case.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function swatchHead() {
  return `<thead><tr><th></th>${SCENARIOS.map((x) => `<th><span class="swatch" style="background:${x.color}"></span>${x.case} <span class="muted">(${x.name})</span></th>`).join("")}</tr></thead>`;
}

function renderProjectionChanges(r) {
  const v = FUTURE_VARS[r.variable];
  $("mean-change").innerHTML = `
    <h4>Change from ${BASELINE_LABEL}</h4>
    <div class="stats-table"><table>${swatchHead()}
      <tbody>${FUTURE_PERIODS.map((p) => `<tr${p === r.period ? ' class="selected-row"' : ""}><td>${periodLabel(p)}</td>${SCENARIOS.map((sc) => {
        const x = projValue(r, sc.key, p, v.annual);
        return `<td>${val(x, v.unit)} <span class="muted">${esc(changeText(r, x))}</span></td>`;
      }).join("")}</tr>`).join("")}</tbody>
    </table></div>`;
}

function trendVerdict(t) {
  if (!t || typeof t.significant !== "boolean") return BLANK;
  return t.significant ? `✅ significant ${esc(t.direction)}` : "➖ no significant trend";
}

function renderFutureTests(r) {
  const t = r.tests || {};
  const v = FUTURE_VARS[r.variable];
  const P = periodLabel(r.period);
  if (!t.available) {
    const why = esc(t.reason || "Not available.");
    $("trend-test").innerHTML = `<h4>Is the trend real? (Mann-Kendall test + Sen's slope)</h4>
      <p class="hint">Year-by-year tests for ${P} aren't available: ${why}</p>`;
    $("scenario-compare-result").innerHTML = `<p class="hint">The Mann-Whitney U test for ${P} isn't available: ${why}</p>`;
    return;
  }
  const u = `${v.base} per decade`;
  const cell = (fn) => SCENARIOS.map((sc) => `<td>${fn(t.trends?.[sc.key] || {})}</td>`).join("");
  $("trend-test").innerHTML = `
    <h4>Is the trend real within ${P}? (Mann-Kendall test + Sen's slope)</h4>
    <div class="stats-table"><table>${swatchHead()}
      <tbody>
        <tr><td>Trend</td>${cell((s) => trendVerdict(s.mann_kendall_sens_slope))}</tr>
        <tr><td>Sen's slope</td>${cell((s) => signed(s.mann_kendall_sens_slope?.sens_slope_per_decade, u))}</tr>
        <tr><td>Mann-Kendall p-value</td>${cell((s) => pval(s.mann_kendall_sens_slope?.p))}</tr>
        <tr><td>Kendall's tau</td>${cell((s) => val(s.mann_kendall_sens_slope?.tau))}</tr>
        <tr><td>Regression slope</td>${cell((s) => signed(s.regression_line?.slope_per_decade, u))}</tr>
        <tr><td>Regression R²</td>${cell((s) => val(s.regression_line?.r2))}</tr>
      </tbody>
    </table></div>
    <p class="hint">Uses one value per year within ${P}${t.source === "nasa" ? ", fetched from NASA NCCS" : ""}. A trend is statistically significant when p &lt; 0.05.</p>`;
  const c = t.comparison;
  let verdict = BLANK;
  if (c && typeof c.significant === "boolean") {
    verdict = c.significant ? "✅ a statistically significant difference" : "➖ no statistically significant difference";
  }
  $("scenario-compare-result").innerHTML = `
    <p class="insight">Over ${P}, ${esc(v.noun)} is on average <strong>${val(c?.scenario_b?.mean, v.unit)}</strong> in the worst case (SSP5-8.5),
    compared with <strong>${val(c?.scenario_a?.mean, v.unit)}</strong> in the best case (SSP1-2.6):
    a difference of <strong>${signed(c?.difference, v.unit)}</strong> (${signed(c?.percent_change, "%")}).
    Mann-Whitney U = ${val(c?.u)}, ${pText(c?.p)}, which means <strong>${verdict}</strong>.</p>
    <p class="hint">Uses ${txt(c?.scenario_a?.n)} yearly values from each scenario. The point at the farm's centre is used for these tests.</p>`;
}

function renderProjectionSeasonal(r) {
  const v = FUTURE_VARS[r.variable];
  const section = $("seasonal-stats");
  ["wet-dry-months", "hot-cool-months", "extremes", "anomalies"].forEach((id) => ($(id).innerHTML = ""));
  if (!v.monthly) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  if (seasonalChart) seasonalChart.destroy();
  const P = periodLabel(r.period);
  const series = [
    { name: `${BASELINE_LABEL} baseline`, color: BASELINE_COLOR, values: r.proj.baseline[v.monthly], dash: true },
    ...SCENARIOS.map((sc) => ({ name: `${sc.case} (${sc.name}), ${P}`, color: sc.color, values: projValue(r, sc.key, r.period, v.monthly) })),
  ].filter((x) => Array.isArray(x.values) && x.values.length === 12);
  const bars = v.group === "precip";
  seasonalChart = new Chart($("seasonal-chart"), {
    type: bars ? "bar" : "line",
    data: {
      labels: MONTH_SHORT,
      datasets: series.map((x) => ({
        label: x.name,
        data: x.values,
        borderColor: x.color,
        backgroundColor: x.color,
        borderWidth: bars ? 0 : 2,
        borderDash: !bars && x.dash ? [5, 5] : [],
        borderRadius: 4,
        pointRadius: 3,
        tension: 0.3,
      })),
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: "top", align: "start", labels: { color: "#0b0b0b", boxWidth: 14 } },
        title: { display: true, text: "Average for each calendar month", color: "#52514e", font: { weight: "normal" } },
        tooltip: { callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtNum(ctx.parsed.y)} ${v.base}` } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: "#52514e" } },
        y: { beginAtZero: bars, grid: { color: "#ecebe6" }, border: { display: false }, ticks: { color: "#52514e" },
             title: { display: true, text: bars ? "mm per month" : v.base, color: "#52514e" } },
      },
      datasets: { bar: { categoryPercentage: 0.85, barPercentage: 0.9 } },
    },
  });
  const [hi, lo] = v.group === "precip" ? ["wettest", "driest"] : ["hottest", "coolest"];
  const lines = series.map((x) => {
    const iHi = x.values.indexOf(Math.max(...x.values));
    const iLo = x.values.indexOf(Math.min(...x.values));
    return `<p><span class="swatch" style="background:${x.color}"></span><strong>${esc(x.name)}:</strong>
      ${hi} month <strong>${MONTHS[iHi]}</strong> (${val(x.values[iHi], v.base)}), ${lo} month <strong>${MONTHS[iLo]}</strong> (${val(x.values[iLo], v.base)}).</p>`;
  }).join("");
  $(v.group === "precip" ? "wet-dry-months" : "hot-cool-months").innerHTML =
    `<h4>${capitalize(hi)} and ${lo} months</h4><p class="hint">Calendar months, averaged over each period.</p>${lines}`;
}

/* ======================= Helpers ======================= */

function round(x, n) { const f = 10 ** n; return Math.round(x * f) / f; }
function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
function fmtMonth(ym) { return `${MONTHS[+ym.slice(5) - 1].slice(0, 3)} ${ym.slice(0, 4)}`; }
function fmtNum(v) {
  if (v == null || Number.isNaN(v)) return "–";
  const a = Math.abs(v);
  return v.toLocaleString(undefined, { maximumFractionDigits: a >= 100 ? 0 : a >= 10 ? 1 : 2 });
}
function boundsCenter(pts) {
  const lats = pts.map((p) => p.lat()), lngs = pts.map((p) => p.lng());
  return { lat: (Math.min(...lats) + Math.max(...lats)) / 2, lng: (Math.min(...lngs) + Math.max(...lngs)) / 2 };
}

/* ======================= Wire up ======================= */

document.addEventListener("DOMContentLoaded", () => {
  // Wake the backend now (free hosting sleeps when idle), so it is ready by the time
  // the farmer has picked a location and options.
  fetch(API + "/api/health").catch(() => {});

  initHistoricalForm();
  initFutureForm();

  $("btn-back").onclick = goBack;
  $("btn-home").onclick = goHome;
  document.querySelectorAll("[data-modal]").forEach((b) => (b.onclick = () => openModal(b.dataset.modal)));
  document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeModals));
  document.querySelectorAll(".modal").forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) closeModals(); }));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModals(); });

  $("search-form").onsubmit = (e) => { e.preventDefault(); if (map) searchLocation($("location-search").value); };
  $("mode-pin").onclick = () => setMode("pin");
  $("mode-draw").onclick = () => setMode("draw");
  $("btn-locate").onclick = locateUser;
  $("btn-undo").onclick = () => { const p = farmPolygon && farmPolygon.getPath(); if (p && p.getLength()) p.pop(); };
  $("btn-clear").onclick = clearSelection;
  $("btn-confirm").onclick = () => {
    if (!state.geometry) return;
    $("location-summary").textContent = `Selected: ${state.label}`;
    show("step-analysis");
  };

  document.querySelectorAll("[data-analysis]").forEach((card) => (card.onclick = () => {
    state.analysis = card.dataset.analysis;
    show(state.analysis === "historical" ? "step-historical" : "step-future");
  }));

  $("historical-form").onsubmit = (e) => {
    e.preventDefault();
    const p = readHistoricalForm();
    $("hist-error").textContent = p.error || "";
    if (!p.error) runHistorical(p);
  };
  $("future-form").onsubmit = (e) => {
    e.preventDefault();
    const p = readFutureForm();
    $("fut-error").textContent = p.error || "";
    if (!p.error) runFuture(p);
  };

  $("btn-download").onclick = downloadCsv;
  $("btn-change").onclick = goBack;
  $("btn-other").onclick = () => {
    stopPolling();
    const other = state.lastRequest.kind === "historical" ? "step-future" : "step-historical";
    state.history = ["step-map", "step-analysis", other];
    show(other, false);
  };
});
