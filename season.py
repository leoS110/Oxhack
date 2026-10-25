"""replay.py for a whole summer, with our controller (plan_mpc) and the farmer baseline
(plan_farmer) side by side, drawn as simple charts for the presentation.

uv run python season.py                                the farm in params.yaml
uv run python season.py --params params_8_fields.yaml  a smaller farm, much faster (saved and
                                                       drawn in outputs/season/params_8_fields/)

The days are always params.yaml's rollout_start to rollout_end (both included). The replay is
exactly replay.py's (and compare.py's, with just these two planners): the forecasts that were
really issued, a new plan with every new run and moisture reading, and combines that harvest in
the weather that really happened. MPC takes about an hour for a summer, so the results are saved in
outputs/season/rollout.pkl and reused while the dates and params.yaml stay the same (the season_*
settings can change freely). Delete the file to replay again. While MPC plans, a bar in the
terminal shows how far it has got.

Charts (16:9 PNGs for slides) in outputs/season/:
  1_money.png       net £ so far for both planners
  2_money_gap.png   what the difference in net £ is made of
  3_plan.png        the value function and where every combine went, as run.py draws them
"""

import pickle
import sys
import time
from datetime import timedelta
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap

from compare import PLANNERS, print_comparison
from harvest.past_data import load_params, past_rollout
from harvest.simulate import run_with_forecasts, running_costs_gbp
from harvest.value import (
    crop_numbers,
    delivered_tonnes_per_ha,
    drying_cost_gbp_per_t,
    predict_moisture,
    start_moisture,
)
from run import net_gbp_per_hour

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "outputs" / "season"

OURS, BASELINE = "mpc", "farmer"  # names in compare.PLANNERS
NAMES = {OURS: "Our controller (MPC)", BASELINE: "Farmer model"}

# the planners keep compare.py's first two colourblind-safe colours
COLOUR = {OURS: "#2a78d6", BASELINE: "#eb6834"}
INK, GREY, LIGHT = "#0b0b0b", "#52514e", "#c3c2b7"
GAIN, LOSS = "#0ca30c", "#d03b3b"

# money_breakdown's items, as the charts name them
MONEY_ITEMS = {
    "grain": "Grain harvested",
    "weight": "Weight from moisture",
    "drying": "Drying charges",
    "standing": "Crop lost standing",
    "diesel": "Diesel",
    "operator": "Operator wages",
    "escort": "Road escort",
    "repairs": "Repairs",
}

STYLE = {
    "figure.figsize": (13.33, 7.5),  # inches, 16:9
    "savefig.dpi": 200,
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"],
    "font.size": 12,
    "axes.titlesize": 13,
    "axes.titleweight": "semibold",
    "axes.titlelocation": "left",
    "axes.labelcolor": GREY,
    "axes.edgecolor": LIGHT,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "grid.color": "#e1e0d9",
    "xtick.color": LIGHT,
    "ytick.color": LIGHT,
    "xtick.labelcolor": GREY,
    "ytick.labelcolor": GREY,
    "legend.frameon": False,
    "lines.linewidth": 2,
}


def main():
    params, _, params_name = load_params(sys.argv[1:])
    start, end = pd.Timestamp(params["rollout_start"]), pd.Timestamp(params["rollout_end"])
    # another params file gets its own folder, so the full farm's saved replay is kept
    out_dir = OUT_DIR if params_name == "params" else OUT_DIR / params_name
    season = load_or_replay(start, end, params, out_dir)
    facts = season_facts(season, params)
    print(f"Charts run {facts['times'][0]:%Y-%m-%d %H:%M} to {facts['times'][-1]:%Y-%m-%d %H:%M}")
    print()
    print_comparison(season["results"], params)
    print()
    print_money(facts)

    out_dir.mkdir(parents=True, exist_ok=True)
    with plt.rc_context(STYLE):
        save(plot_money(facts, params), out_dir / "1_money")
        save(plot_money_gap(facts), out_dir / "2_money_gap")
        save(plot_plan(season, facts, params), out_dir / "3_plan")


# ---------------------------------------------------------------------------------------------
# The replay


