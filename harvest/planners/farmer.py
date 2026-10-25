"""The farmer's baseline: a moisture meter each morning, a look at the weather app, a 12-hour day,
and no models.

See README_farmer.md.
"""

import numpy as np


def plan_farmer(value, state, params):
    """A farmer's day, decided each morning (farmer_day_start_hour, UTC) and then stuck to.

    - The weather: if the forecast has more than farmer_max_wet_hours of rain in the working day,
      every combine stays in the yard all day.
    - Otherwise each combine goes out to a field that is fit (its reading at most
      farmer_fit_above_target_pct above its crop's target_moisture_pct), picked by feel: a field
      already started first, then the readiest (whole points above the target), then the nearest.
    - A combine cuts its field until it's done, moves on to the next the same way, and sets off home
      in time to be back in the yard farmer_day_hours after the day began. It waits out any rain in
      its field.
    - During the day nothing new is decided: what's out carries on, and what stayed home stays
      home until the next morning.

    value:   [field, hour]  expected £/ha (from value.field_values), only its shape is used
    state:   {"remaining_ha": [field], "machine_at": [machine], "moisture_pct": [field],
              "hour_of_day": int, "rain_mm": [member, field, hour]}
             machine_at is a field index, or the number of fields for the depot (the yard);
             moisture_pct is the latest reading, hour_of_day the UTC hour of column 0, rain_mm the
             forecast
    params:  settings from params.yaml
    returns: [machine, hour]  field index each machine should work on, or the number of fields
                              for the depot
    """
    n_fields, n_hours = value.shape
    depot = n_fields
    machines = params["machines"]
    travel = np.ceil(np.array(params["travel_hours"])).astype(int)  # whole hours, as simulate.py
    day_hours = params["farmer_day_hours"]

    # the meter, read the way a farmer does: whole points above the crop's target moisture
    fields = params["fields"]
    target_pct = np.array(
        [params["crops"][field["crop"]]["target_moisture_pct"] for field in fields]
    )
    points_wet = np.round(state["moisture_pct"] - target_pct)  # [field]
    fit = points_wet <= params["farmer_fit_above_target_pct"]  # [field] bool
    readiness = np.maximum(points_wet, 0)  # [field] drier than the target is just as ready
    area = np.array([field["area_ha"] for field in fields])  # [field] ha, to tell what's started

    # the clock: hours into the working day, for every hour of the plan
    into_day = (state["hour_of_day"] + np.arange(n_hours) - params["farmer_day_start_hour"]) % 24
    working = into_day < day_hours  # [hour] bool
    hours_left = day_hours - into_day  # [hour] until the combines must be back in the yard
    # the weather app: chance each hour is too wet to cut, over the members and the farm
    wet = (state["rain_mm"] > params["rain_limit_mm"]).mean(axis=(0, 1))  # [hour]

    remaining = np.array(state["remaining_ha"], dtype=float)  # area we expect is still standing
    at = list(state["machine_at"])  # where each combine is, or is going
    job = [place if place < depot else None for place in at]  # field each combine is cutting
    arrives = [0] * len(machines)  # hour each combine gets to its job
    out_today = True  # planning during the day: what's out carries on
    schedule = np.full((len(machines), n_hours), depot)  # in the yard unless out working

    for hour in range(n_hours):
        morning = working[hour] and into_day[hour] == 0
        if morning:  # the day's one weather decision
            out_today = wet[hour : hour + day_hours].sum() <= params["farmer_max_wet_hours"]
        for m, machine in enumerate(machines):
            if not working[hour] or not out_today:
                job[m], at[m] = None, depot  # night, or a wet day: in the yard
                continue
            finished = job[m] is not None and remaining[job[m]] <= 0
            if morning or finished:  # send it to a field: fit, free, with time to cut and get home
                here = at[m]
                taken = job[:m] + job[m + 1 :]
                in_time = travel[here, :n_fields] + 1 + travel[:n_fields, depot] <= hours_left[hour]
                options = [
                    f
                    for f in range(n_fields)
                    if fit[f] and remaining[f] > 0 and in_time[f] and f not in taken
                ]
                # by feel: finish what's started, then the readiest, then the nearest
                job[m] = min(
                    options,
                    key=lambda f: (remaining[f] >= area[f], readiness[f], travel[here, f]),
                    default=None,
                )
                if job[m] is not None:
                    arrives[m] = hour + travel[here, job[m]]
                    at[m] = job[m]
            if job[m] is not None and hours_left[hour] <= travel[job[m], depot]:
                job[m] = None  # set off now to be home by the end of the day
            if job[m] is None:
                at[m] = depot  # nothing (more) to cut today: home
                continue
            schedule[m, hour] = job[m]
            if hour >= arrives[m]:
                remaining[job[m]] -= machine["ha_per_hour"]  # it expects to cut every hour
    return schedule
