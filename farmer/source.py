"""What the farmer site shows, in farmer terms, from the simulated season.

The only file in farmer/ that imports the rest of the repo: it reads the season the control panel
in webapp/ already works out (webapp.demo_state, read-only) and turns the model's numbers into
what a farmer needs: each field Ready / Soon / Wait / Done, its best time, its grain moisture,
and what each combine is doing and does next. The algorithm itself stays out of sight.
"""

from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from webapp import demo_state

FARMER = Path(__file__).parent


@cache
def settings():
    """farmer/site.yaml"""
    return yaml.safe_load((FARMER / "site.yaml").read_text(encoding="utf-8"))


@cache
def farm():
    """The farm: what doesn't change through the season."""
    base = demo_state.farm()
    plan = demo_state.plan()
    params, demo, _ = demo_state.season()
    site = settings()
    corners = np.concatenate(  # [corner, 2] lat, lng: every parcel's outer ring
        [np.array(parcel[0]) for field in base["fields"] for parcel in field["outline"]]
    )
    return {
        "name": site["farm_name"],
        "place": site["place"],
        "fields": base["fields"],
        "yard": base["yard"],
        "machines": base["machines"],
        "centre": corners.mean(axis=0).round(5).tolist(),  # [lat, lng]
        "map": base["map"],
        "season_start_utc": plan["clock_start_utc"],  # the real moment that plays hour 0
        "hours": plan["hours"],
        "steps_per_hour": plan["steps_per_hour"],
        "site": {key: value for key, value in site.items() if key != "farm_name"},
        "week": past_week(plan, params, demo),
    }


def past_week(plan, params, demo):
    """When the demo replays a past week, what the browser needs to show that week's weather.

    returns None for today's forecast, or {
        "start_date", "end_date": "2024-06-09", local dates of the week
        "model":          Open-Meteo model of the weather that really happened (past_weather_model)
        "day_shift_days": the week's first day minus today, in days: today's time of day is
                          shown on that date
        "rain_chance_pct": {"2024-06-09T08:00": %} chance of rain each hour as the plan in force
                           then saw it, averaged over the fields (archives have no chance of rain)
    }
    """
    if not demo.get("season_start"):
        return None
    clock_day = pd.Timestamp(plan["clock_start_utc"]).tz_convert(demo["time_zone"]).normalize()
    first_day = pd.Timestamp(plan["times_iso"][0]).normalize()
    chance = {}
    for hour, time in enumerate(plan["times_iso"][:-1]):
        p = [p for p in plan["plans"] if p["hour"] <= hour][-1]  # the plan in force
        column = hour - p["hour"]
        chance[time] = round(100 * float(np.mean([row[column] for row in p["rain_chance"]])))
    return {
        "start_date": plan["times_iso"][0][:10],
        "end_date": plan["times_iso"][-1][:10],
        "model": params["past_weather_model"],
        "day_shift_days": (first_day - clock_day.tz_localize(None)).days,
        "rain_chance_pct": chance,
    }


