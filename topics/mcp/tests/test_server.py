"""MCP protocol contract tests for the weather server."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

import weather_mcp.server as server_module
from weather_mcp.models import Units, WeatherLocation, WeatherResult, WeatherUnits
from weather_mcp.server import create_server
from weather_mcp.weather_service import LocationNotFoundError, WeatherProviderError


def sample_weather() -> WeatherResult:
    """Return deterministic weather data for protocol tests."""
    return WeatherResult(
        location=WeatherLocation(
            name="Berlin",
            country="Germany",
            region="Berlin",
            latitude=52.52,
            longitude=13.405,
        ),
        observed_at="2026-09-02T14:00",
        timezone="Europe/Berlin",
        condition="Partly cloudy",
        weather_code=2,
        temperature=73.4,
        apparent_temperature=72.1,
        relative_humidity=54,
        precipitation=0,
        cloud_cover=40,
        wind_speed=8.2,
        wind_direction_degrees=235,
        is_day=True,
        units=WeatherUnits(
            temperature="°F",
            relative_humidity="%",
            precipitation="inch",
            cloud_cover="%",
            wind_speed="mph",
            wind_direction="°",
        ),
    )


@dataclass
class FakeWeatherService:
    """Controllable WeatherService substitute with call recording."""

    result: WeatherResult
    error: Exception | None = None
    calls: list[tuple[str, Units]] = field(default_factory=list)

    async def get_weather(self, city: str, units: Units) -> WeatherResult:
        self.calls.append((city, units))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_server_metadata_and_tool_discovery() -> None:
    fake = FakeWeatherService(sample_weather())

    async with Client(create_server(fake)) as client:
        listed = await client.list_tools()

        assert client.server_info is not None
        assert client.server_info.name == "weather-mcp"
        assert client.server_info.version == "0.1.0"
        assert client.instructions == (
            "Call get_weather with a city name to retrieve current weather."
        )

    assert len(listed.tools) == 1
    tool = listed.tools[0]
    assert tool.name == "get_weather"
    assert tool.output_schema is not None
    assert tool.output_schema["title"] == "WeatherResult"
    assert tool.input_schema["required"] == ["city"]
    city_schema = tool.input_schema["properties"]["city"]
    assert city_schema["minLength"] == 2
    assert city_schema["maxLength"] == 100
    units_schema = tool.input_schema["properties"]["units"]
    assert units_schema["default"] == "metric"
    assert set(units_schema["enum"]) == {"metric", "imperial"}
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is True
    assert tool.annotations.open_world_hint is True


@pytest.mark.asyncio
async def test_get_weather_returns_structured_output_and_normalizes_city() -> None:
    expected = sample_weather()
    fake = FakeWeatherService(expected)

    async with Client(create_server(fake)) as client:
        result = await client.call_tool(
            "get_weather",
            {"city": "  Berlin  ", "units": "imperial"},
        )

    assert result.is_error is not True
    assert result.structured_content == expected.model_dump(mode="json")
    assert fake.calls == [("Berlin", "imperial")]
    assert isinstance(result.content[0], TextContent)
    assert "Open-Meteo" in result.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"city": " x "},
        {"city": "x" * 101},
        {"city": "Berlin", "units": "kelvin"},
    ],
)
async def test_invalid_tool_input_is_an_error_result(arguments: dict[str, Any]) -> None:
    fake = FakeWeatherService(sample_weather())

    async with Client(create_server(fake)) as client:
        result = await client.call_tool("get_weather", arguments)

    assert result.is_error is True
    assert result.structured_content is None
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text.startswith("Error executing tool get_weather:")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_location_not_found_is_a_visible_tool_error() -> None:
    message = "No location found for 'Atlantis'. Try a more specific value."
    fake = FakeWeatherService(sample_weather(), LocationNotFoundError(message))

    async with Client(create_server(fake)) as client:
        result = await client.call_tool("get_weather", {"city": "Atlantis"})

    assert result.is_error is True
    assert result.structured_content is None
    assert isinstance(result.content[0], TextContent)
    assert message in result.content[0].text


@pytest.mark.asyncio
async def test_provider_failure_is_a_sanitized_tool_error() -> None:
    secret_detail = "upstream response included a private diagnostic"
    fake = FakeWeatherService(sample_weather(), WeatherProviderError(secret_detail))

    async with Client(create_server(fake)) as client:
        result = await client.call_tool("get_weather", {"city": "Berlin"})

    assert result.is_error is True
    assert result.structured_content is None
    assert isinstance(result.content[0], TextContent)
    assert (
        "Weather service is temporarily unavailable. Please try again later."
        in result.content[0].text
    )
    assert secret_detail not in result.content[0].text


@pytest.mark.parametrize(
    ("argv", "host", "port"),
    [
        ([], "127.0.0.1", 8000),
        (["--host", "0.0.0.0", "--port", "9000"], "0.0.0.0", 9000),
    ],
)
def test_server_entrypoint_uses_streamable_http_settings(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    host: str,
    port: int,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_run(transport: str, **kwargs: Any) -> None:
        calls.append((transport, kwargs))

    monkeypatch.setattr(server_module.mcp, "run", fake_run)

    server_module.main(argv)

    assert calls == [
        (
            "streamable-http",
            {
                "host": host,
                "port": port,
                "streamable_http_path": "/mcp",
                "json_response": True,
                "stateless_http": True,
            },
        )
    ]


def test_server_entrypoint_handles_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupted_run(_transport: str, **_kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(server_module.mcp, "run", interrupted_run)

    server_module.main([])
