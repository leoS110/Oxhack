"""Live run: simulate the season ahead on today's forecast, with replanning, and show the results.

uv run python run.py

The forecast is data/forecast.csv (uv run python data/download_forecast.py for a fresh one); one
of its members plays the weather that really happens. Grain moisture comes from the moisture model
(harvest/moisture.py), re-run from every reading. Past days: replay.py and compare.py.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from matplotlib.colors import ListedColormap

from harvest.forecast import load_forecast
from harvest.simulate import activities, run_season

ROOT = Path(__file__).parent
FORECAST_CSV = ROOT / "data" / "forecast.csv"


def main():
    params = yaml.safe_load((ROOT / "params.yaml").read_text(encoding="utf-8"))
    weather = load_forecast(FORECAST_CSV, params)  # with temp_c: moisture model from each reading
    result = run_season(weather, params)
    print_results(result, params, start=pd.Timestamp(params["forecast_start"]))
    plot_results(result, params)
    plt.show()


def activation_lines(result, start=None):
    """Crop sequencing (harvest/sequencing.py): when each gated crop became active, one line each.

    start: Timestamp (UTC) of season hour 0, to give the date too, or None
    returns: list of str (empty with sequencing off)
    """
    lines = []
    for crop, when in result.get("activation", {}).items():
        waited = result["waited_ripe_hours"][crop]
        if when is None:
            lines.append(f"{crop}: not active this season (waited {waited} ripe field-hours)")
            continue
        date = (
            ""
            if start is None
            else f" ({start + pd.Timedelta(hours=when['hour']):%Y-%m-%d %H:%M} UTC)"
        )
        barley = (
            "no barley" if when["barley_pct"] is None else f"barley {when['barley_pct']:.1f}% cut"
        )
        lines.append(
            f"{crop}: active from hour {when['hour']}{date}, {barley}; "
            f"waited {waited} ripe field-hours"
        )
    return lines


def print_results(result, params, start=None):
    """Summary in the terminal. start: Timestamp (UTC) of season hour 0, for dates."""
    print(f"Replanned {len(result['plans'])} times over {len(result['earned_gbp'])} hours")
    for line in activation_lines(result, start):
        print(f"Crop sequencing: {line}")
    costs = result["cost_breakdown_gbp"]  # {item: £}
    items = ", ".join(f"{item} {gbp:,.0f}" for item, gbp in costs.items())
    print(f"Earned:        GBP {result['total_gbp']:>9,.0f}")
    print(f"Running costs: GBP {sum(costs.values()):>9,.0f}  ({items})")
    print(f"Net:           GBP {result['net_gbp']:>9,.0f}")
    print()
    print(f"{'field':<15} {'harvested ha':>12} {'left ha':>8}")
    for field, left in zip(params["fields"], result["remaining_ha"]):
        print(f"{field['name']:<15} {field['area_ha'] - left:>12.1f} {left:>8.1f}")
    print()
    # {activity: [machine, hour] bool}
    doing = activities(result["cut_ha"], result["driving"], result["place"], len(params["fields"]))
    activity_columns = "".join(f"{name + ' h':>11}" for name in doing)
    print(f"{'machine':<15}{activity_columns}{'cost GBP':>11}{'refuels':>9}{'fuel l':>8}")
    for m, machine in enumerate(params["machines"]):
        hours = "".join(f"{doing[name][m].sum():>11}" for name in doing)  # one column per activity
        cost = result["running_cost_gbp"][m].sum()  # £
        fuel = result["fuel_litres"][m]  # [hour] litres
        refuels = (np.diff(fuel) > 0).sum()  # hours the tank was filled at the depot
        print(f"{machine['name']:<15}{hours}{cost:>11,.0f}{refuels:>9}{fuel[-1]:>8,.0f}")


def plot_results(result, params, start=None, marked_hours=None):
    """Three charts sharing the same hour axis.

    start:        Timestamp of hour 0, to label the axis with dates (replay.py), or None for hours
    marked_hours: hours to mark with a dashed line, or None for every replan
    """
    field_names = [field["name"] for field in params["fields"]]
    machine_names = [machine["name"] for machine in params["machines"]]
    n_fields = len(field_names)
    n_hours = len(result["earned_gbp"])
    fig, (top, middle, bottom) = plt.subplots(3, 1, figsize=(10, 9), sharex=True)

    # 1. what the planner expected each field to be worth. Each plan is only followed until the
    # next replan, so stitch together those first hours of every plan.
    plans = result["plans"]
    ends = [plan["hour"] for plan in plans[1:]] + [n_hours]  # hour each plan stops being followed
    used = [plan["value"][:, : end - plan["hour"]] for plan, end in zip(plans, ends)]
    value = np.concatenate(used, axis=1)  # [field, hour] £/ha
    # "nearest": one solid block per hour, not smoothed into its neighbours
    image = top.imshow(value, aspect="auto", interpolation="nearest", cmap="viridis")
    top.set_yticks(range(n_fields), field_names)
    top.set_title("Expected £/ha if harvested (the plan being followed)")
    fig.colorbar(image, ax=top, label="£/ha")

    # 2. the path each machine followed, drawn in three layers: the field it harvested, the field
    # it waited in (faded), and driving (grey). Blank = at the depot
    harvested = np.ma.masked_equal(result["harvesting"], -1)
    waiting = (result["harvesting"] == -1) & ~result["driving"] & (result["place"] < n_fields)
    colours = plt.get_cmap("tab10", n_fields)
    blocks = {"aspect": "auto", "interpolation": "nearest"}
    field_colours = {"cmap": colours, "vmin": -0.5, "vmax": n_fields - 0.5, **blocks}
    image = middle.imshow(harvested, **field_colours)
    middle.imshow(np.ma.masked_where(~waiting, result["place"]), alpha=0.3, **field_colours)
    driving = np.ma.masked_where(~result["driving"], result["place"])
    middle.imshow(driving, cmap=ListedColormap(["dimgrey"]), **blocks)
    middle.set_yticks(range(len(machine_names)), machine_names)
    middle.set_title(
        "Path followed: solid = harvesting, faded = waiting, grey = driving, blank = depot"
    )
    fig.colorbar(image, ax=middle, ticks=range(n_fields)).ax.set_yticklabels(field_names)

    # 3. money so far, before and after running costs
    bottom.plot(np.cumsum(result["earned_gbp"]), label="earned")
    bottom.plot(np.cumsum(net_gbp_per_hour(result)), label="after running costs")
    bottom.set_ylabel("£ so far")
    bottom.set_xlabel("hour")
    bottom.set_title("Money earned, and what's left after running costs")
    bottom.legend(frameon=False)
    # an empty colour bar slot, so all three charts are the same width and their hours line up
    fig.colorbar(image, ax=bottom).ax.set_visible(False)

    if start is not None:
        label_dates(bottom, start, n_hours)
    for ax in (top, middle, bottom):
        mark_new_forecasts(ax, result, marked_hours)
    fig.tight_layout()
    return fig


def net_gbp_per_hour(result):
    """Money earned each hour minus what running every machine cost that hour. Returns [hour] £."""
    return result["earned_gbp"] - result["running_cost_gbp"].sum(axis=0)


def label_dates(ax, start, n_hours):
    """Label the hour axis with the date at midnights (UTC), at most 8 of them so they fit."""
    times = start + pd.to_timedelta(np.arange(n_hours), unit="h")
    midnights = np.flatnonzero(times.hour == 0)
    every = max(1, int(np.ceil(len(midnights) / 8)))  # label every this-many days
    midnights = midnights[::every]
    ax.set_xticks(midnights - 0.5, [f"{times[hour]:%d %b}" for hour in midnights])
    ax.set_xlabel("date (UTC)")


def mark_new_forecasts(ax, result, hours=None):
    """Dashed vertical line at each of `hours`, or else at every hour we replanned."""
    if hours is None:
        hours = [plan["hour"] for plan in result["plans"]]
    for hour in hours:
        if hour > 0:  # skip hour 0, the start of the chart
            ax.axvline(hour - 0.5, color="grey", linestyle="--", linewidth=0.8)


if __name__ == "__main__":
    main()
