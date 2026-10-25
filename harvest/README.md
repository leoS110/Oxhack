# harvest/: the pipeline

Four files, one job each, plus `past_data.py` for replaying a past season. To swap a piece out,
write your own version of the function with the **same inputs and outputs** listed below, and
everything else keeps working.

Letters used for array sizes (numbers are for the demo):

| Letter | Meaning | Demo |
|---|---|---|
| `M` | forecast members (possible futures) | 3 in the CSV, 2 after one is held back as "truth"; 51 in past runs |
| `F` | fields, in the order of `fields` in `params.yaml` | 6 |
| `K` | machines, in the order of `machines` in `params.yaml` | 3 |
| `H` | hours | 168 in the CSV, 48 per plan, 48 in the season |

`params` is always the dict loaded from `params.yaml`.

---

## 1. `forecast.py`: `load_forecast(path, params)`

Reads the prediction data.

| | Name | Type / shape | Meaning |
|---|---|---|---|
| in | `path` | file path | the CSV (format in `data/README.md`) |
| in | `params` | dict | uses `params["fields"]` for the field order |
| out | `weather` | dict of 3 arrays | `weather["rain_mm"]`: `[M, F, H]` rain per hour, mm |
| | | | `weather["humidity_pct"]`: `[M, F, H]` relative humidity, % |
| | | | `weather["temp_c"]`: `[M, F, H]` air temperature, °C (lets `predict_moisture` re-run the moisture model from each reading) |

Optional third input **`moisture_path`**: `outputs/moisture_forecast.csv` (written by
`scripts/moisture_forecast.py`). Adds `weather["moisture_pct"]`: `[M, F, H]` grain moisture
(% wet basis, end of each hour), and the value function then slides those curves to each reading
instead of re-running the model (see below). `run.py` no longer passes it: sliding a curve worked
out from a different starting moisture gives the wrong shape. It must come from the same
`forecast.csv`, or loading stops with an error.

Also **`load_moisture(path, params)`**: reads just that CSV, out `[M, F, H]`, each field taking the
column of its own crop. Feed it to `drying_cost_gbp_per_t` (see `scripts/plot_drying_cost.py`).

## 2. `value.py`: `field_values(weather, start_moisture, params, first_hour=0)`

The value function: what is a field worth if we harvest it in a given hour?

| | Name | Type / shape | Meaning |
|---|---|---|---|
| in | `weather` | dict of `[M, F, H]` arrays | the forecast for the hours being planned |
| in | `start_moisture` | `[F]` | latest grain moisture reading per field, % |
| in | `first_hour` | int | season hour of the first column (for the "crop loss over time" part) |
| out | `value` | `[F, H]` | expected £ per hectare if harvested in that hour, averaged over members. 0 means "can't or shouldn't harvest" |

Uses a helper, **`predict_moisture(start_moisture, weather, params)`**:
in `[F]` and `[M, F, H]`, out `[M, F, H]` grain moisture (%) each hour.
The simulator also uses it to get the "true" moisture that the sensor reads.
With `weather["temp_c"]` (`run.py`, and the past forecasts of `replay.py`) it runs the grain
moisture model of `moisture.py` from the reading. With `weather["moisture_pct"]` instead (only when
a moisture CSV is passed to `load_forecast`) it slides each member's precomputed curve to start at
the reading, keeping its hour-to-hour change; that is only right if the curve started near the
reading. Without either, a simple model (dries with low humidity, wets with rain).

And **`member_values(...)`**: same inputs, out `[M, F, H]`, the value in each member before
averaging. `field_values` is its average over members.

How a value is worked out, per member, field and hour:
`delivered_tonnes_per_ha × (price − drying_cost_gbp_per_t)`, shrunk by `loss_pct_per_hour` for
every hour the crop has stood, and 0 in rain or when too wet to combine.
- **`delivered_tonnes_per_ha(moisture, params)`**: `[M, F, H]` → `[M, F, H]`. `yield_t_per_ha` is
  quoted at `yield_moisture_pct`; the dry matter is fixed, so drier grain weighs less and wetter
  grain more. UK terms pay no bonus for dry grain, so the lost weight is lost money.
- **`drying_cost_gbp_per_t(moisture, params)`**: Camgrain's charge per 0.5% band above the crop's
  `free_drying_up_to_pct`, plus the weight the store keeps for drying down to its
  `target_moisture_pct`. Both are per crop: cereals 15% and 14%, oilseed rape 9% and 9%.
