"""Tests for the whole pipeline. Run:  uv run pytest"""

from pathlib import Path

import numpy as np
import pytest
import yaml

from harvest import planners
from harvest.forecast import load_forecast, load_moisture
from harvest.planners import WAIT, plan_farmer, plan_lagrangian, plan_mpc, plan_vgreedy
from harvest.planners.common import follow_plan, send_idle_to_depot
from harvest.planners.lagrangian import field_alone, machine_prices
from harvest.simulate import run_season, run_with_forecasts
from harvest.value import (
    delivered_tonnes_per_ha,
    drying_cost_gbp_per_t,
    field_values,
    member_values,
    predict_moisture,
    start_moisture,
)

ROOT = Path(__file__).parent.parent
MOISTURE_CSV = ROOT / "outputs" / "moisture_forecast.csv"


# Camgrain cereal drying terms (dry to 14%, free up to 15%) for the hand-made crops below
CEREAL_DRYING = {"target_moisture_pct": 14.0, "free_drying_up_to_pct": 15.0}

# rain_farm: hour 0 at 16%, where its yield is quoted; hour 1 at 15%, where the same dry matter
# weighs 84/85 as much and the store keeps 1.25% of it
RAIN_FARM_HOUR_0 = 93.5
RAIN_FARM_HOUR_1 = 98.75 * 0.84 / 0.85  # £97.59/ha
RAIN_FARM_BEST = RAIN_FARM_HOUR_0 + RAIN_FARM_HOUR_1  # £191.09


def demo_params():
    return yaml.safe_load((ROOT / "params.yaml").read_text(encoding="utf-8"))


def start_every_crop_at(params, pct):
    """Hour-0 grain moisture (%) of every crop in params["crops"], for the hand-made farms.

    Only start_moisture_pct is set: these farms have no temperature, so the moisture model never
    runs; predict_moisture uses its simple humidity/rain model instead.
    """
    params["moisture_model"]["crops"] = {
        crop: {"start_moisture_pct": pct} for crop in params["crops"]
    }


def tiny_farm():
    """Hand-made case where we know the answer.

    One machine starts at field 0. Four dry hours. Driving between fields takes 2 hours.
    Field 0: 4 ha worth £50/ha. Field 1: 2 ha worth £80/ha (grain stays at 14%, where the 1 t/ha
    yield is quoted, so it weighs exactly 1 t). The machine cuts 1 ha per hour.
    Staying on field 0 earns 4 x £50 = £200 (the best possible).
    Driving to field 1 earns 2 x £80 = £160 (2 hours lost to driving).
    """
    params = demo_params()
    params.update(
        season_hours=4,
        plan_hours=4,
        sensor_every_hours=4,
        forecast_every_hours=4,
        loss_pct_per_hour=0,
        drying_pct_per_hour=0,
        crops={
            "cheap": {
                "price_gbp_per_t": 50,
                "yield_t_per_ha": 1,
                "yield_moisture_pct": 14,
                **CEREAL_DRYING,
            },
            "dear": {
                "price_gbp_per_t": 80,
                "yield_t_per_ha": 1,
                "yield_moisture_pct": 14,
                **CEREAL_DRYING,
            },
        },
        fields=[
            {"name": "f0", "crop": "cheap", "area_ha": 4},
            {"name": "f1", "crop": "dear", "area_ha": 2},
        ],
        machines=[{"name": "m0", "ha_per_hour": 1, "start": "f0", "fuel_tank_litres": 500}],
        travel_hours=[[0, 2, 2], [2, 0, 2], [2, 2, 0]],
    )
    start_every_crop_at(params, 14)
    dry_weather = {
        "rain_mm": np.zeros((2, 2, 4)),  # [member, field, hour]
        "humidity_pct": np.full((2, 2, 4), 60.0),
    }
    return params, dry_weather


def rain_farm():
    """Hand-made case where waiting for each field's best hour is a trap.

    One machine at the depot cuts 1 ha per hour; no driving time. Two 1 ha fields of the same
    crop at £100/t, 1 t/ha quoted at 16%. The grain dries from 16% to 15% by hour 1, then it rains
    in hour 2. Value of each field: hour 0 £93.50/ha (1 t, less a £4 drying charge and 2.5% weight
    kept), hour 1 £97.59/ha (84/85 t, as drier grain weighs less, less 1.25% weight kept), hour 2
    nothing.
    Best: one field now and the other in hour 1, £191.09. Both fields' best hour is hour 1, so a
    planner that waits for it cuts only one field: £97.59.
    """
    params = demo_params()
    params.update(
        season_hours=3,
        plan_hours=3,
        sensor_every_hours=3,
        forecast_every_hours=3,
        loss_pct_per_hour=0,
        drying_pct_per_hour=1,
        crops={
            "grain": {
                "price_gbp_per_t": 100,
                "yield_t_per_ha": 1,
                "yield_moisture_pct": 16,
                **CEREAL_DRYING,
            }
        },
        fields=[
            {"name": "f0", "crop": "grain", "area_ha": 1},
            {"name": "f1", "crop": "grain", "area_ha": 1},
        ],
        machines=[{"name": "m0", "ha_per_hour": 1, "start": "depot", "fuel_tank_litres": 500}],
        travel_hours=np.zeros((3, 3)).tolist(),
    )
    start_every_crop_at(params, 16)
    rain = np.zeros((2, 2, 3))  # [member, field, hour]
    rain[:, :, 2] = 1.0
    weather = {"rain_mm": rain, "humidity_pct": np.zeros((2, 2, 3))}  # bone-dry air
    return params, weather


