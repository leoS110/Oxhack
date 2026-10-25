"""Crop sequencing: oilseed rape and wheat wait until enough of the farm's barley is harvested.

Settings: sequencing in params.yaml. A gated crop (one with a <crop>_requires_barley_pct, here osr
and wheat) becomes active from the first hour at which the barley % harvested reaches its
threshold, and stays active. Barley is never gated; with no barley on the farm every crop is
active from hour 0. Only cutting waits: a held-back field's moisture is worked out every hour as
usual, and its waiting-loss clock still runs from season hour 0.

Where the rule is applied:
- simulate.py never cuts a gated field before its crop is active (the real harvest).
- plan_mpc builds the rule into its own model (rollout), per scenario.
- Every other planner plans the whole horizon at once, so when a crop activates depends on its own
  plan: sequenced() re-plans until the planned activation hours stop changing (fixed point).
"""

import numpy as np

BARLEY = "barley"
SUFFIX = "_requires_barley_pct"
EPS = 1e-9  # a % this close below a threshold counts as reaching it (float sums of hectares)
IGNORES_VALUE = ("plan_farmer",)  # planners that pick fields by moisture, not by the value matrix


def enabled(params):
    return params.get("sequencing", {}).get("enabled", False)


def thresholds(params):
    """{crop: barley % it needs}, e.g. {"osr": 20, "wheat": 50}."""
    settings = params["sequencing"]
    return {key[: -len(SUFFIX)]: pct for key, pct in settings.items() if key.endswith(SUFFIX)}


def gated_fields(params):
    """[field] the field's crop if it's gated, else None."""
    gated = thresholds(params)
    return [field["crop"] if field["crop"] in gated else None for field in params["fields"]]


def barley_pct_harvested(remaining_ha, params):
    """100 x barley area harvested / barley area on the farm, from the area still standing.

    remaining_ha: [field] (or [scenario, field]) ha still standing
    returns:      float (or [scenario]) %, or None if the farm has no barley.
                  basis: fields, or a barley field without area_ha, counts barley fields finished
                  instead (with a warning in the second case)
    """
    remaining = np.asarray(remaining_ha, dtype=float)
    barley = [f for f, field in enumerate(params["fields"]) if field["crop"] == BARLEY]
    if not barley:
        return None
    areas = [params["fields"][f].get("area_ha") or 0 for f in barley]
    by_fields = params["sequencing"]["basis"] == "fields"
    if not by_fields and min(areas) <= 0:
        print("WARNING sequencing: a barley field has no area_ha; counting barley fields instead")
        by_fields = True
    if by_fields:
        return 100 * (remaining[..., barley] <= EPS).sum(axis=-1) / len(barley)
    harvested = sum(areas) - remaining[..., barley].sum(axis=-1)
    return 100 * harvested / sum(areas)


def update_active(active, pct, params):
    """{crop: True once active}: active from the first time pct reaches the crop's threshold
    (>=, not above), and active for good after that. pct None (no barley): all active."""
    return {
        crop: bool(active.get(crop, False) or pct is None or pct >= threshold - EPS)
        for crop, threshold in thresholds(params).items()
    }


def gate(value, activation_hour, params):
    """A copy of value with each gated field worth 0 before its crop's activation hour.

    value:           [..., field, hour]  £/ha
    activation_hour: {crop: plan hour it becomes active (0 = now; the number of hours = not in
                     this plan)}
    """
    value = np.array(value, dtype=float)
    for f, crop in enumerate(gated_fields(params)):
        if crop is not None:
            value[..., f, : activation_hour[crop]] = 0
    return value


def planned_activation(schedule, value, state, params, active_now):
    """Plan hour each gated crop becomes active if the schedule is carried out as planned.

    schedule:   [machine, hour]  from a planner
    value:      [field, hour]    £/ha the plan was made on (gated), for where cutting happens
    state:      {"remaining_ha": [field], "machine_at": [machine]}
    active_now: {crop: bool} active at plan hour 0
    returns:    {crop: plan hour}, the number of hours if not within the plan
    """
    from harvest.planners.common import follow_plan  # here: harvest.planners imports this module

    n_hours = value.shape[1]
    cutting = follow_plan(schedule, value, state, params)
    remaining = np.array(state["remaining_ha"], dtype=float)
    rates = [machine["ha_per_hour"] for machine in params["machines"]]
    active = dict(active_now)
    hour_of = {crop: 0 if on else n_hours for crop, on in active_now.items()}
    for hour in range(n_hours):
        active = update_active(active, barley_pct_harvested(remaining, params), params)
        for crop, on in active.items():
            if on and hour_of[crop] == n_hours:
                hour_of[crop] = hour
        for m, rate in enumerate(rates):  # what the plan cuts this hour, as follow_plan does
            if cutting["cutting"][m, hour]:
                field = cutting["place"][m, hour]
                remaining[field] -= min(remaining[field], rate)
    return hour_of


def sequenced(planner):
    """planner, with gated crops kept out until the plan's own barley harvest activates them.

    Fixed-point iteration: plan with the gated crops blocked for the whole plan, work out from that
    plan's barley cutting when each crop would activate, re-plan with them blocked only before
    those hours, and repeat until the hours stop changing (at most sequencing.max_iterations; if
    they never settle, the last plan is used and a note printed). A crop already active is never
    blocked. Planners in IGNORES_VALUE (plan_farmer) pick fields by moisture, not value, so for
    them a crop not yet active at plan hour 0 is shown as having nothing left to cut.

    state needs "active_crops": {crop: bool} (simulate.py sets it).
    """
    if getattr(planner, "handles_sequencing", False):
        return planner  # plan_mpc does it in its own model

    def plan(value, state, params):
        active_now = state["active_crops"]
        crops_waiting = {crop for crop, on in active_now.items() if not on}
        fields_waiting = [crop in crops_waiting for crop in gated_fields(params)]
        if not any(fields_waiting):
            return planner(value, state, params)  # nothing held back: plan as usual
        n_hours = value.shape[1]
        hour_of = {crop: 0 if on else n_hours for crop, on in active_now.items()}
        for _ in range(params["sequencing"]["max_iterations"]):
            gated_state, gated_value = blocked(value, state, params, hour_of, planner)
            schedule = planner(gated_value, gated_state, params)
            new_hour_of = planned_activation(schedule, gated_value, state, params, active_now)
            if new_hour_of == hour_of:
                return schedule
            hour_of = new_hour_of
        tries = params["sequencing"]["max_iterations"]
        print(
            f"sequencing: activation hours didn't settle in {tries} re-plans; using the last plan"
        )
        return schedule

    plan.__name__ = planner.__name__
    return plan


def blocked(value, state, params, hour_of, planner):
    """(state, value) for a planner with gated crops blocked before their activation hours."""
    gated_state = dict(state)
    if state.get("value_by_member") is not None:
        gated_state["value_by_member"] = gate(state["value_by_member"], hour_of, params)
    if planner.__name__ in IGNORES_VALUE:  # hide fields whose crop isn't active at plan hour 0
        remaining = np.array(state["remaining_ha"], dtype=float)
        for f, crop in enumerate(gated_fields(params)):
            if crop is not None and hour_of[crop] > 0:
                remaining[f] = 0
        gated_state["remaining_ha"] = remaining
    return gated_state, gate(value, hour_of, params)
