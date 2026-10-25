// Weather: when can I cut (one sentence), forecast maps (rain, temperature, wind, cloud) with a
// time slider, live radar, the next 48 hours rated for cutting, now, and 7 days.
// All real, from Open-Meteo and RainViewer.

import { $, everySecond, load, makeClock, showAlerts, toast, topbar } from "./common.js";
import { farmForecast, gridBounds, gridForecast, gridImage, legend, nowIndex, radarFrames, weatherWord, windArrows } from "./weather-data.js";

const { farm, season } = await load();
// radar is live only: a demo replaying a past week has none to show
if (farm.week) document.querySelector('[data-layer="radar"]').style.display = "none";
const clock = makeClock(farm);
topbar(farm, "weather.html", clock);
everySecond(clock, season, { hour: (hour) => showAlerts(farm, season, hour) });

const site = farm.site;
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
const dayName = (time) => new Date(time).toLocaleDateString("en-GB", { weekday: "short" });
const hourText = (time) => `${dayName(time)} ${time.slice(11, 16)}`;
const capital = (text) => text[0].toUpperCase() + text.slice(1);
$("#place").textContent = farm.name;

// ---------- the forecast map ----------
const map = L.map("weather-map", { zoomSnap: 0.25 });
L.tileLayer(site.weather_base_url, { attribution: site.weather_attribution, maxZoom: 16 }).addTo(map);
const labelPane = map.createPane("labels"); // town names above the weather colours
labelPane.style.zIndex = 450;
labelPane.style.pointerEvents = "none";
L.tileLayer(site.weather_labels_url, { pane: "labels", maxZoom: 16 }).addTo(map);
L.marker(farm.centre, { icon: L.divIcon({ className: "yard-tag", html: farm.name, iconSize: null }) }).addTo(map);

let grid = null;
let layer = "precipitation";
let overlay = null;
let arrows = null;
let radar = null;
let playing = null;

try {
  grid = await gridForecast(farm);
  map.fitBounds(gridBounds(grid));
  $("#hour").max = grid.times.length - 1;
  $("#hour").value = nowIndex(grid.times);
  draw();
} catch {
  map.setView(farm.centre, 9);
  toast("Forecast map unavailable right now.");
}

function draw() {
  const hour = Number($("#hour").value);
  overlay?.remove();
  arrows?.remove();
  overlay = arrows = null;
  if (layer === "radar") return;
  overlay = L.imageOverlay(gridImage(grid, layer, hour), gridBounds(grid), { opacity: 0.9 }).addTo(map);
  if (layer === "wind_speed_10m") arrows = L.layerGroup(windArrows(grid, hour)).addTo(map);
  $("#when").textContent = hour === nowIndex(grid.times) ? "now" : hourText(grid.times[hour]);
  $("#legend").innerHTML = legend(layer);
}

$("#hour").addEventListener("input", draw);
$("#play").addEventListener("click", () => {
  if (playing) return stop();
  $("#play").textContent = "Pause";
  playing = setInterval(() => {
    const slider = $("#hour");
    slider.value = Number(slider.value) >= Number(slider.max) ? nowIndex(grid.times) : Number(slider.value) + 1;
    draw();
  }, site.play_ms_per_hour);
});
function stop() {
  clearInterval(playing);
  playing = null;
  $("#play").textContent = "Play";
}

$("#layer-tabs").addEventListener("click", async (event) => {
  const tab = event.target.closest("[data-layer]");
  if (!tab) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
  layer = tab.dataset.layer;
  stopRadar();
  stop();
  if (grid) draw();
  if (layer === "radar") await startRadar();
  $("#hour").disabled = $("#play").disabled = layer === "radar";
});

