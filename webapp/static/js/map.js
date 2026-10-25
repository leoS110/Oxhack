// The satellite map: field outlines, the cut area growing pass by pass, and the combines moving.
// Positions come from the server (webapp/tracks.py); here we only draw them.

import { colours, cropColour, cropWord } from "./theme.js";

const EARTH_CIRCUMFERENCE_M = 40075016.686; // at the equator, as used by web map tiles

export function createMap(element, farm, plan, onSelectField) {
  const map = L.map(element, { zoomSnap: 0.25, scrollWheelZoom: false });
  L.tileLayer(farm.map.tiles_url, { attribution: farm.map.attribution, maxZoom: 19 }).addTo(map);

  // outline colour = crop (rain and too wet take it over while they last); fill = what cutting is worth
  const outlines = farm.fields.map((field, f) =>
    L.polygon(field.outline, { color: cropColour(field.crop), weight: 2, fillOpacity: 0 })
      .bindTooltip("", { direction: "center", className: "field-tag" })
      .on("click", () => onSelectField(f))
      .addTo(map),
  );
  const farmBounds = L.featureGroup(outlines).getBounds();
  map.fitBounds(farmBounds, { padding: [8, 8] });
  // the map's box can change size as the page lays out (fonts, charts): keep the farm in view
  new ResizeObserver(() => {
    map.invalidateSize();
    map.fitBounds(farmBounds, { padding: [8, 8] });
  }).observe(element);

  // the cut part of each field: the passes driven so far, one pass wide, pale like stubble
  const stubble = farm.fields.map(() =>
    L.polyline([], { color: colours.paper, opacity: 0.75, lineCap: "butt", interactive: false }).addTo(map),
  );
  const setPassWidth = () => stubble.forEach((line) => line.setStyle({ weight: passWidthPixels(map, farm) }));
  map.on("zoomend", setPassWidth);
  setPassWidth();

  // the yard and the combines get their own layer above the field labels (tooltip pane: 650)
  map.createPane("combines").style.zIndex = 700;
  // the label sits just west of the yard; combines park in a row to the south-east (tracks.py)
  const yardIcon = L.divIcon({ className: "yard-tag", html: "Yard", iconSize: [40, 18], iconAnchor: [46, 9] });
  L.marker(farm.yard, { icon: yardIcon, pane: "combines", interactive: false }).addTo(map);
  const combines = farm.machines.map((machine) =>
    L.marker(farm.yard, {
      icon: L.divIcon({ className: "combine", html: machine.letter, iconSize: [22, 22] }),
      pane: "combines",
      keyboard: false,
    })
      .bindTooltip(machine.label, { direction: "top", offset: [0, -12], className: "field-tag" })
      .addTo(map),
  );

  // every animation frame: combine positions and cut areas at a time in hours (fractions allowed)
  function moveTo(time) {
    const frame = time * plan.steps_per_hour;
    plan.tracks.forEach((track, m) => {
      const i = Math.min(Math.floor(frame), track.length - 2);
      const share = frame - i;
      const [a, b] = [track[i], track[i + 1]];
      combines[m].setLatLng([a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share]);
    });
    farm.fields.forEach((field, f) => stubble[f].setLatLngs(pathUpTo(field, cutShare(plan, field, f, time))));
  }

  // once an hour: field colours and labels, combine borders.
  // fieldNow(f) -> {colour: the decision map's colour this hour or null, text: "£1,180/ha"}
  function showHour(hour, selectedField, fieldNow) {
    farm.fields.forEach((field, f) => {
      const raining = plan.true_rain_mm[f][hour] > plan.limits.rain_mm;
      const tooWet = plan.true_moisture_pct[f][hour] > plan.limits.too_wet_pct;
      const cutPct = Math.round(100 * cutShare(plan, field, f, hour));
      outlines[f].setStyle({
        color: raining ? colours.rain : tooWet ? colours.alarm : cropColour(field.crop),
        dashArray: raining ? "6 4" : null,
        weight: f === selectedField ? 3.5 : raining || tooWet ? 2.5 : 2,
        fillColor: fieldNow(f).colour ?? colours.paper,
        fillOpacity: fieldNow(f).colour ? 0.6 : 0,
      });
      const state = cutPct >= 100 ? "all cut" : raining ? "raining" : tooWet ? "too wet" : fieldNow(f).text;
      outlines[f].setTooltipContent(`${field.short} · ${cropWord(field.crop)} · ${state}`);
      outlines[f].getTooltip().options.permanent = f === selectedField; // one label; the rest on hover
      if (f === selectedField) outlines[f].openTooltip();
      else outlines[f].closeTooltip();
    });
    combines.forEach((marker, m) => marker.getElement()?.setAttribute("data-activity", plan.activity[m][hour]));
  }

  return { moveTo, showHour };
}

// Share of a field cut (0-1) at a time in hours, between the hourly values from the server.
export function cutShare(plan, field, f, time) {
  const hour = Math.min(Math.floor(time), plan.hours - 1);
  const standing = plan.standing_ha[f];
  const now = standing[hour] + (standing[hour + 1] - standing[hour]) * (time - hour);
  return 1 - now / field.area_ha;
}

// The passes driven once `share` of the field is cut: the path up to that distance, one line per
// parcel so the stubble doesn't jump across the hedges and woods between them.
function pathUpTo(field, share) {
  const metres = share * field.path_m[field.path_m.length - 1];
  if (metres <= 0) return [];
  let i = 0;
  while (i < field.path_m.length - 2 && field.path_m[i + 1] <= metres) i++;
  const step = field.path_m[i + 1] - field.path_m[i];
  const t = step > 0 ? Math.min(1, (metres - field.path_m[i]) / step) : 0;
  const [a, b] = [field.path[i], field.path[i + 1]];
  const driven = [...field.path.slice(0, i + 1), [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]];
  const starts = field.parcel_starts;
  return starts.map((start, p) => driven.slice(start, starts[p + 1])).filter((line) => line.length > 1);
}

// How many screen pixels one pass (header width) covers at the current zoom.
function passWidthPixels(map, farm) {
  const lat = map.getCenter().lat;
  const metresPerPixel = (EARTH_CIRCUMFERENCE_M * Math.cos((lat * Math.PI) / 180)) / (256 * 2 ** map.getZoom());
  return farm.map.pass_width_m / metresPerPixel;
}
