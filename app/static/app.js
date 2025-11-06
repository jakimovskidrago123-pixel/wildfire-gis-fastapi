// ===================== DOM =====================
const daysEl     = document.getElementById("days");
const sourceEl   = document.getElementById("source");
const minConfEl  = document.getElementById("min_conf");
const statusEl   = document.getElementById("status");

const btnLoad       = document.getElementById("btnLoad");
const btnAnalyze    = document.getElementById("btnAnalyze");
const btnCountry    = document.getElementById("btnCountry");
const btnSelectArea = document.getElementById("btnSelectArea");
const btnMeasure    = document.getElementById("btnMeasure");
const btnClear      = document.getElementById("btnClear");
const chkHull       = document.getElementById("chkHull");   // <- NEW

// ================= Base layers =================
const streets = L.tileLayer(
  "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  { maxZoom: 19, attribution: "© OpenStreetMap contributors" }
);

const satellite = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    maxZoom: 19,
    attribution:
      "Tiles © Esri — Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community",
  }
);

// ===================== Map =====================
const map = L.map("map", {
  center: [41.6, 21.7],
  zoom: 6,
  layers: [streets],
});

// ============== Fire points layer ==============
const fireLayer = L.geoJSON([], {
  pointToLayer: (feature, latlng) => {
    const conf = feature.properties.confidence ?? "n";
    let color = "#ff9933"; // nominal default
    if (typeof conf === "string") {
      const c = conf.toLowerCase();
      if (c === "l") color = "#f4f466";      // low
      else if (c === "h") color = "#ff0000"; // high
    } else if (typeof conf === "number") {
      if (conf <= 40) color = "#f4f466";
      else if (conf > 70) color = "#ff0000";
    }
    return L.circleMarker(latlng, {
      radius: 6,
      fillColor: color,
      color: "#333",
      weight: 1,
      opacity: 1,
      fillOpacity: 0.85,
    }).bindPopup(`
      <b>FRP:</b> ${feature.properties.frp ?? "N/A"}<br>
      <b>Confidence:</b> ${feature.properties.confidence}<br>
      <b>Acquired:</b> ${feature.properties.acq_date ?? ""} ${feature.properties.acq_time ?? ""}
    `);
  },
}).addTo(map);

// ============== Convex hull layer (LOCKED) ==============
const hullLayer = L.geoJSON(null, {
  style: {
    color: "#ff9f1a",
    weight: 3,
    fillColor: "#ffd37a",
    fillOpacity: 0.25
  }
}).addTo(map);

// ============ Layers control (toggle) ============
L.control.layers(
  { "Streets (OSM)": streets, "Satellite (Esri)": satellite },
  { Fires: fireLayer, "Convex hull": hullLayer },
  { collapsed: true }
).addTo(map);

// ============== Confidence Legend ==============
const legend = L.control({ position: "bottomright" });
legend.onAdd = function () {
  const div = L.DomUtil.create("div", "map-legend");
  div.innerHTML = `
    <h4>VIIRS Confidence</h4>
    <div class="legend-item"><span class="legend-swatch" style="background:#f4f466"></span><span>Low</span></div>
    <div class="legend-item"><span class="legend-swatch" style="background:#ff9933"></span><span>Nominal</span></div>
    <div class="legend-item"><span class="legend-swatch" style="background:#ff0000"></span><span>High</span></div>
  `;
  L.DomEvent.disableClickPropagation(div);
  return div;
};
legend.addTo(map);

