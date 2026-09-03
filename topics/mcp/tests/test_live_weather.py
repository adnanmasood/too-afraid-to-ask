"""Opt-in integration check against the public Open-Meteo APIs."""

from __future__ import annotations

import os

import pytest

from weather_mcp.weather_service import OpenMeteoWeatherService

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_TESTS") != "1",
        reason="set RUN_LIVE_TESTS=1 to call the real Open-Meteo APIs",
    ),
]


@pytest.mark.asyncio
async def test_live_weather_for_berlin() -> None:
    result = await OpenMeteoWeatherService().get_weather("Berlin, Germany", "metric")

    assert result.location.name == "Berlin"
    assert result.location.country == "Germany"
    assert -90 <= result.location.latitude <= 90
    assert -180 <= result.location.longitude <= 180
    assert result.observed_at
    assert result.timezone
    assert result.condition
    assert result.units.temperature
    assert result.source == "Open-Meteo"
    assert result.source_url == "https://open-meteo.com/"
