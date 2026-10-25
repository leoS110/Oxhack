// The control panel: one clock drives the weather charts, the farm map and the plan (decision map).
// Everything shown comes from /api/farm and /api/plan (webapp/demo_state.py).

import { getJSON } from "./api.js";
import { drawDecisionMap, drawHumidity, drawMoisture, drawRain, layerSteps, layerValue, valueColour } from "./charts.js";
import { createMap, cutShare } from "./map.js";
import { cropWord, crops } from "./theme.js";

const $ = (id) => document.getElementById(id);
const money = (gbp) => `£${Math.round(gbp).toLocaleString("en-GB")}`;
const escape = (text) => text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

try {
  await main();
} catch (error) {
  $("combines").innerHTML = `<tr><td class="loading">Could not load the demo: ${escape(error.message)}</td></tr>`;
  throw error;
}

async function main() {
  const [farm, plan] = await Promise.all([getJSON("/api/farm"), getJSON("/api/plan")]);
  let time = 0; // hours since the start of the season, fractions allowed
  let shownHour = -1;
  let selectedField = plan.cards[0][0].field ?? 0;
  let selectedCombine = 0; // whose reasons show under the combines table
  let playing = false;
  const options = new URLSearchParams(location.search); // panel.html?t=30&layer=chance
  let layer = options.get("layer") === "chance" ? "chance" : "value"; // colours: £/ha, or chance it can be cut

  const map = createMap($("map"), farm, plan, selectField);
  fillStaticText(farm, plan);
  const picker = farm.fields.map((field, f) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = field.label;
    button.addEventListener("click", () => selectField(f));
    $("field-picker").append(button);
    return button;
  });

  const layerButtons = [...document.querySelectorAll("#layer-picker button")];
  layerButtons.forEach((button) =>
    button.addEventListener("click", () => {
      layer = button.dataset.layer;
      showHour(shownHour, true);
    }),
  );

  const scrub = $("scrub");
  scrub.max = plan.hours;
  scrub.addEventListener("input", () => {
    pause();
    setTime(Number(scrub.value));
  });
  $("play").addEventListener("click", () => (playing ? pause() : play()));
  document.addEventListener("keydown", (event) => {
    if (event.code !== "Space" || event.target.tagName === "BUTTON") return;
    event.preventDefault();
    playing ? pause() : play();
  });
  window.addEventListener("resize", () => showHour(shownHour, true));

  function setTime(newTime) {
    time = Math.max(0, Math.min(newTime, plan.hours));
    scrub.value = time;
    map.moveTo(time);
    showHour(Math.min(Math.floor(time), plan.hours - 1));
  }

  function play() {
    if (time >= plan.hours) setTime(0);
    playing = true;
    $("play").textContent = "Pause";
    let last = performance.now();
    const tick = (now) => {
      if (!playing) return;
      setTime(time + (now - last) / 1000 / plan.play_seconds_per_hour);
      last = now;
      if (time >= plan.hours) pause();
      else requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function pause() {
    playing = false;
    $("play").textContent = "Play";
  }

  function selectField(f) {
    selectedField = f;
    showHour(shownHour, true);
  }

  // everything that only changes once an hour (or when a field is picked)
  function showHour(hour, force = false) {
    if (hour === shownHour && !force) return;
    shownHour = hour;
    const planIndex = plan.plans.findLastIndex((p) => p.hour <= hour);
    const planNow = plan.plans[planIndex];
    $("clock").textContent = plan.labels[hour];
    $("scrub-label").textContent = plan.labels[hour];
    $("totals").textContent = totals(farm, plan, hour);
    showCombines(farm, plan.cards[hour], selectedCombine, (m) => {
      selectedCombine = m;
      const field = plan.cards[hour][m].field;
      field === null ? showHour(shownHour, true) : selectField(field);
    });
    const colour = valueColour(plan, layer);
    map.showHour(hour, selectedField, (f) => {
      const value = layerValue(planNow, layer, f, hour - planNow.hour);
      return { colour: plan.standing_ha[f][hour] > 0 ? colour(value) : null, text: describe(value, layer) };
    });
    layerButtons.forEach((button) => button.setAttribute("aria-pressed", button.dataset.layer === layer));
    showPlanChange(plan, planNow, hour);
    showDecisionKey(plan, layer);
    picker.forEach((button, f) => button.setAttribute("aria-pressed", f === selectedField));
    showFieldSummary(farm, plan, selectedField, hour);
    drawDecisionMap($("decision-map"), farm, plan, hour, planIndex, layer);
    // rain and humidity on top, the grain moisture they cause underneath and biggest
    const height = $("weather-charts").clientHeight;
    drawRain($("rain-chart"), farm, plan, selectedField, hour, planNow, Math.round(0.24 * height));
    drawHumidity($("humidity-chart"), farm, plan, selectedField, hour, planNow, Math.round(0.28 * height));
    drawMoisture($("moisture-chart"), farm, plan, selectedField, hour, planNow, height - Math.round(0.24 * height) - Math.round(0.28 * height) - 4);
  }

  // opens at the hour it is now (the demo runs in real time); ?t=30 opens at hour 30 instead
  const realHour = (Date.now() - Date.parse(plan.clock_start_utc)) / 3.6e6;
  setTime(options.has("t") ? Number(options.get("t")) : Math.max(0, Math.min(realHour, plan.hours)));
}

function fillStaticText(farm, plan) {
  $("farm-name").textContent = farm.farm_name;
  $("forecast-info").textContent = `${plan.forecast.forecast}, ${plan.forecast.members} members, from ${plan.forecast.start}`;
  const values = {
    ...plan.limits,
    members: plan.forecast.members,
    forecast_name: plan.forecast.forecast,
    truth: plan.forecast.truth,
    credit: plan.forecast.credit,
    fields: farm.fields.length,
    real_fields: farm.fields.reduce((sum, field) => sum + field.outline.length, 0), // one outline each
    total_ha: Math.round(farm.fields.reduce((sum, field) => sum + field.area_ha, 0)).toLocaleString("en-GB"),
  };
  document.querySelectorAll("[data-fill]").forEach((span) => (span.textContent = values[span.dataset.fill]));
  // the map key for the outline colours: each crop and how much of it there is
  const cropHa = (crop) => farm.fields.filter((field) => field.crop === crop).reduce((sum, field) => sum + field.area_ha, 0);
  $("crop-key").innerHTML =
    "outline: " +
    Object.keys(crops)
      .filter((crop) => cropHa(crop) > 0)
      .map((crop) => `<span class="crop-line crop-${crop}"></span>${cropWord(crop)} <span class="num">${Math.round(cropHa(crop)).toLocaleString("en-GB")} ha</span>`)
      .join(" ");
}

// a field's value this hour in words, for its label on the map
function describe(value, layer) {
  if (layer === "chance") return `${Math.round(100 * value)}% can cut`;
  return value > 0 ? `${money(value)}/ha` : "can't cut";
}

// what the plan in force learned and changed when it was made; dark for the first hour after
function showPlanChange(plan, planNow, hour) {
  const element = $("plan-change");
  element.innerHTML = `<span class="label">Replan ${escape(plan.labels[planNow.hour])}</span>${planNow.change.map(escape).join(" ")}`;
  element.classList.toggle("fresh", hour === planNow.hour);
}

// the colour steps of the decision map, with their lower edges
function showDecisionKey(plan, layer) {
  const colour = valueColour(plan, layer);
  const steps = layerSteps(plan, layer);
  const label = (low) => (layer === "chance" ? `${Math.round(100 * low)}%+` : `${money(low)}+`);
  $("decision-key").innerHTML = steps
    .map((low, i) => `<span class="step"><span class="swatch" style="background:${colour(i === 0 ? 1e-9 : low)}"></span>${label(low)}</span>`)
    .join("");
}

function totals(farm, plan, hour) {
  const totalHa = farm.fields.reduce((sum, field) => sum + field.area_ha, 0);
  const standingHa = plan.standing_ha.reduce((sum, field) => sum + field[hour], 0);
  const sum = (values) => values.slice(0, hour).reduce((a, b) => a + b, 0);
  return (
    `Cut ${(totalHa - standingHa).toFixed(1)} of ${totalHa} ha · ` +
    `grain value ${money(sum(plan.earned_gbp))} · running costs ${money(sum(plan.running_cost_gbp))}`
  );
}

// One timetable row per combine: what it's doing now and its next move. Clicking a row shows
// that combine's reasons underneath (and its field in the weather block).
function showCombines(farm, cards, selected, onSelect) {
  $("combines").innerHTML = "";
  cards.forEach((card, m) => {
    const row = document.createElement("tr");
    row.className = m === selected ? "selected" : "";
    row.innerHTML = `
      <td class="letter">${escape(farm.machines[m].letter)}</td>
      <td class="now"><span class="dot dot-${card.status}" title="${card.status}"></span>${escape(card.headline)}</td>
      <td class="next">next: ${escape(card.next)}</td>`;
    row.addEventListener("click", () => onSelect(m));
    $("combines").append(row);
  });
  const card = cards[selected];
  const reasons = [card.detail, ...card.why].filter(Boolean).map(escape).join(" · ");
  $("combine-why").innerHTML = `<span class="label">Why ${escape(farm.machines[selected].letter)}</span>${reasons || "Nothing to explain: parked."}`;
}

function showFieldSummary(farm, plan, f, hour) {
  const field = farm.fields[f];
  const every = plan.limits.sensor_every_hours;
  const readingHour = hour - (hour % every);
  const cutPct = Math.round(100 * cutShare(plan, field, f, hour));
  $("field-title").textContent = field.label;
  $("field-summary").textContent =
    `${cropWord(field.crop)} · ${field.area_ha} ha · ${cutPct}% cut · ` +
    `meter ${plan.true_moisture_pct[f][readingHour].toFixed(1)}% at ${plan.labels[readingHour].slice(-5)}`;
}