// ===================== Utils ====================
function setStatus(t) { statusEl.textContent = t; }
async function fetchJSON(url) {
  console.log("[DEBUG] Fetch:", url);
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
function getBboxFromMap() {
  const b = map.getBounds();
  return [
    b.getWest().toFixed(4),
    b.getSouth().toFixed(4),
    b.getEast().toFixed(4),
    b.getNorth().toFixed(4),
  ].join(",");
}
function bboxFromBounds(bounds) {
  return [
    bounds.getWest().toFixed(4),
    bounds.getSouth().toFixed(4),
    bounds.getEast().toFixed(4),
    bounds.getNorth().toFixed(4),
  ].join(",");
}

// ========== Map interaction lock helpers ==========
let interactionsState = {};
function disableMapInteractions() {
  interactionsState = {
    dragging: map.dragging.enabled(),
    scrollWheelZoom: map.scrollWheelZoom.enabled(),
    doubleClickZoom: map.doubleClickZoom.enabled(),
    boxZoom: map.boxZoom.enabled(),
    keyboard: map.keyboard?.enabled?.() ?? true,
    touchZoom: map.touchZoom?.enabled?.() ?? true,
  };
  map.dragging.disable();
  map.scrollWheelZoom.disable();
  map.doubleClickZoom.disable();
  map.boxZoom.disable();
  map.keyboard && map.keyboard.disable();
  map.touchZoom && map.touchZoom.disable();
}
function enableMapInteractions() {
  if (interactionsState.dragging) map.dragging.enable();
  if (interactionsState.scrollWheelZoom) map.scrollWheelZoom.enable();
  if (interactionsState.doubleClickZoom) map.doubleClickZoom.enable();
  if (interactionsState.boxZoom) map.boxZoom.enable();
  if (interactionsState.keyboard && map.keyboard) map.keyboard.enable();
  if (interactionsState.touchZoom && map.touchZoom) map.touchZoom.enable();
}

// ================ Hull helpers (LOCKED) ================
function clearHull() {
  hullLayer.clearLayers();
}

async function computeHullForCurrentView() {
  const bbox    = getBboxFromMap();
  const days    = daysEl.value;
  const source  = sourceEl.value;
  const minConf = minConfEl.value;

  setStatus("Computing hull...");
  try {
    const data = await fetchJSON(
      `/api/hull?bbox=${bbox}&days=${days}&source=${encodeURIComponent(source)}&min_conf=${minConf}`
    );
    clearHull();
    if (data && data.features && data.features.length) {
      hullLayer.addData(data);
      const area = data.features[0]?.properties?.area_km2;
      setStatus(`Hull loaded (${area ?? "?"} km²).`);
    } else {
      setStatus("No hull (not enough points).");
    }
  } catch (e) {
    console.error(e);
    setStatus("Hull failed.");
    clearHull();
  }
}

// ================== Core actions ==================
async function loadFires() {
  const bbox    = getBboxFromMap();
  const days    = daysEl.value;
  const source  = sourceEl.value;
  const minConf = minConfEl.value;

  setStatus("Loading fire data...");
  try {
    const data = await fetchJSON(
      `/api/fires?bbox=${bbox}&days=${days}&source=${encodeURIComponent(source)}&min_conf=${minConf}`
    );
    fireLayer.clearLayers();
    fireLayer.addData(data);
    setStatus(`Loaded ${data.features.length} points.`);

    // LOCKED HULL: compute once now (if requested), do not auto-update on pan
    if (chkHull && chkHull.checked) {
      await computeHullForCurrentView();
    } else {
      clearHull();
    }
  } catch (e) {
    console.error(e);
    setStatus("Error loading fires.");
  }
}

async function analyzeFires() {
  const bbox    = getBboxFromMap();
  const days    = daysEl.value;
  const source  = sourceEl.value;
  const minConf = minConfEl.value;
  const chartDiv = document.getElementById("chart");

  setStatus("Analyzing...");
  try {
    const data = await fetchJSON(
      `/api/analyze?bbox=${bbox}&days=${days}&source=${encodeURIComponent(source)}&min_conf=${minConf}`
    );

    if (window.Plotly) {
      const byDate = Array.isArray(data.by_date) ? data.by_date : [];
      const x = byDate.map(d => d.date);
      const y = byDate.map(d => d.count);

      const tsTrace = {
        x, y, type: "scatter", mode: "lines+markers", name: "Fires / day",
        marker: { size: 6 }, line: { width: 2 },
        hovertemplate: "%{x}<br>%{y} fires<extra></extra>"
      };

      const conf = data.confidence || { low: 0, nominal: 0, high: 0 };
      const confTrace = {
        x: ["Low", "Nominal", "High"],
        y: [conf.low || 0, conf.nominal || 0, conf.high || 0],
        type: "bar", name: "Confidence",
        xaxis: "x2", yaxis: "y2",
        marker: { opacity: 0.85 },
        hovertemplate: "%{x}: %{y}<extra></extra>"
      };

      const layout = {
        margin: { l: 50, r: 20, t: 20, b: 40 },
        grid: { rows: 1, columns: 2, subplots: [["xy", "x2y2"]] },
        xaxis:  { title: "Date" },
        yaxis:  { title: "Count", rangemode: "tozero" },
        xaxis2: { title: "" },
        yaxis2: { title: "Points", rangemode: "tozero" },
        showlegend: false, responsive: true, height: 340
      };

      // Summary + chart
      const summary = document.createElement("div");
      summary.style.cssText = "padding:6px 12px;font:500 13px/1.2 system-ui, Arial;";
      const meanFrp = (data.mean_frp == null) ? "n/a" : data.mean_frp;
      summary.textContent = `Analyzed ${data.count} hotspots • Hull: ${data.hull_area_km2} km² • BBOX: ${data.bbox_area_km2} km² • Mean FRP: ${meanFrp}`;

      chartDiv.innerHTML = "";
      chartDiv.parentNode.insertBefore(summary, chartDiv);
      Plotly.newPlot(chartDiv, [tsTrace, confTrace], layout, { displayModeBar: false, responsive: true });
    } else {
      chartDiv.innerHTML = `<div style="padding:10px">Hotspots: <b>${data.count}</b></div>`;
    }
    setStatus(`Analyzed ${data.count} hotspots.`);
  } catch (e) {
    console.error(e);
    document.getElementById("chart").innerHTML = "<p>Error loading analysis data.</p>";
    setStatus("Analysis failed.");
  }
}

async function byCountry() {
  const bbox    = getBboxFromMap();
  const days    = daysEl.value;
  const source  = sourceEl.value;
  const minConf = minConfEl.value;
  setStatus("Summarizing by country...");
  try {
    const data = await fetchJSON(
      `/api/by_country?bbox=${bbox}&days=${days}&source=${encodeURIComponent(source)}&min_conf=${minConf}&top=15`
    );
    const items = data.items || [];
    const chartDiv = document.getElementById("chart");

    if (window.Plotly && items.length) {
      Plotly.newPlot(chartDiv, [{
        x: items.map(d => d.country),
        y: items.map(d => d.count),
        type: "bar"
      }], {
        margin: { t: 20, b: 120 },
        xaxis: { tickangle: -45 },
        title: `Top countries in view (total points: ${data.total})`
      });

      const tbl = document.createElement("div");
      tbl.style.padding = "8px";
      tbl.innerHTML = `
        <table style="border-collapse:collapse;font-size:13px">
          <thead>
            <tr>
              <th style="text-align:left;padding:4px 8px;border-bottom:1px solid #ccc">Country</th>
              <th style="text-align:right;padding:4px 8px;border-bottom:1px solid #ccc">Count</th>
              <th style="text-align:right;padding:4px 8px;border-bottom:1px solid #ccc">Mean FRP</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(r => `
              <tr>
                <td style="padding:4px 8px;border-bottom:1px solid #eee">${r.country}</td>
                <td style="padding:4px 8px;text-align:right;border-bottom:1px solid #eee">${r.count}</td>
                <td style="padding:4px 8px;text-align:right;border-bottom:1px solid #eee">${r.mean_frp ?? ""}</td>
              </tr>`).join("")}
          </tbody>
        </table>`;
      chartDiv.appendChild(tbl);
    } else {
      chartDiv.innerHTML = `<div style="padding:10px">No data.</div>`;
    }
    setStatus(`Countries summarized (total points: ${data.total}).`);
  } catch (e) {
    console.error(e);
    setStatus("By-country failed.");
  }
}

function clearAll() {
  fireLayer.clearLayers();
  clearHull(); // <- make sure hull is cleared too

  if (selectionRect) { map.removeLayer(selectionRect); selectionRect = null; }
  if (selectionPopup) { map.removeLayer(selectionPopup); selectionPopup = null; }
  if (measureLine) { map.removeLayer(measureLine); measureLine = null; }
  if (measureTempLine) { map.removeLayer(measureTempLine); measureTempLine = null; }
  if (measureTooltip) { map.removeLayer(measureTooltip); measureTooltip = null; }
  endSelectMode();
  endMeasureMode(true);
  document.getElementById("chart").innerHTML = "";
  setStatus("Cleared.");
}

// ============ Rectangle selection =============
let selecting = false;
let selectionStart = null;
let selectionRect = null;
let selectionPopup = null;

function startSelectMode() {
  if (selecting || measuring) return;
  selecting = true;
  disableMapInteractions();
  map.getContainer().classList.add("cursor-crosshair");
  setStatus("Select area: click + drag to draw a rectangle (Esc to cancel).");
}
function endSelectMode() {
  if (!selecting) return;
  selecting = false;
  map.getContainer().classList.remove("cursor-crosshair");
  enableMapInteractions();
}
function finalizeRectangle(bounds) {
  const bbox    = bboxFromBounds(bounds);
  const days    = daysEl.value;
  const source  = sourceEl.value;
  const minConf = minConfEl.value;

  fetchJSON(`/api/fires?bbox=${bbox}&days=${days}&source=${encodeURIComponent(source)}&min_conf=${minConf}`)
    .then((data) => {
      const count  = data.features.length;
      const center = bounds.getCenter();
      if (selectionPopup) map.removeLayer(selectionPopup);
      selectionPopup = L.popup({ closeButton: true })
        .setLatLng(center)
        .setContent(`<b>${count}</b> hotspots in selection`)
        .openOn(map);
      setStatus(`Selection: ${count} hotspots.`);
    })
    .catch((e) => {
      console.error(e);
      setStatus("Selection failed.");
    })
    .finally(() => endSelectMode());
}
map.on("mousedown", (e) => {
  if (!selecting) return;
  selectionStart = e.latlng;
  if (selectionRect) { map.removeLayer(selectionRect); selectionRect = null; }
});
map.on("mousemove", (e) => {
  if (!selecting || !selectionStart) return;
  const bounds = L.latLngBounds(selectionStart, e.latlng);
  if (!selectionRect) {
    selectionRect = L.rectangle(bounds, {
      color: "#00aaff", weight: 2, fillOpacity: 0.06, className: "selection-rect",
    }).addTo(map);
  } else {
    selectionRect.setBounds(bounds);
  }
});
map.on("mouseup", (e) => {
  if (!selecting || !selectionStart) return;
  const bounds = L.latLngBounds(selectionStart, e.latlng);
  selectionStart = null;
  finalizeRectangle(bounds);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && selecting) {
    if (selectionRect) { map.removeLayer(selectionRect); selectionRect = null; }
    if (selectionPopup) { map.removeLayer(selectionPopup); selectionPopup = null; }
    endSelectMode();
    setStatus("Selection cancelled.");
  }
});