def round_running_costs(params):
    """Running costs easy to add up by hand. Per hour: cutting 1 ha costs £10 diesel + £15
    operator + £30 repairs = £55; driving £20 diesel + £15 + £5 escort + £30 = £70; waiting in a
    field £15 (the operator); the depot nothing."""
    params["running_costs"] = {
        "diesel_gbp_per_litre": 1.0,
        "cutting_diesel_litres_per_ha": 10,
        "road_diesel_litres_per_hour": 20,
        "operator_gbp_per_hour": 15,
        "road_escort_gbp_per_hour": 5,
        "repairs_gbp_per_engine_hour": 30,
    }
    return params


def stay_on_field_0(value, state, params):
    return np.zeros((1, value.shape[1]), dtype=int)


def drive_to_field_1(value, state, params):
    return np.ones((1, value.shape[1]), dtype=int)


def go_to_depot(value, state, params):
    return np.full((1, value.shape[1]), value.shape[0])


def one_combine_farm(n_fields, area_ha, start):
    """Settings for planning straight from a value matrix: one 1 ha/hour combine, 1 h drives.

    Grain left at the end of the plan is worth nothing, unless a test changes it. Going to the
    depot must save more than £20.
    """
    params = demo_params()
    params.update(
        fields=[{"name": f"f{f}", "crop": "wheat", "area_ha": area_ha[f]} for f in range(n_fields)],
        machines=[{"name": "m0", "ha_per_hour": 1, "start": start, "fuel_tank_litres": 500}],
        travel_hours=(1 - np.eye(n_fields + 1)).tolist(),
        idle_in_field_gbp_per_hour=20,
        drive_gbp_per_hour=10,
        depot_when_idle=True,
        depot_margin_gbp=20,
        mpc_terminal_value_pct=0,
    )
    return params


def test_forecast_loads_as_member_field_hour_arrays():
    params = demo_params()
    weather = load_forecast(ROOT / "data" / "forecast.csv", params)
    n_members, n_fields, n_hours = weather["rain_mm"].shape
    assert n_fields == len(params["fields"])
    assert n_members > 1 and n_hours >= params["season_hours"]


def test_value_has_one_number_per_field_and_hour():
    params = demo_params()
    weather = load_forecast(ROOT / "data" / "forecast.csv", params)
    value = field_values(weather, start_moisture(weather, params), params)
    assert value.shape == weather["rain_mm"].shape[1:]
    assert (value >= 0).all()


def test_drying_cost_follows_camgrain_bands():
    params = demo_params()
    params["fields"] = [{"name": "f0", "crop": "free"}, {"name": "f1", "crop": "dear"}]
    params["crops"] = {
        "free": {"price_gbp_per_t": 0, **CEREAL_DRYING},
        "dear": {"price_gbp_per_t": 200, **CEREAL_DRYING},
    }
    moisture = np.array([[[14.0, 15.0, 15.1, 15.5, 15.6, 16.0]] * 2])  # [member, field, hour]
    cost = drying_cost_gbp_per_t(moisture, params)
    assert cost[0, 0] == pytest.approx([0, 0, 2, 2, 4, 4])  # price 0 leaves only the charge
    # at 16%: £4 charge + 2 points x 1.25% of a £200 tonne = £5 of weight lost
    assert cost[0, 1, -1] == pytest.approx(9)


def test_moisture_csv_gives_each_field_its_own_crop(tmp_path):
    params = demo_params()
    params["fields"] = [{"name": "f0", "crop": "wheat"}, {"name": "f1", "crop": "barley"}]
    rows = ["hour,member,field,wheat_moisture_pct,barley_moisture_pct"]
    for member in range(2):
        for hour in range(3):
            for field in ["f1", "f0"]:  # out of order on purpose
                rows.append(f"{hour},{member},{field},{20 + hour},{10 + member}")
    path = tmp_path / "moisture.csv"
    path.write_text("\n".join(rows))
    moisture = load_moisture(path, params)  # [member, field, hour]
    assert moisture.shape == (2, 2, 3)
    assert moisture[1, 0] == pytest.approx([20, 21, 22])  # f0 is wheat
    assert moisture[1, 1] == pytest.approx([11, 11, 11])  # f1 is barley
    assert drying_cost_gbp_per_t(moisture, params).shape == (2, 2, 3)


