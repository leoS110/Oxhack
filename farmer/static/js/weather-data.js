// Real weather, fetched by the browser: Open-Meteo forecasts (no key) and RainViewer radar
// (no key). Kept in sessionStorage for site.cache_minutes so pages don't ask again and again.

const OPEN_METEO = "https://api.open-meteo.com/v1/forecast";
const HISTORICAL = "https://historical-forecast-api.open-meteo.com/v1/forecast";
const RAINVIEWER = "https://api.rainviewer.com/public/weather-maps.json";

async function cached(key, minutes, fetcher) {
  const saved = JSON.parse(sessionStorage.getItem(key) ?? "null");
  if (saved && Date.now() - saved.at < minutes * 60000) return saved.data;
  const data = await fetcher();
  try {
    sessionStorage.setItem(key, JSON.stringify({ at: Date.now(), data }));
  } catch {
    // storage full: fine, just fetch again next time
  }
  return data;
}

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`weather service answered ${response.status}`);
  return response.json();
}

// A demo replaying a past week (farm.week, from source.py) shows that week's weather: the record of
// the model its real weather comes from, on those days, at today's time of day. The record has no
// chance of rain, so that comes from the demo's own forecast. Live radar has no past, so pages hide it.
let dayShift = 0; // days from today to the date shown: 0 live, negative for a past week
function source(farm) {
  dayShift = farm.week?.day_shift_days ?? 0;
  const week = farm.week;
  if (!week) return { base: OPEN_METEO, span: null, key: "live" };
  return { base: HISTORICAL, span: `&start_date=${week.start_date}&end_date=${week.end_date}&models=${week.model}`, key: week.start_date };
}

// fill the chance of rain the archive lacks: hourly from the demo's forecast, daily the highest hour
function addRainChance(data, chance) {
  data.hourly.precipitation_probability = data.hourly.time.map((time) => chance[time] ?? null);
  data.daily.precipitation_probability_max = data.daily.time.map((day) => {
    const hours = data.hourly.time.filter((time) => time.startsWith(day) && chance[time] !== undefined);
    return hours.length ? Math.max(...hours.map((time) => chance[time])) : null;
  });
  return data;
}

// The forecast at the farm: hourly for the next days and a daily summary.
export function farmForecast(farm) {
  const { base, span, key } = source(farm);
  const [lat, lng] = farm.centre;
  const url =
    `${base}?latitude=${lat}&longitude=${lng}&timezone=Europe%2FLondon${span ?? `&forecast_days=${farm.site.forecast_days}`}` +
    "&hourly=temperature_2m,precipitation,precipitation_probability,relative_humidity_2m,wind_speed_10m,wind_direction_10m,weather_code" +
    "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max";
  return cached(`weather-farm-${key}`, farm.site.cache_minutes, async () => {
    const data = await getJSON(url);
    return farm.week ? addRainChance(data, farm.week.rain_chance_pct) : data;
  });
}

// The forecast on a grid of points around the farm, for drawing weather maps.
// returns {n, lats [n] north to south, lngs [n] west to east, times [hour], values: {name: [point][hour]}}
export function gridForecast(farm) {
  const { grid_points: n, grid_km: km, forecast_hours: hours } = farm.site;
  const { base, span, key } = source(farm);
  const [lat0, lng0] = farm.centre;
  const dLat = km / 111 / (n - 1);
  const dLng = km / (111 * Math.cos((lat0 * Math.PI) / 180)) / (n - 1);
  const lats = Array.from({ length: n }, (_, i) => +(lat0 + km / 222 - i * dLat).toFixed(3));
  const lngs = Array.from({ length: n }, (_, j) => +(lng0 - (km / 222) / Math.cos((lat0 * Math.PI) / 180) + j * dLng).toFixed(3));
  const points = lats.flatMap((lat) => lngs.map((lng) => [lat, lng])); // row by row, north first
  const names = ["precipitation", "temperature_2m", "wind_speed_10m", "wind_direction_10m", "cloud_cover"];
  const url =
    `${base}?latitude=${points.map((p) => p[0]).join(",")}&longitude=${points.map((p) => p[1]).join(",")}` +
    `&hourly=${names.join(",")}${span ?? `&forecast_hours=${hours}`}&timezone=Europe%2FLondon`;
  return cached(`weather-grid-${key}`, farm.site.cache_minutes, async () => {
    const answers = await getJSON(url); // one answer per point, in the order asked
    const values = Object.fromEntries(names.map((name) => [name, answers.map((a) => a.hourly[name])]));
    return { n, lats, lngs, times: answers[0].hourly.time, values };
  });
}

// Radar frames from the last two hours: [{time: Date, url: tile template}]
export async function radarFrames() {
  const data = await getJSON(RAINVIEWER);
  return data.radar.past.map((frame) => ({
    time: new Date(frame.time * 1000),
    url: `${data.host}${frame.path}/256/{z}/{x}/{y}/2/1_1.png`, // colour scheme 2, smoothed, with snow
  }));
}

