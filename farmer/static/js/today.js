// Today: one sentence that answers "should I cut?", then the fields (with which combine the plan
// sends there and when), the combines, the real weather at the farm, and two charts of the plan.

import { $, ACTIVITY, crop, everySecond, lamp, load, makeClock, showAlerts, topbar } from "./common.js";
import { drawRoutes, drawWorth, worthShades, worthSteps } from "./plan-charts.js";
import { farmForecast, nowIndex, weatherWord } from "./weather-data.js";

const { farm, season } = await load();
const clock = makeClock(farm);
topbar(farm, "today.html", clock);
const totalHa = farm.fields.reduce((sum, field) => sum + field.area_ha, 0);

let weather = null; // the real forecast at the farm, once it arrives
farmForecast(farm)
  .then((data) => {
    weather = data;
    showWeather();
    showVerdict(Math.floor(clock()));
  })
  .catch(() => ($("#now-weather").innerHTML = `<p class="muted">Forecast unavailable.</p>`));

let shownHour = Math.floor(clock()); // declared before everySecond, which calls showHour at once
everySecond(clock, season, { hour: showHour });
addEventListener("resize", () => showCharts(shownHour));

function showHour(hour) {
  shownHour = hour;
  showVerdict(hour);
  showFields(hour);
  showCombines(hour);
  showCharts(hour);
  $("#harvested").textContent = `${season.harvested_ha[hour]} of ${totalHa} ha cut`;
  const rain = rainSentence();
  showAlerts(farm, season, hour, rain && !rain.startsWith("Dry for") ? [rain] : []);
}

// the answer, then one line of why
function showVerdict(hour) {
  const statuses = season.status.map((row) => row[hour]);
  const count = (s) => statuses.filter((x) => x === s).length;
  const rain = rainSentence();
  let status, title, line;
  if (count("done") === statuses.length) [status, title, line] = ["done", "Harvest finished.", `All ${totalHa} ha cut.`];
  else if (count("ready")) [status, title, line] = ["ready", "Cut today.", `${count("ready")} of ${statuses.length} fields ready. ${rain}`];
  else if (count("soon")) [status, title, line] = ["soon", "Wait a few hours.", `${count("soon")} field${count("soon") > 1 ? "s" : ""} ready within ${farm.site.soon_hours} h. ${rain}`];
  else [status, title, line] = ["wait", "Not today.", `Grain too wet to cut. ${rain}`];
  $("#verdict").innerHTML = `<h1><span class="lamp ${status}"></span>${title}</h1><p>${line}</p>`;
}

// "Dry until 23:00." / "Raining now." / "Dry for the next 24 h." from the real forecast
function rainSentence() {
  if (!weather) return "";
  const h = weather.hourly;
  const start = nowIndex(h.time);
  for (let i = start; i < Math.min(start + 24, h.time.length); i++) {
    if (h.precipitation[i] >= farm.site.dry_mm) return i === start ? "Raining now." : `Dry until ${h.time[i].slice(11)}.`;
  }
  return "Dry for the next 24 h.";
}

function showWeather() {
  const h = weather.hourly;
  const i = nowIndex(h.time);
  const hours = Array.from({ length: 12 }, (_, k) => i + k).filter((k) => k < h.time.length);
  $("#now-weather").innerHTML = `
    <p class="now-figure">${Math.round(h.temperature_2m[i])}<small>°C</small></p>
    <p class="now-word">${weatherWord(h.weather_code[i]).word[0].toUpperCase() + weatherWord(h.weather_code[i]).word.slice(1)}.</p>
    <p class="facts">rain ${h.precipitation_probability[i] ?? 0}% · wind ${Math.round(h.wind_speed_10m[i])} km/h · humidity ${h.relative_humidity_2m[i]}%</p>
    <table class="hours">
      <tr>${hours.map((k) => `<td>${h.time[k].slice(11, 13)}</td>`).join("")}</tr>
      <tr>${hours.map((k) => `<td><div class="bar" title="${h.precipitation_probability[k] ?? 0}% chance of rain"><span style="height:${h.precipitation_probability[k] ?? 0}%"></span></div></td>`).join("")}</tr>
      <tr>${hours.map((k) => `<td>${Math.round(h.temperature_2m[k])}°</td>`).join("")}</tr>
    </table>
    <p class="label" style="margin-top:6px">Bars: chance of rain each hour</p>`;
}

function showFields(hour) {
  $("#fields").innerHTML = farm.fields
    .map((field, f) => {
      const status = season.status[f][hour];
      const cut = season.cut_pct[f][hour];
      const best = season.best[f][hour];
      return `<tr class="link" data-href="map.html#field-${f}">
        <td class="name">${field.label}</td>
        <td class="wide">${crop(field.crop)}</td>
        <td class="r wide">${field.area_ha} ha</td>
        <td>${String(cut).padStart(3, " ")}%<span class="cutbar"><span style="width:${cut}%"></span></span></td>
        <td class="r">${status === "done" ? "" : `${season.moisture_pct[f][hour].toFixed(1)}%`}</td>
        <td>${lamp(status)}</td>
        <td>${status === "done" ? "done" : (season.planned[f][hour] ?? "not this week")}</td>
        <td class="wide">${status === "done" ? "" : (best ?? "none in view")}</td></tr>`;
    })
    .join("");
}

// the plan in force, as two charts on one hour axis
function showCharts(hour) {
  const p = season.plans.filter((plan) => plan.hour <= hour).at(-1);
  $("#plan-made").textContent = `plan of ${season.labels[p.hour].slice(0, 3)} ${season.labels[p.hour].slice(-5)}`;
  drawWorth($("#worth-chart"), farm, season, hour);
  drawRoutes($("#route-chart"), farm, season, hour);
  const shades = worthShades();
  $("#worth-key").innerHTML =
    worthSteps(season)
      .map((low, i) => `<span class="swatch" style="background:${shades[i]}"></span>£${low.toLocaleString("en-GB")}+`)
      .join(" ") +
    ` <span class="swatch blank"></span>can't be cut · box: where the plan sends a combine · bar: cut so far · pale: before now`;
}

function showCombines(hour) {
  $("#combines").innerHTML = farm.machines
    .map((machine, m) => {
      const activity = season.activity[m][hour];
      const next = season.next[hour][m];
      return `<tr class="link" data-href="map.html#combine-${m}">
        <td style="width:36px"><span class="marker ${activity}">${machine.letter}</span></td>
        <td class="name">${activity === "yard" ? ACTIVITY.yard : season.doing[hour][m].replace(/^\w/, (c) => c.toLowerCase())}</td>
        <td>${next.startsWith("No change") ? "stays put" : next}</td></tr>`;
    })
    .join("");
}

document.addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-href]");
  if (row) location.href = row.dataset.href;
});