// ================ Measure distance ================
let measuring = false;
let measurePts = [];
let measureLine = null;
let measureTempLine = null;
let measureTooltip = null;

function startMeasureMode() {
  if (measuring || selecting) return;
  measuring = true;
  disableMapInteractions();
  map.getContainer().classList.add("cursor-measure");
  measurePts = [];
  if (measureLine) { map.removeLayer(measureLine); measureLine = null; }
  if (measureTempLine) { map.removeLayer(measureTempLine); measureTempLine = null; }
  if (measureTooltip) { map.removeLayer(measureTooltip); measureTooltip = null; }
  setStatus("Measure: click to add points, double-click or Esc to finish.");
}
function endMeasureMode(cancelOnly = false) {
  if (!measuring) return;
  measuring = false;
  map.getContainer().classList.remove("cursor-measure");
  enableMapInteractions();
  if (cancelOnly) {
    if (measureLine) { map.removeLayer(measureLine); measureLine = null; }
    if (measureTempLine) { map.removeLayer(measureTempLine); measureTempLine = null; }
    if (measureTooltip) { map.removeLayer(measureTooltip); measureTooltip = null; }
    measurePts = [];
    setStatus("Measure cancelled.");
  }
}
function formatMeters(m) { return m >= 1000 ? `${(m/1000).toFixed(2)} km` : `${m.toFixed(1)} m`; }
function computePolylineLength(latlngs) {
  if (latlngs.length < 2) return 0;
  let d = 0; for (let i=1; i<latlngs.length; i++) d += latlngs[i-1].distanceTo(latlngs[i]);
  return d;
}
map.on("click", (e) => {
  if (!measuring) return;
  measurePts.push(e.latlng);
  if (!measureLine) {
    measureLine = L.polyline(measurePts, { color: "#0077ff", weight: 3 }).addTo(map);
  } else {
    measureLine.setLatLngs(measurePts);
  }
  if (!measureTooltip) {
    measureTooltip = L.tooltip({ permanent: false, direction: "top" })
      .setLatLng(e.latlng).setContent("0 m").addTo(map);
  }
});
map.on("mousemove", (e) => {
  if (!measuring || measurePts.length === 0) return;
  const live = [...measurePts, e.latlng];
  if (!measureTempLine) {
    measureTempLine = L.polyline(live, { color: "#00c3ff", weight: 2, dashArray: "5,6" }).addTo(map);
  } else {
    measureTempLine.setLatLngs(live);
  }
  const dist = computePolylineLength(live);
  measureTooltip.setLatLng(e.latlng).setContent(formatMeters(dist));
});
map.on("dblclick", () => {
  if (!measuring) return;
  const finalLatLngs = measureTempLine ? measureTempLine.getLatLngs() : measurePts;
  const dist = computePolylineLength(finalLatLngs);
  if (measureTooltip) {
    const last = finalLatLngs[finalLatLngs.length - 1];
    measureTooltip.setLatLng(last).setContent(`<b>${formatMeters(dist)}</b>`);
  }
  if (measureTempLine) { map.removeLayer(measureTempLine); measureTempLine = null; }
  setStatus(`Distance: ${formatMeters(dist)} (double-click completed)`);
  endMeasureMode(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && measuring) endMeasureMode(true);
});

// =============== Bind buttons / events ===============
btnLoad.addEventListener("click", loadFires);
btnAnalyze.addEventListener("click", analyzeFires);
btnCountry.addEventListener("click", byCountry);
btnSelectArea.addEventListener("click", startSelectMode);
btnMeasure.addEventListener("click", startMeasureMode);
btnClear.addEventListener("click", clearAll);

// Checkbox: compute once or hide; no auto-recompute on pan/zoom
if (chkHull) {
  chkHull.addEventListener("change", async () => {
    if (chkHull.checked) {
      await computeHullForCurrentView();
    } else {
      clearHull();
      setStatus("Hull hidden.");
    }
  });
}

// IMPORTANT: Do NOT auto-recompute hull on pan/zoom.
// If you previously had a map.on('moveend', ...) for hull, remove it.

setStatus('Ready. Click “Load fires in view”.');
