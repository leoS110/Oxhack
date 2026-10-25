// Live map: satellite, fields shaded by status, combines moving along their GPS tracks, and real
// weather layers (radar, rain forecast, wind) that zoom out to show the area around the farm.

import { $, ACTIVITY, crop, everySecond, lamp, load, makeClock, showAlerts, telemetry, toast, topbar } from "./common.js";
import { gridBounds, gridForecast, gridImage, legend, nowIndex, radarFrames, windArrows } from "./weather-data.js";

const { farm, season } = await load();
// radar is live only: a demo replaying a past week has none to show
if (farm.week) $("#layer-radar").closest("label").style.display = "none";
const clock = makeClock(farm);
topbar(farm, "map.html", clock);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();

const map = L.map("map", { zoomSnap: 0.25, zoomControl: false });
L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer(farm.map.tiles_url, { attribution: farm.map.attribution, maxZoom: 19 }).addTo(map);
const FARM_VIEW = { paddingTopLeft: [340, 30], paddingBottomRight: [270, 70] }; // clear of the panels

// outline colour = crop, fill = status. Each field is a group of real fields: its label sits in
// the middle of the group, not on its biggest field, so the labels overlap less.
const fields = farm.fields.map((field) => L.polygon(field.outline, { color: css(field.crop), weight: 2, fillOpacity: 0.4 }).addTo(map));
const labels = farm.fields.map((field) =>
  L.tooltip({ permanent: true, direction: "center", className: "field-tag" }).setLatLng(field.centre).setContent(field.label).addTo(map),
);
// a label that would sit on another is hidden until you zoom in; the bigger group keeps its label
const labelOrder = farm.fields.map((_, f) => f).sort((a, b) => farm.fields[b].area_ha - farm.fields[a].area_ha);
function declutter() {
  const kept = [];
  labelOrder.forEach((f) => {
    const element = labels[f].getElement();
    if (!element) return;
    element.style.visibility = "";
    const box = element.getBoundingClientRect();
    const overlaps = kept.some((k) => box.left < k.right && box.right > k.left && box.top < k.bottom && box.bottom > k.top);
    if (overlaps) element.style.visibility = "hidden";
    else kept.push(box);
  });
}
map.on("zoomend", declutter);
$("#crop-key").innerHTML = [...new Set(farm.fields.map((field) => field.crop))]
  .map((name) => `<span>${crop(name)}</span>`)
  .join("");
const farmBounds = L.featureGroup(fields).getBounds();
map.fitBounds(farmBounds, FARM_VIEW);

// the yard label and the combines sit above the field labels (Leaflet's tooltip pane is 650)
map.createPane("top").style.zIndex = 700;
L.marker(farm.yard, { pane: "top", interactive: false, icon: L.divIcon({ className: "yard-tag", html: "Yard", iconSize: [40, 18], iconAnchor: [46, 9] }) }).addTo(map);

const trails = farm.machines.map(() => L.polyline([], { color: css("straw"), weight: 3, opacity: 0.95 }).addTo(map));
const markers = farm.machines.map((machine, m) =>
  L.marker(farm.yard, {
    pane: "top",
    icon: L.divIcon({ className: "", html: `<div class="marker" id="marker-${m}">${machine.letter}</div>`, iconSize: [26, 26] }),
  })
    .bindPopup(() => combinePopup(m))
    .addTo(map),
);

// ---------- combines: GPS positions, smooth between the positions sent 6 times an hour ----------
function position(m, frame) {
  const track = season.tracks[m];
  const i = Math.min(Math.floor(frame), track.length - 2);
  const share = frame - i;
  return [track[i][0] + (track[i + 1][0] - track[i][0]) * share, track[i][1] + (track[i + 1][1] - track[i][1]) * share];
}

function moveCombines() {
  const frame = clock() * farm.steps_per_hour;
  const back = (farm.site.trail_minutes / 60) * farm.steps_per_hour;
  farm.machines.forEach((_, m) => {
    const now = position(m, frame);
    markers[m].setLatLng(now);
    const past = $("#layer-trails").checked ? season.tracks[m].slice(Math.max(0, Math.ceil(frame - back)), Math.floor(frame) + 1) : [];
    trails[m].setLatLngs(past.length ? [...past, now] : []);
  });
  requestAnimationFrame(moveCombines);
}
requestAnimationFrame(moveCombines);

function combinePopup(m) {
  const hour = Math.floor(clock());
  const machine = farm.machines[m];
  const reading = telemetry(farm, season, m, clock());
  const activity = season.activity[m][hour];
  return `<h2>${machine.label}</h2>
    <div class="muted">${activity === "yard" ? ACTIVITY.yard : season.doing[hour][m]}</div>
    <div class="readings">
      <div><b>${Math.round(reading.fuel_pct)}%</b><span>fuel</span></div>
      <div><b>${Math.round(reading.tank_pct)}%</b><span>tank</span></div>
      <div><b>${reading.speed_kmh.toFixed(1)}</b><span>km/h</span></div>
    </div>
    <div class="popup-actions">
      <select class="button small" id="send-${m}" aria-label="Field">${farm.fields.map((f, i) => `<option value="${i}">${f.label}</option>`).join("")}</select>
      <button class="button small" data-send="${m}">Send there</button>
      <button class="button small secondary" data-toast="${machine.label} heading to the yard.">Back to yard</button>
      <button class="button small secondary" data-toast="Calling the driver of ${machine.label}…" style="grid-column:1/-1">Call driver</button>
    </div>`;
}
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-send]");
  if (!button) return;
  const m = Number(button.dataset.send);
  toast(`${farm.machines[m].label} sent to ${farm.fields[$(`#send-${m}`).value].label}.`);
  map.closePopup();
});

