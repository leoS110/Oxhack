"""Run every planner on the same past days and compare them.

uv run python compare.py                         rollout_start to rollout_end in params.yaml
uv run python compare.py 2026-10-10 2026-10-24   your own dates (whole days, UTC, both included)

Each planner replays the same days as replay.py does: the forecasts that were really issued, and
the weather that really happened (needs internet the first time, see harvest/past_data.py).
Every `plan_*` function listed in `__all__` in harvest/planners/__init__.py is picked up
automatically, so a new planner shows up here as soon as you add it there. Each planner gets the
standard run.py window, then one window puts them all side by side and another shows each one's
fuel in every combine's tank over time. Planners are ranked by
net £: money earned minus running costs (running_costs in params.yaml), the same for every planner.
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import yaml

from harvest import planners
from harvest.past_data import past_rollout, rollout_days
from harvest.simulate import activities, run_with_forecasts
from run import activation_lines, label_dates, mark_new_forecasts, net_gbp_per_hour, plot_results

ROOT = Path(__file__).parent

# every plan_* in harvest/planners, named without the prefix: {"vgreedy": plan_vgreedy}
PLANNERS = {
    name.removeprefix("plan_"): getattr(planners, name)
    for name in planners.__all__
    if name.startswith("plan_")
}

# one colour per planner, in this order (checked to be colourblind-safe); planners past 8 are grey
COLOURS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GREY = "#8a8984"


def main():
    params = yaml.safe_load((ROOT / "params.yaml").read_text(encoding="utf-8"))
    start, end = rollout_days(sys.argv[1:], params)
    truth, get_forecast, first_hour, runs_used = past_rollout(start, end, params)
    # every planner gets the same forecasts; each run is only loaded once
    results = {}
    for name, function in PLANNERS.items():
        print(f"running {name}...")
        results[name] = run_with_forecasts(truth, get_forecast, params, function, first_hour.hour)
    new_runs = list(runs_used)  # season hours a new forecast run came in

    print()
    print_comparison(results, params)
    for name, result in results.items():  # crop sequencing: when each held-back crop was let in
        for line in activation_lines(result, first_hour):
            print(f"{name:<34} {line}")
    for name, result in results.items():
        fig = plot_results(result, params, start=first_hour, marked_hours=new_runs)
        fig.suptitle(f"{name} (dashed line = a new forecast run)")
        fig.canvas.manager.set_window_title(name)
        fig.tight_layout()
    plot_comparison(results, first_hour, new_runs)
    plot_fuel(results, params, first_hour, new_runs)
    plt.show()


def print_comparison(results, params):
    """One row per planner, best net first. Hours are added up over all the machines.
    "vs farmer" is how much more net £ than plan_farmer, the baseline without our forecasts."""
    columns = ["GBP net", "vs farmer", "GBP earned", "GBP costs", "ha left"]
    columns += ["driving h", "waiting h", "depot h"]
    farmer_net = results["farmer"]["net_gbp"] if "farmer" in results else np.nan
    print(f"{'planner':<34}" + "".join(f"{column:>11}" for column in columns))
    for name in sorted(results, key=lambda name: -results[name]["net_gbp"]):
        result = results[name]
        # {activity: [machine, hour] bool}
        doing = activities(
            result["cut_ha"], result["driving"], result["place"], len(params["fields"])
        )
        money = [result["total_gbp"], result["running_cost_gbp"].sum()]
        hours = [doing[activity].sum() for activity in ("driving", "waiting", "depot")]
        print(
            f"{name:<34}"
            + f"{result['net_gbp']:>11,.0f}"
            + f"{result['net_gbp'] - farmer_net:>+11,.0f}"
            + "".join(f"{gbp:>11,.0f}" for gbp in money)
            + f"{result['remaining_ha'].sum():>11.1f}"
            + "".join(f"{hour:>11}" for hour in hours)
        )


def plot_comparison(results, start, marked_hours):
    """Net £ (earned minus running costs) hour by hour and in total, every planner in one window.

    start:        Timestamp of hour 0, to label the hour axis with dates
    marked_hours: hours to mark with a dashed line (a new forecast run)
    """
    # colour follows the planner, so it matches between the two charts
    colour = {name: COLOURS[i] if i < len(COLOURS) else GREY for i, name in enumerate(results)}
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10, 8))
    fig.canvas.manager.set_window_title("Planner comparison")

    # 1. net money so far, one line per planner
    for name, result in results.items():
        net_so_far = np.cumsum(net_gbp_per_hour(result))  # [hour] £
        top.plot(net_so_far, color=colour[name], linewidth=2, label=name)
    top.set_ylabel("£ net so far")
    top.set_title("Money earned minus running costs, over the season")
    top.yaxis.set_major_formatter("{x:,.0f}")
    top.grid(axis="y", alpha=0.3)
    label_dates(top, start, len(result["earned_gbp"]))
    mark_new_forecasts(top, result, marked_hours)  # the same hours for every planner
    top.legend(frameon=False)

    # 2. season net per planner, best at the top (barh draws the first bar at the bottom)
    ranked = sorted(results, key=lambda name: results[name]["net_gbp"])
    totals = [results[name]["net_gbp"] for name in ranked]
    bars = bottom.barh(ranked, totals, color=[colour[name] for name in ranked])
    bottom.bar_label(bars, labels=[f"£{total:,.0f}" for total in totals], padding=4)
    bottom.margins(x=0.15)  # room for the labels
    bottom.xaxis.set_major_formatter("{x:,.0f}")
    bottom.set_xlabel("£ net (earned minus running costs)")
    bottom.set_title("Net for the season")

    for ax in (top, bottom):
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def plot_fuel(results, params, start, marked_hours):
    """Diesel in each combine's tank over the season, one chart per planner, in one window.

    Each line drops while the combine cuts or drives and jumps back up when it refuels at the
    depot. Shown as % of each tank, so the one dotted line is every combine's reserve
    (fuel_reserve_pct), which the simulator never lets a tank go below.

    start:        Timestamp of hour 0, to label the hour axis with dates
    marked_hours: hours to mark with a dashed line (a new forecast run)
    """
    machines = params["machines"]
    tank = np.array([machine["fuel_tank_litres"] for machine in machines])  # [machine] litres
    height = 2 * len(results) + 1  # inches: 2 per planner, plus room for the title
    fig, axes = plt.subplots(len(results), 1, figsize=(10, height), sharex=True, squeeze=False)
    axes = axes[:, 0]  # [planner] one chart each
    fig.canvas.manager.set_window_title("Fuel by planner")

    for ax, (name, result) in zip(axes, results.items()):
        fuel_pct = 100 * result["fuel_litres"] / tank[:, None]  # [machine, hour] % of the tank
        for m, machine in enumerate(machines):
            ax.plot(fuel_pct[m], linewidth=1.2, label=machine["name"])
        ax.axhline(params["fuel_reserve_pct"], color=GREY, linestyle=":", linewidth=1)
        ax.set_ylim(0, 105)
        ax.set_ylabel("% of tank")
        ax.set_title(name, loc="left")
        ax.spines[["top", "right"]].set_visible(False)
        mark_new_forecasts(ax, result, marked_hours)
    axes[0].legend(frameon=False, ncol=len(machines), loc="lower right")
    label_dates(axes[-1], start, len(result["earned_gbp"]))
    fig.suptitle("Diesel in each tank (dotted = reserve, dashed = a new forecast run)")
    fig.tight_layout()
    return fig


if __name__ == "__main__":
    main()
