# Lagrangian relaxation

`lagrangian.py` implements `plan_lagrangian`, following the relaxation of weakly coupled dynamic
programs of Adelman and Mersereau (2008).

**Formulation.** Each field $i$ is a subproblem whose action $x_{it} \ge 0$ is the area cut in
hour $t$, subject to $x_{it} \le \bar r$ (one combine per field, $\bar r$ the largest combine
rate) and $\sum_t x_{it} \le a_i$, the standing area. Fields are coupled only through the hourly
capacity constraint $\sum_i x_{it} \le b$, with $b$ the combined rate of all combines. The
objective is $\sum_{i,t} V_{it} x_{it}$.

**Relaxation.** Dualising the capacity constraint with hourly multipliers $\lambda_t \ge 0$ gives

$$L(\lambda) = b \sum_t \lambda_t + \sum_i \max_{x_i} \sum_t (V_{it} - \lambda_t)\, x_{it},$$

which separates by field. Each inner problem is solved exactly by allocating area to the hours of
highest positive net value, at most $\bar r$ per hour (`field_alone`). Since area may be split
across hours, this is the LP relaxation of the subproblem and the dual bound equals the LP bound
(Geoffrion, 1974). For any $\lambda \ge 0$, $L(\lambda)$ bounds the profit attainable from $V$.
The multipliers, read as the shadow price of combine time in each hour, are found by projected
subgradient descent with normalised steps $\alpha_0/(k+1)$, keeping the lowest bound
(`machine_prices`).

**Policy.** The schedule is the one-step greedy policy with respect to the value function formed
by the field subproblems at the final $\lambda$ (Adelman and Mersereau, §3.2). A free combine is
sent to the unoccupied field with the largest gain per hectare from cutting on arrival rather
than leaving the field to its subproblem, with travel time charged at $\lambda$. It keeps its
current field if that field is within `lagrangian_wait_margin_gbp_per_ha` of indifference. The
schedule then goes through `send_idle_to_depot` (`common.py`, described in `README_greedy.md`),
which sends a combine to the depot for a gap in cutting when that costs less than waiting in the
field. `plan_mpc` warm-starts from this schedule.

**Departures from the paper.** (i) Finite horizon, undiscounted. (ii) Deterministic subproblems on
the expected value; uncertainty is handled only by replanning. (iii) Multipliers are indexed by
hour. State-dependent multipliers break separability in general (§4.3.2), but the hour is common
to every subproblem. (iv) Travel time depends on combine position rather than on a field, so it is
left out of the relaxation and restored in the policy.

The tests use $\min_\lambda L(\lambda)$ as an upper limit on every planner's earnings under a
perfect forecast.

## References

Adelman, D. and Mersereau, A.J. (2008). Relaxations of weakly coupled stochastic dynamic programs.
*Operations Research* 56(3): 712-727.

Geoffrion, A.M. (1974). Lagrangean relaxation for integer programming. *Mathematical Programming
Study* 2: 82-114.

Held, M., Wolfe, P. and Crowder, H.P. (1974). Validation of subgradient optimization.
*Mathematical Programming* 6: 62-88.


## Crop sequencing

With `sequencing` on, oilseed rape and wheat can't be cut until enough barley is. When that happens
depends on this planner's own plan, and the relaxation can't price it (it isn't a per-hour limit on
combine time, and "only after the barley is X% cut" doesn't split by field), so `simulate.py` wraps
it in `harvest.sequencing.sequenced`: plan with those crops blocked, find when the plan's barley
harvest lets them in, re-plan with them blocked only before then, until those hours settle.