def load_or_replay(start, end, params, out_dir):
    """The season saved in out_dir if it was replayed with these dates and params, else a new
    replay (saved there)."""
    path = out_dir / "rollout.pkl"
    if path.exists():
        saved = pickle.loads(path.read_bytes())
        same_days = (saved["start"], saved["end"]) == (start, end)
        if same_days and replay_params(saved["params"]) == replay_params(params):
            print(f"Drawing the season saved in {path.relative_to(ROOT)} (delete it to replay)")
            if "value" not in saved:  # saved before season.py kept the value function
                saved["value"] = replay_value(start, end, params)
                path.write_bytes(pickle.dumps(saved))
            return saved
    season = replay_both(start, end, params)
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps(season))
    return season


def replay_params(params):
    """params without the season_* settings, which don't change the replay."""
    return {key: value for key, value in params.items() if not key.startswith("season_")}


def replay_both(start, end, params):
    """replay.py with both planners, on the same forecasts (as compare.py does).

    returns: {"params", "start", "end", "first_hour", "truth",
              "results": {planner: result of run_with_forecasts},
              "value": [field, hour] £/ha, from followed_value}
    """
    truth, get_forecast, first_hour, _ = past_rollout(start, end, params)
    results = {}
    for name in (BASELINE, OURS):
        print(f"running {name}...")
        planner = PLANNERS[name]
        if name == OURS:  # the slow one
            planner = with_progress_bar(
                planner, count_plans(truth["rain_mm"].shape[2], params), params
            )
        results[name] = run_with_forecasts(truth, get_forecast, params, planner, first_hour.hour)
    # the value tables come from the forecast and the readings, never the planner, so both
    # planners had the same ones
    value = followed_value(results[BASELINE]["plans"], truth["rain_mm"].shape[2])
    for result in results.values():
        del result["plans"]  # a big table per plan, and the charts only need value
    return {
        "params": params,
        "start": start,
        "end": end,
        "first_hour": first_hour,
        "truth": truth,
        "results": results,
        "value": value,
    }


def replay_value(start, end, params):
    """followed_value for a saved season, by replaying just the farmer model (quick): the value
    tables are the same whichever planner runs."""
    print("working out the value function again with the farmer model...")
    truth, get_forecast, first_hour, _ = past_rollout(start, end, params)
    result = run_with_forecasts(truth, get_forecast, params, PLANNERS[BASELINE], first_hour.hour)
    return followed_value(result["plans"], truth["rain_mm"].shape[2])


def followed_value(plans, n_hours):
    """What the planners expected each field to be worth, as run.py draws it: each plan is only
    followed until the next replan, so the first hours of every plan stitched together.

    plans: from run_with_forecasts
    returns: [field, hour] £/ha
    """
    ends = [plan["hour"] for plan in plans[1:]] + [n_hours]  # hour each plan stops being followed
    used = [plan["value"][:, : end - plan["hour"]] for plan, end in zip(plans, ends)]
    return np.concatenate(used, axis=1)


def count_plans(n_hours, params):
    """How many plans run_with_forecasts makes: one every hour with a new reading or forecast."""
    hours = np.arange(n_hours)
    new_reading = hours % params["sensor_every_hours"] == 0
    new_forecast = hours % params["forecast_every_hours"] == 0
    return int((new_reading | new_forecast).sum())


def with_progress_bar(planner, n_plans, params):
    """planner, drawing a bar in the terminal that fills a little with every plan it makes."""
    started = time.monotonic()
    made = 0

    def planner_with_bar(value, state, plan_params):
        nonlocal made
        schedule = planner(value, state, plan_params)
        made += 1
        draw_bar(made, n_plans, time.monotonic() - started, params["season_bar_width"])
        return schedule

    return planner_with_bar


def draw_bar(done, total, seconds, width):
    """[########------] 120/270 plans, 0:10:04 so far, about 0:12:35 left (redrawn in place)."""
    filled = round(width * done / total)
    left = seconds / done * (total - done)
    print(
        f"\r  [{'#' * filled}{'-' * (width - filled)}] {done}/{total} plans, "
        f"{timedelta(seconds=round(seconds))} so far, about {timedelta(seconds=round(left))} left",
        end="\n" if done == total else "",
        flush=True,
    )


# ---------------------------------------------------------------------------------------------
# The analysis


