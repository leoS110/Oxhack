# farmer/: the website for farmers

The same simulated season as the demo control panel (`webapp/`), shown the way a farmer wants it:
in the demo panel's look (one brand: its colours and fonts are read from `webapp/static`), few
words, no algorithm in sight. A home page, then four dashboard pages, phone and desktop.

```
uv run --group web uvicorn farmer.server:app --port 8001    then open http://localhost:8001
uv run pytest farmer/tests -q                               tests (no internet, no server)
```

Runs next to the demo (port 8000). Restart the server after changing Python files or `site.yaml`.

## Pages

| Page | What it shows |
|---|---|
| **Home** (`index.html`) | The title, the slogan and **Log in**, which opens the dashboard on the Map (no account check), next to b-roll of the harvest: three aerial clips of machines at work, no people in shot, taking turns, each with its credit (`static/media/CREDITS.md`) |
| **Map** (`map.html`) | Satellite map, fields filled by status and outlined by crop (labels that would overlap hide until you zoom in), combines moving along their GPS tracks with a trail, a card per combine (readings, Send there / Back to yard / Call driver). Layer switches for live rain radar, rain forecast and wind, which zoom out to the area around the farm |
| **Today** (`today.html`) | One big answer (Cut now / Wait a little / Not today), the real weather now and for the next 12 h, every field (Ready / Soon / Wait / Done, % cut, grain moisture, which combine the plan sends and when, best time), every combine (what it's doing, next move), and two charts of the plan in force on one hour axis: what each field is worth hour by hour (with where each combine is sent) and where each combine goes (done so far, then the plan) |
| **Weather** (`weather.html`) | Forecast maps (rain, temperature, wind, clouds, radar) with a time slider and play, the next 7 days, and the next 48 h rated for harvesting (good / fair / poor) with the best window |
| **Control** (`control.html`) | Each combine: fuel, grain tank, speed, switches (engine, header, auto-steer, lights), Send / Yard / Call. Plan switches and Update plan, alert switches, fields with Skip and priority |

## Real and pretend

- **Real:** the weather. The browser fetches it live from [Open-Meteo](https://open-meteo.com)
  (forecast at the farm and on a 9 x 9 grid around it, for the maps) and
  [RainViewer](https://www.rainviewer.com/api.html) (radar, last 2 hours, up to zoom 7). No keys.
  When the demo replays a past week (`season_start` in `webapp/demo.yaml`), it shows that week's
  real weather instead: Open-Meteo's record of the Met Office 2 km model the demo's weather comes
  from, with the chance of rain from the demo's own forecast (the record has none). Radar only
  exists live, so its switches are hidden then.
- **From the model:** fields, combines and GPS tracks come from the simulated season through
  `source.py`, which reads `webapp.demo_state`.
- **Pretend:** machine readings (fuel, tank, speed) and every button and switch on the control
  panel. They respond (a switch stays switched, a toast says what happened) but don't change the plan.

On today's forecast, one forecast member plays the real weather (see the demo's caveats), so a
field can show "Wait" while the real sky is dry. A past week plays the weather that really
followed, the same record the weather pages show.

## The clock

The site follows real time through the season. On today's forecast, `forecast_start` in
`params.yaml` is hour 0. A past week is played on today's date: the clock shows the week's date at
today's time of day (at 08:20 it says Sun 09 Jun 08:20) and runs on from there. `?t=30` starts at
hour 30 and `&speed=60` runs 60 times faster; both are remembered while you browse. Click the red
clock to go back to live. The weather always shows the live hour.

## Files

| File | Job |
|---|---|
| `source.py` | **the only file importing the rest of the repo** (`webapp.demo_state`, read-only): the season in farmer terms |
| `server.py` | FastAPI: `GET /api/farm`, `GET /api/season`, the pages in `static/`, and the demo's colours and fonts at `/brand/` (read-only from `webapp/static`) |
| `site.yaml` | every number the site uses (status rules, weather grid, map, pretend readings) |
| `DESIGN.md` | **the site's design rules (on top of `webapp/DESIGN.md`): status lamps, one sentence per page, tables. Read before changing a page** |
| `static/js/common.js` | top bar, clock, alerts, toasts, pretend switches and readings |
| `static/js/weather-data.js` | fetching the real weather, colour scales, drawing forecast maps |
| `static/js/today.js`, `livemap.js`, `weather.js`, `control.js` | one per page |
| `static/js/plan-charts.js` | Today's two plan charts, plain SVG in the demo's colours |