def test_drier_grain_weighs_less_and_wetter_grain_more():
    params = demo_params()
    params["fields"] = [{"name": "f0", "crop": "grain"}]
    params["crops"] = {"grain": {"yield_t_per_ha": 10, "yield_moisture_pct": 15, **CEREAL_DRYING}}
    moisture = np.array([[[10.0, 15.0, 20.0]]])  # [member, field, hour]
    # 10 t at 15% is 8.5 t of dry matter, whatever the moisture
    assert delivered_tonnes_per_ha(moisture, params)[0, 0] == pytest.approx(
        [8.5 / 0.90, 10, 8.5 / 0.80]
    )


def test_oilseeds_are_dried_to_9_pct_and_cereals_to_14():
    params = demo_params()
    params["fields"] = [{"name": "f0", "crop": "cereal"}, {"name": "f1", "crop": "oilseed"}]
    params["crops"] = {
        "cereal": {"price_gbp_per_t": 100, **CEREAL_DRYING},
        "oilseed": {
            "price_gbp_per_t": 100,
            "target_moisture_pct": 9.0,
            "free_drying_up_to_pct": 9.0,
        },
    }
    moisture = np.array([[[9.0, 10.0, 15.0], [9.0, 10.0, 15.0]]])  # [member, field, hour]
    cost = drying_cost_gbp_per_t(moisture, params)
    assert cost[0, 0] == pytest.approx(
        [0, 0, 1.25]
    )  # cereal: free up to 15%, weight kept above 14%
    # oilseed: free at 9%; at 10%, 2 bands (£4) + 1 point x 1.25% of £100 kept
    assert cost[0, 1, :2] == pytest.approx([0, 4 + 1.25])


def test_value_uses_moisture_forecast_shifted_to_the_reading():
    params, weather = tiny_farm()
    params["crops"]["dear"]["price_gbp_per_t"] = 200
    # the moisture model says field 1 dries 1 point per hour from 18%
    weather["moisture_pct"] = np.tile([18.0, 17.0, 16.0, 15.0], (2, 2, 1))  # [member, field, hour]
    assert start_moisture(weather, params) == pytest.approx([18, 18])
    # a reading of 16% keeps the same drying, just 2 points lower: 16, 15, 14, 13
    value = field_values(weather, np.array([16.0, 16.0]), params)
    # 13%: no drying charge, but the 1 t quoted at 14% weighs only 0.86/0.87 t this dry
    assert value[1, 3] == pytest.approx(200 * 0.86 / 0.87)
    assert value[1, 0] < value[1, 3]  # wetter grain costs more to dry


def test_live_forecast_runs_the_moisture_model_from_each_crops_start():
    params = demo_params()
    weather = load_forecast(ROOT / "data" / "forecast.csv", params)
    assert weather["temp_c"].shape == weather["rain_mm"].shape  # so predict_moisture re-runs it
    assert "moisture_pct" not in weather
    crops = params["moisture_model"]["crops"]
    starts = [crops[field["crop"]]["start_moisture_pct"] for field in params["fields"]]
    assert start_moisture(weather, params) == pytest.approx(starts)
    moisture = predict_moisture(start_moisture(weather, params), weather, params)
    assert moisture[:, :, 0] == pytest.approx(np.tile(starts, (moisture.shape[0], 1)))
    assert moisture.max() <= 100 * 0.52 / 1.52 + 1e-9  # never above the rain cap (rain_max_mc)


def test_moisture_csv_must_match_the_forecast():
    params = demo_params()
    weather = load_forecast(ROOT / "data" / "forecast.csv", params, MOISTURE_CSV)
    assert weather["moisture_pct"].shape == weather["rain_mm"].shape


def test_simulator_counts_driving_and_harvesting():
    params, weather = tiny_farm()
    assert run_season(weather, params, stay_on_field_0)["total_gbp"] == pytest.approx(200)
    assert run_season(weather, params, drive_to_field_1)["total_gbp"] == pytest.approx(160)


def test_simulator_records_the_path_followed():
    params, weather = tiny_farm()
    result = run_season(weather, params, drive_to_field_1)
    assert result["place"][0] == pytest.approx([1, 1, 1, 1])  # heading for field 1 from hour 0
    assert result["driving"][0].tolist() == [True, True, False, False]  # 2 hours on the road
    assert result["harvesting"][0] == pytest.approx([WAIT, WAIT, 1, 1])


def test_running_costs_follow_what_each_combine_does():
    """Drive to field 1 for 2 hours (£70 each), then cut 1 ha in each of 2 hours (£55 each)."""
    params, weather = tiny_farm()
    round_running_costs(params)
    result = run_season(weather, params, drive_to_field_1)
    assert result["running_cost_gbp"][0] == pytest.approx([70, 70, 55, 55])
    assert result["cut_ha"][0] == pytest.approx([0, 0, 1, 1])
    breakdown = {"diesel": 2 * 20 + 2 * 10, "operator": 4 * 15, "escort": 2 * 5, "repairs": 4 * 30}
    assert result["cost_breakdown_gbp"] == pytest.approx(breakdown)
    assert result["total_gbp"] == pytest.approx(160)  # running costs don't change what's earned
    assert result["net_gbp"] == pytest.approx(160 - 250)


