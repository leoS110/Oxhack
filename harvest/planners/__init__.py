"""The planners: which field should each machine work on in each hour?

A planner only says where machines should be. simulate.py handles driving and harvesting.
One file per architecture, each with its own README_<name>.md. Every plan_* listed in __all__
is run by compare.py.
"""

from harvest.planners.common import WAIT
from harvest.planners.farmer import plan_farmer
from harvest.planners.greedy import (
    plan_urgencygreedy,
    plan_urgencygreedy_withlookahead,
    plan_vgreedy,
)
from harvest.planners.lagrangian import plan_lagrangian
from harvest.planners.mpc import plan_mpc

# the order here is the order compare.py runs and colours them in
__all__ = [
    "WAIT",
    "plan_vgreedy",
    "plan_urgencygreedy",
    "plan_urgencygreedy_withlookahead",
    "plan_lagrangian",
    "plan_mpc",
    "plan_farmer",  # the baseline: what a farmer would do without our forecasts
]
