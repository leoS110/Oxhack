"""The one demo farm, held in memory, as the JSON the browser reads.

farm(): what doesn't change (fields, yard, combines, map settings).
plan(): the simulated season: positions, cards, forecasts and what really happened.
The season is run once, the first time it's asked for. (The farmer's changes to the plan, coming
next, will live here too.)
"""

import json
from functools import cache

import numpy as np

from webapp import bridge, explain, tracks

FARM_GEOJSON = bridge.WEBAPP / "data" / "demo_farm.geojson"


@cache
def season():
    """(params, demo, run_demo result), worked out once."""
    params, demo = bridge.load_settings()
    return params, demo, bridge.run_demo(params, demo)


@cache
def geometry():
    """Field outlines and the yard from the GeoJSON, in params.yaml field order.

    A field is one or more parcels (a MultiPolygon), in the order a combine works them.
    returns: (labels [field] str, outlines [field] of [parcel] of [corner, 2] lon/lat,
              yard [2] lon/lat)
    """
    params, _, _ = season()
    features = json.loads(FARM_GEOJSON.read_text(encoding="utf-8"))["features"]
    by_name = {feature["properties"]["name"]: feature for feature in features}
    fields = [by_name[field["name"]] for field in params["fields"]]
    labels = [feature["properties"]["label"] for feature in fields]
    # each parcel's outer ring; GeoJSON repeats the first corner at the end, so drop it
    outlines = [
        [np.array(parcel[0][:-1]) for parcel in feature["geometry"]["coordinates"]]
        for feature in fields
    ]
    yard = np.array(by_name["depot"]["geometry"]["coordinates"], dtype=float)
    return labels, outlines, yard


@cache
def parcel_paths():
    """[field] of [parcel] of [point, 2] metres from the yard: the passes in each parcel."""
    params, demo, _ = season()
    _, outlines, yard = geometry()
    return [
        [tracks.passes(tracks.to_metres(parcel, yard), demo["pass_width_m"]) for parcel in parcels]
        for parcels in outlines
    ]


@cache
def field_paths():
    """[field] of [point, 2] metres from the yard: the passes a combine drives in each field,
    one parcel after another."""
    return [np.concatenate(parcels) for parcels in parcel_paths()]


def machine_label(machine):
    """combine_a -> "Combine A"."""
    return machine["name"].replace("_", " ").title()


def latlng(lonlat):
    """[..., 2] lon/lat -> nested lists of [lat, lng], the order Leaflet wants, to ~10 cm."""
    return np.round(np.asarray(lonlat)[..., ::-1], 6).tolist()


def farm():
    params, demo, _ = season()
    labels, outlines, yard = geometry()
    fields = []
    for f, field in enumerate(params["fields"]):
        path = tracks.to_lonlat(field_paths()[f], yard)
        parcel_lengths = [len(parcel) for parcel in parcel_paths()[f]]
        fields.append(
            {
                "name": field["name"],
                "label": labels[f],
                "short": labels[f].split()[0],  # "Culford Heath" -> "Culford", for tight spaces
                "crop": field["crop"],
                "area_ha": field["area_ha"],
                "centre": [field["lat"], field["lon"]],  # the middle of its parcels, for labels
                # [parcel][ring][corner][lat, lng]: Leaflet draws this as one MultiPolygon
                "outline": [[latlng(parcel)] for parcel in outlines[f]],
                "path": latlng(path),
                "path_m": np.round(tracks.distance_along(field_paths()[f]), 1).tolist(),
                # where each parcel's passes start in path, so the drawn stubble can break there
                "parcel_starts": np.cumsum([0] + parcel_lengths[:-1]).tolist(),
            }
        )
    machines = [
        {
            "name": machine["name"],
            "label": machine_label(machine),
            "letter": machine["name"].split("_")[-1].upper(),  # drawn on the map marker
            "ha_per_hour": machine["ha_per_hour"],
        }
        for machine in params["machines"]
    ]
    return {
        "farm_name": demo["farm_name"],
        "fields": fields,
        "yard": latlng(yard),
        "machines": machines,
        "map": {
            "tiles_url": demo["map_tiles_url"],
            "attribution": demo["map_attribution"],
            "pass_width_m": demo["pass_width_m"],
        },
    }