def test_waiting_in_a_field_pays_the_operator_and_the_depot_costs_nothing():
    """Rain all day, so nothing can be cut. Left on field 0, the combine waits there and pays
    its operator (£15 an hour). Sent home, it drives 2 hours (£70 each), then costs nothing."""
    params, weather = tiny_farm()
    round_running_costs(params)
    weather["rain_mm"][:] = 5.0
    waits = run_season(weather, params, stay_on_field_0)
    assert waits["running_cost_gbp"][0] == pytest.approx([15, 15, 15, 15])
    goes_home = run_season(weather, params, go_to_depot)
    assert goes_home["running_cost_gbp"][0] == pytest.approx([70, 70, 0, 0])


def test_simulator_replans_with_each_new_forecast():
    """The weather stays dry, but the first forecast says it will rain on field 0, so the machine
    waits. Two hours in, a new forecast says dry: it replans and harvests from then on."""
    params, weather = tiny_farm()
    params.update(sensor_every_hours=4, forecast_every_hours=2)
    truth = {name: values[[0]] for name, values in weather.items()}  # [1, field, hour], all dry
    rainy = {name: values.copy() for name, values in weather.items()}
    rainy["rain_mm"][:, 0] = 5.0  # [member, field, hour]
    asked_at = []

    def get_forecast(hour):
        asked_at.append(hour)
        forecast = rainy if hour < 2 else weather
        return {name: values[:, :, hour:] for name, values in forecast.items()}

    def cut_field_0_if_worth_it(value, state, params):
        return np.full((1, value.shape[1]), 0 if value[0, 0] > 0 else WAIT)

    result = run_with_forecasts(truth, get_forecast, params, cut_field_0_if_worth_it)
    assert asked_at == [0, 2]
    assert [plan["hour"] for plan in result["plans"]] == [0, 2]
    assert result["harvesting"][0] == pytest.approx([WAIT, WAIT, 0, 0])


def test_moisture_model_runs_from_the_reading_when_there_is_temperature():
    """Past forecasts (replay.py) have temperature, so grain moisture comes from moisture.py: it
    starts at the reading and dries in warm, dry air."""
    params = demo_params()
    params["fields"] = [{"name": "f0", "crop": "wheat"}]
    weather = {  # [member, field, hour]
        "temp_c": np.full((2, 1, 5), 25.0),
        "humidity_pct": np.full((2, 1, 5), 40.0),
        "rain_mm": np.zeros((2, 1, 5)),
    }
    moisture = predict_moisture(np.array([18.0]), weather, params)
    assert moisture.shape == (2, 1, 5)
    assert moisture[:, 0, 0] == pytest.approx(18)
    assert (np.diff(moisture[0, 0]) < 0).all()  # drier every hour


def test_simulator_lets_machines_go_to_the_depot():
    params, weather = tiny_farm()
    result = run_season(weather, params, go_to_depot)
    assert result["total_gbp"] == 0
    assert (result["harvesting"] == -1).all()


def test_fuel_tank_burns_the_diesel_running_costs_charge_for():
    """Drive to field 1 for 2 hours (20 l each), then cut 1 ha in each of 2 hours (10 l each)."""
    params, weather = tiny_farm()
    round_running_costs(params)
    result = run_season(weather, params, drive_to_field_1)
    assert result["fuel_litres"][0] == pytest.approx([480, 460, 450, 440])
    burned_litres = 500 - result["fuel_litres"][0, -1]  # diesel is £1 a litre here
    assert burned_litres == pytest.approx(result["cost_breakdown_gbp"]["diesel"])


def test_low_fuel_sends_the_combine_home_to_refuel():
    """A 60 l tank, no reserve, 1 hour drives (20 l), 10 l per ha cut. After 4 hours of cutting
    20 l is left: not enough for another hour (10 l) and the drive home (20 l), so the combine
    goes home though the plan says stay. At the depot it waits until it has enough to get back,
    cut an hour and get home again (50 l), then carries on with the plan."""
    params, _ = tiny_farm()
    round_running_costs(params)
    n_hours = 8
    params.update(
        season_hours=n_hours,
        plan_hours=n_hours,
        sensor_every_hours=n_hours,
        forecast_every_hours=n_hours,
        travel_hours=(1 - np.eye(3)).tolist(),
        fuel_reserve_pct=0,
        depot_refuel_litres_per_hour=100,
    )
    params["machines"][0]["fuel_tank_litres"] = 60
    params["fields"][0]["area_ha"] = 8
    weather = {  # [member, field, hour]
        "rain_mm": np.zeros((2, 2, n_hours)),
        "humidity_pct": np.full((2, 2, n_hours), 60.0),
    }
    result = run_season(weather, params, stay_on_field_0)
    depot = 2
    assert result["fuel_litres"][0] == pytest.approx([50, 40, 30, 20, 0, 60, 40, 30])
    assert result["place"][0].tolist() == [0, 0, 0, 0, depot, depot, 0, 0]
    assert result["harvesting"][0].tolist() == [0, 0, 0, 0, WAIT, WAIT, WAIT, 0]
    assert result["total_gbp"] == pytest.approx(5 * 50)


