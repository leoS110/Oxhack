"""Tests for crop sequencing (harvest/sequencing.py): oilseed rape and wheat wait for barley.
No internet: hand-made farms and weather. Run:  uv run pytest"""

import copy
from pathlib import Path

import numpy as np
import pytest
import yaml

import harvest.planners as planners
from harvest.forecast import load_forecast
from harvest.sequencing import barley_pct_harvested, update_active
from harvest.simulate import run_season

ROOT = Path(__file__).parent.parent
PLANNERS = [getattr(planners, name) for name in planners.__all__ if name.startswith("plan_")]


def demo_params():
    return yaml.safe_load((ROOT / "params.yaml").read_text(encoding="utf-8"))


def sequenced_farm(n_hours=24, crops=("barley", "osr", "wheat"), areas=(10.0, 6.0, 6.0)):
    """10 ha of barley, 6 of oilseed rape, 6 of wheat, thresholds 20 / 50; two 1 ha/h combines,
    no driving time, dry weather all day. Oilseed rape is worth far more than barley, so a planner
    would cut it first if it could."""
    params = demo_params()
    params["sequencing"].update(
        enabled=True, osr_requires_barley_pct=20, wheat_requires_barley_pct=50, basis="area"
    )
    params.update(
        season_hours=n_hours,
        plan_hours=n_hours,
        sensor_every_hours=6,
        forecast_every_hours=6,
        fields=[
            {"name": f"f{i}", "crop": crop, "area_ha": area}
            for i, (crop, area) in enumerate(zip(crops, areas))
        ],
        machines=[
            {"name": "m0", "ha_per_hour": 1.0, "start": "depot", "fuel_tank_litres": 5000},
            {"name": "m1", "ha_per_hour": 1.0, "start": "depot", "fuel_tank_litres": 5000},
        ],
        travel_hours=np.zeros((len(crops) + 1, len(crops) + 1)).tolist(),
    )
    for crop in set(crops):
        params["moisture_model"]["crops"][crop]["start_moisture_pct"] = 14
    shape = (3, len(crops), n_hours)  # [member, field, hour]
    weather = {"rain_mm": np.zeros(shape), "humidity_pct": np.full(shape, 60.0)}
    return params, weather


def barley_cut_before_each_hour(result, params):
    """[hour] barley hectares harvested before the start of each hour."""
    barley = [f for f, field in enumerate(params["fields"]) if field["crop"] == "barley"]
    cut = np.zeros(result["cut_ha"].shape[1])
    for f in barley:
        cut += (result["cut_ha"] * (result["harvesting"] == f)).sum(axis=0)
    return np.concatenate([[0.0], np.cumsum(cut)[:-1]])


@pytest.mark.parametrize("planner", PLANNERS)
def test_osr_waits_for_2_ha_of_barley_and_wheat_for_5(planner):
    params, weather = sequenced_farm()
    result = run_season(weather, params, planner)
    barley_before = barley_cut_before_each_hour(result, params)
    for f, crop, needs_ha in [(1, "osr", 2.0), (2, "wheat", 5.0)]:
        hours_cut = np.where((result["harvesting"] == f).any(axis=0))[0]
        assert (barley_before[hours_cut] >= needs_ha - 1e-9).all(), (crop, hours_cut)
        when = result["activation"][crop]
        if when is not None:  # it activated: at the first hour enough barley was cut
            assert barley_before[when["hour"]] >= needs_ha - 1e-9
            assert when["hour"] == 0 or barley_before[when["hour"] - 1] < needs_ha
    if planner is not planners.plan_farmer:  # the farmer picks by moisture and its 12-hour day
        assert result["activation"]["osr"] is not None


def test_each_plan_keeps_osr_and_wheat_out_until_its_own_barley_reaches_them():
    params, weather = sequenced_farm()
    result = run_season(weather, params, planners.plan_lagrangian)
    first = result["plans"][0]  # planned at hour 0, nothing cut yet
    from harvest.planners.common import follow_plan

    state = {"remaining_ha": np.array([10.0, 6.0, 6.0]), "machine_at": np.array([3, 3])}
    plan = follow_plan(first["schedule"], first["value"], state, params)
    barley_ha = (plan["cutting"] & (plan["place"] == 0)).sum(axis=0)  # 1 ha per combine-hour
    barley_before = np.concatenate([[0], np.cumsum(barley_ha)[:-1]])
    for f, needs_ha in [(1, 2.0), (2, 5.0)]:
        planned = (plan["cutting"] & (plan["place"] == f)).any(axis=0)
        planned_ok = barley_before >= needs_ha
        # the value the planner saw is 0 before then, so it can't plan to cut it any earlier
        assert not (planned & ~planned_ok).any()


