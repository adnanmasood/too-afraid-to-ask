"""Opt-in smoke test for the public Open-Meteo APIs."""

from __future__ import annotations

import os

import pytest

from travel_a2a.weather_service import OpenMeteoForecastService

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_TESTS") != "1",
        reason="set RUN_LIVE_TESTS=1 to call Open-Meteo",
    ),
]


@pytest.mark.asyncio
async def test_live_open_meteo_contract_uses_stable_types() -> None:
    """Check structure and attribution without asserting changing weather values."""

    result = await OpenMeteoForecastService().get_forecast("Berlin, Germany", 2, "metric")

    assert result.location.name
    assert result.location.country
    assert result.timezone
    assert len(result.daily) == 2
    assert all(day.condition for day in result.daily)
    assert all(isinstance(day.temperature_min, float) for day in result.daily)
    assert all(isinstance(day.temperature_max, float) for day in result.daily)
    assert all(0 <= day.precipitation_probability <= 100 for day in result.daily)
    assert result.source == "Open-Meteo"
    assert result.source_url == "https://open-meteo.com/"
