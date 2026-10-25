"""The replanning loop: step through the season hour by hour.

Every `sensor_every_hours` a new grain moisture reading comes in, and every `forecast_every_hours`
a newer forecast. Each time, the value function is worked out again and the planner makes a new
plan. Machines follow the latest plan. This file (not the planner) handles driving time and what
actually gets harvested, on the weather that really happens, and what running the combines costs
(running_costs in params.yaml).

It also keeps each combine's fuel tank, a layer above the planners: it burns the diesel each hour
of cutting and driving, refills at the depot, sends a combine home when its tank runs low whatever
the plan says, and passes the fuel left to the planner as telemetry (state["fuel_litres"]).

Two ways in:
- run_season: one fixed forecast; one of its members plays the weather that really happens.
- run_with_forecasts: the real weather, and a function that gives the newest forecast at any hour
  (replay.py and compare.py use it with the forecasts that were really issued, see
  harvest/past_data.py).
"""

import numpy as np
import pandas as pd

from harvest import sequencing
from harvest.planners import WAIT, plan_urgencygreedy
from harvest.value import field_values, member_values, predict_moisture, start_moisture


def run_season(weather, params, planner=plan_urgencygreedy):  # choose which planner to employ here
    """Simulate the season on one forecast that never changes.

    weather: {name: [member, field, hour]}  from forecast.load_forecast
    params:  settings from params.yaml
    planner: function(value, state, params) -> schedule [machine, hour]
             (field index, number of fields = go to the depot, WAIT = stay where it is)
    returns: see run_with_forecasts
    """
    # one forecast member plays the weather that really happens; the planner sees the others
    member = params["truth_member"]  # int
    n_hours = params["season_hours"]  # int
    # {name: [1, field, hour]}
    truth = {name: values[[member], :, :n_hours] for name, values in weather.items()}
    # {name: [member - 1, field, hour]}
    forecast = {name: np.delete(values, member, axis=0) for name, values in weather.items()}

    def get_forecast(hour):  # the same forecast every time, from that hour on
        return {name: values[:, :, hour:] for name, values in forecast.items()}

    start_hour_utc = pd.Timestamp(params["forecast_start"]).hour  # the CSV's first hour
    return run_with_forecasts(truth, get_forecast, params, planner, start_hour_utc)


