"""Scenario-based cooperative model predictive control (MPC).

See README_mpc.md.
"""

import numpy as np

from harvest import sequencing
from harvest.planners.common import WAIT
from harvest.planners.lagrangian import plan_lagrangian

# Model predictive control (MPC): plan ahead with a model, carry out the start of the plan, then
# plan again from where you've got to. Rawlings, Mayne & Diehl (2017), "Model Predictive Control:
# Theory, Computation, and Design", 2nd ed., Nob Hill. The pieces, and where each one lives here:
#   plant        the farm and the weather that really happens: simulate.py
#   model        rollout(): the same rules as simulate.py, played out in each scenario below
#   state x      where each combine is, and the hectares still standing in each field
#   input u      schedule[machine, hour]: the place each combine should be at (or be driving to)
#   horizon      plan_hours. Only the first sensor_every_hours (6) are carried out before
#                simulate.py replans from the new reading: the "receding horizon"
#   objective    schedule_profit_gbp: £ cut, minus driving, minus combines parked in a field with
#                nothing to cut (stage cost), plus what the grain still standing at the end is
#                worth (terminal cost, terminal_gbp_per_ha). Maximising profit is minimising the
#                cost J = -profit. The objective is money rather than "follow a setpoint", which
#                makes this economic MPC (Rawlings, Angeli & Bates (2012), "Fundamentals of
#                economic model predictive control", 51st IEEE Conference on Decision and Control)
#   constraints  driving takes travel_hours, no cutting when the value is 0 (rain or too wet), a
#                field can't give more than it has, one combine per field at a time
#   optimiser    cooperative distributed MPC, below
#
# Scenarios. Planning on the average forecast ("certainty equivalence") thinks a combine in a field
# cuts every hour that any member is dry, at a watered-down price. Really it cuts at the full price
# when it's dry and sits idle when it isn't, and the field lasts longer. So each plan is played out
# in up to mpc_scenarios forecast members (scenario_values) and scored by its average profit: a
# sample average approximation of the expected profit (Shapiro, Dentcheva & Ruszczyński (2009),
# "Lectures on Stochastic Programming", SIAM; scenario MPC is surveyed in Mesbah (2016),
# "Stochastic model predictive control: an overview and perspectives for future research", IEEE
# Control Systems Magazine 36(6)). One plan serves every scenario; it adapts to the weather that
# turns up only when simulate.py replans (open-loop scenario MPC). mpc_scenarios: 0 plans on the
# average forecast alone.
#
# Optimiser. Each combine is a subsystem with its own input (its route), and all of them share one
# objective. They take turns: one combine finds its best route with the others' routes held fixed,
# and the new route is kept only if the whole plan's profit goes up. Profit never goes down, so the
# turns stop (Stewart, Venkat, Rawlings, Wright & Pannocchia (2010), "Cooperative distributed model
# predictive control", Systems & Control Letters 59(8); Rawlings, Mayne & Diehl ch. 6). It can stop
# at a plan that no single combine can improve alone but two together could: from "everyone at the
# depot", the first combine takes the best fields and the last finds nothing worth taking without
# leaving the others idle. So it starts from plan_lagrangian's plan (warm_start), which already uses
# every combine, and can only end up better than that in the model.
#
# One combine's best route is a longest-path problem on a time-expanded graph (best_route): a node
# is "at this place at the start of this hour", an edge is "stay an hour" or "drive somewhere".
# Dynamic programming solves it exactly, backwards from the end of the plan (Bertsekas, "Dynamic
# Programming and Optimal Control", Vol. I: deterministic finite-state problems are shortest
# paths). The node also counts hours cut since arriving, so the combine sees a field run out under
# it. For routing machines between fields in general, see Bochtis & Sørensen (2009), "The vehicle
# routing problem in field logistics part I", Biosystems Engineering 104(4).
#
# Known simplifications:
# - A route doesn't remember earlier visits: coming back to a field it part-cut, it thinks the
#   field is as full as when it first arrived. improve_route patches this by re-planning with the
#   fields the plan empties marked empty from then on, which can make it miss a route that cuts a
#   field later than the first try did. rollout() always counts properly, so a worse plan is never
#   kept, and the next replan starts from the true area anyway.
# - The terminal value is a rough guess (a share of the last hours' average value). Without one,
#   the plan cuts drying grain too early because nothing after the horizon counts.
# - A combine still driving at replan time is treated as already there (the planner is only told
#   where machines are), the same as the other planners.
#
# Crop sequencing (harvest/sequencing.py) is part of the model: in rollout() oilseed rape and wheat
# can't be cut in a scenario until that scenario's planned barley harvest reaches its threshold,
# counted from the barley already cut (state["active_crops"]). A route is planned (best_route) on
# values with them blocked before the hours the current plan activates them in each scenario;
# rollout() then scores it with the rule applied exactly, so a plan that breaks it is never kept.