// The index of the current hour in an Open-Meteo hourly time list (local times, "2026-10-25T14:00").
// For a past week: the same time of day, dayShift calendar days away (so summer time doesn't shift it)
export function nowIndex(times) {
  const local = new Date(new Date().toLocaleString("en-US", { timeZone: "Europe/London" })); // wall clock
  const day = new Date(Date.UTC(local.getFullYear(), local.getMonth(), local.getDate() + dayShift));
  const pad = (n) => String(n).padStart(2, "0");
  const key = `${day.getUTCFullYear()}-${pad(day.getUTCMonth() + 1)}-${pad(day.getUTCDate())}T${pad(local.getHours())}:00`;
  return Math.max(0, times.indexOf(key));
}

// WMO weather codes -> a word, and whether it's wet
export function weatherWord(code) {
  if (code <= 1) return { word: "clear", wet: false };
  if (code === 2) return { word: "some cloud", wet: false };
  if (code === 3) return { word: "overcast", wet: false };
  if (code <= 48) return { word: "fog", wet: false };
  if (code <= 57) return { word: "drizzle", wet: true };
  if (code <= 67 || (code >= 80 && code <= 82)) return { word: "rain", wet: true };
  if (code <= 77 || code === 85 || code === 86) return { word: "snow", wet: true };
  return { word: "thunder", wet: true };
}

// Colour scales for the weather maps: [value, [r, g, b, alpha 0-255]] stops, interpolated between
export const SCALES = {
  precipitation: {
    word: "Rain", unit: "mm/h",
    stops: [[0, [120, 180, 255, 0]], [0.1, [140, 200, 255, 120]], [0.5, [47, 128, 237, 170]], [2, [20, 60, 180, 200]], [6, [130, 40, 170, 220]]],
  },
  temperature_2m: {
    word: "Temperature", unit: "°C",
    stops: [[-5, [70, 90, 200, 170]], [3, [60, 160, 230, 170]], [9, [70, 190, 120, 170]], [14, [240, 200, 60, 170]], [20, [240, 130, 40, 170]], [28, [210, 50, 50, 170]]],
  },
  wind_speed_10m: {
    word: "Wind", unit: "km/h",
    stops: [[0, [220, 240, 230, 60]], [10, [120, 210, 190, 130]], [25, [40, 160, 170, 170]], [45, [90, 70, 190, 190]], [70, [170, 40, 140, 210]]],
  },
  cloud_cover: {
    word: "Clouds", unit: "%",
    stops: [[0, [255, 255, 255, 0]], [40, [235, 238, 242, 90]], [80, [200, 205, 212, 170]], [100, [160, 166, 175, 210]]],
  },
};

export function colourAt(scale, value) {
  const stops = scale.stops;
  if (value <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    const [v1, c1] = stops[i];
    const [v0, c0] = stops[i - 1];
    if (value <= v1) return c0.map((c, k) => c + ((c1[k] - c) * (value - v0)) / (v1 - v0));
  }
  return stops.at(-1)[1];
}

// A smooth coloured picture of one variable at one hour, stretched over the grid's area.
export function gridImage(grid, name, hour, size = 200) {
  const { n } = grid;
  const values = grid.values[name].map((series) => series[hour]); // [point], row by row
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const context = canvas.getContext("2d");
  const image = context.createImageData(size, size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      // position in grid units, then blend the four surrounding points
      const gy = (y / (size - 1)) * (n - 1);
      const gx = (x / (size - 1)) * (n - 1);
      const [i, j] = [Math.min(Math.floor(gy), n - 2), Math.min(Math.floor(gx), n - 2)];
      const [fy, fx] = [gy - i, gx - j];
      const at = (r, c) => values[r * n + c];
      const value =
        at(i, j) * (1 - fx) * (1 - fy) + at(i, j + 1) * fx * (1 - fy) + at(i + 1, j) * (1 - fx) * fy + at(i + 1, j + 1) * fx * fy;
      const colour = colourAt(SCALES[name], value);
      image.data.set(colour.map(Math.round), (y * size + x) * 4);
    }
  }
  context.putImageData(image, 0, 0);
  return canvas.toDataURL();
}

// map corners of the grid picture: [[south, west], [north, east]]
export const gridBounds = (grid) => [[grid.lats.at(-1), grid.lngs[0]], [grid.lats[0], grid.lngs.at(-1)]];

export function legend(name) {
  const scale = SCALES[name];
  const colours = scale.stops.map(([, c]) => `rgba(${c[0]},${c[1]},${c[2]},${Math.max(0.35, c[3] / 255)})`);
  return `<span>${scale.stops[0][0]}</span><span class="scale" style="background:linear-gradient(90deg,${colours.join(",")})"></span><span>${scale.stops.at(-1)[0]} ${scale.unit}</span>`;
}

// wind arrows at every grid point for one hour (they point where the wind blows to)
export function windArrows(grid, hour) {
  return grid.lats.flatMap((lat, i) =>
    grid.lngs.map((lng, j) => {
      const k = i * grid.n + j;
      const from = grid.values.wind_direction_10m[k][hour];
      const speed = grid.values.wind_speed_10m[k][hour];
      const size = 13 + Math.min(14, speed / 2); // stronger wind, bigger arrow
      return L.marker([lat, lng], {
        interactive: false,
        icon: L.divIcon({
          className: "wind-arrow",
          html: `<span style="font-size:${size}px;display:inline-block;transform:rotate(${from + 180}deg)">↑</span>`,
          iconSize: [30, 30],
        }),
      });
    }),
  );
}
