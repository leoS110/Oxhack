"""Tests for harvest/past_data.py (past forecasts and past weather), all without the internet.

Run:  uv run pytest
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml

from harvest import past_data
from harvest.past_data import (
    forecast_runs,
    is_complete,
    load_params,
    load_run,
    past_forecasts,
    past_rollout,
    past_weather,
    published_runs,
    relative_humidity_pct,
    saved_table,
    season_dates,
    to_hourly,
)
from harvest.simulate import run_with_forecasts

ROOT = Path(__file__).parent.parent


def demo_params():
    return yaml.safe_load((ROOT / "params.yaml").read_text(encoding="utf-8"))


def two_fields(params):
    params["fields"] = [
        {"name": "f0", "crop": "wheat", "area_ha": 1, "lat": 50.0, "lon": -1.0},
        {"name": "f1", "crop": "barley", "area_ha": 1, "lat": 52.5, "lon": 13.4},
    ]
    return params


def offline(monkeypatch):
    """Make going online fail the test."""

    def fail(*args):
        raise AssertionError("went online")

    monkeypatch.setattr(past_data, "open_archive", fail)
    monkeypatch.setattr(past_data, "download_weather", fail)


def test_forecast_steps_become_hours():
    """Steps 3 then 6 hours apart: temperature goes in straight lines, and each step's rain rate
    (the average since the step before) fills the hours since the step before."""
    params = demo_params()
    step_hours = np.array([0, 3, 6, 12])
    steps = {  # [member, field, step]
        "temp_c": np.array([[[0.0, 3.0, 6.0, 12.0]]]),
        "dew_point_c": np.array([[[0.0, 3.0, 6.0, 12.0]]]),
        "precip_mm_per_s": np.array([[[np.nan, 1.0, 2.0, 3.0]]]) / 3600,  # 1, 2, 3 mm an hour
    }
    hourly = to_hourly(steps, step_hours, params)
    assert hourly["temp_c"][0, 0] == pytest.approx(np.arange(12))
    assert hourly["rain_mm"][0, 0] == pytest.approx([1] * 3 + [2] * 3 + [3] * 6)
    assert hourly["humidity_pct"][0, 0] == pytest.approx(100)  # dew point = temperature


def test_humidity_from_dew_point():
    params = demo_params()
    # 20 °C air with a 10 °C dew point holds about half the water it could (tables: 52.5%)
    assert relative_humidity_pct(20.0, 10.0, params) == pytest.approx(52.5, abs=0.1)
    saturated = relative_humidity_pct(np.array([5.0, 25.0]), np.array([5.0, 25.0]), params)
    assert saturated == pytest.approx(100)


def test_a_run_is_only_used_once_it_is_published():
    params = demo_params()
    params["forecast_delay_hours"] = 8
    runs = pd.date_range("2026-10-10", periods=3, freq="D")  # 00 UTC every day
    assert published_runs(pd.Timestamp("2026-10-11 07:00"), runs, params)[-1] == runs[0]
    assert published_runs(pd.Timestamp("2026-10-11 08:00"), runs, params)[-1] == runs[1]
    assert len(published_runs(pd.Timestamp("2026-10-10 07:00"), runs, params)) == 0


def test_replay_dates_are_cut_to_what_the_archives_have(capsys):
    params = demo_params()
    params["forecast_delay_hours"] = 8
    runs = pd.date_range("2024-04-01", "2026-10-25", freq="D")
    now = pd.Timestamp("2026-10-25 09:30")

    days = pd.Timestamp("2026-10-10"), pd.Timestamp("2026-10-12")
    assert season_dates(*days, runs, params, now) == (pd.Timestamp("2026-10-10"), 72)  # 3 days
    assert "warning" not in capsys.readouterr().out

    # starts before the archive and ends after now: both cut, with a warning each
    days = pd.Timestamp("2024-03-01"), pd.Timestamp("2026-10-25")
    first, n_hours = season_dates(*days, runs, params, now)
    assert first == pd.Timestamp("2024-04-01 08:00")  # when the first run was published
    assert first + pd.Timedelta(hours=n_hours) == pd.Timestamp("2026-10-25 09:00")  # last full hour
    assert capsys.readouterr().out.count("warning") == 2

    # the archive is missing the newest run: still replays, with a warning
    season_dates(*days, runs[:-1], params, now)
    assert "newest forecast" in capsys.readouterr().out

    with pytest.raises(ValueError):  # all in the future
        season_dates(pd.Timestamp("2026-10-26"), pd.Timestamp("2026-10-27"), runs, params, now)


def test_get_forecast_uses_the_newest_complete_run(monkeypatch):
    params = two_fields(demo_params())
    params["forecast_delay_hours"] = 8
    runs = pd.date_range("2026-10-10", periods=3, freq="D")  # 00 UTC every day

    def fake_load_run(run, params):  # hour h of every run holds h; the newest is partial
        if run == runs[-1]:
            return None
        return {"temp_c": np.tile(np.arange(48.0), (51, 2, 1))}  # [member, field, hour]

    monkeypatch.setattr(past_data, "load_run", fake_load_run)
    get_forecast, runs_used = past_forecasts(runs, pd.Timestamp("2026-10-11 06:00"), params)
    assert get_forecast(0)["temp_c"][0, 0, 0] == 30  # 11 Oct 06:00: the 10 Oct run, 30 h in
    assert get_forecast(6)["temp_c"][0, 0, 0] == 12  # 12:00: the 11 Oct run is out, 12 h in
    assert get_forecast(30)["temp_c"][0, 0, 0] == 36  # 12 Oct run partial: 11 Oct run, 36 h in
    assert runs_used == {0: runs[0], 6: runs[1]}


def test_the_saved_list_of_runs_is_used_while_it_is_new_enough(tmp_path, monkeypatch):
    params = demo_params()
    params["forecast_delay_hours"] = 8
    params["forecast_run_every_hours"] = 24
    monkeypatch.setattr(past_data, "FORECAST_DIR", tmp_path)
    runs = pd.date_range("2026-10-10", "2026-10-20", freq="D")  # 00 UTC every day
    opened = []

    def fake_open_archive(params):  # only the run times are read
        opened.append(True)
        return {"init_time": SimpleNamespace(values=runs.values)}

    monkeypatch.setattr(past_data, "open_archive", fake_open_archive)
    assert list(forecast_runs(pd.Timestamp("2026-10-15"), params)) == list(runs)
    assert len(opened) == 1  # nothing saved yet
    # a 21 Oct run would only be out at 08:00 that day, after a replay to the 20th ends
    assert list(forecast_runs(pd.Timestamp("2026-10-20"), params)) == list(runs)
    assert len(opened) == 1
    forecast_runs(pd.Timestamp("2026-10-21"), params)  # ... but it could matter on the 21st
    assert len(opened) == 2


def test_a_saved_run_is_read_back_without_downloading(tmp_path, monkeypatch):
    params = two_fields(demo_params())
    monkeypatch.setattr(past_data, "FORECAST_DIR", tmp_path)
    offline(monkeypatch)
    rows = []
    for member in range(2):
        for field in params["fields"]:
            for hour in [6, 0, 3]:  # out of order on purpose
                rows.append(
                    {
                        "member": member,
                        "field": field["name"],
                        "hour": hour,
                        "lat": field["lat"],
                        "lon": field["lon"],
                        "temp_c": 10.0 + hour,
                        "dew_point_c": 5.0,
                        "precip_mm_per_s": np.nan if hour == 0 else 0.0,
                    }
                )
    path = tmp_path / params["past_forecast_dataset"] / "2026-10-10T00.csv"
    path.parent.mkdir()
    pd.DataFrame(rows).to_csv(path, index=False)

    weather = load_run(pd.Timestamp("2026-10-10"), params)
    assert weather["temp_c"].shape == (2, 2, 6)  # [member, field, hour]
    assert weather["temp_c"][1, 1] == pytest.approx(10 + np.arange(6))
    assert weather["rain_mm"] == pytest.approx(0)

    params["fields"][1]["lat"] = 51.0  # the field moved, so the saved run no longer fits
    assert saved_table(path, params) is None


def test_runs_still_being_added_are_spotted():
    table = pd.DataFrame(
        {
            "hour": [0, 3, 6],
            "temp_c": [1.0, 2.0, 3.0],
            "dew_point_c": [0.0, 0.0, 0.0],
            "precip_mm_per_s": [np.nan, 0.0, 0.0],  # never any rain rate at hour 0
        }
    )
    assert is_complete(table)
    table.loc[2, "temp_c"] = np.nan
    assert not is_complete(table)


def fake_open_meteo(params):
    """Open-Meteo's answer for 2 days from midnight, one dict per field.

    Temperature = hour of the answer, rain = that + 100 x field index.
    """
    return [
        {
            "hourly": {
                "temperature_2m": [float(hour) for hour in range(48)],
                "relative_humidity_2m": [80.0] * 48,
                "precipitation": [float(hour) + 100 * f for hour in range(48)],
            }
        }
        for f in range(len(params["fields"]))
    ]


def test_past_weather_gives_each_hour_the_rain_that_fell_during_it(tmp_path, monkeypatch):
    """Open-Meteo reports an hour's rain at its END: 06:00-07:00 is the value at 07:00."""
    params = two_fields(demo_params())
    monkeypatch.setattr(past_data, "WEATHER_DIR", tmp_path)
    monkeypatch.setattr(past_data, "download_weather", lambda *args: fake_open_meteo(params))
    weather = past_weather(pd.Timestamp("2026-10-10 06:00"), 3, params)
    assert weather["rain_mm"].shape == (1, 2, 3)  # [member, field, hour]
    assert weather["temp_c"][0, 0] == pytest.approx([6, 7, 8])  # starts at 06:00
    assert weather["rain_mm"][0, 0] == pytest.approx([7, 8, 9])
    assert weather["rain_mm"][0, 1] == pytest.approx([107, 108, 109])


