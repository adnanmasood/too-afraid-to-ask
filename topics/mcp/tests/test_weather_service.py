"""Deterministic tests for the Open-Meteo adapter."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from weather_mcp.weather_service import (
    GEOCODING_URL,
    LocationNotFoundError,
    OpenMeteoWeatherService,
    WeatherProviderError,
    describe_wmo_code,
)


def _geocoding_payload() -> dict[str, object]:
    return {
        "results": [
            {
                "name": "Berlin",
                "country": "Germany",
                "admin1": "Berlin",
                "latitude": 52.52,
                "longitude": 13.41,
            }
        ]
    }


def _forecast_payload(*, code: int = 2) -> dict[str, object]:
    return {
        "timezone": "Europe/Berlin",
        "current_units": {
            "time": "iso8601",
            "interval": "seconds",
            "temperature_2m": "°C",
            "relative_humidity_2m": "%",
            "apparent_temperature": "°C",
            "is_day": "",
            "precipitation": "mm",
            "weather_code": "wmo code",
            "cloud_cover": "%",
            "wind_speed_10m": "km/h",
            "wind_direction_10m": "°",
        },
        "current": {
            "time": "2026-09-02T12:15",
            "interval": 900,
            "temperature_2m": 20.4,
            "relative_humidity_2m": 61,
            "apparent_temperature": 19.8,
            "is_day": 1,
            "precipitation": 0.1,
            "weather_code": code,
            "cloud_cover": 42,
            "wind_speed_10m": 13.2,
            "wind_direction_10m": 245,
        },
    }


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _query(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.url.query.decode())


def cast_mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


@pytest.mark.asyncio
async def test_metric_weather_is_normalized_and_attributed() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url).startswith(GEOCODING_URL):
            return httpx.Response(200, json=_geocoding_payload())
        return httpx.Response(200, json=_forecast_payload())

    async with _client(handler) as client:
        result = await OpenMeteoWeatherService(client).get_weather("  Berlin  ", "metric")

    assert result.location.model_dump() == {
        "name": "Berlin",
        "country": "Germany",
        "region": "Berlin",
        "latitude": 52.52,
        "longitude": 13.41,
    }
    assert result.observed_at == "2026-09-02T12:15"
    assert result.timezone == "Europe/Berlin"
    assert result.condition == "Partly cloudy"
    assert result.weather_code == 2
    assert result.temperature == 20.4
    assert result.apparent_temperature == 19.8
    assert result.relative_humidity == 61
    assert result.precipitation == 0.1
    assert result.cloud_cover == 42
    assert result.wind_speed == 13.2
    assert result.wind_direction_degrees == 245
    assert result.is_day is True
    assert result.units.model_dump() == {
        "temperature": "°C",
        "relative_humidity": "%",
        "precipitation": "mm",
        "cloud_cover": "%",
        "wind_speed": "km/h",
        "wind_direction": "°",
    }
    assert result.source == "Open-Meteo"
    assert result.source_url == "https://open-meteo.com/"

    assert _query(requests[0]) == {
        "name": ["Berlin"],
        "count": ["1"],
        "language": ["en"],
        "format": ["json"],
    }
    assert requests[0].extensions["timeout"] == {
        "connect": 10.0,
        "read": 10.0,
        "write": 10.0,
        "pool": 10.0,
    }
    forecast_query = _query(requests[1])
    assert forecast_query["temperature_unit"] == ["celsius"]
    assert forecast_query["wind_speed_unit"] == ["kmh"]
    assert forecast_query["precipitation_unit"] == ["mm"]
    assert "wind_direction_10m" in forecast_query["current"][0]


@pytest.mark.asyncio
async def test_unicode_city_and_imperial_query_parameters_are_preserved() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = _geocoding_payload() if len(requests) == 1 else _forecast_payload()
        return httpx.Response(200, json=payload)

    async with _client(handler) as client:
        await OpenMeteoWeatherService(client).get_weather("München", "imperial")

    assert _query(requests[0])["name"] == ["München"]
    query = _query(requests[1])
    assert query["temperature_unit"] == ["fahrenheit"]
    assert query["wind_speed_unit"] == ["mph"]
    assert query["precipitation_unit"] == ["inch"]
    assert query["timezone"] == ["auto"]


@pytest.mark.asyncio
async def test_first_ranked_geocoding_result_is_used_and_region_is_optional() -> None:
    geocoding = _geocoding_payload()
    first = cast_dict(geocoding["results"])[0]
    first.pop("admin1")
    cast_dict(geocoding["results"]).append(
        {
            "name": "Berlin",
            "country": "United States",
            "admin1": "New Hampshire",
            "latitude": 44.47,
            "longitude": -71.19,
        }
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=geocoding if len(requests) == 1 else _forecast_payload(),
        )

    async with _client(handler) as client:
        result = await OpenMeteoWeatherService(client).get_weather("Berlin", "metric")

    assert result.location.region is None
    assert _query(requests[1])["latitude"] == ["52.52"]
    assert _query(requests[1])["longitude"] == ["13.41"]


def cast_dict(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"results": []}, {"results": None}])
async def test_location_not_found_and_malformed_absence_are_safe(
    payload: dict[str, object],
) -> None:
    async with _client(lambda _: httpx.Response(200, json=payload)) as client:
        service = OpenMeteoWeatherService(client)
        if payload.get("results") is None and "results" in payload:
            with pytest.raises(WeatherProviderError, match="temporarily unavailable"):
                await service.get_weather("Atlantis", "metric")
        else:
            with pytest.raises(LocationNotFoundError, match="Atlantis"):
                await service.get_weather("Atlantis", "metric")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(503, json={"reason": "secret upstream detail"}),
        lambda request: httpx.Response(200, content=b"not-json"),
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request)),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline", request=request)),
    ],
)
async def test_http_json_timeout_and_network_failures_use_safe_error(
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    async with _client(handler) as client:
        with pytest.raises(WeatherProviderError) as exc_info:
            await OpenMeteoWeatherService(client).get_weather("Berlin", "metric")

    assert "temporarily unavailable" in str(exc_info.value)
    assert "secret upstream detail" not in str(exc_info.value)
    assert "offline" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forecast_payload",
    [
        {},
        {"timezone": "Europe/Berlin", "current": {}},
        {
            **_forecast_payload(),
            "current": {**cast_mapping(_forecast_payload()["current"]), "is_day": 9},
        },
        {
            **_forecast_payload(),
            "current": {
                **cast_mapping(_forecast_payload()["current"]),
                "temperature_2m": "hot",
            },
        },
    ],
)
async def test_malformed_forecast_is_a_provider_error(
    forecast_payload: dict[str, object],
) -> None:
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200,
            json=_geocoding_payload() if call_count == 1 else forecast_payload,
        )

    async with _client(handler) as client:
        with pytest.raises(WeatherProviderError, match="invalid data"):
            await OpenMeteoWeatherService(client).get_weather("Berlin", "metric")


@pytest.mark.asyncio
@pytest.mark.parametrize("city", ["", " ", "A", "x" * 101])
async def test_city_validation_happens_before_a_request(city: str) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"unexpected request: {request.url}")

    async with _client(unexpected) as client:
        with pytest.raises(ValueError, match="city"):
            await OpenMeteoWeatherService(client).get_weather(city, "metric")


@pytest.mark.asyncio
async def test_invalid_units_are_rejected_at_runtime() -> None:
    async with _client(lambda _: httpx.Response(500)) as client:
        with pytest.raises(ValueError, match="units"):
            await OpenMeteoWeatherService(client).get_weather("Berlin", "kelvin")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_injected_client_remains_caller_owned() -> None:
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200,
            json=_geocoding_payload() if call_count == 1 else _forecast_payload(),
        )

    client = _client(handler)
    await OpenMeteoWeatherService(client).get_weather("Berlin", "metric")
    assert client.is_closed is False
    await client.aclose()


@pytest.mark.parametrize(
    ("code", "description"),
    [
        (0, "Clear sky"),
        (48, "Depositing rime fog"),
        (67, "Heavy freezing rain"),
        (77, "Snow grains"),
        (82, "Violent rain showers"),
        (99, "Thunderstorm with heavy hail"),
        (1234, "Unknown weather condition (WMO code 1234)"),
    ],
)
def test_wmo_descriptions(code: int, description: str) -> None:
    assert describe_wmo_code(code) == description


def test_payload_helpers_are_json_serializable() -> None:
    # A small guard against accidentally adding non-JSON fixture values.
    json.dumps(_geocoding_payload())
    json.dumps(_forecast_payload())