def scenario_values(value, state, params):
    """The futures plan_mpc scores its plans in: up to mpc_scenarios forecast members.

    Members are spread evenly through state["value_by_member"]. When there are none (a planner
    called without it) or mpc_scenarios is 0, the one scenario is the average forecast.

    value:   [field, hour]              expected £/ha
    returns: [scenario, field, hour]    £/ha
    """
    by_member = state.get("value_by_member")
    if by_member is None or params["mpc_scenarios"] == 0:
        return value[None]
    n_scenarios = min(params["mpc_scenarios"], len(by_member))
    return by_member[np.linspace(0, len(by_member) - 1, n_scenarios).round().astype(int)]


def terminal_gbp_per_ha(scenarios, params):
    """MPC terminal cost: what a hectare still standing when the plan ends is worth, per field.

    Without it, grain left at the end of the plan counts for nothing, so the plan rushes to cut
    everything inside the horizon, even grain that is still drying and would be worth more later.
    Assume it can be cut later for about its average value over the plan's last hours, times
    mpc_terminal_value_pct (under 100 discounts later for being less certain). Tried on the demo:
    the best hour's value instead of the average makes the plan wait too long, even forever.

    scenarios: [scenario, field, hour]  £/ha
    returns:   [field]                  £/ha
    """
    last_hours = scenarios[:, :, -params["mpc_terminal_hours"] :]
    return params["mpc_terminal_value_pct"] / 100 * last_hours.mean(axis=(0, 2))


def ha_cut(left_ha, rate_ha_per_hour, value_gbp_per_ha):
    """Hectares a combine cuts in one hour: its rate, or what's left if less, only if worth > 0.

    Works on single numbers and on numpy arrays alike.
    """
    return np.clip(left_ha, 0, rate_ha_per_hour) * (value_gbp_per_ha > 0)