def test_past_weather_is_read_back_without_downloading(tmp_path, monkeypatch):
    params = two_fields(demo_params())
    monkeypatch.setattr(past_data, "WEATHER_DIR", tmp_path)
    monkeypatch.setattr(past_data, "download_weather", lambda *args: fake_open_meteo(params))
    first_hour = pd.Timestamp("2026-10-10 06:00")
    downloaded = past_weather(first_hour, 3, params)

    offline(monkeypatch)
    saved = past_weather(first_hour, 3, params)
    for name in downloaded:
        assert saved[name] == pytest.approx(downloaded[name])

    params["fields"][1]["lat"] = 51.0  # the field moved, so the saved weather no longer fits
    with pytest.raises(AssertionError, match="went online"):
        past_weather(first_hour, 3, params)


def test_a_past_rollout_gives_run_with_forecasts_what_it_needs(monkeypatch):
    """Two past days with made-up calm weather: 48 season hours, and each day's run comes in at
    the first replan after it's published (08:00, so 12:00)."""
    params = demo_params()
    params["forecast_delay_hours"] = 8
    runs = pd.date_range("2026-10-01", "2026-10-20", freq="D")  # 00 UTC every day

    def calm(n_members, n_hours):  # {name: [member, field, hour]}
        shape = (n_members, len(params["fields"]), n_hours)
        return {
            "temp_c": np.full(shape, 15.0),
            "humidity_pct": np.full(shape, 60.0),
            "rain_mm": np.zeros(shape),
        }

    monkeypatch.setattr(past_data, "forecast_runs", lambda end, params: runs)
    monkeypatch.setattr(past_data, "past_weather", lambda first, n_hours, params: calm(1, n_hours))
    monkeypatch.setattr(past_data, "load_run", lambda run, params: calm(2, 360))
    days = pd.Timestamp("2026-10-10"), pd.Timestamp("2026-10-11")
    truth, get_forecast, first_hour, runs_used = past_rollout(*days, params)
    result = run_with_forecasts(truth, get_forecast, params)

    assert first_hour == pd.Timestamp("2026-10-10")
    assert len(result["earned_gbp"]) == 48
    assert runs_used == {0: runs[8], 12: runs[9], 36: runs[10]}  # the 9, 10 and 11 Oct runs


def test_a_params_file_on_the_command_line_goes_over_params_yaml():
    args = ["2024-07-01", "--params", str(ROOT / "params_8_fields.yaml"), "2024-07-10", "mpc"]
    params, rest, name = load_params(args)
    full, same_args, full_name = load_params(["2024-07-01"])
    assert rest == ["2024-07-01", "2024-07-10", "mpc"] and name == "params_8_fields"
    assert same_args == ["2024-07-01"] and full_name == "params"
    assert len(params["fields"]) == 8 < len(full["fields"])
    assert np.shape(params["travel_hours"]) == (9, 9)  # the 8 fields and the depot
    assert params["machines"] == full["machines"]  # everything else from params.yaml
    # the same journeys as the full farm's table
    names = [field["name"] for field in full["fields"]]
    rows = [names.index(field["name"]) for field in params["fields"]] + [len(names)]
    assert params["travel_hours"] == [[full["travel_hours"][i][j] for j in rows] for i in rows]