- Value peaks at 14-14.5% for cereals and 9% for oilseed rape, and falls on both sides.

And **`start_moisture(weather, params)`**: in `[M, F, H]`, out `[F]` grain moisture (%) of each
field at hour 0: hour 0 of `weather["moisture_pct"]` averaged over members, or otherwise (`run.py`,
`replay.py`) the `start_moisture_pct` of each field's crop under `moisture_model` in `params.yaml`
(placeholders until there is a real reading).

## 3. `planners/`: `plan_*(value, state, params)`

Decides which field each machine works on, hour by hour. One file per architecture, each with a
short README on the method:

| File | Planners | Notes |
|---|---|---|
| `greedy.py` | `plan_vgreedy`, `plan_urgencygreedy`, `plan_urgencygreedy_withlookahead` | `README_greedy.md` |
| `lagrangian.py` | `plan_lagrangian` | `README_lagrangian.md` |
| `mpc.py` | `plan_mpc` | `README_mpc.md` |
| `farmer.py` | `plan_farmer` | `README_farmer.md`: the baseline, a farmer's 12-hour day decided each morning from the moisture reading and a look at the rain forecast |

Every planner except `plan_mpc` and `plan_farmer` ends with `send_idle_to_depot` (`common.py`): a
combine with a gap in cutting waits at the depot when that costs less than standing in a field
(costs in `params.yaml`, `depot_when_idle: false` to switch it off). `plan_mpc` weighs the same
costs itself; `plan_farmer` goes home every night, on wet days, and when nothing is fit to cut.

`from harvest.planners import plan_mpc` works for any of them. A new planner goes in its own file
and in `__all__` in `planners/__init__.py`, which is what `compare.py` runs. Every planner has the
same inputs and outputs:

| | Name | Type / shape | Meaning |
|---|---|---|---|
| in | `value` | `[F, H]` | from `field_values` |
| in | `state["remaining_ha"]` | `[F]` | hectares still standing in each field |
| in | `state["machine_at"]` | `[K]` int | where each machine is: field index `0..F-1`, or `F` = depot |
| in | `state["value_by_member"]` | `[M, F, H]` | optional: £/ha in each forecast member (`value` is their average). `simulate.py` passes it; only `plan_mpc` uses it |
| in | `state["fuel_litres"]` | `[K]` | optional: diesel left in each combine's tank, telemetry from the simulator (tank sizes: `fuel_tank_litres` under `machines`). `simulate.py` passes it; no planner uses it yet |
| in | `state["moisture_pct"]` | `[F]` | optional: the latest grain moisture reading of each field, %. `simulate.py` passes it; only `plan_farmer` uses it (and needs it) |
| in | `state["hour_of_day"]` | int | optional: hour of the day (UTC, 0-23) of the plan's first column. `simulate.py` passes it; only `plan_farmer` uses it (and needs it) |
| in | `state["rain_mm"]` | `[M, F, H]` | optional: the forecast's rain for the plan hours, mm. `simulate.py` passes it; only `plan_farmer` uses it (and needs it) |
| out | `schedule` | `[K, H]` int | field index each machine should work on each hour, `F` = go to the depot, or `-1` = wait where it is |

The planner only says where machines should be. Driving time, harvesting and fuel happen in the
simulator. The planners don't plan refuelling yet: the simulator sends a combine home when its tank
runs low, so `follow_plan` (`common.py`) can differ from what really happens.

## 4. `simulate.py`: the replanning loop that ties everything together

Two ways in, with the same result:

- **`run_season(weather, params, planner=plan_urgencygreedy)`**: one forecast that never changes
  (`run.py`). It holds back forecast member `truth_member` as "the weather that
  really happens"; the planner never sees it. The season is `season_hours` long.
- **`run_with_forecasts(truth, get_forecast, params, planner=plan_urgencygreedy, start_hour_utc=0)`**:
  the forecast is updated as the season goes (`replay.py`, `compare.py`, with
  `past_data.past_rollout`). `start_hour_utc` is the hour of the day of season hour 0, so planners
  can tell the time (`state["hour_of_day"]`); `run_season` takes it from `forecast_start`.