def season_facts(season, params):
    """Everything the money charts draw, worked out once from the saved season.

    Season arrays are [hour]; per planner ones are dicts {planner: array}.
    """
    truth = season["truth"]
    n_hours = truth["rain_mm"].shape[2]
    times = season["first_hour"] + pd.to_timedelta(np.arange(n_hours), unit="h")
    # the grain moisture the combines cut at, worked out as simulate.py does
    moisture = predict_moisture(start_moisture(truth, params), truth, params)[0]
    facts = {
        "times": times,  # [hour] start of each season hour, UTC
        "x": mdates.date2num(times),  # [hour] matplotlib's dates (in days), for the time axes
        "net_so_far": {},  # [hour] £ earned minus running costs, by the end of each hour
        "money": {},  # {item: £} from money_breakdown
        "finish_hour": {},  # last hour of cutting if every field was cleared, else None
    }
    for name, result in season["results"].items():
        cut_hours = np.flatnonzero(result["cut_ha"].sum(axis=0))  # hours any combine cut
        cleared = result["remaining_ha"].sum() < 0.01
        facts["net_so_far"][name] = np.cumsum(net_gbp_per_hour(result))
        facts["money"][name] = money_breakdown(result, moisture, params)
        facts["finish_hour"][name] = cut_hours[-1] if cleared else None
    return facts


def cut_by_field(result, shape):
    """Hectares cut in each field each hour, all combines together. Returns [field, hour]."""
    machines, hours = np.nonzero(result["cut_ha"])
    cut = np.zeros(shape)
    fields = result["harvesting"][machines, hours]
    np.add.at(cut, (fields, hours), result["cut_ha"][machines, hours])
    return cut


def money_breakdown(result, true_moisture, params):
    """Where a planner's net £ came from, item by item. The items add up to result["net_gbp"].

    A hectare cut earns (value.py) its tonnes at the moisture it was cut at, times the price less
    the store's drying charge, shrunk by loss_pct_per_hour for every hour it stood. Split into:
      grain:    yield_t_per_ha (the yield at its own quoted moisture) at the full price
      weight:   tonnes gained (wet grain) or lost (dry grain) because of the moisture at cutting
      drying:   the store's drying charge and the weight it keeps for drying
      standing: crop lost by standing in the field
    then the running costs, as simulate.running_costs_gbp charges them.

    result:        from run_with_forecasts
    true_moisture: [field, hour] %, the moisture the combines cut at
    returns: {"grain", "weight", "drying", "standing", "diesel", "operator", "escort",
              "repairs"}: £ over the season, earnings positive and costs negative
    """
    cut = cut_by_field(result, true_moisture.shape)  # [field, hour] ha
    price = crop_numbers(params, "price_gbp_per_t")[0]  # [field, 1]
    yield_t = crop_numbers(params, "yield_t_per_ha")[0]
    tonnes = delivered_tonnes_per_ha(true_moisture[None], params)[0]  # [field, hour] t/ha
    drying = drying_cost_gbp_per_t(true_moisture[None], params)[0]  # [field, hour] £/t
    hours_waited = np.arange(true_moisture.shape[1])
    kept = (1 - params["loss_pct_per_hour"] / 100) ** hours_waited  # [hour] share not yet lost
    costs = running_costs_gbp(result["cut_ha"], result["driving"], result["place"], params)
    return {
        "grain": (cut * yield_t * price).sum(),
        "weight": (cut * (tonnes - yield_t) * price).sum(),
        "drying": -(cut * tonnes * drying).sum(),
        "standing": -(cut * tonnes * (price - drying) * (1 - kept)).sum(),
        **{item: -cost.sum() for item, cost in costs.items()},
    }


def print_money(facts):
    """money_breakdown for both planners in the terminal, with the difference."""
    print(f"{'GBP':<22}{BASELINE:>12}{OURS:>12}{'difference':>12}")
    for item, label in MONEY_ITEMS.items():
        base, ours = facts["money"][BASELINE][item], facts["money"][OURS][item]
        print(f"{label:<22}{base:>12,.0f}{ours:>12,.0f}{ours - base:>+12,.0f}")
    base, ours = (sum(facts["money"][name].values()) for name in (BASELINE, OURS))
    print(f"{'net':<22}{base:>12,.0f}{ours:>12,.0f}{ours - base:>+12,.0f}")


# ---------------------------------------------------------------------------------------------
# The charts: each returns a figure