def test_planners_see_the_fuel_left_in_each_tank():
    """state["fuel_litres"] is the tanks' telemetry, read fresh at every replan."""
    params, weather = tiny_farm()
    round_running_costs(params)
    params.update(sensor_every_hours=2, forecast_every_hours=2)
    seen = []  # state["fuel_litres"] at each replan

    def stay_and_record_fuel(value, state, params):
        seen.append(state["fuel_litres"].tolist())
        return stay_on_field_0(value, state, params)

    run_season(weather, params, stay_and_record_fuel)
    assert seen == [[500], [480]]  # full at the start, then 2 ha cut at 10 l/ha


def test_planner_finds_the_best_answer_on_the_tiny_farm():
    params, weather = tiny_farm()
    assert run_season(weather, params)["total_gbp"] == pytest.approx(200)


def test_field_alone_cuts_its_best_hours_first():
    # 5 ha, at most 3 ha an hour: 3 ha in the £5 hour, the other 2 ha in the £3 hour
    cut, gbp = field_alone(np.array([1.0, 5.0, 3.0, 0.0, -2.0]), 5, 3)
    assert cut == pytest.approx([0, 3, 2, 0, 0])
    assert gbp == pytest.approx(3 * 5 + 2 * 3)


def test_prices_stay_zero_when_combines_are_never_short():
    params = demo_params()
    value = np.array([[10.0, 30.0, 20.0, 0.0]])  # one field: it can never ask for too much
    price, bound = machine_prices(value, np.array([5.0]), params)
    assert price == pytest.approx(0)
    assert bound == pytest.approx(3 * 30 + 2 * 20)  # its 5 ha in its two best hours


def test_prices_rise_on_the_hour_everyone_wants():
    params, weather = rain_farm()
    value = field_values(weather, start_moisture(weather, params), params)
    price, bound = machine_prices(value, np.array([1.0, 1.0]), params)
    # hour 1 must cost about £5.25/ha more than hour 0, so one field takes hour 0 instead
    assert price[1] - price[0] == pytest.approx(RAIN_FARM_HOUR_1 - RAIN_FARM_HOUR_0, abs=0.5)
    assert bound == pytest.approx(
        RAIN_FARM_BEST, abs=0.5
    )  # the best answer: the bound is tight here


def test_lagrangian_cuts_before_the_rain():
    params, weather = rain_farm()
    assert run_season(weather, params, plan_lagrangian)["total_gbp"] == pytest.approx(
        RAIN_FARM_BEST
    )


def test_lagrangian_finds_the_best_answer_on_the_tiny_farm():
    params, weather = tiny_farm()
    assert run_season(weather, params, plan_lagrangian)["total_gbp"] == pytest.approx(200)


def test_mpc_finds_the_best_answer_on_the_tiny_farm():
    params, weather = tiny_farm()
    assert run_season(weather, params, plan_mpc)["total_gbp"] == pytest.approx(200)


def test_mpc_cuts_before_the_rain():
    params, weather = rain_farm()
    assert run_season(weather, params, plan_mpc)["total_gbp"] == pytest.approx(RAIN_FARM_BEST)


def test_mpc_leaves_a_field_mid_job_and_comes_back():
    """Field 0: 20 ha at £50/ha. Field 1: 2 ha at £200/ha, but only dry in hours 4 and 5.

    Staying on field 0 makes £500. Best: cut field 0 for 3 hours, drive (hour 3), cut field 1,
    drive back (hour 6), cut field 0 again: £700 - £20 driving = £680.
    """
    params = one_combine_farm(2, [20, 2], start="f0")
    value = np.zeros((2, 10))  # [field, hour]
    value[0] = 50
    value[1, 4:6] = 200
    state = {"remaining_ha": np.array([20.0, 2.0]), "machine_at": np.array([0])}
    schedule = plan_mpc(value, state, params)
    assert schedule[0] == pytest.approx([0, 0, 0, 1, 1, 1, 0, 0, 0, 0])


def test_mpc_waits_out_the_rain_at_the_depot():
    """Dry in hours 0-1 and 10-11, rain in between. Waiting in the field costs 8 x £20 = £160,
    going home and back costs 2 x £10 = £20, so the combine goes home (place 1 = the depot)."""
    params = one_combine_farm(1, [100], start="f0")
    value = np.zeros((1, 12))  # [field, hour]
    value[0, [0, 1, 10, 11]] = 50
    state = {"remaining_ha": np.array([100.0]), "machine_at": np.array([0])}
    schedule = plan_mpc(value, state, params)
    assert schedule[0] == pytest.approx([0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0])