| | Name | Type / shape | Meaning |
|---|---|---|---|
| in | `weather` | dict of `[M, F, H]` | `run_season`: from `load_forecast` |
| in | `truth` | dict of `[1, F, H]` | `run_with_forecasts`: the weather that really happens, one column per season hour (the season is this long) |
| in | `get_forecast` | function | `run_with_forecasts`: `get_forecast(hour)` gives the newest forecast at that season hour, dict of `[M, F, H]` starting at that hour, at least `plan_hours + forecast_every_hours` long |
| in | `planner` | function | any `plan_*` function (default: `plan_urgencygreedy`) |
| out | `result["total_gbp"]` | float | money earned over the season |
| out | `result["earned_gbp"]` | `[H]` | money earned each hour |
| out | `result["running_cost_gbp"]` | `[K, H]` | what running each machine cost each hour (see below) |
| out | `result["cost_breakdown_gbp"]` | dict of floats | `{"diesel", "operator", "escort", "repairs"}`: £ over the season |
| out | `result["net_gbp"]` | float | money earned minus running costs: what `compare.py` ranks planners by |
| out | `result["cut_ha"]` | `[K, H]` | hectares each machine cut each hour |
| out | `result["harvesting"]` | `[K, H]` int | field each machine harvested each hour, `-1` = driving or waiting |
| out | `result["place"]` | `[K, H]` int | where each machine is, or is driving to: field index, `F` = depot |
| out | `result["driving"]` | `[K, H]` bool | True while the machine is on the road |
| out | `result["fuel_litres"]` | `[K, H]` | diesel left in each tank at the end of each hour |
| out | `result["remaining_ha"]` | `[F]` | hectares left standing at the end |
| out | `result["plans"]` | list | one per replan: `{"hour", "value" [F, H], "schedule" [K, H]}` |

What it does:
1. Every `sensor_every_hours` it reads the true grain moisture (the "sensor reading").
2. Every `forecast_every_hours` it calls `get_forecast` for the newest forecast.
3. After either one, it replans: `member_values` on the forecast from that hour, starting from the
   latest reading, then the planner for a new schedule.
4. Every hour, each machine follows the schedule:
   - a new field (or the depot) means driving there first (`travel_hours`, rounded up to whole
     hours); a machine already driving finishes the trip before it follows a new plan;
   - at the depot it refuels (`depot_refuel_litres_per_hour`, up to `fuel_tank_litres`);
   - at its field it cuts `ha_per_hour`, only if the true value that hour is above 0, until the field is empty;
   - money = hectares cut × the true £/ha.
   - Fuel, a layer above the planners: every combine starts with a full tank and burns the same
     litres the running costs charge for (`cutting_diesel_litres_per_ha`,
     `road_diesel_litres_per_hour`; none while waiting, engine off). Each hour it isn't on the
     road, **`must_refuel(fuel_litres, here, going_to, machine, travel, params)`** checks the tank
     covers the trip the plan asks for, an hour of cutting and the drive home with
     `fuel_reserve_pct` of the tank left. If not, the combine goes to the depot whatever the plan
     says, and stays until it has enough. So no tank drops below the reserve. The fuel left is
     passed to the planner as `state["fuel_litres"]` at every replan.
5. At the end it works out the running costs from what each machine did each hour, with
   `running_costs` in `params.yaml` (UK numbers, with sources). These are the farm's real costs,
   not the planners' own `idle_in_field_gbp_per_hour` and `drive_gbp_per_hour`, so every planner is
   scored the same way whatever it planned with:

   | Doing | Costs per hour | About |
   |---|---|---|
   | cutting | diesel per ha cut + operator + repairs | £35/ha at 2.5 ha/h |
   | driving | diesel per hour + operator + road escort + repairs | £95/h |
   | waiting in a field | operator on paid standby (engine off) | £19/h |
   | at the depot | nothing: the operator is stood down (the diesel pumped in was paid for as it burned) | £0 |

Two helpers, both `[machine, hour]` in and out:

| Function | In | Out |
|---|---|---|
| `activities(cut_ha, driving, place, n_fields)` | `cut_ha` `[K, H]`, `driving` `[K, H]`, `place` `[K, H]` from `result` | `{"cutting", "driving", "waiting", "depot"}`: `[K, H]` bool each, exactly one True per hour |
| `running_costs_gbp(cut_ha, driving, place, params)` | the same | `{"diesel", "operator", "escort", "repairs"}`: `[K, H]` £ each |

## 4b. `past_data.py`: the forecasts that were really issued, for `replay.py` and `compare.py`