def plan():
    params, demo, run = season()
    labels, _, yard = geometry()
    result, times = run["result"], run["times"]
    n_fields = len(params["fields"])
    n_hours = result["place"].shape[1]
    names = [field["name"] for field in params["fields"]]
    start_place = [
        n_fields if m["start"] == "depot" else names.index(m["start"]) for m in params["machines"]
    ]
    area = np.array([field["area_ha"] for field in params["fields"]], dtype=float)
    positions = tracks.combine_positions(
        result["place"],
        result["driving"],
        start_place,
        1 - run["standing_ha"] / area[:, None],
        field_paths(),
        np.zeros(2),  # the paths are in metres from the yard
        demo["steps_per_hour"],
        demo["combine_gap_m"],
        demo["yard_gap_m"],
    )
    activity = np.select(  # [machine, hour] one word each
        [run["doing"][name] for name in ("cutting", "driving", "waiting", "depot")],
        ["cutting", "driving", "waiting", "yard"],
        default="",  # never used: exactly one activity is True each hour
    )
    night_from, night_to = demo["night_hours"]
    machine_labels = [machine_label(machine) for machine in params["machines"]]
    changes = explain.plan_changes(run, params, demo, labels, machine_labels)
    return {
        "hours": n_hours,
        "steps_per_hour": demo["steps_per_hour"],
        "wait": bridge.WAIT,  # in a schedule: stay where you are
        "band_percentiles": demo["band_percentiles"],
        "play_seconds_per_hour": demo["play_seconds_per_hour"],
        "labels": [f"{time:%a %d %b %H:%M}" for time in times],  # [hour + 1]
        "times_iso": [f"{time:%Y-%m-%dT%H:%M}" for time in times],  # [hour + 1] local time
        "days": [{"hour": h, "label": f"{t:%a %d}"} for h, t in enumerate(times) if t.hour == 0],
        "night": [bool(t.hour >= night_from or t.hour < night_to) for t in times[:-1]],
        "forecast": {
            "start": f"{times[0]:%a %d %b %H:%M}",
            "members": run["n_members"],
            **run["source"],  # "forecast", "truth", "credit": where the weather comes from
        },
        # the real moment that plays hour 0: the demo runs in real time (bridge.clock_start)
        "clock_start_utc": f"{run['clock_start']:%Y-%m-%dT%H:%M:%S}Z",
        "limits": {
            "too_wet_pct": params["max_harvest_moisture_pct"],
            # per crop since drying moved under crops: in params.yaml, so one per field
            "free_drying_pct": [
                params["crops"][field["crop"]]["free_drying_up_to_pct"]
                for field in params["fields"]
            ],  # [field]
            "rain_mm": params["rain_limit_mm"],
            "rain_chance_pct": demo["rain_chance_pct"],
            "sensor_every_hours": params["sensor_every_hours"],
        },
        "tracks": latlng(tracks.to_lonlat(positions, yard)),  # [machine][frame][lat, lng]
        "place": result["place"].tolist(),  # [machine][hour] field index, n_fields = yard
        "activity": activity.tolist(),  # [machine][hour]
        "standing_ha": np.round(run["standing_ha"], 2).tolist(),  # [field][hour + 1]
        "earned_gbp": np.round(result["earned_gbp"]).tolist(),  # [hour]
        "running_cost_gbp": np.round(result["running_cost_gbp"].sum(axis=0)).tolist(),  # [hour]
        "true_moisture_pct": np.round(run["true_moisture_pct"], 1).tolist(),  # [field][hour]
        "true_rain_mm": np.round(run["true_rain_mm"], 1).tolist(),  # [field][hour]
        "true_humidity_pct": np.round(run["true_humidity_pct"]).tolist(),  # [field][hour]
        # one colour scale for the whole season, so colours compare across replans
        # rounded like value_gbp_per_ha below, so no rounded value is above it
        "value_max_gbp_per_ha": float(np.round(max(plan["value"].max() for plan in run["plans"]))),
        "plans": [
            {
                "hour": plan["hour"],
                "schedule": plan["schedule"].tolist(),  # [machine][hour from plan start]
                "value_gbp_per_ha": np.round(plan["value"]).tolist(),  # [field][hour]
                "cut_chance": np.round(plan["cut_chance"], 2).tolist(),  # [field][hour]
                "moisture_pct": np.round(plan["moisture_pct"], 1).tolist(),  # [band][field][hour]
                "rain_chance": np.round(plan["rain_chance"], 2).tolist(),  # [field][hour]
                "humidity_pct": np.round(plan["humidity_pct"]).tolist(),  # [band][field][hour]
                "change": change,  # [sentence] what this replan learned and changed
            }
            for plan, change in zip(run["plans"], changes)
        ],
        "cards": explain.all_cards(run, params, demo, labels),  # [hour][machine]
    }