// radar: the last two hours, looping
async function startRadar() {
  try {
    const frames = await radarFrames();
    const options = { maxNativeZoom: site.radar_max_zoom, maxZoom: 16, opacity: 0, zIndex: 400 };
    const layers = frames.map((frame) => L.tileLayer(frame.url, options).addTo(map));
    let k = 0;
    const timer = setInterval(() => {
      layers.forEach((l, i) => l.setOpacity(i === k ? 0.8 : 0));
      $("#when").textContent = `radar ${frames[k].time.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
      k = (k + 1) % layers.length;
    }, 600);
    radar = { layers, timer };
    $("#legend").innerHTML = "";
  } catch {
    toast("Radar unavailable right now.");
  }
}
function stopRadar() {
  if (!radar) return;
  clearInterval(radar.timer);
  radar.layers.forEach((l) => l.remove());
  radar = null;
}

// ---------- the sentence, now, 7 days, the next 48 hours ----------
try {
  const forecast = await farmForecast(farm);
  const ratings = showNext48(forecast.hourly);
  showVerdict(forecast.hourly, ratings);
  showNow(forecast.hourly);
  showDays(forecast.daily);
} catch {
  $("#verdict").innerHTML = `<h1>Forecast unavailable.</h1><p>Try again in a few minutes.</p>`;
}

function showNow(h) {
  const i = nowIndex(h.time);
  $("#now").innerHTML = `
    <p class="now-figure">${Math.round(h.temperature_2m[i])}<small>°C</small></p>
    <p class="now-word">${capital(weatherWord(h.weather_code[i]).word)}.</p>
    <p class="facts">rain ${h.precipitation_probability[i] ?? 0}% · wind ${Math.round(h.wind_speed_10m[i])} km/h · humidity ${h.relative_humidity_2m[i]}%</p>`;
}

function showDays(d) {
  $("#days").innerHTML = d.time
    .map((day, k) => {
      const rain = d.precipitation_sum[k];
      return `<tr><td class="name">${k === 0 ? "Today" : dayName(day)}</td><td>${weatherWord(d.weather_code[k]).word}</td>
        <td class="r">${Math.round(d.temperature_2m_max[k])}°</td><td class="r muted">${Math.round(d.temperature_2m_min[k])}°</td>
        <td class="r" style="color:${rain >= 0.1 ? "var(--rain)" : "inherit"}">${rain >= 0.1 ? `${rain.toFixed(1)} mm` : "dry"}</td></tr>`;
    })
    .join("");
}

// each of the next 48 hours: good (dry, air dry enough), fair (dry, damp air) or poor
function showNext48(h) {
  const start = nowIndex(h.time);
  const hours = Array.from({ length: site.forecast_hours }, (_, k) => start + k).filter((i) => i < h.time.length);
  const rate = (i) => {
    if (h.precipitation[i] >= site.dry_mm) return "poor";
    if (h.relative_humidity_2m[i] < site.good_humidity_pct) return "good";
    return h.relative_humidity_2m[i] < site.fair_humidity_pct ? "fair" : "poor";
  };
  const ratings = hours.map((i) => ({ i, rating: rate(i) }));
  $("#windows").innerHTML = ratings.map(({ i, rating }) => `<span class="${rating}" title="${hourText(h.time[i])}: ${rating}"></span>`).join("");
  $("#window-days").innerHTML = hours.map((i, k) => `<span>${k === 0 ? "now" : h.time[i].endsWith("00:00") ? dayName(h.time[i]) : ""}</span>`).join("");

  // temperature over rain, on the same hour axis
  const HOUR_MS = 3.6e6;
  const rows = hours.map((i) => ({ t: new Date(h.time[i]), rain: h.precipitation[i], temp: h.temperature_2m[i] }));
  const x = {
    type: "time", domain: [rows[0].t, new Date(+rows.at(-1).t + HOUR_MS)], label: null, ticks: "6 hours",
    tickFormat: (t) => (t.getHours() === 0 ? dayName(t) : `${String(t.getHours()).padStart(2, "0")}:00`),
  };
  const common = {
    width: $("#hourly-chart").clientWidth, marginLeft: 40, marginRight: 8,
    style: { fontFamily: css("font-data"), fontSize: "11px", background: "transparent", color: css("ink") },
  };
  $("#hourly-chart").replaceChildren(
    Plot.plot({ ...common, height: 120, x: { ...x, axis: null }, y: { label: "°C", grid: true },
      marks: [Plot.lineY(rows, { x: "t", y: "temp", stroke: css("ink"), strokeWidth: 1.5 })] }),
    Plot.plot({ ...common, height: 110, x, y: { label: "rain mm", grid: true },
      marks: [Plot.rectY(rows, { x1: "t", x2: (r) => new Date(+r.t + HOUR_MS), y: "rain", fill: css("rain"), inset: 0.5 }), Plot.ruleY([0], { stroke: css("rule") })] }),
  );
  return ratings;
}

// the answer: the longest run of good hours, and when the next rain comes
function showVerdict(h, ratings) {
  let best = [0, 0]; // [first, length] of the longest run of good hours
  let runStart = null;
  ratings.forEach(({ rating }, k) => {
    if (rating === "good" && runStart === null) runStart = k;
    if (rating === "good" && k - runStart + 1 > best[1]) best = [runStart, k - runStart + 1];
    if (rating !== "good") runStart = null;
  });
  const [first, length] = best;
  const rainAt = ratings.find(({ i }) => h.precipitation[i] >= site.dry_mm);
  const rain = rainAt ? `Next rain ${hourText(h.time[rainAt.i])}.` : "No rain in the next 48 h.";
  if (!length) {
    $("#verdict").innerHTML = `<h1><span class="lamp wait"></span>No good cutting weather in 48 h.</h1><p>${rain}</p>`;
    return;
  }
  const from = ratings[first].i;
  const to = ratings[first + length - 1].i;
  const starts = first === 0 ? "now" : hourText(h.time[from]);
  $("#verdict").innerHTML = `<h1><span class="lamp ready"></span>Best cutting weather: ${starts} to ${hourText(h.time[to + 1] ?? h.time[to])}.</h1>
    <p>${length} dry hours with the air dry enough. ${rain}</p>`;
}