def test_mpc_leaves_drying_grain_for_after_the_plan():
    """10 ha, worth £50/ha in hours 0-5 and £100/ha in hours 6-11 as it dries. With the terminal
    value (90% of the last 6 hours' £100 = £90/ha), cutting in the first 6 hours loses £40/ha, so
    the combine goes home (place 1) and comes back for the dry hours. Without it, it cuts all day.
    """
    params = one_combine_farm(1, [10], start="f0")
    value = np.zeros((1, 12))  # [field, hour]
    value[0, :6], value[0, 6:] = 50, 100
    state = {"remaining_ha": np.array([10.0]), "machine_at": np.array([0])}
    params.update(mpc_terminal_hours=6, mpc_terminal_value_pct=90)
    assert plan_mpc(value, state, params)[0] == pytest.approx([1, 1, 1, 1, 1] + [0] * 7)
    params.update(mpc_terminal_value_pct=0)
    assert plan_mpc(value, state, params)[0, :2] == pytest.approx([0, 0])


def test_mpc_stays_home_when_the_field_is_dry_in_only_half_the_futures():
    """One field worth £30/ha when dry, and dry in one of two forecast members. On the average
    (£15/ha every hour) cutting looks worth it. Played out in both members, an hour in the field
    earns £30 half the time and costs £40 idle the other half: -£5, so it stays at the depot."""
    params = one_combine_farm(1, [100], start="depot")
    params.update(idle_in_field_gbp_per_hour=40, mpc_scenarios=50)
    by_member = np.zeros((2, 1, 6))  # [member, field, hour]
    by_member[0] = 30
    value = by_member.mean(axis=0)
    state = {"remaining_ha": np.array([100.0]), "machine_at": np.array([1])}
    assert plan_mpc(value, state, params)[0, 0] == 0  # average only: drives to the field
    state["value_by_member"] = by_member
    assert plan_mpc(value, state, params)[0] == pytest.approx([1] * 6)


def test_idle_combine_waits_out_the_rain_at_the_depot():
    """The rain case plan_mpc gets right above, for the other planners. Standing in the field
    through hours 2-9 costs 8 x £20 = £160, home and back 2 x £10 = £20, so the combine goes home
    (place 1) and leaves at hour 9 to be back for the dry hours. It cuts the same hours as before.
    """
    params = one_combine_farm(1, [100], start="f0")
    value = np.zeros((1, 12))  # [field, hour]
    value[0, [0, 1, 10, 11]] = 50
    state = {"remaining_ha": np.array([100.0]), "machine_at": np.array([0])}
    stay = np.zeros((1, 12), dtype=int)  # [machine, hour] on field 0 all day
    schedule = send_idle_to_depot(stay, value, state, params)
    assert schedule[0] == pytest.approx([0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0])
    cut_before = follow_plan(stay, value, state, params)["cutting"]
    assert (follow_plan(schedule, value, state, params)["cutting"] == cut_before).all()
    # plan_vgreedy keeps the combine on its field, and its last step sends it home
    assert plan_vgreedy(value, state, params)[0] == pytest.approx(schedule[0])
    params.update(depot_when_idle=False)
    assert send_idle_to_depot(stay, value, state, params)[0] == pytest.approx([0] * 12)


def test_idle_combine_stays_for_a_short_gap():
    """Rain in hours 2-3 only. Standing there costs 2 x £20 = £40, home and back £20: that saves
    £20, not more than the £20 margin, so the combine stays. With no margin it goes home."""
    params = one_combine_farm(1, [100], start="f0")
    value = np.zeros((1, 6))  # [field, hour]
    value[0, [0, 1, 4, 5]] = 50
    state = {"remaining_ha": np.array([100.0]), "machine_at": np.array([0])}
    stay = np.zeros((1, 6), dtype=int)
    assert send_idle_to_depot(stay, value, state, params)[0] == pytest.approx([0] * 6)
    params.update(depot_margin_gbp=0)
    assert send_idle_to_depot(stay, value, state, params)[0] == pytest.approx([0, 0, 1, 0, 0, 0])


def test_combine_with_nothing_left_to_cut_goes_home():
    """2 ha, cut in hours 0-1. Then the field is empty: 4 more hours there cost £80, the drive
    home £10, so it goes home and stays."""
    params = one_combine_farm(1, [2], start="f0")
    value = np.full((1, 6), 50.0)  # [field, hour]
    state = {"remaining_ha": np.array([2.0]), "machine_at": np.array([0])}
    stay = np.zeros((1, 6), dtype=int)
    assert send_idle_to_depot(stay, value, state, params)[0] == pytest.approx([0, 0, 1, 1, 1, 1])