| Function | In | Out |
|---|---|---|
| `rollout_days(args, params)` | command-line arguments | `(start, end)`: the first two arguments as days, or `rollout_start`/`rollout_end` |
| `past_rollout(start, end, params)` | first and last day | `(truth, get_forecast, first_hour, runs_used)` for `run_with_forecasts`: the three below put together |
| `open_archive(params)` | | the dynamical.org archive of ECMWF ensemble runs (`past_forecast_dataset`), opened once, the first time something isn't saved |
| `forecast_runs(end, params)` | last day | start times of the archive's runs, at least up to `end`; from the list saved in `data/past_forecasts/` while it's new enough, otherwise from the archive |
| `season_dates(start, end, runs, params, now)` | first and last day | first hour and number of hours, cut (with a warning) to what the archives have and to the past |
| `past_forecasts(runs, first_hour, params)` | | `(get_forecast, runs_used)`: `get_forecast` for `run_with_forecasts`, giving the newest run published by then (`forecast_delay_hours` after it starts) as `{"temp_c", "humidity_pct", "rain_mm"}` `[M, F, H]`; `runs_used` = `{season hour: run}` for each new run |
| `past_weather(first_hour, n_hours, params)` | | the weather that happened, from Open-Meteo (`past_weather_model`): `{"temp_c", "humidity_pct", "rain_mm"}` `[1, F, H]`; saved in `data/replay_weather/` the first time |
| `load_run(run, params)` | a run's start time | that run, hourly, `[M, F, H]` from its start; saved in `data/past_forecasts/` the first time |
| `to_hourly(steps, step_hours, params)` | archive steps (every 3 or 6 h) `[M, F, step]` | `[M, F, H]`: temperature in straight lines, rain spread over each step, humidity from the dew point (Magnus) |

In both, the rain at hour `h` is the rain that falls from `h` to `h + 1`, and it's all
precipitation (rain, showers and snow), so the forecasts and the real weather match.

---

## 5. `moisture.py`: grain moisture from temperature, humidity and rain

A physically based model of grain moisture for wheat, barley and oilseed rape (`"osr"`). It reads
`temp_c`, `humidity_pct` and `rain_mm` straight from the forecast CSV.
The pipeline runs it through `predict_moisture`, from every moisture reading, because the forecast
has temperature (`run.py`, `replay.py`, `compare.py`). `scripts/moisture_forecast.py` also saves a
run from the starting moistures to `outputs/moisture_forecast.csv`, for the plots. All constants, including each crop's starting moisture
(`start_moisture_pct`, placeholders for now), are in `params.yaml` under `moisture_model`, with
sources. To use the official ASABE D245.7 Henderson constants, replace `henderson_k/c/n` there.

| Function | In | Out |
|---|---|---|
| `simulate(crop, M0_wb_percent, T_c, RH, rain_mm, dt=1.0)` | start moisture % wb; `T_c` (°C), `RH` (decimal) and `rain_mm` (mm per step, required), `[H]` or `[M, F, H]`, hour last | same shape, moisture % wb at the **end** of each step (`predict_moisture` gives the start) |
| `forecast_moisture(path)` | the forecast CSV (needs `temp_c`, `humidity_pct`, `rain_mm`) | the CSV as a table plus `wheat_moisture_pct`, `barley_moisture_pct`, `osr_moisture_pct` for every hour, member and field, rain rule included |
| `emc_db(crop, T, RH)` | any shape, °C and decimal | equilibrium moisture, kg water / kg dry matter |
| `k_of_T(crop, T)` | any shape, °C | drying constant, 1/hour |
| `wb_to_db(m)`, `db_to_wb(m)` | moisture as a decimal | the other basis, decimal |

Each takes an optional `constants=` dict (default: from `params.yaml`) so you can try other values.

- **Dry hour:** equilibrium moisture from modified Henderson (RH clipped to 0.05-0.95), drying rate
  from Arrhenius, then `M = Me + (M - Me) * exp(-k * dt)` in dry basis (Lewis, one step at a time).