def rollout(schedule, scenarios, state, params):
    """The MPC's model: play a schedule forward hour by hour in each scenario, as simulate.py would.

    Differences from the simulator: it plays the forecast scenarios, not the true weather, it
    charges what running the plan costs (driving, and combines idle in a field), and grain still
    standing at the end is worth terminal_gbp_per_ha.

    schedule:  [machine, hour]          place each machine should be at: field index, or the
                                        number of fields for the depot
    scenarios: [scenario, field, hour]  £/ha (scenario_values)
    state:     {"remaining_ha": [field], "machine_at": [machine]}
    params:    settings from params.yaml
    returns:   {"left_ha": [scenario, field, hour + 1]  area standing at the start of each hour
                                                       (last column: the end of the plan)
                "profit_gbp": float}  £ cut minus running costs, averaged over the scenarios
    """
    n_scenarios, n_fields, n_hours = scenarios.shape
    machines = params["machines"]
    travel = np.ceil(np.array(params["travel_hours"])).astype(int)  # whole hours, as simulate.py
    idle_gbp = params["idle_in_field_gbp_per_hour"]
    remaining = np.tile(np.array(state["remaining_ha"], dtype=float), (n_scenarios, 1))
    at = list(state["machine_at"])  # place each machine is at or driving to
    ready = [0] * len(machines)  # hour each machine finishes driving
    left = np.zeros((n_scenarios, n_fields, n_hours + 1))
    profit = np.zeros(n_scenarios)
    # crop sequencing: which crops may be cut yet, in each scenario
    gated = sequencing_rule(state, params, n_fields)
    if gated:
        active = {crop: np.full(n_scenarios, on) for crop, on in state["active_crops"].items()}
        activation = {crop: np.where(on, 0, n_hours) for crop, on in active.items()}

    for hour in range(n_hours):
        left[:, :, hour] = remaining
        if gated:  # active from the start of the first hour the barley % is reached
            pct = sequencing.barley_pct_harvested(remaining, params)  # [scenario] or None
            thresholds = sequencing.thresholds(params)
            for crop in active:
                now = pct is None or pct >= thresholds[crop] - sequencing.EPS
                activation[crop] = np.where(now & ~active[crop], hour, activation[crop])
                active[crop] = active[crop] | now
        for m, machine in enumerate(machines):
            target = schedule[m, hour]
            if hour >= ready[m] and target != at[m]:  # set off; changes wait until it arrives
                ready[m] = hour + travel[at[m], target]
                at[m] = target
            if hour < ready[m]:
                profit -= params["drive_gbp_per_hour"]
            elif at[m] < n_fields:  # in a field: cut if there's grain and it's worth cutting
                field = at[m]
                value = scenarios[:, field, hour]  # [scenario]
                if gated and gated[field] is not None:  # not yet active: nothing to cut
                    value = value * active[gated[field]]
                ha = ha_cut(remaining[:, field], machine["ha_per_hour"], value)  # [scenario]
                remaining[:, field] -= ha
                profit += ha * value - idle_gbp * (ha == 0)
            # at the depot: costs nothing
    left[:, :, n_hours] = remaining
    profit += remaining @ terminal_gbp_per_ha(scenarios, params)
    model = {"left_ha": left, "profit_gbp": profit.mean()}
    if gated:
        model["activation"] = activation  # {crop: [scenario] plan hour it became active}
    return model


def sequencing_rule(state, params, n_fields):
    """[field] gated crop or None, if crop sequencing applies to this plan; else None."""
    if not sequencing.enabled(params) or "active_crops" not in state:
        return None
    gated = sequencing.gated_fields(params)
    return gated if any(crop is not None for crop in gated) and len(gated) == n_fields else None


def blocked_scenarios(scenarios, activation, params):
    """scenarios with each gated field worth 0 before its crop activates in that scenario."""
    scenarios = scenarios.copy()
    for f, crop in enumerate(sequencing.gated_fields(params)):
        if crop is not None:
            for s, hour in enumerate(activation[crop]):
                scenarios[s, f, :hour] = 0
    return scenarios


def schedule_profit_gbp(schedule, scenarios, state, params):
    """The MPC objective: £ the model says the schedule makes, net of running costs."""
    return rollout(schedule, scenarios, state, params)["profit_gbp"]


def what_others_leave(schedule, m, scenarios, state, params):
    """What the other combines' routes leave for combine m: standing area, and fields they hold.

    The standing area comes from running the plan with combine m parked at the depot.

    returns: ([field, hour] ha standing at the start of each hour without m's cutting, averaged
                            over the scenarios,
              [place, hour] bool, True where another combine is at or driving to that field)
    """
    _, n_fields, n_hours = scenarios.shape
    depot = n_fields
    without_m = schedule.copy()
    without_m[m] = depot
    machine_at = np.array(state["machine_at"])
    machine_at[m] = depot
    left = rollout(without_m, scenarios, dict(state, machine_at=machine_at), params)["left_ha"]

    taken = np.zeros((n_fields + 1, n_hours), dtype=bool)
    for other in range(len(schedule)):
        if other != m:
            taken[schedule[other], np.arange(n_hours)] = True
    taken[depot] = False  # the depot holds any number of combines
    return left[:, :, :n_hours].mean(axis=0), taken