def plot_money(facts, params):
    """Net £ so far through the season, one line per planner."""
    fig = slide()
    ax = fig.add_subplot()
    finals = {name: facts["net_so_far"][name][-1] for name in NAMES}
    for name in NAMES:
        net = facts["net_so_far"][name]
        ax.plot(facts["x"], net, color=COLOUR[name], label=NAMES[name])
        mark_finish(ax, facts, name, net)
        on_top = finals[name] == max(finals.values())  # the top line's label goes above it
        ax.annotate(
            gbp(finals[name]),
            (facts["x"][-1], finals[name]),
            xytext=(-4, 8 if on_top else -8),
            textcoords="offset points",
            ha="right",
            va="bottom" if on_top else "top",
            fontsize=14,
            weight="semibold",
        )
    ax.yaxis.set_major_formatter(lambda value, _: gbp(value))
    ax.set_ylabel("net £ so far")
    ax.grid(axis="y")
    ax.legend(loc="upper left")
    date_axis(ax, facts)
    gap = finals[OURS] - finals[BASELINE]
    titles(
        fig,
        f"Our controller ends the season {gbp(gap)} ahead ({gap / abs(finals[BASELINE]):+.0%})",
        f"{season_dates(facts)}. Net £: grain sold at the store's prices after drying charges, "
        "minus diesel, wages, road escort and repairs.\nDot: the day a planner cleared the farm. "
        "Our controller can work round the clock; the farmer model works a "
        f"{params['farmer_day_hours']}-hour day.",
    )
    return fig


def plot_money_gap(facts):
    """The difference in net £ (ours minus the farmer's), item by item."""
    fig = slide()
    ax = fig.add_subplot()
    labels = list(MONEY_ITEMS.values())[::-1]  # barh draws the first bar at the bottom
    changes = [facts["money"][OURS][item] - facts["money"][BASELINE][item] for item in MONEY_ITEMS]
    changes = changes[::-1]
    bars = ax.barh(labels, changes, height=0.6, color=[GAIN if c >= 0 else LOSS for c in changes])
    ax.bar_label(bars, labels=[signed_gbp(change) for change in changes], padding=5)
    ax.axvline(0, color=GREY, linewidth=0.8)
    ax.margins(x=0.15)  # room for the labels
    ax.xaxis.set_major_formatter(lambda value, _: signed_gbp(value))
    ax.set_xlabel("our controller minus the farmer model, £ over the season")
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x")
    ax.set_axisbelow(True)
    titles(
        fig,
        f"Where the {gbp(sum(changes))} difference comes from",
        f"{season_dates(facts)}. Green: our controller comes out ahead on that item. Red: it comes "
        "out behind.\nGrain harvested is every hectare cut at the store's price; the items below "
        "it are what moisture, drying, waiting and running the combines take off.",
    )
    return fig


def plot_plan(season, facts, params):
    """run.py's first two charts for the whole season: the value function the planners steered
    by, and where every combine went under each planner, on one date axis."""
    fig = slide()
    value_ax, ours_ax, base_ax = fig.subplots(3, 1, sharex=True, height_ratios=(3, 1.2, 1.2))
    n_fields = len(params["fields"])
    n_hours = len(facts["x"])
    extent_x = (edge(facts, 0), edge(facts, n_hours))

    # "nearest": one solid block per hour, not smoothed into its neighbours
    image = value_ax.imshow(
        season["value"],
        aspect="auto",
        interpolation="nearest",
        cmap="viridis",
        extent=(*extent_x, n_fields - 0.5, -0.5),
    )
    names = [field["name"].replace("_", " ") for field in params["fields"]]
    value_ax.set_yticks(range(n_fields), names, fontsize=8)
    value_ax.set_title("Expected £/ha if harvested (the plan being followed)")
    fig.colorbar(image, ax=value_ax, label="£/ha")

    for ax, name in ((ours_ax, OURS), (base_ax, BASELINE)):
        image = draw_path(ax, season["results"][name], params, extent_x)
        ax.set_title(
            f"{NAMES[name]}: solid = harvesting, faded = waiting, black = driving, blank = depot"
        )
    bar = fig.colorbar(image, ax=[ours_ax, base_ax], ticks=range(n_fields))
    bar.ax.set_yticklabels(names, fontsize=6)
    bar.ax.invert_yaxis()  # the first field at the top, as in the value chart

    for ax in (value_ax, ours_ax):
        ax.tick_params(labelbottom=False)
    date_axis(base_ax, facts)
    titles(
        fig,
        "What every field was worth, and where the combines went",
        f"{season_dates(facts)}. Top: what the newest forecast and moisture reading said a hectare "
        "would earn if cut that hour, the same for both planners.\nBelow: the field each combine "
        "was in, coloured as the scale on the right.",
    )
    return fig


