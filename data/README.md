# data/: prediction data

`forecast.csv` is the weather forecast the pipeline runs on: 3 possible futures ("members")
for every field and hour. The committed copy is real Met Office MOGREPS-UK ensemble data at 2 km
(`ukmo_uk_ensemble_2km`) from the
[Open-Meteo Ensemble API](https://open-meteo.com/en/docs/ensemble-api), `forecast_hours` from
`forecast_start` in `params.yaml`. The 2 km model covers the UK only and stops about 120 h after
its run time, so keep `forecast_hours` inside that, and at least `season_hours`. For more members,
set `forecast_model: ecmwf_ifs_europe_ensemble` (ECMWF 9 km, 51 members, ~144 h) and download again.

The demo farm is 10,000 acres around Elveden, Suffolk: 312 real fields traced from satellite
imagery, in 20 single-crop groups (the model's fields). A group's `lat`/`lon` is the centre of its
fields; every field's own centre, area and crop is in `data/fields.csv` (`field`, `group`, `crop`,
`area_ha`, `acres`, `lat`, `lon`). The groups are up to 15 km apart, so they fall in different 2 km
grid cells and get slightly different weather. Their `lat`/`lon` only pick the weather; driving
times come from `travel_hours`.

## Format

One row per hour, member and field:

| column | meaning | unit |
|---|---|---|
| `hour` | hours since the forecast start: 0, 1, 2, ... | hours |
| `member` | which forecast member: 0 = main run, 1 to 2 = variations (1 to 50 for ECMWF) | |
| `field` | field name, matching `name` in `params.yaml` | |
| `temp_c` | air temperature at 2 m | °C |
| `humidity_pct` | relative humidity at 2 m | % |
| `rain_mm` | rain during that hour (not snow or showers) | mm |

```
hour,member,field,temp_c,humidity_pct,rain_mm
0,0,barn_field,10.3,75,0.0
```

Every field in `params.yaml` needs the same set of members and hours. Extra columns are ignored.

## Using your own predictions

Either write a CSV in the format above, or change `load_forecast()` in `harvest/forecast.py` to
read your format. It must still return arrays shaped `[member, field, hour]` (see
`harvest/README.md`).

## Getting a fresh forecast (needs internet)

```bash
uv run python data/download_forecast.py
```

This overwrites `forecast.csv` using the fields' `lat`/`lon` and the `forecast_*` settings in
`params.yaml`.

## Past forecasts, for replaying a past season (needs internet the first time)

```bash
uv run python replay.py 2026-10-10 2026-10-25
```

`replay.py` and `compare.py` use the forecasts that were really issued: every 00 UTC run of the ECMWF ensemble
(51 members, 0.25° grid) since 2024-04-01, from the
[dynamical.org archive](https://dynamical.org/catalog/ecmwf-ifs-ens-forecast-15-day-0-25-degree/)
(`past_forecast_dataset` in `params.yaml`, free, no account). A run can only be used
`forecast_delay_hours` after it starts, when ECMWF publishes it. The weather that really happened
comes from Open-Meteo's [historical forecast API](https://open-meteo.com/en/docs/historical-forecast-api)
(`past_weather_model`, the Met Office UK 2 km model, hourly), up to the last full hour before now.
No archive keeps past Met Office UK ensemble runs, so replays plan on ECMWF while the live
forecast is MOGREPS-UK.

Each run is downloaded the first time it's needed (about 5-10 s) and saved in
`data/past_forecasts/<dataset>/<run start>.csv` (not in git), as the archive has it:

| column | meaning | unit |
|---|---|---|
| `member` | 0 = main run, 1 to 50 = variations | |
| `field` | field name, matching `name` in `params.yaml` | |
| `hour` | hours since the run started: 0, 3, 6, ... 144, then every 6 to 360 | hours |
| `lat`, `lon` | the field's position it was downloaded for (moving a field downloads it again) | ° |
| `temp_c` | air temperature at 2 m | °C |
| `dew_point_c` | dew point at 2 m (humidity comes from this and the temperature) | °C |
| `precip_mm_per_s` | precipitation rate, average since the row before (empty at hour 0) | mm/s |

`harvest/past_data.py` turns that into one value per hour: `temp_c`, `humidity_pct` and `rain_mm`
(precipitation from hour `h` to `h + 1`). Next to the runs, `runs.csv` lists the start time of
every run the archive had when it was last opened; it's opened again only when the next run after
the newest one in it (`forecast_run_every_hours` later) could be published before the replay ends.

The weather that happened is saved too, in `data/replay_weather/<model>/<first hour>_<hours>h.csv`
(not in git): one row per season hour and field with `hour`, `member` (0), `field`, `lat`, `lon`,
`temp_c`, `humidity_pct` and `rain_mm`. So replaying the same dates again, once they're over,
needs no internet. Delete either folder any time; it's downloaded again when needed.

## Past weather (needs internet)

```bash
uv run python data/download_past_weather.py                         # past_weather_days before forecast_start
uv run python data/download_past_weather.py 2026-10-01 2026-10-20   # your own dates, inclusive
```

Writes `past_weather.csv`: one row per hour and field, the weather that already happened (one best
estimate, so no `member` column). It comes from the `past_weather_model` in `params.yaml`, the
Met Office UK 2 km model, on the same grid as the forecast. The ensemble API keeps no past data.

| column | meaning | unit |
|---|---|---|
| `time` | UTC hour, e.g. `2026-10-04T00:00` | |
| `field` | field name, matching `name` in `params.yaml` | |
| `temp_c` | air temperature at 2 m | °C |
| `humidity_pct` | relative humidity at 2 m | % |
| `rain_mm` | rain during that hour | mm |

Dates that aren't available are cut off with a warning, and the script stops if nothing is left.
Open-Meteo has about 3 months back to about 2 weeks ahead, and the model itself may keep less.