def run_with_forecasts(truth, get_forecast, params, planner=plan_urgencygreedy, start_hour_utc=0):
    """Simulate a season in which the forecast gets updated as time goes on.

    truth:          {name: [1, field, hour]}  the weather that really happens, one column per hour
                    of the season (the season is as long as this)
    get_forecast:   function(hour) -> {name: [member, field, hour]}  the newest forecast at that
                    season hour, its first column being that hour. Called every
                    forecast_every_hours; it must cover at least plan_hours + forecast_every_hours
    params:         settings from params.yaml
    planner:        function(value, state, params) -> schedule [machine, hour]
    start_hour_utc: int, hour of the day (UTC) of season hour 0, so planners can tell the time
    returns: {
        "total_gbp":    float            money earned
        "earned_gbp":   [hour]           money earned in each hour
        "running_cost_gbp": [machine, hour]  what running each machine cost (running_costs_gbp)
        "cost_breakdown_gbp": {"diesel", "operator", "escort", "repairs"}  £ over the season
        "net_gbp":      float            money earned minus running costs
        "cut_ha":       [machine, hour]  hectares each machine cut
        "harvesting":   [machine, hour]  field each machine harvested, WAIT (-1) if none
        "place":        [machine, hour]  where each machine is, or is driving to: field index,
                                         or number of fields = the depot
        "driving":      [machine, hour]  True while the machine is on the road
        "fuel_litres":  [machine, hour]  diesel left in each tank at the end of each hour
        "remaining_ha": [field]          area left standing at the end
        "plans":        one per replan: {"hour", "value" [field, hour], "schedule" [machine, hour]}
        "moisture_pct": [field, hour]    true grain moisture at the start of each hour, %
        "activation":   {crop: {"hour", "barley_pct"} or None}  with sequencing on: the season hour
                                         each gated crop became active and the barley % then
                                         (None: not in this season). {} with sequencing off
        "waited_ripe_hours": {crop: int}  field-hours a gated field could have been cut (true
                                         value above 0, grain left) but its crop wasn't active
    }
    With sequencing on (params.yaml), oilseed rape and wheat aren't cut until enough barley is
    (harvest/sequencing.py): the simulator never cuts them before, and the planner is wrapped in
    sequencing.sequenced so it plans for it.
    """
    fields, machines = params["fields"], params["machines"]  # list of dicts: [field], [machine]
    names = [field["name"] for field in fields]  # [field] str
    n_hours = truth["rain_mm"].shape[2]  # int

    first_moisture = start_moisture(truth, params)  # [field] %
    true_moisture = predict_moisture(first_moisture, truth, params)[0]  # [field, hour]
    true_value = field_values(truth, first_moisture, params)  # [field, hour]
    travel = np.ceil(np.array(params["travel_hours"])).astype(int)  # [field+1, field+1] int hours
    depot = len(fields)  # int, place index of the depot
    litres = params["running_costs"]  # what cutting and driving burn

    remaining = np.array([field["area_ha"] for field in fields], dtype=float)  # [field] ha
    tank = np.array([m["fuel_tank_litres"] for m in machines], dtype=float)  # [machine] litres
    fuel = tank.copy()  # [machine] litres in each tank now: every combine starts full
    fuel_litres = np.zeros((len(machines), n_hours))  # [machine, hour] left at the end of each hour
    machine_at = np.array(  # [machine] int field index, len(fields) = depot
        [len(fields) if m["start"] == "depot" else names.index(m["start"]) for m in machines]
    )
    arrives_at = np.zeros(len(machines), dtype=int)  # [machine] hour a driving machine gets there
    earned = np.zeros(n_hours)  # [hour] £
    cut_ha = np.zeros((len(machines), n_hours))  # [machine, hour] ha
    harvesting = np.full((len(machines), n_hours), WAIT)  # [machine, hour] field index or WAIT
    place = np.zeros((len(machines), n_hours), dtype=int)  # [machine, hour] field index or depot
    driving = np.zeros((len(machines), n_hours), dtype=bool)  # [machine, hour]
    plans = []  # list of dicts, one per replan

    # crop sequencing: which crops may be cut yet (harvest/sequencing.py)
    sequencing_on = sequencing.enabled(params)
    gated = sequencing.gated_fields(params) if sequencing_on else [None] * len(fields)
    plan_with = sequencing.sequenced(planner) if sequencing_on else planner
    active = {}  # {crop: bool}
    activation = {crop: None for crop in sequencing.thresholds(params)} if sequencing_on else {}
    waited = {crop: 0 for crop in activation}  # {crop: field-hours ripe but not yet active}

    for hour in range(n_hours):
        if sequencing_on:  # active from the start of the first hour the barley % is reached
            barley_pct = sequencing.barley_pct_harvested(remaining, params)
            active = sequencing.update_active(active, barley_pct, params)
            for crop, on in active.items():
                if on and activation[crop] is None:
                    activation[crop] = {"hour": hour, "barley_pct": barley_pct}
            for f, crop in enumerate(gated):
                if crop is not None and not active[crop]:
                    waited[crop] += int(true_value[f, hour] > 0 and remaining[f] > 0)
        new_reading = hour % params["sensor_every_hours"] == 0  # bool
        new_forecast = hour % params["forecast_every_hours"] == 0  # bool
        if new_reading:
            reading = true_moisture[:, hour]  # [field] %
        if new_forecast:
            forecast = get_forecast(hour)  # {name: [member, field, hour since forecast_hour]}
            forecast_hour = hour  # int, season hour of the forecast's first column
        if new_reading or new_forecast:
            # new field values -> new plan. Between readings, it starts from the last reading
            first = hour - forecast_hour  # int, the forecast column for this hour
            end = first + params["plan_hours"]  # int
            # {name: [member, field, plan_hour]}
            upcoming = {name: values[:, :, first:end] for name, values in forecast.items()}
            # [member, field, plan_hour] £/ha
            by_member = member_values(upcoming, reading, params, first_hour=hour)
            value = by_member.mean(axis=0)  # [field, plan_hour] £/ha, same as field_values
            state = {
                "remaining_ha": remaining.copy(),
                "machine_at": machine_at.copy(),
                "value_by_member": by_member,  # optional extra; only plan_mpc uses it
                "fuel_litres": fuel.copy(),  # telemetry from the tanks; no planner uses it yet
                # only plan_farmer uses these three
                "moisture_pct": reading.copy(),  # latest sensor reading
                "hour_of_day": (start_hour_utc + hour) % 24,  # UTC, of plan column 0
                "rain_mm": upcoming["rain_mm"],  # [member, field, plan_hour] forecast rain
                "active_crops": dict(active),  # {crop: bool}, with sequencing on
            }
            schedule = plan_with(value, state, params)  # [machine, plan_hour] field index or WAIT
            plans.append({"hour": hour, "value": value, "schedule": schedule})
            plan_start = hour  # int, season hour of schedule column 0

        for m, machine in enumerate(machines):  # int, dict
            target = schedule[m, hour - plan_start]  # int field index or WAIT
            here = machine_at[m]  # int, where it is or is driving to
            going_to = here if target == WAIT else target  # int
            on_the_road = hour < arrives_at[m]  # bool, a trip once started is always finished
            if not on_the_road and must_refuel(fuel[m], here, going_to, machine, travel, params):
                target = depot  # the fuel gauge overrules the plan
            if hour >= arrives_at[m] and target != WAIT and target != machine_at[m]:
                arrives_at[m] = hour + travel[machine_at[m], target]  # start driving
                machine_at[m] = target
            arrived = hour >= arrives_at[m] and target == machine_at[m]  # bool
            in_field = target < len(fields)  # bool, False at the depot
            # crop sequencing: a field whose crop isn't active yet isn't cut; the combine waits
            held_back = arrived and in_field and gated[target] is not None
            held_back = held_back and not active[gated[target]]
            if (
                arrived
                and in_field
                and not held_back
                and remaining[target] > 0
                and true_value[target, hour] > 0
            ):
                ha = min(remaining[target], machine["ha_per_hour"])  # float
                remaining[target] -= ha
                earned[hour] += ha * true_value[target, hour]
                cut_ha[m, hour] = ha
                harvesting[m, hour] = target
            place[m, hour] = machine_at[m]
            driving[m, hour] = hour < arrives_at[m]
            # the same litres running_costs_gbp charges for; waiting in a field burns nothing
            fuel[m] -= (
                cut_ha[m, hour] * litres["cutting_diesel_litres_per_ha"]
                + driving[m, hour] * litres["road_diesel_litres_per_hour"]
            )
            if machine_at[m] == depot and not driving[m, hour]:  # parked at the depot: fill up
                fuel[m] = min(tank[m], fuel[m] + params["depot_refuel_litres_per_hour"])
            fuel_litres[m, hour] = fuel[m]

    costs = running_costs_gbp(cut_ha, driving, place, params)  # {item: [machine, hour] £}
    running_cost = sum(costs.values())  # [machine, hour] £
    return {
        "total_gbp": earned.sum(),
        "earned_gbp": earned,
        "running_cost_gbp": running_cost,
        "cost_breakdown_gbp": {item: cost.sum() for item, cost in costs.items()},
        "net_gbp": earned.sum() - running_cost.sum(),
        "cut_ha": cut_ha,
        "harvesting": harvesting,
        "place": place,
        "driving": driving,
        "fuel_litres": fuel_litres,
        "remaining_ha": remaining,
        "plans": plans,
        "moisture_pct": true_moisture,
        "activation": activation,
        "waited_ripe_hours": waited,
    }