def test_combine_waits_at_the_depot_and_leaves_just_in_time():
    """The field is dry only in hours 4-5. Driving out at once and standing there for hours 1-3
    costs £10 + £60; waiting at the depot and driving out at hour 3 costs £10."""
    params = one_combine_farm(1, [100], start="depot")
    value = np.zeros((1, 6))  # [field, hour]
    value[0, 4:] = 50
    state = {"remaining_ha": np.array([100.0]), "machine_at": np.array([1])}
    drive_out = np.zeros((1, 6), dtype=int)  # [machine, hour] to field 0 from hour 0
    schedule = send_idle_to_depot(drive_out, value, state, params)
    assert schedule[0] == pytest.approx([1, 1, 1, 0, 0, 0])
    cutting = follow_plan(schedule, value, state, params)["cutting"][0]  # [hour]
    assert cutting.tolist() == [False, False, False, False, True, True]


def test_depot_visits_never_change_which_hours_are_cut():
    """On the demo forecast, for every planner that ends with send_idle_to_depot, starting at the
    depot or in the fields: combines cut the same hours with and without the depot visits, and no
    two are sent to one field."""
    params = demo_params()
    weather = load_forecast(ROOT / "data" / "forecast.csv", params)
    n_fields, n_hours = len(params["fields"]), params["plan_hours"]
    n_machines = len(params["machines"])
    value = field_values(weather, start_moisture(weather, params), params)[:, :n_hours]
    area = np.array([field["area_ha"] for field in params["fields"]], dtype=float)
    plans = [
        planners.plan_vgreedy,
        planners.plan_urgencygreedy,
        planners.plan_urgencygreedy_withlookahead,
        plan_lagrangian,
    ]
    depot_visits = 0
    for machine_at in [np.full(n_machines, n_fields), np.arange(n_machines)]:
        state = {"remaining_ha": area, "machine_at": machine_at}
        for plan in plans:
            with_depot = plan(value, state, dict(params, depot_when_idle=True))
            without = plan(value, state, dict(params, depot_when_idle=False))
            cut_with = follow_plan(with_depot, value, state, params)["cutting"]
            cut_without = follow_plan(without, value, state, params)["cutting"]
            assert (cut_with == cut_without).all()
            for hour in range(n_hours):
                in_fields = [place for place in with_depot[:, hour] if 0 <= place < n_fields]
                assert len(in_fields) == len(set(in_fields))
            depot_visits += (with_depot != without).sum()
    assert (
        depot_visits > 0
    )  # the pass did something on the demo, so the checks above mean something


def farmer_farm(area_ha, day_hours, plan_hours):
    """one_combine_farm with a farmer's day starting at hour 0 (UTC) and fit up to 18% wheat."""
    params = one_combine_farm(len(area_ha), area_ha, start="depot")
    params.update(
        farmer_day_start_hour=0,
        farmer_day_hours=day_hours,
        farmer_fit_above_target_pct=4,
        farmer_max_wet_hours=4,
    )
    value = np.full((len(area_ha), plan_hours), 50.0)  # [field, hour], ignored by the farmer
    return params, value


def test_farmer_picks_fields_by_feel_and_is_home_by_the_end_of_the_day():
    """An 8-hour day. Field 0 (20%, 6 points over the 14% target) is too wet. Fields 1 (13%) and
    2 (14.4%) are both ready; field 2 is nearer (1 h, against 2 h), so it goes first: 2 ha at
    1 ha/h after the drive. Then field 1, 1 h away, until it must set off home (2 h) at hour 6 to be
    back (place 3) by hour 8. It stays home for the night."""
    params, value = farmer_farm([2, 10, 2], day_hours=8, plan_hours=10)
    params["travel_hours"] = [  # fields 0-2, then the depot
        [0, 1, 1, 1],
        [1, 0, 1, 2],
        [1, 1, 0, 1],
        [1, 2, 1, 0],
    ]
    state = {
        "remaining_ha": np.array([2.0, 10.0, 2.0]),
        "machine_at": np.array([3]),
        "moisture_pct": np.array([20.0, 13.0, 14.4]),
        "hour_of_day": 0,
        "rain_mm": np.zeros((2, 3, 10)),  # [member, field, hour]
    }
    assert plan_farmer(value, state, params)[0] == pytest.approx([2, 2, 2, 1, 1, 1, 3, 3, 3, 3])


def test_farmer_finishes_what_it_started_and_waits_on_a_wet_day():
    """A 6-hour day. Field 0 is half cut (17%), field 1 untouched and readier (14%): the combine
    goes back to field 0, and sets off home (1 h) at hour 5. With a wet forecast it stays in the
    yard (place 2) all day."""
    params, value = farmer_farm([10, 10], day_hours=6, plan_hours=6)
    state = {
        "remaining_ha": np.array([5.0, 10.0]),
        "machine_at": np.array([2]),
        "moisture_pct": np.array([17.0, 14.0]),
        "hour_of_day": 0,
        "rain_mm": np.zeros((2, 2, 6)),  # [member, field, hour]
    }
    assert plan_farmer(value, state, params)[0] == pytest.approx([0, 0, 0, 0, 0, 2])
    state["rain_mm"] = np.ones((2, 2, 6))  # 6 wet hours, more than the 4 it puts up with
    assert plan_farmer(value, state, params)[0] == pytest.approx([2] * 6)