- **Rain hour** (`rain_mm >= rain_threshold_mm` and `rain_mm > 0`; the threshold is 0.3 mm/h, the
  project's own choice), every crop: the dry step is **replaced** by
  `M = min(M + rain_rise_per_hour * rain_rise_scale * dt, rain_max_mc)`, i.e. +0.028 kg/kg dry
  basis per rain hour at scale 1, capped at 0.52 kg/kg (about 34% wet basis). From Smith, Bailey
  & Ingram (1981), eqns 6a and 6b. `rain_rise_scale` is per crop: 1.0 for wheat and barley (the
  rule unchanged) and 1.0 for oilseed rape, an **assumption** by analogy with barley. 0 switches a
  crop's rain response off (rain hours then take the dry step).

Try it:
- `uv run python scripts/moisture_forecast.py`: every crop on `data/forecast.csv`, saved to
  `outputs/moisture_forecast.csv`
- `uv run python scripts/plot_moisture.py [field]`: plot every crop on the forecast for one field
- `uv run python scripts/plot_moisture_demo.py`: plot a week of made-up weather, with rain spells
- `uv run python scripts/moisture_sensitivity.py`: Ea 30,000-55,000 J/mol, k_ref ±50%
- `uv run python scripts/rain_sensitivity.py`: rain threshold 0 / 0.3 / 0.5 mm and oilseed rape
  `rain_rise_scale` 0 / 0.75 / 1.0, on the forecast (values in `rain_sensitivity` in `params.yaml`)

**Known limitations**
- Dew, solar radiation, wind and crop maturity are not modelled.
- The k values come from forced-air dryer tests (40-60 °C, ~6% RH), not standing crop, so field
  use at 0-30 °C is an extrapolation. The oilseed rape `k_ref` is a placeholder (copied from wheat).
  All rate constants should be calibrated against measured field moisture where available.
- The same k is used for drying and wetting.
- The EMC curve is the same for drying and wetting (no hysteresis).
- The Henderson constants are our own fit to the PAMI tables, not the official ASABE D245.7 values.
- Rain rule:
  - It was fitted together with Smith et al.'s own k and EMC. Paired with this model's
    Henderson/Arrhenius dry-hour constants it is **not validated**; in one comparison, grain
    recovered from rain much faster here than in Smith et al.'s model.
  - It gives moisture at the **end** of a rain period. Values during rain hours are not validated
    predictions.
  - It depends on how **long** it rains, not how much. The authors tried amount-based terms and
    found no better fit.
  - Smith et al.'s RMS error against field data was about 0.03-0.065 kg/kg (3-6.5 moisture points).
  - It was fitted to rain-gauge readings, and the paper gives no threshold. Forecast rain is a
    different kind of data. Every hour at or above `rain_threshold_mm` (0.3 mm/h, the project's
    choice) counts as a full rain hour: 17% of hours in the current forecast, against 33% with
    any rain at all.
  - Oilseed rape: its rain rise is the barley rule applied by analogy, with no published source
    for rapeseed (Sokhansanj, Singh & Wasserman 1984 is the intended source, not yet read). Its
    cap `rain_max_mc` (0.52 kg/kg) comes from barley and has no rapeseed basis. Its `k_ref` is
    still a placeholder, and how fast it recovers after rain depends on it.

**References**
- ASABE Standard D245.7, *Moisture Relationships of Plant-based Agricultural Products* (constants not accessed)
- Henderson, S.M. (1952), *A basic concept of equilibrium moisture*, Agricultural Engineering 33: 29-32
- PAMI, *Equilibrium Moisture Content Charts for Grain Storage Management* (tables used for the EMC fit)
- Lewis, W.K. (1921), *The rate of drying of solid materials*, J. Ind. Eng. Chem. 13(5): 427-432
- Noroña et al., *Drying kinetics of wheat, barley and maize grain* (k and Ea for wheat and barley)
- Rapeseed Ea: *Thin layer drying rate and loss of viability modelling for rapeseed (canola)*, and a
  later study giving 27.86 kJ/mol
- Smith, E.A., Bailey, P.H. & Ingram, G.W. (1981), *Prediction of the field moisture content of
  mature barley and wheat by commonly used drying equations*, J. agric. Engng Res. 26: 171-178
  (rain rule)
- Sokhansanj, S., Singh, D. & Wasserman, J. (1984), *Drying characteristics of wheat, barley and
  canola subjected to repetitive wetting and drying cycles*, Trans. ASAE 27(3): 903-906 (not yet
  read; the intended source for a real oilseed rape rain term)
- Possible barley wetting data (not used yet): Montanuci et al. (2015), Heat Mass Transfer 51:
  363-372; Tagawa et al. (2003), Trans. ASAE 46(2): 361-366