def best_route(start, rate_ha_per_hour, scenarios, left_ha, taken, params):
    """Best route for one combine, with the other combines' routes fixed (dynamic programming).

    A node is (hour, place, hours cut since arriving). From each node the combine either stays an
    hour (cuts if there's grain and it's dry, else sits idle) or drives to another place, which
    resets the count. best[hour, place, cut_hours] is the most £ it can make from that node to the
    end of the plan, filled in backwards from the end (Bellman's equation). A hectare cut earns its
    value minus the terminal value it would have kept standing, so the totals match rollout().

    With several scenarios an hour in a field with grain earns its average over them: cut where
    it's dry, idle where it isn't. The count of hours cut goes up whenever any scenario is dry, so
    the route expects fields to run out a little early; rollout() counts each scenario properly.

    start:            int            place it starts at (field index, or the number of fields
                                     for the depot)
    rate_ha_per_hour: float          what it cuts in an hour
    scenarios:        [scenario, field, hour]  £/ha (scenario_values)
    left_ha:          [field, hour]  area the other combines leave standing (what_others_leave)
    taken:            [place, hour]  bool, another combine holds that field (what_others_leave)
    params:           settings from params.yaml
    returns:          [hour] int     place each hour; while driving, where it's driving to
    """
    _, n_fields, n_hours = scenarios.shape
    n_places = n_fields + 1  # the fields, then the depot
    depot = n_fields
    travel = np.ceil(np.array(params["travel_hours"])).astype(int)
    idle_gbp = params["idle_in_field_gbp_per_hour"]
    drive_gbp = params["drive_gbp_per_hour"]
    value = scenarios.mean(axis=0)  # [field, hour] expected £/ha
    dry = (scenarios > 0).mean(axis=0)  # [field, hour] share of scenarios it can be cut in
    # [field, hour] expected £ for each hectare it would cut, net of the terminal value it uses up
    gain = value - terminal_gbp_per_ha(scenarios, params)[:, None] * dry
    most_cut_hours = int(np.ceil(left_ha.max() / rate_ha_per_hour))  # longest a field can last
    cut_hours = np.arange(most_cut_hours + 1)
    one_more = np.minimum(cut_hours + 1, most_cut_hours)

    # the end of the plan is worth 0: what's left standing is already counted in `gain`
    best = np.zeros((n_hours + 1, n_places, most_cut_hours + 1))
    go_to = np.zeros((n_hours, n_places, most_cut_hours + 1), dtype=int)  # itself = stay

    for hour in reversed(range(n_hours)):
        # 1. stay put for this hour
        stay = np.full((n_places, most_cut_hours + 1), -np.inf)
        stay[depot] = best[hour + 1, depot, 0]
        for field in range(n_fields):
            if taken[field, hour]:
                continue  # one combine per field
            left_now = left_ha[field, hour] - rate_ha_per_hour * cut_hours
            ha = ha_cut(left_now, rate_ha_per_hour, value[field, hour])
            stay[field] = np.where(
                ha > 0,
                ha * gain[field, hour]
                - idle_gbp * (1 - dry[field, hour])
                + best[hour + 1, field, one_more],
                -idle_gbp + best[hour + 1, field, cut_hours],
            )
        best[hour] = stay
        go_to[hour] = np.arange(n_places)[:, None]

        # 2. or drive somewhere else. What that's worth doesn't depend on the hours cut so far.
        for place in range(n_places):
            for there in range(n_places):
                hours = travel[place, there]
                arrive = hour + hours
                if there == place or (there < n_fields and taken[there, hour:arrive].any()):
                    continue
                if hours == 0:
                    gbp = stay[there, 0]  # no driving time: carry on this hour at the new place
                elif arrive >= n_hours:
                    gbp = -drive_gbp * (n_hours - hour)  # still driving when the plan ends
                else:
                    gbp = -drive_gbp * hours + best[arrive, there, 0]
                better = gbp > best[hour, place]
                best[hour, place][better] = gbp
                go_to[hour, place][better] = there

    # follow the best choices forwards from the start
    route = np.zeros(n_hours, dtype=int)
    place, cut_so_far, hour = start, 0, 0
    while hour < n_hours:
        there = go_to[hour, place, cut_so_far]
        if there != place:
            hours = travel[place, there]
            route[hour : hour + hours] = there
            hour, place, cut_so_far = hour + hours, there, 0
            if hours > 0:
                continue
        route[hour] = place  # stay this hour (after a hop with no driving time: at the new place)
        if place < n_fields:
            left_now = left_ha[place, hour] - rate_ha_per_hour * cut_so_far
            cut_so_far += int(ha_cut(left_now, rate_ha_per_hour, value[place, hour]) > 0)
        hour += 1
    return route