def test_farmer_sticks_to_the_morning_decision_during_the_day():
    """Replanning at hour 2 of a 6-hour day. A combine in field 1 carries on although the new
    reading says it's too wet (25%), until it heads home at hour 3. A combine in the yard stays
    there although field 0 is fit: the farmer decided this morning."""
    params, value = farmer_farm([10, 10], day_hours=6, plan_hours=6)
    state = {
        "remaining_ha": np.array([10.0, 8.0]),
        "machine_at": np.array([1]),
        "moisture_pct": np.array([14.0, 25.0]),
        "hour_of_day": 2,
        "rain_mm": np.zeros((2, 2, 6)),  # [member, field, hour]
    }
    assert plan_farmer(value, state, params)[0] == pytest.approx([1, 1, 1, 2, 2, 2])
    state["machine_at"] = np.array([2])
    assert plan_farmer(value, state, params)[0] == pytest.approx([2] * 6)


def test_member_values_average_to_field_values():
    params = demo_params()
    weather = load_forecast(ROOT / "data" / "forecast.csv", params)
    moisture = start_moisture(weather, params)
    by_member = member_values(weather, moisture, params)
    assert by_member.shape == weather["rain_mm"].shape
    assert by_member.mean(axis=0) == pytest.approx(field_values(weather, moisture, params))


def test_mpc_never_puts_two_combines_on_one_field():
    params = demo_params()
    weather = load_forecast(ROOT / "data" / "forecast.csv", params)
    n_fields, n_hours = len(params["fields"]), params["plan_hours"]
    value = field_values(weather, start_moisture(weather, params), params)[:, :n_hours]
    area = np.array([field["area_ha"] for field in params["fields"]], dtype=float)
    at_depot = np.full(len(params["machines"]), n_fields)
    schedule = plan_mpc(value, {"remaining_ha": area, "machine_at": at_depot}, params)
    assert schedule.shape == (len(params["machines"]), n_hours)
    for hour in range(n_hours):
        in_fields = schedule[:, hour][schedule[:, hour] < n_fields]
        assert len(in_fields) == len(set(in_fields))


def test_no_planner_earns_more_than_the_lagrangian_bound():
    """With a perfect forecast and a single plan, the bound must hold for every planner."""
    params = demo_params()
    params.update(season_hours=48, plan_hours=48, sensor_every_hours=48, forecast_every_hours=48)
    forecast = load_forecast(ROOT / "data" / "forecast.csv", params)
    # two copies of one member: the forecast the planners see is exactly the weather that happens
    weather = {name: np.repeat(values[[0], :, :48], 2, axis=0) for name, values in forecast.items()}
    value = field_values(weather, start_moisture(weather, params), params)
    area = np.array([field["area_ha"] for field in params["fields"]], dtype=float)
    _, bound = machine_prices(value, area, params)

    every_planner = [
        getattr(planners, name) for name in planners.__all__ if name.startswith("plan_")
    ]
    for plan in every_planner:
        assert run_season(weather, params, plan)["total_gbp"] <= bound + 1e-6

    at_depot = np.full(len(params["machines"]), len(params["fields"]))
    schedule = plan_lagrangian(value, {"remaining_ha": area, "machine_at": at_depot}, params)
    assert schedule.shape == (len(params["machines"]), 48)
    n_fields = len(params["fields"])
    for hour in range(48):  # never two combines on one field; the depot holds any number
        in_fields = [place for place in schedule[:, hour] if 0 <= place < n_fields]
        assert len(in_fields) == len(set(in_fields))


def test_demo_runs_end_to_end():
    params = demo_params()
    weather = load_forecast(ROOT / "data" / "forecast.csv", params, MOISTURE_CSV)
    result = run_season(weather, params)
    assert result["total_gbp"] > 0
    assert 0 < result["net_gbp"] < result["total_gbp"]  # running costs, but less than earned
    assert result["remaining_ha"].min() >= 0
    assert result["harvesting"].shape == (len(params["machines"]), params["season_hours"])
    assert result["running_cost_gbp"].shape == result["harvesting"].shape
    assert len(result["plans"]) == params["season_hours"] // params["sensor_every_hours"]
    tank = np.array([machine["fuel_tank_litres"] for machine in params["machines"]])
    reserve = tank * params["fuel_reserve_pct"] / 100
    assert result["fuel_litres"].shape == result["harvesting"].shape
    assert (result["fuel_litres"].min(axis=1) >= reserve).all()  # home before the reserve
    assert (result["fuel_litres"].max(axis=1) <= tank).all()
