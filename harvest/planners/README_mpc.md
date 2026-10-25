# Scenario-based cooperative MPC

`mpc.py` implements `plan_mpc`, a receding-horizon economic MPC (Rawlings, Angeli and Bates,
2012). Each replan optimises a route for every combine over `plan_hours`; the simulator executes
the first `sensor_every_hours` and replans from the new moisture reading.

**Model and objective.** The prediction model (`rollout`) reproduces the simulator: travel times
rounded up to whole hours, one combine per field, cutting only in hours of positive value. The
stage reward is the value of area cut, less `drive_gbp_per_hour` while travelling and
`idle_in_field_gbp_per_hour` while standing in a field without cutting. A terminal reward
values standing area at `mpc_terminal_value_pct` of its mean value over the last
`mpc_terminal_hours`; without it the controller harvests drying crop too early.

**Uncertainty.** One plan is scored by its mean profit over up to `mpc_scenarios` evenly spaced
forecast members, a sample average approximation of expected profit (Shapiro et al., 2009). The
plan is open loop within the horizon, with feedback only through replanning. `mpc_scenarios: 0`
gives certainty-equivalent control on the mean forecast.

**Optimisation.** As in cooperative distributed MPC (Stewart et al., 2010), each combine in turn
re-optimises its route with the others held fixed, and the route is accepted only if the total
objective increases. The single-combine problem is a longest path on a time-expanded graph with
nodes (hour, location, hours cut since arrival), solved exactly by backward dynamic programming
(Bertsekas, 2017; `best_route`). The objective is monotone, so the iteration stops (after at most `mpc_rounds`) at
a plan no single combine can improve alone, which need not be globally optimal. It is
warm-started from the `plan_lagrangian` schedule to avoid poor fixed points reached from an empty
plan.

**Approximations.** The route DP ignores the combine's own earlier cutting when it returns to a
field; `improve_route` repeats the DP (`mpc_route_passes`) with fields emptied by the previous
attempt set to zero. The DP's count of hours cut advances if any scenario is dry, and the terminal
value is heuristic. Plans are always scored by the full `rollout`, so these approximations can
cost optimality but never cause a worse plan to be accepted. Combines in transit at a replan are
treated as already at their destination.

## References

Rawlings, J.B., Mayne, D.Q. and Diehl, M.M. (2017). *Model Predictive Control: Theory,
Computation, and Design*, 2nd ed. Nob Hill.

Rawlings, J.B., Angeli, D. and Bates, C.N. (2012). Fundamentals of economic model predictive
control. *51st IEEE Conference on Decision and Control*.

Stewart, B.T., Venkat, A.N., Rawlings, J.B., Wright, S.J. and Pannocchia, G. (2010). Cooperative
distributed model predictive control. *Systems & Control Letters* 59(8): 460-469.

Shapiro, A., Dentcheva, D. and Ruszczyński, A. (2009). *Lectures on Stochastic Programming*. SIAM.

Bertsekas, D.P. (2017). *Dynamic Programming and Optimal Control*, Vol. I, 4th ed. Athena
Scientific.


## Crop sequencing

With `sequencing` on, the rule is part of the MPC's model: in `rollout` oilseed rape and wheat can't
be cut in a scenario until that scenario's planned barley harvest (counted from what's already cut)
reaches the threshold. Each route is planned (`best_route`) on values blocked until the hours the
current plan lets them in, scenario by scenario, and every plan is scored with the rule applied
exactly. The warm start is the sequenced Lagrangian plan.