def draw_path(ax, result, params, extent_x):
    """The path each combine followed, as run.py draws it, in three layers: the field it
    harvested, the field it waited in (faded), and driving (black: tab20 has greys). Blank = at
    the depot. Returns the harvested layer's image, for a colour bar."""
    n_fields = len(params["fields"])
    machines = params["machines"]
    harvested = np.ma.masked_equal(result["harvesting"], -1)
    waiting = (result["harvesting"] == -1) & ~result["driving"] & (result["place"] < n_fields)
    blocks = {
        "aspect": "auto",
        "interpolation": "nearest",
        "extent": (*extent_x, len(machines) - 0.5, -0.5),
    }
    colours = plt.get_cmap("tab20", n_fields)  # one colour per field, up to 20 fields
    field_colours = {"cmap": colours, "vmin": -0.5, "vmax": n_fields - 0.5, **blocks}
    image = ax.imshow(harvested, **field_colours)
    ax.imshow(np.ma.masked_where(~waiting, result["place"]), alpha=0.3, **field_colours)
    driving = np.ma.masked_where(~result["driving"], result["place"])
    ax.imshow(driving, cmap=ListedColormap([INK]), **blocks)
    labels = [m["name"].replace("_", " ") for m in machines]
    ax.set_yticks(range(len(machines)), labels, fontsize=9)
    ax.tick_params(axis="y", length=0)
    return image


# ---------------------------------------------------------------------------------------------
# Pieces the charts share


def save(fig, path):
    """fig as path.png (path without the .png)."""
    path = path.with_suffix(".png")
    fig.savefig(path)
    plt.close(fig)
    print(f"saved {path.relative_to(ROOT).as_posix()}")


def slide():
    """An empty figure with room at the top for titles()."""
    fig = plt.figure(layout="constrained", facecolor="white")
    fig.get_layout_engine().set(rect=(0, 0, 1, 0.86), h_pad=0.06)
    return fig


def titles(fig, title, subtitle):
    """A slide's headline and the line or two under it, in the room slide() left at the top."""
    fig.text(0.012, 0.975, title, fontsize=19, weight="semibold", va="top")
    fig.text(0.012, 0.925, subtitle, fontsize=11.5, color=GREY, va="top", linespacing=1.4)


def season_dates(facts):
    """'09 Jun to 10 Sep 2024', the days the season covers."""
    return f"{facts['times'][0]:%d %b} to {facts['times'][-1]:%d %b %Y}"


def edge(facts, hour):
    """matplotlib's date for the start of a season hour (hour may be the season's length)."""
    return facts["x"][0] + hour / 24


def date_axis(ax, facts):
    """The whole season along the bottom, from its first hour to the end of its last: a date
    every Monday, or every day over two weeks or less."""
    ax.set_xlim(edge(facts, 0), edge(facts, len(facts["x"])))
    if len(facts["x"]) > 14 * 24:
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    else:
        ax.xaxis.set_major_locator(mdates.DayLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b"))
    ax.set_xlabel("date (UTC)")


def mark_finish(ax, facts, name, values):
    """A dot, and the date, where a planner cleared the farm."""
    hour = facts["finish_hour"][name]
    if hour is None:
        return
    ax.plot(
        facts["x"][hour],
        values[hour],
        "o",
        color=COLOUR[name],
        markersize=9,
        markeredgecolor="white",
        markeredgewidth=2,
        zorder=5,
    )
    ax.annotate(
        f"done {facts['times'][hour]:%d %b}",
        (facts["x"][hour], values[hour]),
        xytext=(6, -6),
        textcoords="offset points",
        va="top",
        fontsize=11,
        color=GREY,
    )


def gbp(value):
    """£ in short words: £3.12M, £412k, £950, -£12k."""
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e6:
        return f"{sign}£{value / 1e6:.2f}M"
    if value >= 1e4:
        return f"{sign}£{value / 1e3:.0f}k"
    if value >= 1e3:
        return f"{sign}£{value / 1e3:.1f}k"
    return f"{sign}£{value:.0f}"


def signed_gbp(value):
    """gbp with a + in front of gains."""
    return ("+" if value >= 0 else "") + gbp(value)


if __name__ == "__main__":
    main()
