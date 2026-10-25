# webapp/: the farmer's control panel

A website around the model in `harvest/`. It only reads from the rest of the repo, never changes it.

```
uv run --group web uvicorn webapp.server:app            then open http://localhost:8000
uv run pytest webapp/tests -q                            tests (no internet, no server)
```

`panel.html?t=30` opens the panel at hour 30 (handy for a demo); add `&layer=chance` for the
"chance it can be cut" colours. Space plays and pauses.
Restart the server after changing Python files (`--reload` hung on Windows); pages and
scripts in `static/` only need a browser refresh.
The map tiles and the chart and map libraries load from the internet; record a backup video.

## What it shows

The season from `run.py` (one forecast member plays the real weather; the planner never sees it),
in three blocks on one screen:

- **Weather** (top left), for the picked field: chance of rain, air humidity, and the grain moisture
  they cause, each as the forecast band from the latest meter reading plus what really happened
- **Farm** (top right): the satellite map of a demo farm near Oxford, combines moving (driving
  between fields, cutting each one in passes up and down), fields shaded by what cutting them is
  worth now; under it one timetable row per combine (now, next move) and the clicked combine's
  reasons (grain moisture, chance of rain, £/ha now against a day later)
- **Plan** (bottom), the decision map: every field × every hour, coloured by what cutting it then is worth (or the
  share of forecasts in which it can be cut), with each combine's route drawn through it: solid
  where it went, dashed where the plan in force sends it. It redraws at every replan, with a line
  saying what the new meter reading changed. The satellite map's fields carry the same colours

One time control drives everything. Next: the farmer changing the plan (send a combine to a field,
skip a field, combine unavailable until..., enter a meter reading), with a preview of what it costs.

## Files

| File | Job |
|---|---|
| `bridge.py` | **the only file that imports `harvest/`**. Runs the season, adds the forecast each plan saw and the share of forecasts in which each field can be cut |
| `tracks.py` | field outline → passes up and down; the season → where each combine is, 6 times an hour |
| `explain.py` | the combine cards and what each replan changed, in words, from the model's numbers |
| `demo_state.py` | the demo farm in memory, as the JSON the browser reads |
| `server.py` | FastAPI: `GET /api/farm`, `GET /api/plan`, and the pages in `static/` |
| `demo.yaml` | numbers only the web app uses (planner, map tiles, playback speed...) |
| `data/demo_farm.geojson` | field outlines and the yard |
| `static/` | `panel.html`, `css/` (tokens + layout), `js/` (map, charts, panel), fonts, favicon |
| `DESIGN.md` | **design rules for everything in `static/`: read before changing a page** |

## The demo's week

`season_start` in `demo.yaml` picks it. A date (e.g. `"2024-06-09"`) replays that week as
`replay.py` does: the ECMWF ensemble forecasts as issued then, and the weather that really
followed (`harvest/past_data.py`; the first start downloads them into `data/`, after that it runs
offline). Empty: today's forecast, `data/forecast.csv`, one member playing the real weather, as
`run.py` does. Either way the panel opens at the hour it is now: a past week is played on today's
date, from today's time of day (`bridge.clock_start`). The tests always use today's forecast, so
they never go online (`tests/conftest.py`).

## The demo farm

`data/demo_farm.geojson` has one MultiPolygon per field and a point for the yard. A field in the
model is a group of real fields around Elveden, Suffolk, traced from the Esri imagery the map shows
(one polygon each, listed in the order a combine works them; their ids, matching
`../data/fields.csv`, are in `"fields"`). Each feature has `"name"` (the field's name in
`params.yaml`, which links them), `"label"` (what the farmer sees) and `"crop"`. To redraw them, open the
file at [geojson.io](https://geojson.io), move the corners onto the hedgerows, keep the properties,
and save it back. The model still uses `area_ha` and `travel_hours` from `params.yaml`, not the
drawn shapes.

## API

`GET /api/farm`: `fields` (label, crop, area, `centre` `[lat, lng]`, outline `[parcel][ring][corner][lat, lng]`, passes as
`[lat, lng]` and `parcel_starts`, where each parcel's passes begin), `yard`, `machines`, `map` settings.

`GET /api/plan`: for every hour: `labels`, `activity` and `place` `[machine][hour]`, `cards`
`[hour][machine]`, `standing_ha` `[field][hour + 1]`, `earned_gbp` and `running_cost_gbp` `[hour]`,
`true_moisture_pct` and `true_rain_mm` `[field][hour]`; `tracks` `[machine][frame][lat, lng]`;
and per replan, `plans`: `hour`, `schedule` `[machine][hour]`, `moisture_pct` `[band][field][hour]`,
`rain_chance`, `value_gbp_per_ha` and `cut_chance` `[field][hour]`, `change` (sentences);
`value_max_gbp_per_ha` (one colour scale for the season).
