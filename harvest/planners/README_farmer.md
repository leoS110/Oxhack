# The farmer's baseline

`farmer.py` implements `plan_farmer`: a model of how a farmer runs the harvest without our models,
so `compare.py` can show what the other planners are worth. From what we've heard, a farmer takes
one moisture reading a day, assigns the combines from it, and waits if the weather isn't
favourable. The value function is ignored (`value` is used only for its shape). Every setting
below is in `params.yaml` and is our own guess, not a sourced figure.

## The day

The working day starts at `farmer_day_start_hour` (09:00 UTC, 10:00 in summer time, once the dew is
off) and lasts `farmer_day_hours` (12), the drives out of the yard and back home included. Outside
it the combines are in the yard (the depot), where the operators are stood down and the tanks are
filled overnight.

Each morning the farmer makes the day's one decision, from the latest moisture reading and a look
at the forecast (`state["rain_mm"]`):

1. **Is it worth going out?** If the forecast has more than `farmer_max_wet_hours` (4) hours too
   wet to cut in the working day (rain above `rain_limit_mm`, averaged over the members and the
   fields), every combine stays in the yard all day.
2. **Which fields are fit?** With $m_f$ the reading of field $f$ rounded to whole points, the way a
   farmer reads a meter, and $m^*_f$ its crop's `target_moisture_pct` (14% cereals, 9% oilseed
   rape): fit when $m_f - m^*_f \le$ `farmer_fit_above_target_pct` (4, so 18% and 13%).
3. **Which field for each combine, by feel**, among the fit fields no other combine has:
   - a field already started first (finish what you started),
   - then the readiest: fewest points above its target (drier than the target counts as ready),
   - then the nearest.

   Closeness is in moisture points and road hours, not £: crop prices, drying charges and weight
   loss don't come into it.

During the day:
- a combine cuts its field until it's done, then moves to the next field the same way, if there's
  time to drive there, cut for an hour and get home;
- it sets off home in time to be in the yard by the end of the day, done or not, and goes back to
  that field the next morning if it's still fit;
- if it rains, it waits in its field (paying the operator's standby) until it's dry;
- nothing new is decided: readings and forecasts that come in later change nothing until the next
  morning. Combines that stayed home stay home, and combines that are out carry on.

The farmer expects to cut every hour once in a field (travel time included), so it plans to move
on as soon as the field should be done. One shortcut: when a combine finishes a field during the
day, its next field is picked with the latest reading, not the morning's (the planner only gets the
latest one).

## What it needs

Beyond the usual planner inputs: `state["moisture_pct"]` (the latest reading),
`state["hour_of_day"]` (UTC hour of the plan's first column, from `start_hour_utc` in
`run_with_forecasts`) and `state["rain_mm"]` (the forecast's rain for the plan hours).
`simulate.py` passes all three.

## What it misses, and the other planners can use

- the moisture forecast: it may cut a field at 17% that would be at 14% by tomorrow, and pay to dry
  it, or leave a field that is about to get wet;
- value: a hectare of oilseed rape is worth more than a hectare of barley;
- the hour-by-hour forecast: it doesn't hurry to cut a field before a wet spell, or skip a trip
  the rain will waste, and it stands in its field through the rain;
- the night: it works 12 hours a day. **The other planners work round the clock**, so part of what
  they gain over the farmer is just the extra hours.
