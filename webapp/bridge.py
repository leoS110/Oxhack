"""The only file in webapp/ that talks to the model in harvest/.

It runs the demo's week one of two ways, set by season_start in demo.yaml:
- empty: today's forecast, the way run.py does (one forecast member plays the weather that really
  happens; the planner only sees the others);
- a date: that past week replayed the way replay.py does (the forecasts as issued then, and the
  weather that really followed, from harvest/past_data.py).
Then it collects what the control panel shows on top of the result: the forecast as the planner
saw it at each replan, and what really happened. If harvest/ changes its inputs or outputs, this
is the file to fix.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from harvest import planners
from harvest.forecast import load_forecast
from harvest.past_data import past_rollout
from harvest.simulate import activities, run_with_forecasts
from harvest.value import predict_moisture, start_moisture

ROOT = Path(__file__).parent.parent
WEBAPP = Path(__file__).parent
FORECAST_CSV = ROOT / "data" / "forecast.csv"
MOISTURE_CSV = ROOT / "outputs" / "moisture_forecast.csv"  # from scripts/moisture_forecast.py
WAIT = (
    planners.WAIT
)  # in a schedule: stay where you are (passed on so explain.py needn't import harvest)


def load_settings():
    """params.yaml (the model's numbers) and webapp/demo.yaml (the web app's own)."""
    params = yaml.safe_load((ROOT / "params.yaml").read_text(encoding="utf-8"))
    demo = yaml.safe_load((WEBAPP / "demo.yaml").read_text(encoding="utf-8"))
    return params, demo


def run_demo(params, demo):
    """Run the demo's week (see the top of this file) and collect everything the panel needs.

    returns: {
        "result":            run_season's result (see harvest/README.md)
        "plans":             one per replan: run_season's {"hour", "value", "schedule"} plus
                             "moisture_pct" [band, field, hour]: demo band_percentiles over members
                             "rain_chance" [field, hour]: share of members raining > rain_limit_mm
                             "humidity_pct" [band, field, hour]: band_percentiles over members
                             "cut_chance" [field, hour]: share of members in which it can be cut
        "true_moisture_pct": [field, hour]  grain moisture that really happened (start of hour)
        "true_rain_mm":      [field, hour]  rain that really fell
        "true_humidity_pct": [field, hour]  air humidity that really happened
        "standing_ha":       [field, hour + 1]  area still standing at the start of each hour
        "doing":             {"cutting", "driving", "waiting", "depot"}: [machine, hour] bool
        "times":             [hour + 1] Timestamps in demo time_zone, start of each hour
        "clock_start":       Timestamp (UTC), the real moment that plays hour 0, so the demo runs
                             in real time (see clock_start)
        "n_members":         int, forecast members the planner sees
        "source":            {"forecast", "truth", "credit"}: where the weather comes from, in words
    }
    """
    week = past_week if demo.get("season_start") else live_week
    truth, forecast_at, first_hour, source = week(params, demo)
    chosen = getattr(planners, demo["planner"])
    cut_chances = []  # one [field, hour] per replan, in the order run_season replans

    def planner(value, state, params):
        """The chosen planner, unchanged; on the way we note what the ensemble says."""
        # value_by_member is £/ha in each forecast member, 0 where that member can't cut
        cut_chances.append((state["value_by_member"] > 0).mean(axis=0))
        return chosen(value, state, params)

    result = run_with_forecasts(truth, forecast_at, params, planner)
    n_hours = truth["rain_mm"].shape[2]
    true_moisture = predict_moisture(start_moisture(truth, params), truth, params)[0]

    plans = [
        {**outlook(plan, forecast_at(plan["hour"]), true_moisture, params, demo), "cut_chance": c}
        for plan, c in zip(result["plans"], cut_chances)
    ]
    start = pd.Timestamp(first_hour).tz_localize("UTC")
    times = pd.date_range(start, periods=n_hours + 1, freq="h").tz_convert(demo["time_zone"])
    n_fields = len(params["fields"])
    return {
        "result": result,
        "plans": plans,
        "true_moisture_pct": true_moisture,
        "true_rain_mm": truth["rain_mm"][0],
        "true_humidity_pct": truth["humidity_pct"][0],
        "standing_ha": standing_ha(result, params),
        "doing": activities(result["cut_ha"], result["driving"], result["place"], n_fields),
        "times": times,
        "clock_start": clock_start(times, demo),
        "n_members": forecast_at(0)["rain_mm"].shape[0],
        "source": source,
    }


def live_week(params, demo):
    """Today's forecast (data/forecast.csv), split as run_season does: one member plays the
    weather that really happens, the planner sees the others.

    returns: (truth {name: [1, field, hour]}, forecast_at, first hour (UTC), source in words)
             forecast_at(hour) -> {name: [member, field, hour from then]}, the forecast the
             planner has at that season hour
    """
    weather = load_forecast(FORECAST_CSV, params, MOISTURE_CSV)
    member, n_hours = params["truth_member"], params["season_hours"]
    truth = {name: values[[member], :, :n_hours] for name, values in weather.items()}
    forecast = {name: np.delete(values, member, axis=0) for name, values in weather.items()}

    def forecast_at(hour):
        return {name: values[:, :, hour:] for name, values in forecast.items()}

    source = {
        "forecast": "Met Office UK 2 km ensemble forecast",
        "truth": "In this demo one forecast member plays the weather that really happened; the "
        "plan never sees it. Each field gets the forecast for its own 2 km square.",
        "credit": "Forecast: Met Office MOGREPS-UK via Open-Meteo",
    }
    return truth, forecast_at, pd.Timestamp(params["forecast_start"]), source


def past_week(params, demo):
    """The week from demo.yaml's season_start, replayed as replay.py does: the newest forecast
    run published by each hour, and the weather that really followed. Downloaded once, then
    read from data/past_forecasts/ and data/replay_weather/.

    returns: as live_week
    """
    first_day = pd.Timestamp(demo["season_start"])
    last_day = (first_day + pd.Timedelta(hours=params["season_hours"] - 1)).normalize()
    truth, get_forecast, first_hour, _ = past_rollout(first_day, last_day, params)
    truth = {name: values[:, :, : params["season_hours"]] for name, values in truth.items()}
    fetched = {}  # season hour -> the forecast run_with_forecasts asked for then, from that hour

    def forecast_at(hour):
        """The forecast the planner has at this hour: the last one fetched, from this hour on.

        Cut at the end of the week, as today's forecast is, so no plan runs past the last hour.
        """
        if hour % params["forecast_every_hours"] == 0 and hour not in fetched:
            fetched[hour] = get_forecast(hour)
        last = max(h for h in fetched if h <= hour)
        first, end = hour - last, params["season_hours"] + 1 - last
        return {name: values[:, :, first:end] for name, values in fetched[last].items()}

    source = {
        "forecast": "ECMWF ensemble forecast as issued then (0.25°, about 28 km)",
        "truth": f"This is the week from {first_hour:%A %d %B %Y} replayed: the plan only saw the "
        "forecasts issued at the time, and the combines met the weather that really followed "
        "(the Met Office 2 km record).",
        "credit": "Forecast: ECMWF IFS ENS via dynamical.org · Weather: Met Office UKV via "
        "Open-Meteo",
    }
    return truth, forecast_at, first_hour, source


def clock_start(times, demo):
    """The real moment that plays season hour 0, so the demo runs in real time (UTC Timestamp).

    Today's forecast starts when it starts. A past week is played on today's date: hour 0 is
    today at the time of day the week starts, so the clock on screen shows the time of day it is.
    times: [hour + 1] Timestamps in demo time_zone
    """
    if not demo.get("season_start"):
        return times[0].tz_convert("UTC")
    # in wall-clock time, so it still matches when the week and today differ in summer time
    now = pd.Timestamp.now(tz=demo["time_zone"])
    wall, start = now.tz_localize(None), times[0].tz_localize(None)
    since_start = wall - (wall.normalize() + (start - start.normalize()))
    return (now - since_start).tz_convert("UTC")


def outlook(plan, forecast, true_moisture, params, demo):
    """Add to one plan the forecast its planner saw: moisture and humidity spread, chance of rain.

    plan:          {"hour", "value" [field, hour], "schedule" [machine, hour]} from run_season
    forecast:      {name: [member, field, hour]} the members the planner sees, from the plan's hour
    true_moisture: [field, hour] the moisture the sensor reads
    returns: plan plus "moisture_pct" and "humidity_pct" [band, field, hour], "rain_chance"
             [field, hour]
    """
    hour = plan["hour"]
    n_plan = plan["value"].shape[1]  # shorter than plan_hours near the end of the forecast
    # the plan starts from the latest reading, as in simulate.py
    reading = true_moisture[:, hour - hour % params["sensor_every_hours"]]  # [field] %
    upcoming = {name: values[:, :, :n_plan] for name, values in forecast.items()}
    moisture = predict_moisture(reading, upcoming, params)  # [member, field, hour] %
    rain_chance = (upcoming["rain_mm"] > params["rain_limit_mm"]).mean(axis=0)  # [field, hour]
    return {
        **plan,
        "moisture_pct": np.percentile(moisture, demo["band_percentiles"], axis=0),
        "rain_chance": rain_chance,
        "humidity_pct": np.percentile(upcoming["humidity_pct"], demo["band_percentiles"], axis=0),
    }


def standing_ha(result, params):
    """Area still standing in each field at the start of each hour, and at the end.

    returns: [field, hour + 1] ha
    """
    area = np.array([field["area_ha"] for field in params["fields"]], dtype=float)  # [field]
    n_machines, n_hours = result["cut_ha"].shape
    cut = np.zeros((len(area), n_hours))  # [field, hour] ha cut by all machines
    for m in range(n_machines):
        hours = np.flatnonzero(result["cut_ha"][m] > 0)
        cut[result["harvesting"][m, hours], hours] += result["cut_ha"][m, hours]
    cut_so_far = np.concatenate([np.zeros((len(area), 1)), np.cumsum(cut, axis=1)], axis=1)
    return area[:, None] - cut_so_far