def must_refuel(fuel_litres, here, going_to, machine, travel, params):
    """Should a combine head to the depot now, whatever the plan says?

    Yes if the tank can't cover the trip to where the plan sends it, an hour of cutting there,
    and the drive home, with fuel_reserve_pct of the tank still left on reaching the depot.
    Checked every hour the combine isn't on the road, so (given a tank that can get it home from
    where it starts) it never runs dry in a field. The planners don't plan refuelling yet.

    fuel_litres: float  diesel in the tank now
    here:        int    where it is: field index, or number of fields = the depot
    going_to:    int    where the plan sends it (here when it's told to WAIT)
    machine:     dict   one entry of params["machines"]
    travel:      [field+1, field+1] int hours
    returns:     bool
    """
    depot = len(travel) - 1
    if going_to == depot:
        return False  # going home anyway
    litres = params["running_costs"]
    road_hours = travel[here, going_to] + travel[going_to, depot]  # there, then home
    needed = (
        road_hours * litres["road_diesel_litres_per_hour"]
        + machine["ha_per_hour"] * litres["cutting_diesel_litres_per_ha"]  # an hour of cutting
        + machine["fuel_tank_litres"] * params["fuel_reserve_pct"] / 100
    )
    return fuel_litres < needed


def activities(cut_ha, driving, place, n_fields):
    """What each machine is doing each hour. In every hour exactly one of the four is True.

    cut_ha:   [machine, hour] hectares cut
    driving:  [machine, hour] bool, on the road
    place:    [machine, hour] where it is or is driving to: field index, or n_fields = the depot
    returns:  {"cutting", "driving", "waiting", "depot"}: [machine, hour] bool each. Waiting means
              in a field without cutting: raining, too wet, nothing left, or told to wait
    """
    cutting = cut_ha > 0
    depot = (place == n_fields) & ~driving
    waiting = ~cutting & ~driving & ~depot
    return {"cutting": cutting, "driving": driving, "waiting": waiting, "depot": depot}


def running_costs_gbp(cut_ha, driving, place, params):
    """What running the combines costs, item by item, from what each one does each hour.

    These are the farm's real costs (running_costs in params.yaml, with sources), not the
    planners' own simpler numbers, so every planner is scored the same way.
    - diesel:   per hectare cut, and per hour on the road
    - operator: every hour cutting, driving or waiting in a field (paid standby). Nothing at the
                depot, where the operator is stood down
    - escort:   every hour on the road (a combine over 3.5 m wide needs an attendant)
    - repairs:  every hour the engine runs: cutting or driving

    cut_ha, driving, place: as for activities
    returns: {"diesel", "operator", "escort", "repairs"}: [machine, hour] £ each
    """
    costs = params["running_costs"]
    doing = activities(cut_ha, driving, place, len(params["fields"]))
    engine_on = doing["cutting"] | doing["driving"]  # [machine, hour] bool
    litres = (  # [machine, hour]
        cut_ha * costs["cutting_diesel_litres_per_ha"]
        + doing["driving"] * costs["road_diesel_litres_per_hour"]
    )
    return {
        "diesel": litres * costs["diesel_gbp_per_litre"],
        "operator": (engine_on | doing["waiting"]) * costs["operator_gbp_per_hour"],
        "escort": doing["driving"] * costs["road_escort_gbp_per_hour"],
        "repairs": engine_on * costs["repairs_gbp_per_engine_hour"],
    }
