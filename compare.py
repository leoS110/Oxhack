"""Run every planner on the same past days and compare them.

uv run python compare.py                         rollout_start to rollout_end in params.yaml
uv run python compare.py 2026-10-10 2026-10-24   your own dates (whole days, UTC, both included)
uv run python compare.py --params params_8_fields.yaml ...   a smaller farm, much faster

Each planner replays the same days as replay.py does: the forecasts that were really issued, and
the weather that really happened (needs internet the first time, see harvest/past_data.py).
Every `plan_*` function listed in `__all__` in harvest/planners/__init__.py is picked up
automatically, so a new planner shows up here as soon as you add it there. Each planner gets the
standard run.py window, then one window puts them all side by side (also saved, slide-sized, as
outputs/planner_comparison.png) and another shows each one's fuel in every combine's tank over
time. Planners are ranked by net £: money earned minus running costs (running_costs in
params.yaml), the same for every planner.
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from harvest import planners
from harvest.past_data import load_params, past_rollout, rollout_days
from harvest.simulate import activities, run_with_forecasts
from run import label_dates, mark_new_forecasts, net_gbp_per_hour, plot_results

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
# text and surface colours, the same as the charts in scripts/
TEXT, MUTED, SURFACE, GRID = "#0b0b0b", "#52514e", "#fcfcfb", "#e4e3df"
COMPARISON_PNG = ROOT / "outputs" / "planner_comparison.png"  # for slides


def main():
    params, args, _ = load_params(sys.argv[1:])
    start, end = rollout_days(args, params)
    truth, get_forecast, first_hour, runs_used = past_rollout(start, end, params)
    # every planner gets the same forecasts; each run is only loaded once
    results = {}
    for name, function in PLANNERS.items():
        print(f"running {name}...")
        results[name] = run_with_forecasts(truth, get_forecast, params, function, first_hour.hour)
    new_runs = list(runs_used)  # season hours a new forecast run came in

    print()
    print_comparison(results, params)
    for name, result in results.items():
        fig = plot_results(result, params, start=first_hour, marked_hours=new_runs)
        fig.suptitle(f"{name} (dashed line = a new forecast run)")
        fig.canvas.manager.set_window_title(name)
        fig.tight_layout()
    comparison = plot_comparison(results, first_hour)
    comparison.savefig(COMPARISON_PNG, dpi=200, facecolor=SURFACE)
    print(f"saved {COMPARISON_PNG.relative_to(ROOT)}")
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


def short_gbp(gbp):
    """£1.23m, £45k or £800: short enough to read on a slide."""
    sign = "-" if gbp < 0 else ""
    if abs(gbp) >= 1e6:
        return f"{sign}£{abs(gbp) / 1e6:,.2f}m"
    if abs(gbp) >= 1e3:
        return f"{sign}£{abs(gbp) / 1e3:,.0f}k"
    return f"{sign}£{abs(gbp):,.0f}"


def plot_comparison(results, start):
    """Net £ (earned minus running costs) over the season and in total, every planner in one
    16:9 window, styled to go straight onto a slide (main also saves it as COMPARISON_PNG).

    start: Timestamp of hour 0, to label the hour axis with dates
    """
    # colour follows the planner, so it matches between the two charts; the farmer baseline is
    # grey so our planners stand out against it
    ours = [name for name in results if name != "farmer"]
    colour = {name: COLOURS[i] if i < len(COLOURS) else GREY for i, name in enumerate(ours)}
    colour["farmer"] = GREY
    label = {name: name.replace("_", " ") for name in results}
    label["farmer"] = "farmer (baseline)"
    ranked = sorted(results, key=lambda name: results[name]["net_gbp"])  # worst first
    n_hours = len(results[ranked[0]]["earned_gbp"])
    end = start + np.timedelta64(n_hours - 1, "h")

    fig, (left, right) = plt.subplots(
        1, 2, figsize=(13.33, 7.5), gridspec_kw={"width_ratios": [1.8, 1]}, facecolor=SURFACE
    )
    fig.canvas.manager.set_window_title("Planner comparison")
    fig.suptitle(
        f"Net £ by planner, {start.day} {start:%b} to {end.day} {end:%b %Y}",
        x=0.02,
        ha="left",
        fontsize=18,
        fontweight="bold",
        color=TEXT,
    )

    # 1. net money so far, one line per planner, the best drawn last so it sits on top
    for rank, name in enumerate(ranked):
        net_so_far = np.cumsum(net_gbp_per_hour(results[name]))  # [hour] £
        left.plot(
            net_so_far,
            color=colour[name],
            linewidth=2.2,
            linestyle="--" if name == "farmer" else "-",
            label=label[name],
            zorder=2 + rank,
        )
    label_dates(left, start, n_hours)
    left.set_xlabel("")
    left.set_xlim(0, n_hours - 1)
    left.yaxis.set_major_formatter(lambda gbp, _: short_gbp(gbp))
    left.set_title("Money earned minus running costs, so far", loc="left", fontsize=14, pad=12)
    handles, names = left.get_legend_handles_labels()
    legend = left.legend(handles[::-1], names[::-1], frameon=False, loc="upper left", fontsize=11)
    for text in legend.get_texts():
        text.set_color(TEXT)  # best first, same order as the bars

    # 2. season net per planner, best at the top (barh draws the first bar at the bottom),
    # labelled with the total and how much more it made than the farmer
    totals = [results[name]["net_gbp"] for name in ranked]
    bars = right.barh(
        [label[name] for name in ranked], totals, height=0.6, color=[colour[n] for n in ranked]
    )
    farmer_net = results["farmer"]["net_gbp"] if "farmer" in results else None
    texts = []
    for name, total in zip(ranked, totals):
        text = short_gbp(total)
        if farmer_net is not None and name != "farmer":
            text += f"  ({'+' if total >= farmer_net else ''}{short_gbp(total - farmer_net)})"
        texts.append(text)
    right.bar_label(bars, labels=texts, padding=6, fontsize=11, color=TEXT)
    low, high = min(0, min(totals)), max(0, max(totals))
    span = (high - low) or 1
    right.set_xlim(low - (0.45 * span if low < 0 else 0), high + 0.45 * span)  # room for labels
    right.axvline(0, color=MUTED, linewidth=1)
    right.xaxis.set_visible(False)
    right.spines["bottom"].set_visible(False)
    right.set_title("Season total (vs farmer)", loc="left", fontsize=14, pad=12)

    for ax in (left, right):
        ax.set_facecolor(SURFACE)
        ax.set_axisbelow(True)
        ax.title.set_color(TEXT)
        ax.tick_params(colors=MUTED, labelsize=12, length=0)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
    left.grid(axis="y", color=GRID, linewidth=0.8)
    right.tick_params(axis="y", colors=TEXT)
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
