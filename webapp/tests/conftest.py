"""The tests run the demo on today's forecast (data/forecast.csv, in the repo), never a past week
(demo.yaml season_start): that would be downloaded the first time, and tests never use the
internet."""

import pytest

from webapp import bridge


@pytest.fixture(autouse=True, scope="session")
def todays_forecast():
    load = bridge.load_settings

    def settings():
        params, demo = load()
        return params, {**demo, "season_start": None}

    bridge.load_settings = settings
    yield
    bridge.load_settings = load