def improve_route(schedule, m, scenarios, state, params):
    """Re-plan combine m's route with the other combines' routes fixed.

    best_route forgets earlier visits, so it can plan to keep cutting a field it has already
    emptied, and even to drive out and straight back to "refill" it. So after each try, play the
    plan through the model, mark the fields it empties (in every scenario) as empty from that hour
    on, and try again.

    schedule:  [machine, hour]          the current plan
    m:         int                      the combine to re-plan
    scenarios: [scenario, field, hour]  £/ha (scenario_values)
    returns:   ([machine, hour] the best plan tried, float its profit £)
    """
    n_hours = scenarios.shape[2]
    left_ha, taken = what_others_leave(schedule, m, scenarios, state, params)
    activation = rollout(schedule, scenarios, state, params).get("activation")
    if activation is not None:  # route on values blocked until the current plan activates them
        scenarios_for_route = blocked_scenarios(scenarios, activation, params)
    else:
        scenarios_for_route = scenarios
    rate = params["machines"][m]["ha_per_hour"]
    start = state["machine_at"][m]
    best_schedule, best_profit = schedule, -np.inf
    for _ in range(params["mpc_route_passes"]):
        candidate = schedule.copy()
        candidate[m] = best_route(start, rate, scenarios_for_route, left_ha, taken, params)
        model = rollout(candidate, scenarios, state, params)
        if model["profit_gbp"] > best_profit:
            best_schedule, best_profit = candidate, model["profit_gbp"]
        empty = model["left_ha"][:, :, :n_hours].max(axis=0) <= 1e-9  # [field, hour]
        left_ha = np.where(empty, 0, left_ha)
    return best_schedule, best_profit


def warm_start(value, state, params):
    """The plan plan_mpc starts from: plan_lagrangian's, with its WAITs read as "stay where it is".

    Cooperative MPC only ever improves the plan it starts from, so starting from a good one that
    already uses every combine stops it settling on a worse plan.

    returns: [machine, hour]  field index, or the number of fields for the depot
    """
    if sequencing_rule(state, params, value.shape[0]):
        schedule = sequencing.sequenced(plan_lagrangian)(value, state, params)
    else:
        schedule = plan_lagrangian(value, state, params)
    for m in range(len(schedule)):
        place = state["machine_at"][m]
        for hour in range(schedule.shape[1]):
            if schedule[m, hour] == WAIT:
                schedule[m, hour] = place
            place = schedule[m, hour]
    return schedule


def plan_mpc(value, state, params):
    """Cooperative MPC: each combine in turn re-plans its own route around the others' routes.

    1. Start from plan_lagrangian's plan (warm_start).
    2. For each combine: work out what the others leave it, find its best route by dynamic
       programming (improve_route), and keep that route if the whole plan's profit, averaged over
       the forecast scenarios (scenario_values), goes up.
    3. Repeat until no combine can improve the plan alone, or for mpc_rounds rounds.

    simulate.py carries out the first sensor_every_hours of the schedule and then calls this
    again. A combine may leave a field half cut for a better one and come back later, and it goes
    back to the depot when parking in a field would cost more than the drive.

    value:   [field, hour]    expected £/ha (from value.field_values)
    state:   {"remaining_ha": [field], "machine_at": [machine]}
             machine_at is a field index, or the number of fields for the depot.
             Optional "value_by_member": [member, field, hour] £/ha, to plan for several futures.
    params:  settings from params.yaml
    returns: [machine, hour]  place each machine should be at: field index, or the number of
                              fields for the depot. Never WAIT.
    """
    scenarios = scenario_values(value, state, params)  # [scenario, field, hour]
    schedule = warm_start(value, state, params)
    profit = schedule_profit_gbp(schedule, scenarios, state, params)

    for _ in range(params["mpc_rounds"]):
        improved = False
        for m in range(len(params["machines"])):
            candidate, candidate_profit = improve_route(schedule, m, scenarios, state, params)
            if candidate_profit > profit + 1e-9:  # keep only real improvements, so it stops
                schedule, profit, improved = candidate, candidate_profit, True
        if not improved:
            break
    return schedule


plan_mpc.handles_sequencing = True  # crop sequencing is in its model; simulate.py doesn't wrap it
