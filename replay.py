"""Replay past days with one planner: the forecasts really issued, and the weather that followed.

uv run python replay.py                              rollout_start to rollout_end in params.yaml
uv run python replay.py 2026-10-10 2026-10-25        your own dates (whole days, UTC, both included)
uv run python replay.py 2026-10-10 2026-10-25 mpc    ... and another planner (any plan_* name)

Every forecast_every_hours it takes the newest forecast run published by then and replans; every
sensor_every_hours too, with a new grain moisture reading. Runs and the weather are downloaded
the first time (needs internet) and then read from data/past_forecasts/ and data/replay_weather/,
so dates that are over replay offline after that. Machines harvest in the weather that really
happened. At the end it prints the path each machine followed and plots it.
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import yaml

from compare import PLANNERS
from harvest.past_data import ONE_HOUR, past_rollout, rollout_days
from harvest.planners import WAIT
from harvest.simulate import run_with_forecasts
from run import plot_results, print_results

ROOT = Path(__file__).parent
DEFAULT_PLANNER = "mpc"


def main():
    params = yaml.safe_load((ROOT / "params.yaml").read_text(encoding="utf-8"))
    args = sys.argv[1:]
    start, end = rollout_days(args, params)
    name = args[2] if len(args) >= 3 else DEFAULT_PLANNER
    if name not in PLANNERS:
        sys.exit(f"Unknown planner {name!r}. Pick one of: {', '.join(PLANNERS)}")

    print(f"Planner: {name}")
    truth, get_forecast, first_hour, runs_used = past_rollout(start, end, params)
    result = run_with_forecasts(truth, get_forecast, params, PLANNERS[name], first_hour.hour)

    print()
    print_results(result, params)
    print()
    print_path(result, params, first_hour)
    fig = plot_results(result, params, start=first_hour, marked_hours=list(runs_used))
    fig.suptitle(f"Replay with {name} (dashed line = a new forecast run)")
    fig.canvas.manager.set_window_title("Replay")
    fig.tight_layout()
    plt.show()


def print_path(result, params, first_hour):
    """The path each machine followed: one line per stretch of hours doing the same thing."""
    n_hours = len(result["earned_gbp"])
    for m, machine in enumerate(params["machines"]):
        print(machine["name"])
        doing = [activity(result, m, hour, params) for hour in range(n_hours)]  # [hour] str
        stretch_start = 0
        for hour in range(1, n_hours + 1):
            if hour == n_hours or doing[hour] != doing[stretch_start]:
                times = [first_hour + h * ONE_HOUR for h in (stretch_start, hour)]
                print(
                    f"  {times[0]:%a %d %b %H:%M} to {times[1]:%a %d %b %H:%M}  "
                    f"{doing[stretch_start]} ({hour - stretch_start} h)"
                )
                stretch_start = hour


def activity(result, m, hour, params):
    """What machine m was doing in that hour, in words."""
    names = [field["name"] for field in params["fields"]] + ["the depot"]  # [place] str
    field = result["harvesting"][m, hour]
    where = result["place"][m, hour]
    if field != WAIT:
        return f"harvest {names[field]}"
    if result["driving"][m, hour]:
        return f"drive to {names[where]}"
    if where == len(params["fields"]):
        return "at the depot"
    return f"wait at {names[where]}"


if __name__ == "__main__":
    main()