def test_the_trigger_fires_at_exactly_the_threshold():
    params, _ = sequenced_farm()
    pct = barley_pct_harvested(np.array([8.0, 6.0, 6.0]), params)  # 2 of 10 ha cut
    assert pct == pytest.approx(20.0)
    assert update_active({}, pct, params) == {"osr": True, "wheat": False}  # >=, not above
    assert update_active({}, 19.99, params)["osr"] is False


def test_no_barley_means_osr_and_wheat_are_active_from_hour_0():
    params, weather = sequenced_farm(crops=("osr", "wheat"), areas=(6.0, 6.0))
    assert barley_pct_harvested(np.array([6.0, 6.0]), params) is None
    result = run_season(weather, params, planners.plan_urgencygreedy)
    assert result["activation"]["osr"]["hour"] == 0
    assert result["activation"]["wheat"]["hour"] == 0
    assert result["waited_ripe_hours"] == {"osr": 0, "wheat": 0}


def test_activation_is_permanent():
    params, _ = sequenced_farm()
    active = update_active({}, 55.0, params)
    assert active == {"osr": True, "wheat": True}
    assert update_active(active, 5.0, params) == {"osr": True, "wheat": True}  # stays active


def test_barley_fields_without_area_are_counted_instead(capsys):
    params, _ = sequenced_farm()
    del params["fields"][0]["area_ha"]
    params["fields"].append({"name": "f3", "crop": "barley", "area_ha": 4.0})
    remaining = np.array([0.0, 6.0, 6.0, 4.0])  # one of the two barley fields finished
    assert barley_pct_harvested(remaining, params) == pytest.approx(50)
    assert "counting barley fields" in capsys.readouterr().out


@pytest.mark.parametrize("planner", PLANNERS)
def test_sequencing_off_is_the_old_behaviour(planner):
    # off, or on with nothing held back (thresholds 0: active from hour 0), plans and harvests
    # exactly the same as without the rule
    params, weather = sequenced_farm()
    off = copy.deepcopy(params)
    off["sequencing"]["enabled"] = False
    zero = copy.deepcopy(params)
    zero["sequencing"].update(osr_requires_barley_pct=0, wheat_requires_barley_pct=0)
    result_off, result_zero = run_season(weather, off, planner), run_season(weather, zero, planner)
    assert result_off["activation"] == {}
    assert result_zero["total_gbp"] == result_off["total_gbp"]
    assert (result_zero["harvesting"] == result_off["harvesting"]).all()


def test_sequencing_off_on_the_demo_farm_changes_nothing_but_the_rule():
    params = demo_params()
    weather = load_forecast(ROOT / "data" / "forecast.csv", params)
    params["sequencing"]["enabled"] = False
    off = run_season(weather, params)
    params["sequencing"]["enabled"] = True
    params["sequencing"].update(osr_requires_barley_pct=0, wheat_requires_barley_pct=0)
    zero = run_season(weather, params)
    assert zero["net_gbp"] == off["net_gbp"]
    assert (zero["harvesting"] == off["harvesting"]).all()


def test_held_back_fields_keep_drying():
    params, weather = sequenced_farm()
    weather["humidity_pct"][:] = 40.0  # dry air: the grain dries every hour, cut or not
    for crop in ("barley", "osr", "wheat"):
        params["moisture_model"]["crops"][crop]["start_moisture_pct"] = 15
    gated = run_season(weather, params, planners.plan_urgencygreedy)
    params["sequencing"]["enabled"] = False
    ungated = run_season(weather, params, planners.plan_urgencygreedy)
    assert gated["moisture_pct"] == pytest.approx(ungated["moisture_pct"])
    hour = gated["activation"]["wheat"]["hour"]
    assert hour > 0
    # wheat's moisture when it's let in is what it would have been without the rule, not 15%
    assert gated["moisture_pct"][2, hour] == pytest.approx(ungated["moisture_pct"][2, hour])
    assert gated["moisture_pct"][2, hour] < 15