// ---------- once an hour: field shading, marker borders, the combines list ----------
function showHour(hour) {
  const shade = $("#layer-status").checked;
  farm.fields.forEach((field, f) => {
    const status = season.status[f][hour];
    fields[f].setStyle({ fillColor: css(status), fillOpacity: shade && status !== "done" ? 0.4 : 0 });
    labels[f].setContent(`${field.label} · ${lamp(status)}`);
  });
  declutter(); // the words changed, so the labels' widths did too
  farm.machines.forEach((_, m) => {
    const element = document.getElementById(`marker-${m}`);
    if (element) element.className = `marker ${season.activity[m][hour]}`;
  });
  $("#combine-list").innerHTML = farm.machines
    .map((machine, m) => {
      const activity = season.activity[m][hour];
      return `<div class="combine-row" data-combine="${m}"><span class="marker ${activity}">${machine.letter}</span>
        <div>${activity === "yard" ? ACTIVITY.yard : season.doing[hour][m]}<small>next: ${season.next[hour][m]}</small></div></div>`;
    })
    .join("");
  showAlerts(farm, season, hour);
}
everySecond(clock, season, { hour: showHour });

$("#combine-list").addEventListener("click", (event) => {
  const row = event.target.closest("[data-combine]");
  if (!row) return;
  const m = Number(row.dataset.combine);
  map.flyTo(markers[m].getLatLng(), 17, { duration: 0.8 });
  setTimeout(() => markers[m].openPopup(), 850);
});

// links from Today: map.html#field-3 or #combine-1
const [kind, index] = location.hash.slice(1).split("-");
if (kind === "field" && fields[index]) map.fitBounds(fields[index].getBounds(), { ...FARM_VIEW, maxZoom: 17 });
if (kind === "combine" && markers[index]) setTimeout(() => $(`[data-combine="${index}"]`)?.click(), 300);

// ---------- real weather layers ----------
let grid = null;
let radar = null; // {layers, timer}
const weatherLayers = { rain: null, wind: null };
const getGrid = async () => (grid ??= await gridForecast(farm));

async function showRadar(on) {
  if (radar) {
    clearInterval(radar.timer);
    radar.layers.forEach((layer) => layer.remove());
    radar = null;
  }
  if (!on) return;
  const frames = await radarFrames();
  const options = { maxNativeZoom: farm.site.radar_max_zoom, maxZoom: 19, opacity: 0, zIndex: 400 };
  const layers = frames.map((frame) => L.tileLayer(frame.url, options).addTo(map));
  let k = 0;
  const timer = setInterval(() => {
    layers.forEach((layer, i) => layer.setOpacity(i === k ? 0.75 : 0));
    $("#weather-note").textContent = `radar ${frames[k].time.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
    k = (k + 1) % layers.length;
  }, 700);
  radar = { layers, timer };
}

async function showRain(on) {
  weatherLayers.rain?.remove();
  weatherLayers.rain = null;
  if (!on) return;
  const g = await getGrid();
  weatherLayers.rain = L.imageOverlay(gridImage(g, "precipitation", nowIndex(g.times)), gridBounds(g), { opacity: 0.85 }).addTo(map);
}

async function showWind(on) {
  weatherLayers.wind?.remove();
  weatherLayers.wind = null;
  if (!on) return;
  const g = await getGrid();
  weatherLayers.wind = L.layerGroup(windArrows(g, nowIndex(g.times))).addTo(map);
}

// weather is about the area around the farm: zoom out while a weather layer is on
async function weatherView() {
  const any = ["#layer-radar", "#layer-rain", "#layer-wind"].some((id) => $(id).checked);
  if (any) map.flyToBounds(gridBounds(await getGrid()), { duration: 0.8 });
  else map.flyToBounds(farmBounds, { ...FARM_VIEW, duration: 0.8 });
  $("#layer-legend").innerHTML = $("#layer-rain").checked ? legend("precipitation") : "";
  if (!$("#layer-radar").checked) $("#weather-note").textContent = any ? "forecast for this hour" : "";
}

const offline = () => toast("Weather unavailable right now.");
$("#layer-status").addEventListener("change", () => showHour(Math.floor(clock())));
$("#layer-radar").addEventListener("change", (e) => showRadar(e.target.checked).then(weatherView).catch(offline));
$("#layer-rain").addEventListener("change", (e) => showRain(e.target.checked).then(weatherView).catch(offline));
$("#layer-wind").addEventListener("change", (e) => showWind(e.target.checked).then(weatherView).catch(offline));
