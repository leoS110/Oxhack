// Shared by every page: the data from the server, the live clock, the top strip, alerts, the
// short messages pretend buttons answer with, pretend switches and machine readings.
// What things look like: farmer/DESIGN.md.

export const $ = (selector, root = document) => root.querySelector(selector);

export const ACTIVITY = { cutting: "cutting", driving: "on the road", waiting: "waiting", yard: "at the yard" };

// a status lamp and its word: "ready", "soon", "wait", "done"
export const lamp = (status) => `<span class="lamp ${status}"></span>${status}`;

// a crop in farmer words, after a bar in its outline colour on the map: "wheat", "barley", "OSR"
export const cropWord = (name) => (name === "osr" ? "OSR" : name);
export const crop = (name) => `<span class="crop-line crop-${name}"></span>${cropWord(name)}`;

export async function load() {
  const get = async (url) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} answered ${response.status}`);
    return response.json();
  };
  const [farm, season] = await Promise.all([get("/api/farm"), get("/api/season")]);
  return { farm, season };
}

// ---------- the clock ----------
// Live by default: the season started at farm.season_start_utc, so the hour is "now" minus that.
// ?t=30 starts at hour 30 and ?speed=60 runs faster; both are remembered while browsing
// (sessionStorage) so every page shows the same moment. Clicking the clock goes back to live.
const CLOCK_KEY = "tth-clock";

export function makeClock(farm) {
  const params = new URLSearchParams(location.search);
  if (params.has("t") || params.has("speed")) {
    const start = params.has("t") ? Number(params.get("t")) : realHour(farm);
    const speed = Number(params.get("speed") ?? farm.site.default_speed);
    sessionStorage.setItem(CLOCK_KEY, JSON.stringify({ start, wall: Date.now(), speed }));
  }
  const saved = JSON.parse(sessionStorage.getItem(CLOCK_KEY) ?? "null");
  const clock = () => {
    const hour = saved ? saved.start + ((Date.now() - saved.wall) / 3.6e6) * saved.speed : realHour(farm);
    return Math.max(0, Math.min(hour, farm.hours - 0.001));
  };
  clock.live = !saved;
  return clock;
}

const realHour = (farm) => (Date.now() - Date.parse(farm.season_start_utc)) / 3.6e6;

// "Sun 25 Oct 14:00" plus the minutes of a fractional hour -> "Sun 25 Oct 14:37"
export function clockText(season, time) {
  const label = season.labels[Math.floor(time)];
  return `${label.slice(0, -2)}${String(Math.floor((time % 1) * 60)).padStart(2, "0")}`;
}
export const shortTime = (label) => `${label.slice(0, 3)} ${label.slice(-5)}`;

// ---------- top strip ----------
const PAGES = [
  ["map.html", "Map"], // first: Log in on the home page opens the map
  ["today.html", "Today"],
  ["weather.html", "Weather"],
  ["control.html", "Control"],
];

export function topbar(farm, page, clock) {
  $("#topbar").innerHTML = `
    <a class="label home-link" href="index.html" title="Home">Time to Harvest</a>
    <span class="farm">${farm.name}, ${farm.place}</span>
    <nav class="nav">${PAGES.map(([href, word]) => `<a href="${href}" class="${href === page ? "active" : ""}">${word}</a>`).join("")}</nav>
    <div class="right">
      <span class="clock" id="clock-box" title="${clock.live ? "Live" : "Back to live"}"><span class="mode">${clock.live ? "LIVE" : "REPLAY"}</span><time id="clock"></time></span>
      <button class="alerts-button" id="alerts-button">Alerts</button>
      <div class="dropdown" id="alerts"></div>
    </div>`;
  $("#clock-box").addEventListener("click", () => {
    sessionStorage.removeItem(CLOCK_KEY);
    location.href = location.pathname;
  });
  $("#alerts-button").addEventListener("click", () => $("#alerts").classList.toggle("open"));
  document.body.insertAdjacentHTML("beforeend", `<div class="toasts" id="toasts"></div>`);
  // every pretend button on any page: data-toast="what it did"
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-toast]");
    if (button) toast(button.dataset.toast);
  });
}

// what changed on the farm around this hour, in the Alerts list
export function showAlerts(farm, season, hour, extra = []) {
  const items = [...extra];
  farm.fields.forEach((field, f) => {
    const now = season.status[f][hour];
    const before = season.status[f][Math.max(0, hour - 6)];
    if (now === "done" && before !== "done") items.push(`${field.label} finished.`);
    else if (now === "ready" && before !== "ready") items.push(`${field.label} ready to cut.`);
  });
  $("#alerts").innerHTML = (items.length ? items : ["Nothing new."]).map((text) => `<p>${text}</p>`).join("");
  $("#alerts-button").textContent = items.length ? `Alerts (${items.length})` : "Alerts";
}

export function toast(text) {
  const element = document.createElement("div");
  element.className = "toast";
  element.textContent = text;
  $("#toasts").append(element);
  setTimeout(() => element.remove(), 2600);
}

// ---------- every second: tick(time) always, hour(hour) when the hour changes ----------
export function everySecond(clock, season, { tick, hour }) {
  let shown = -1;
  const step = () => {
    const time = clock();
    $("#clock").textContent = clockText(season, time);
    if (Math.floor(time) !== shown) {
      shown = Math.floor(time);
      hour?.(shown);
    }
    tick?.(time);
  };
  step();
  setInterval(step, 1000);
}

// ---------- pretend machine readings (no real telemetry yet) ----------
// Worked out from what the combine did up to this hour, so they move believably.
export function telemetry(farm, season, m, time) {
  const fake = farm.site.fake_telemetry;
  const hour = Math.floor(time);
  let engineSinceYard = 0;
  let cuttingHours = 0;
  for (let h = 0; h <= hour; h++) {
    const activity = season.activity[m][h];
    if (activity === "yard") engineSinceYard = 0;
    if (activity === "cutting" || activity === "driving") engineSinceYard += 1;
    if (activity === "cutting") cuttingHours += h < hour ? 1 : time % 1;
  }
  const tonnes = cuttingHours * farm.machines[m].ha_per_hour * fake.yield_t_per_ha;
  const activity = season.activity[m][hour];
  const wobble = Math.sin(Date.now() / 1700 + m) * 0.4; // a little movement, like a real gauge
  const speed = activity === "cutting" ? fake.cutting_kmh + wobble : activity === "driving" ? fake.road_kmh + 3 * wobble : 0;
  return {
    fuel_pct: Math.max(12, 100 - fake.fuel_pct_per_engine_hour * engineSinceYard),
    tank_pct: activity === "yard" ? 0 : ((tonnes % fake.tank_t) / fake.tank_t) * 100,
    speed_kmh: speed,
  };
}

// A pretend switch: a hairline row with an ON / OFF box, remembered while browsing so the next
// update doesn't flip it back. say: what the message calls it, if the label alone is ambiguous.
export function fakeSwitch(key, label, initial, say = label) {
  const saved = sessionStorage.getItem(`switch-${key}`);
  const on = saved === null ? initial : saved === "1";
  return `<label class="switch"><span>${label}</span>
    <input type="checkbox" data-switch="${key}" data-label="${say}" ${on ? "checked" : ""}><span class="toggle"></span></label>`;
}
document.addEventListener("change", (event) => {
  const input = event.target.closest("[data-switch]");
  if (!input) return;
  sessionStorage.setItem(`switch-${input.dataset.switch}`, input.checked ? "1" : "0");
  toast(`${input.dataset.label}: ${input.checked ? "on" : "off"}.`);
});
