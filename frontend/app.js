const state = {
  historyStack: [1],
  locationType: null, // "point" or "polygon"
  geometry: null,     // GeoJSON Polygon (auto-buffered if user clicked a point)
  locationLabel: "",
  analysisType: null,
};

let map, draw, pointMarker = null, geocoder, chartInstance = null;

function initMap() {
  geocoder = new google.maps.Geocoder();
  map = new google.maps.Map(document.getElementById("map"), {
    center: { lat: 1.3521, lng: 103.8198 },
    zoom: 11,
    mapTypeId: "hybrid",
  });

  // Enable Google Places Autocomplete on the search input
  const input = document.getElementById("location-search");
  const autocomplete = new google.maps.places.Autocomplete(input);
  autocomplete.addListener("place_changed", () => {
    const place = autocomplete.getPlace();
    if (place.geometry && place.geometry.location) {
      setPointLocation(place.geometry.location.lat(), place.geometry.location.lng(), place.formatted_address);
    }
  });

  // Direct click on map selects a point
  map.addListener("click", (e) => {
    if (draw && draw.getMode() === "polygon") return;
    setPointLocation(e.latLng.lat(), e.latLng.lng());
  });

  // Initialize Terra Draw for polygon selection
  map.addListener("projection_changed", () => {
    if (draw) return;
    draw = new terraDraw.TerraDraw({
      adapter: new terraDrawGoogleMapsAdapter.TerraDrawGoogleMapsAdapter({
        lib: google.maps,
        map,
        coordinatePrecision: 9,
      }),
      modes: [new terraDraw.TerraDrawPolygonMode()],
    });
    draw.start();

    draw.on("finish", (id) => {
      const feature = draw.getSnapshot().find((f) => f.id === id);
      if (pointMarker) pointMarker.setMap(null);
      state.locationType = "polygon";
      state.geometry = feature.geometry;
      state.locationLabel = "Custom Drawn Field Polygon";
      document.getElementById("btn-step1-next").disabled = false;
      draw.setMode("static");
    });
  });
}

// Converts a clicked/searched point into a small ~1km bounding polygon for NASA Harmony
function setPointLocation(lat, lng, label = null) {
  if (draw) draw.clear();
  if (pointMarker) pointMarker.setMap(null);

  pointMarker = new google.maps.Marker({ position: { lat, lng }, map });
  map.panTo({ lat, lng });

  const d = 0.005; // ~550m buffer so single-point queries always intersect a grid pixel
  state.locationType = "point";
  state.geometry = {
    type: "Polygon",
    coordinates: [[
      [lng - d, lat - d],
      [lng + d, lat - d],
      [lng + d, lat + d],
      [lng - d, lat + d],
      [lng - d, lat - d],
    ]],
  };
  state.locationLabel = label || `Coordinates: ${lat.toFixed(4)}, ${lng.toFixed(4)}`;
  document.getElementById("btn-step1-next").disabled = false;
}

function searchLocation() {
  const query = document.getElementById("location-search").value.trim();
  const coordMatch = query.match(/^(-?\d+(\.\d+)?),\s*(-?\d+(\.\d+)?)$/);
  if (coordMatch) {
    setPointLocation(parseFloat(coordMatch[1]), parseFloat(coordMatch[3]));
    return;
  }
  geocoder.geocode({ address: query }, (results, status) => {
    if (status === "OK" && results[0]) {
      const loc = results[0].geometry.location;
      setPointLocation(loc.lat(), loc.lng(), results[0].formatted_address);
    } else {
      alert("Location not found. Try a city name or 'lat, lng' coordinates.");
    }
  });
}

function startPolygonDraw() {
  if (pointMarker) pointMarker.setMap(null);
  draw.clear();
  document.getElementById("btn-step1-next").disabled = true;
  draw.setMode("polygon");
}

// --- Navigation & Wizard State ---
function renderView(viewId) {
  document.querySelectorAll(".step-view").forEach((el) => el.classList.remove("active"));
  document.getElementById(viewId).classList.add("active");

  // Hide Back button ONLY on the map (step-1)
  const backBtn = document.getElementById("btn-back");
  if (viewId === "step-1") {
    backBtn.classList.add("hidden");
  } else {
    backBtn.classList.remove("hidden");
  }
}

function goToStep(stepNum) {
  if (stepNum === 2) {
    document.getElementById("selected-location-summary").textContent = `Selected: ${state.locationLabel}`;
    state.historyStack.push("step-2");
    renderView("step-2");
  }
}

function selectAnalysis(type) {
  state.analysisType = type;
  const viewId = type === "historical" ? "step-3-historical" : "step-3-future";
  state.historyStack.push(viewId);
  renderView(viewId);
}

function goBack() {
  if (state.historyStack.length > 1) {
    state.historyStack.pop();
    const prev = state.historyStack[state.historyStack.length - 1];
    renderView(prev === 1 ? "step-1" : prev);
  }
}

function goHome() {
  state.historyStack = ["step-1"];
  renderView("step-1");
}

function toggleCustomYears() {
  const val = document.getElementById("fut-period").value;
  document.getElementById("custom-years-row").classList.toggle("hidden", val !== "custom");
}

function openModal(id) { document.getElementById(id).classList.remove("hidden"); }
function closeModals() { document.querySelectorAll(".modal").forEach((m) => m.classList.add("hidden")); }

// --- Submit to FastAPI Backend ---
async function submitAnalysis(mode) {
  state.historyStack.push("step-4");
  renderView("step-4");
  const statusEl = document.getElementById("status-banner");
  statusEl.textContent = "Submitting request to NASA Harmony...";

  let payload = { mode, geometry: state.geometry };

  if (mode === "historical") {
    payload.variable = document.getElementById("hist-variable").value;
    payload.start_date = document.getElementById("hist-start").value + "-01";
    payload.end_date = document.getElementById("hist-end").value + "-28";
    payload.frequency = document.getElementById("hist-frequency").value;
  } else {
    const period = document.getElementById("fut-period").value;
    const startYear = period === "custom" ? parseInt(document.getElementById("fut-start-year").value) : 2026;
    const endYear = period === "custom" ? parseInt(document.getElementById("fut-end-year").value) : 2026 + parseInt(period);
    payload.variable = document.getElementById("fut-variable").value;
    payload.start_date = `${startYear}-01-01`;
    payload.end_date = `${endYear}-12-31`;
    payload.ssp = document.getElementById("fut-ssp").value;
  }

  const res = await fetch("/api/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const { job_id } = await res.json();

  const poll = setInterval(async () => {
    const sRes = await fetch(`/api/status/${job_id}?mode=${mode}&variable=${payload.variable}&frequency=${payload.frequency || "yearly"}`);
    const result = await sRes.json();
    statusEl.textContent = `NASA Harmony Status: ${result.status} (${result.progress}%)`;

    if (result.status === "successful") {
      clearInterval(poll);
      statusEl.textContent = `Showing results for ${state.locationLabel}`;
      renderChart(result.data, payload.variable);
    } else if (result.status === "failed") {
      clearInterval(poll);
      statusEl.textContent = "Job failed. Try a smaller date range or different variable.";
    }
  }, 4000);
}

function renderChart(data, label) {
  const ctx = document.getElementById("results-chart").getContext("2d");
  if (chartInstance) chartInstance.destroy();
  chartInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels: data.map((d) => d.date),
      datasets: [{ label, data: data.map((d) => d.value), borderColor: "#2e7d32", fill: false }],
    },
  });
}