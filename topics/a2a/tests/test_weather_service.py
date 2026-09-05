"""Deterministic tests for the one-to-seven-day Open-Meteo adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest

from travel_a2a.weather_service import (
    FORECAST_URL,
    GEOCODING_URL,
    LocationNotFoundError,
    OpenMeteoForecastService,
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
                "longitude": 13.405,
            }
        ]
    }


def _forecast_payload(
    *,
    days: int = 3,
    code: int = 2,
    imperial: bool = False,
) -> dict[str, object]:
    dates = [f"2026-09-{4 + index:02d}" for index in range(days)]
    return {
        "timezone": "Europe/Berlin",
        "daily_units": {
            "time": "iso8601",
            "weather_code": "wmo code",
            "temperature_2m_max": "°F" if imperial else "°C",
            "temperature_2m_min": "°F" if imperial else "°C",
            "precipitation_sum": "inch" if imperial else "mm",
            "precipitation_probability_max": "%",
            "wind_speed_10m_max": "mp/h" if imperial else "km/h",
        },
        "daily": {
            "time": dates,
            "weather_code": [code] * days,
            "temperature_2m_max": [21.5 + index for index in range(days)],
            "temperature_2m_min": [11.0 + index for index in range(days)],
            "precipitation_sum": [0.2 * index for index in range(days)],
            "precipitation_probability_max": [10 + 5 * index for index in range(days)],
            "wind_speed_10m_max": [12.0 + index for index in range(days)],
        },
    }


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _query(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.url.query.decode())


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


@pytest.mark.asyncio
async def test_metric_forecast_is_normalized_and_attributed() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url).startswith(GEOCODING_URL):
            return httpx.Response(200, json=_geocoding_payload())
        assert str(request.url).startswith(FORECAST_URL)
        return httpx.Response(200, json=_forecast_payload())

    async with _client(handler) as client:
        result = await OpenMeteoForecastService(client).get_forecast(
            "  Berlin  ",
            3,
            "metric",
        )

    assert result.location.model_dump() == {
        "name": "Berlin",
        "country": "Germany",
        "region": "Berlin",
        "latitude": 52.52,
        "longitude": 13.405,
    }
    assert result.timezone == "Europe/Berlin"
    assert result.units.model_dump() == {
        "temperature": "°C",
        "precipitation": "mm",
        "precipitation_probability": "%",
        "wind_speed": "km/h",
    }
    assert [item.date for item in result.daily] == [
        date(2026, 9, 4),
        date(2026, 9, 5),
        date(2026, 9, 6),
    ]
    assert result.daily[0].condition == "Partly cloudy"
    assert result.daily[2].temperature_min == 13.0
    assert result.daily[2].temperature_max == 23.5
    assert result.daily[2].precipitation_sum == pytest.approx(0.4)
    assert result.daily[2].precipitation_probability == 20
    assert result.daily[2].wind_speed_max == 14.0
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
    query = _query(requests[1])
    assert query["forecast_days"] == ["3"]
    assert query["timezone"] == ["auto"]
    assert query["temperature_unit"] == ["celsius"]
    assert query["wind_speed_unit"] == ["kmh"]
    assert query["precipitation_unit"] == ["mm"]
    assert set(query["daily"][0].split(",")) == {
        "weather_code",
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_sum",
        "precipitation_probability_max",
        "wind_speed_10m_max",
    }


@pytest.mark.asyncio
async def test_unicode_destination_and_imperial_query_parameters_are_preserved() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = (
            _geocoding_payload() if len(requests) == 1 else _forecast_payload(days=1, imperial=True)
        )
        return httpx.Response(200, json=payload)

    async with _client(handler) as client:
        await OpenMeteoForecastService(client).get_forecast("München", 1, "imperial")

    assert _query(requests[0])["name"] == ["München"]
    query = _query(requests[1])
    assert query["forecast_days"] == ["1"]
    assert query["temperature_unit"] == ["fahrenheit"]
    assert query["wind_speed_unit"] == ["mph"]
    assert query["precipitation_unit"] == ["inch"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "unexpected"),
    [
        ("time", "unix"),
        ("weather_code", "text"),
        ("temperature_2m_max", "°F"),
        ("temperature_2m_min", "°F"),
        ("precipitation_sum", "inch"),
        ("precipitation_probability_max", "ratio"),
        ("wind_speed_10m_max", "mp/h"),
    ],
)
async def test_provider_unit_mismatch_is_rejected(
    field: str,
    unexpected: str,
) -> None:
    forecast = _forecast_payload()
    daily_units = _mapping(forecast["daily_units"])
    daily_units[field] = unexpected
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_geocoding_payload() if calls == 1 else forecast,
        )

    async with _client(handler) as client:
        with pytest.raises(WeatherProviderError, match="invalid data"):
            await OpenMeteoForecastService(client).get_forecast("Berlin", 3, "metric")


@pytest.mark.asyncio
async def test_first_geocoding_result_is_used_and_region_is_optional() -> None:
    geocoding = _geocoding_payload()
    results = geocoding["results"]
    assert isinstance(results, list)
    first = results[0]
    assert isinstance(first, dict)
    first.pop("admin1")
    results.append(
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
            json=geocoding if len(requests) == 1 else _forecast_payload(days=1),
        )

    async with _client(handler) as client:
        result = await OpenMeteoForecastService(client).get_forecast("Berlin", 1, "metric")

    assert result.location.region is None
    assert _query(requests[1])["latitude"] == ["52.52"]
    assert _query(requests[1])["longitude"] == ["13.405"]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"results": []}])
async def test_unknown_location_requests_a_more_specific_destination(
    payload: dict[str, object],
) -> None:
    async with _client(lambda _: httpx.Response(200, json=payload)) as client:
        with pytest.raises(LocationNotFoundError, match="more specific") as exc_info:
            await OpenMeteoForecastService(client).get_forecast("Atlantis", 2, "metric")

    assert "Atlantis" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(503, json={"reason": "private upstream detail"}),
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
            await OpenMeteoForecastService(client).get_forecast("Berlin", 2, "metric")

    message = str(exc_info.value)
    assert "temporarily unavailable" in message
    assert "private upstream detail" not in message
    assert "offline" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.clear(),
        lambda payload: _mapping(payload["daily"]).update(time=["not-a-date"] * 3),
        lambda payload: _mapping(payload["daily"]).update(weather_code=[2]),
        lambda payload: _mapping(payload["daily"]).update(
            precipitation_probability_max=[10, 200, 10]
        ),
        lambda payload: _mapping(payload["daily"]).update(temperature_2m_min=[30.0, 30.0, 30.0]),
    ],
)
async def test_malformed_forecast_is_a_safe_provider_error(
    mutation: Callable[[dict[str, object]], None],
) -> None:
    forecast = _forecast_payload()
    mutation(forecast)
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_geocoding_payload() if calls == 1 else forecast,
        )

    async with _client(handler) as client:
        with pytest.raises(WeatherProviderError, match="invalid data"):
            await OpenMeteoForecastService(client).get_forecast("Berlin", 3, "metric")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("temperature_2m_max", "21.5"),
        ("temperature_2m_max", True),
        ("weather_code", "2"),
        ("weather_code", True),
        ("weather_code", 2.5),
        ("precipitation_probability_max", "10"),
    ],
)
async def test_provider_numbers_reject_strings_booleans_and_fractional_integers(
    field: str,
    invalid: object,
) -> None:
    forecast = _forecast_payload()
    daily = _mapping(forecast["daily"])
    daily[field] = [invalid, invalid, invalid]
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_geocoding_payload() if calls == 1 else forecast,
        )

    async with _client(handler) as client:
        with pytest.raises(WeatherProviderError, match="invalid data"):
            await OpenMeteoForecastService(client).get_forecast("Berlin", 3, "metric")


@pytest.mark.asyncio
async def test_provider_accepts_real_json_integer_and_float_numbers() -> None:
    forecast = _forecast_payload()
    geocoding = _geocoding_payload()
    place = _mapping(cast_list(geocoding["results"])[0])
    place["latitude"] = 52
    place["longitude"] = 13.0
    daily = _mapping(forecast["daily"])
    daily["weather_code"] = [2.0, 2, 2.0]
    daily["temperature_2m_max"] = [21, 22.5, 23]
    daily["precipitation_probability_max"] = [10.0, 15, 20.0]
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=geocoding if calls == 1 else forecast)

    async with _client(handler) as client:
        result = await OpenMeteoForecastService(client).get_forecast("Berlin", 3, "metric")

    assert result.location.latitude == 52.0
    assert result.location.longitude == 13.0
    assert [day.weather_code for day in result.daily] == [2, 2, 2]
    assert [day.temperature_max for day in result.daily] == [21.0, 22.5, 23.0]
    assert [day.precipitation_probability for day in result.daily] == [10, 15, 20]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["52.52", True])
async def test_geocoder_coordinates_reject_numeric_strings_and_booleans(
    invalid: object,
) -> None:
    geocoding = _geocoding_payload()
    _mapping(cast_list(geocoding["results"])[0])["latitude"] = invalid

    async with _client(lambda _: httpx.Response(200, json=geocoding)) as client:
        with pytest.raises(WeatherProviderError, match="invalid data"):
            await OpenMeteoForecastService(client).get_forecast("Berlin", 3, "metric")


def cast_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


@pytest.mark.asyncio
async def test_provider_forecast_dates_must_be_consecutive() -> None:
    forecast = _forecast_payload()
    _mapping(forecast["daily"])["time"] = [
        "2026-09-04",
        "2026-09-06",
        "2026-09-07",
    ]
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_geocoding_payload() if calls == 1 else forecast,
        )

    async with _client(handler) as client:
        with pytest.raises(WeatherProviderError, match="invalid data"):
            await OpenMeteoForecastService(client).get_forecast("Berlin", 3, "metric")


@pytest.mark.asyncio
async def test_provider_text_with_control_characters_is_rejected() -> None:
    geocoding = _geocoding_payload()
    _mapping(cast_list(geocoding["results"])[0])["name"] = "Berlin\n# forged"
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=geocoding if calls == 1 else _forecast_payload(),
        )

    async with _client(handler) as client:
        with pytest.raises(WeatherProviderError, match="invalid data"):
            await OpenMeteoForecastService(client).get_forecast("Berlin", 3, "metric")


@pytest.mark.asyncio
async def test_unexpected_provider_runtime_failure_uses_safe_error() -> None:
    secret = "private transport implementation detail"

    def handler(_: httpx.Request) -> httpx.Response:
        raise RuntimeError(secret)

    async with _client(handler) as client:
        with pytest.raises(WeatherProviderError) as exc_info:
            await OpenMeteoForecastService(client).get_forecast("Berlin", 3, "metric")

    assert "temporarily unavailable" in str(exc_info.value)
    assert secret not in str(exc_info.value)


@pytest.mark.asyncio
async def test_unexpected_provider_day_count_is_rejected() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_geocoding_payload() if calls == 1 else _forecast_payload(days=2),
        )

    async with _client(handler) as client:
        with pytest.raises(WeatherProviderError, match="invalid data"):
            await OpenMeteoForecastService(client).get_forecast("Berlin", 3, "metric")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("destination", "days", "units"),
    [
        ("A", 1, "metric"),
        ("Berlin\nGermany", 1, "metric"),
        ("Berlin", 0, "metric"),
        ("Berlin", 8, "metric"),
        ("Berlin", True, "metric"),
        ("Berlin", 1, "kelvin"),
    ],
)
async def test_input_validation_happens_before_a_request(
    destination: str,
    days: object,
    units: object,
) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"unexpected request: {request.url}")

    async with _client(unexpected) as client:
        with pytest.raises(ValueError):
            await OpenMeteoForecastService(client).get_forecast(
                destination,
                days,  # type: ignore[arg-type]
                units,  # type: ignore[arg-type]
            )


@pytest.mark.asyncio
async def test_injected_client_remains_caller_owned() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_geocoding_payload() if calls == 1 else _forecast_payload(days=1),
        )

    client = _client(handler)
    await OpenMeteoForecastService(client).get_forecast("Berlin", 1, "metric")
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
