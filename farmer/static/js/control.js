// Control: one sentence on where the combines are, each combine with its readings, switches and
// actions, then the plan, alerts and fields. The machine readings and every control here are
// pretend (see DESIGN.md): they answer and remember their state, but don't change the plan yet.

import { $, ACTIVITY, everySecond, fakeSwitch, lamp, load, makeClock, showAlerts, telemetry, toast, topbar } from "./common.js";

const { farm, season } = await load();
const clock = makeClock(farm);
topbar(farm, "control.html", clock);

const fieldOptions = farm.fields.map((field, f) => `<option value="${f}">${field.label}</option>`).join("");
const isNight = (hour) => season.night[hour]; // the demo's night_hours, for the work lights

function showVerdict(hour) {
  const doing = farm.machines.map((_, m) => season.activity[m][hour]);
  const count = (a) => doing.filter((x) => x === a).length;
  const out = doing.length - count("yard");
  const parts = [["cutting", "cutting"], ["driving", "on the road"], ["waiting", "waiting"], ["yard", "at the yard"]]
    .filter(([a]) => count(a))
    .map(([a, words]) => `${count(a)} ${words}`);
  const title = out ? `${out} of ${doing.length} combines out.` : "All combines at the yard.";
  $("#verdict").innerHTML = `<h1><span class="lamp ${out ? "ready" : "done"}"></span>${title}</h1><p>${parts.join(", ")}.</p>`;
}

function machineBlock(m, hour) {
  const machine = farm.machines[m];
  const activity = season.activity[m][hour];
  return `<section class="machine">
    <div class="head"><h2>${machine.label}</h2><span class="label">${ACTIVITY[activity]}</span></div>
    <p class="doing">${activity === "yard" ? "parked" : season.doing[hour][m]}</p>
    <div class="readings" id="readings-${m}"></div>
    ${fakeSwitch(`${m}-engine`, "Engine", activity !== "yard", `${machine.label} engine`)}
    ${fakeSwitch(`${m}-header`, "Header", activity === "cutting", `${machine.label} header`)}
    ${fakeSwitch(`${m}-steer`, "Auto-steer", true, `${machine.label} auto-steer`)}
    ${fakeSwitch(`${m}-lights`, "Work lights", isNight(hour), `${machine.label} work lights`)}
    <div class="actions">
      <select class="button small" id="send-${m}" aria-label="Field">${fieldOptions}</select>
      <button class="button small" data-send="${m}">Send</button>
      <button class="button small secondary" data-toast="${machine.label} heading to the yard.">Yard</button>
      <button class="button small secondary" data-toast="Calling the driver of ${machine.label}…">Call</button>
    </div></section>`;
}

function readings(m, time) {
  const reading = telemetry(farm, season, m, time);
  const low = reading.fuel_pct < 25 ? ` style="color:var(--alarm)"` : "";
  return `<div><b${low}>${Math.round(reading.fuel_pct)}%</b><span>fuel</span></div>
    <div><b>${Math.round(reading.tank_pct)}%</b><span>tank</span></div>
    <div><b>${reading.speed_kmh.toFixed(1)}</b><span>km/h</span></div>`;
}

// a small text toggle in the fields table, remembered while browsing
function flag(key, word, sayOn, sayOff) {
  const on = sessionStorage.getItem(`flag-${key}`) === "1";
  return `<button class="star ${on ? "on" : ""}" data-flag="${key}" data-on="${sayOn}" data-off="${sayOff}">${word}</button>`;
}

function showHour(hour) {
  showVerdict(hour);
  $("#machines").innerHTML = farm.machines.map((_, m) => machineBlock(m, hour)).join("");
  const every = season.limits.sensor_every_hours; // a new moisture reading, a new plan
  $("#plan-age").textContent = `Plan updated ${season.labels[hour - (hour % every)].slice(-5)}.`;
  $("#fields-table").innerHTML = farm.fields
    .map(
      (field, f) => `<tr><td class="name">${field.label}</td><td>${lamp(season.status[f][hour])}</td>
        <td class="r">${season.cut_pct[f][hour]}%</td>
        <td class="r">${flag(`skip-${f}`, "Skip", `${field.label} skipped for now.`, `${field.label} back in the plan.`)}
          ${flag(`first-${f}`, "First", `${field.label} cut first.`, `${field.label} back to normal order.`)}</td></tr>`,
    )
    .join("");
  showAlerts(farm, season, hour);
}

$("#plan-panel").innerHTML = `
  <h2>Plan</h2>
  ${fakeSwitch("plan-auto", "Plan by itself", true, "Automatic plan")}
  ${fakeSwitch("plan-weather", "Follow the forecast", true, "Following the forecast")}
  ${fakeSwitch("plan-night", "Cut at night", false, "Night cutting")}
  <div class="actions" style="margin-top:14px">
    <button class="button" id="replan">Update plan</button>
    <button class="button secondary" data-toast="Plan sent to the ${farm.machines.length} drivers.">Send to drivers</button>
  </div>
  <p class="muted num" id="plan-age" style="margin:10px 0 0;font-size:13px"></p>`;

$("#alerts-panel").innerHTML = `
  <h2>Alerts</h2>
  ${fakeSwitch("alert-rain", "Rain coming", true, "Rain alerts")}
  ${fakeSwitch("alert-ready", "Field ready", true, "Field ready alerts")}
  ${fakeSwitch("alert-moisture", "Moisture over 20%", true, "Moisture alerts")}
  ${fakeSwitch("alert-daily", "Daily summary, 07:00", false, "Daily summary")}
  ${fakeSwitch("alert-sms", "By text message", true, "Text messages")}`;

$("#fields-panel").innerHTML = `
  <h2>Fields</h2>
  <table class="data"><tbody id="fields-table"></tbody></table>`;

// pretend actions with a visible answer
document.addEventListener("click", (event) => {
  const send = event.target.closest("[data-send]");
  if (send) {
    const m = Number(send.dataset.send);
    toast(`${farm.machines[m].label} sent to ${farm.fields[$(`#send-${m}`).value].label}.`);
  }
  const button = event.target.closest("[data-flag]");
  if (button) {
    const on = button.classList.toggle("on");
    sessionStorage.setItem(`flag-${button.dataset.flag}`, on ? "1" : "0");
    toast(on ? button.dataset.on : button.dataset.off);
  }
});
$("#replan").addEventListener("click", () => {
  const button = $("#replan");
  button.disabled = true;
  button.textContent = "Updating…";
  setTimeout(() => {
    button.disabled = false;
    button.textContent = "Update plan";
    toast("Plan up to date.");
  }, 1400);
});

everySecond(clock, season, {
  hour: showHour,
  tick: (time) => farm.machines.forEach((_, m) => ($(`#readings-${m}`).innerHTML = readings(m, time))),
});