@cache
def season():
    """Everything that changes hour by hour, worked out once.

    returns: {
        "labels":       [hour + 1] "Sun 25 Oct 14:00"
        "status":       [field][hour] "ready" | "soon" | "wait" | "done"
        "moisture_pct": [field][hour] grain moisture the plan expects (middle of the forecasts)
        "best":         [field][hour] best hour still to come, "now", or None if none
        "planned":      [field][hour] who the plan in force sends next and when: "A · Mon 07:00",
                        "now · A" while being cut, or None if this plan doesn't get there
        "cut_pct":      [field][hour + 1] share of the field cut, %
        "doing":        [hour][machine] "Cutting Long Ground" ...
        "next":         [hour][machine] "Brook Furlong, Sun 09:00" ...
        "activity", "place": [machine][hour], "tracks": [machine][frame][lat, lng]
        "harvested_ha": [hour + 1], "earned_gbp": [hour + 1] totals so far
        "plans":        one per replan: "hour", "schedule" [machine][hour from plan start]
                        (field index, n_fields = yard, "wait" = stay put), "value_gbp_per_ha"
                        [field][hour from plan start], for the Today charts
        "value_max_gbp_per_ha", "night" [hour] bool, "days" [{"hour", "label"}], "wait"
    }
    """
    plan = demo_state.plan()
    farm_ = farm()
    site = settings()
    n_hours, n_fields = plan["hours"], len(farm_["fields"])
    area = np.array([field["area_ha"] for field in farm_["fields"]], dtype=float)
    standing = np.array(plan["standing_ha"])  # [field, hour + 1] ha

    letters = [machine["letter"] for machine in farm_["machines"]]
    status = np.full((n_fields, n_hours), "wait", dtype=object)
    moisture = np.zeros((n_fields, n_hours))
    best = np.full((n_fields, n_hours), None, dtype=object)
    planned = np.full((n_fields, n_hours), None, dtype=object)
    for hour in range(n_hours):
        p = [p for p in plan["plans"] if p["hour"] <= hour][-1]  # the plan in force
        column = hour - p["hour"]
        value = np.array(p["value_gbp_per_ha"])[:, column:]  # [field, hour from now] £/ha
        cuttable = (value > 0) & (np.array(p["cut_chance"])[:, column:] >= site["ready_chance"])
        moisture[:, hour] = np.array(p["moisture_pct"][1])[:, column]
        schedule = np.array(p["schedule"])[:, column:]  # [machine, hour from now]
        for f in range(n_fields):
            status[f, hour] = field_status(standing[f, hour], cuttable[f], site["soon_hours"])
            if standing[f, hour] > 0 and value[f].max() > 0:
                j = int(np.argmax(value[f]))
                best[f, hour] = "now" if j == 0 else short_label(plan["labels"][hour + j])
            if standing[f, hour] > 0:
                planned[f, hour] = next_visit(f, hour, schedule, plan, letters)

    return {
        "labels": plan["labels"],
        "status": status.tolist(),
        "moisture_pct": np.round(moisture, 1).tolist(),
        "best": best.tolist(),
        "planned": planned.tolist(),
        "cut_pct": np.round(100 * (1 - standing / area[:, None])).tolist(),
        "doing": [[card["headline"] for card in cards] for cards in plan["cards"]],
        "next": [[card["next"] for card in cards] for cards in plan["cards"]],
        "activity": plan["activity"],
        "place": plan["place"],
        "tracks": plan["tracks"],
        "harvested_ha": np.round(area.sum() - standing.sum(axis=0), 1).tolist(),
        "earned_gbp": np.concatenate([[0], np.cumsum(plan["earned_gbp"])]).round().tolist(),
        "limits": plan["limits"],
        "plans": [
            {
                "hour": p["hour"],
                "schedule": p["schedule"],
                "value_gbp_per_ha": p["value_gbp_per_ha"],
            }
            for p in plan["plans"]
        ],
        "value_max_gbp_per_ha": plan["value_max_gbp_per_ha"],
        "night": plan["night"],
        "days": plan["days"],
        "wait": plan["wait"],
    }


def next_visit(f, hour, schedule, plan, letters):
    """Who the plan in force sends to field f next, and when.

    schedule: [machine, hour from now] the plan in force, field index per hour
    returns: "now · A" while a combine is cutting it, "A · Mon 07:00" for the first hour a
             combine is sent there (as the control panel's "next"), or None if it never is
    """
    cutting = [
        letters[m]
        for m in range(len(letters))
        if plan["place"][m][hour] == f and plan["activity"][m][hour] == "cutting"
    ]
    if cutting:
        return "now · " + ", ".join(cutting)
    visits = np.argwhere(schedule == f)  # [(machine, hour from now)]
    if len(visits) == 0:
        return None
    first = visits[:, 1].min()
    who = ", ".join(letters[m] for m, j in visits if j == first)
    return f"{who} · {short_label(plan['labels'][hour + first])}"


def field_status(standing_ha, cuttable, soon_hours):
    """ "done", "ready" (cuttable now), "soon" (cuttable within soon_hours) or "wait".

    cuttable: [hour from now] bool, worth cutting in enough forecasts
    """
    if standing_ha <= 0:
        return "done"
    if cuttable[0]:
        return "ready"
    if cuttable[:soon_hours].any():
        return "soon"
    return "wait"


def short_label(label):
    """ "Sun 25 Oct 14:00" -> "Sun 14:00"."""
    return f"{label[:3]} {label[-5:]}"
