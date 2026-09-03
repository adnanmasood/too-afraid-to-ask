"""Open-Meteo implementation of the weather service used by the MCP server."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from weather_mcp.models import Units, WeatherLocation, WeatherResult, WeatherUnits

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_URL = "https://open-meteo.com/"
DEFAULT_TIMEOUT_SECONDS = 10.0

_CURRENT_FIELDS = (
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "is_day",
    "precipitation",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
)

_WMO_DESCRIPTIONS: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

_SAFE_PROVIDER_MESSAGE = (
    "The weather provider is temporarily unavailable or returned invalid data. "
    "Please try again later."
)


class LocationNotFoundError(ValueError):
    """Raised when the geocoder cannot match a requested city."""


class WeatherProviderError(RuntimeError):
    """Raised with a safe message when Open-Meteo cannot provide valid data."""


class WeatherService(Protocol):
    """Small interface consumed by the MCP tool, allowing test substitutes."""

    async def get_weather(self, city: str, units: Units) -> WeatherResult:
        """Return current weather for a city."""


class _GeocodingPlace(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    name: str = Field(min_length=1)
    country: str = Field(min_length=1)
    admin1: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class _GeocodingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[_GeocodingPlace] = Field(default_factory=list)


class _CurrentWeather(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    time: str = Field(min_length=1)
    temperature_2m: float
    relative_humidity_2m: float = Field(ge=0, le=100)
    apparent_temperature: float
    is_day: Literal[0, 1]
    precipitation: float = Field(ge=0)
    weather_code: int
    cloud_cover: float = Field(ge=0, le=100)
    wind_speed_10m: float = Field(ge=0)
    wind_direction_10m: float = Field(ge=0, le=360)


class _CurrentUnits(BaseModel):
    model_config = ConfigDict(extra="ignore")

    temperature_2m: str = Field(min_length=1)
    relative_humidity_2m: str = Field(min_length=1)
    apparent_temperature: str = Field(min_length=1)
    precipitation: str = Field(min_length=1)
    cloud_cover: str = Field(min_length=1)
    wind_speed_10m: str = Field(min_length=1)
    wind_direction_10m: str = Field(min_length=1)


class _ForecastResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timezone: str = Field(min_length=1)
    current: _CurrentWeather
    current_units: _CurrentUnits


def describe_wmo_code(code: int) -> str:
    """Return a readable WMO interpretation, including a useful fallback."""

    return _WMO_DESCRIPTIONS.get(code, f"Unknown weather condition (WMO code {code})")


class OpenMeteoWeatherService:
    """Fetch current conditions from Open-Meteo's unauthenticated APIs.

    An injected client remains owned by the caller. Without one, a short-lived
    client is created and shared by the geocoding and forecast requests.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self._client = client
        self._timeout = timeout

    async def get_weather(self, city: str, units: Units = "metric") -> WeatherResult:
        """Geocode ``city``, then fetch and normalize its current conditions."""

        normalized_city = city.strip()
        if not 2 <= len(normalized_city) <= 100:
            raise ValueError("city must contain between 2 and 100 characters")
        if units not in ("metric", "imperial"):
            raise ValueError("units must be either 'metric' or 'imperial'")

        if self._client is not None:
            return await self._get_weather(self._client, normalized_city, units)

        async with httpx.AsyncClient(
            timeout=self._timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "weather-mcp-learning-project/0.1.0",
            },
        ) as client:
            return await self._get_weather(client, normalized_city, units)

    async def _get_weather(
        self,
        client: httpx.AsyncClient,
        city: str,
        units: Units,
    ) -> WeatherResult:
        geocoding_payload = await self._get_json(
            client,
            GEOCODING_URL,
            params={
                "name": city,
                "count": 1,
                "language": "en",
                "format": "json",
            },
        )
        try:
            geocoding = _GeocodingResponse.model_validate(geocoding_payload)
        except ValidationError as exc:
            raise WeatherProviderError(_SAFE_PROVIDER_MESSAGE) from exc

        if not geocoding.results:
            raise LocationNotFoundError(
                f"No location found for {city!r}. Try a more specific value, "
                "such as 'Paris, France'."
            )
        place = geocoding.results[0]

        forecast_params: dict[str, str | float] = {
            "latitude": place.latitude,
            "longitude": place.longitude,
            "current": ",".join(_CURRENT_FIELDS),
            "timezone": "auto",
        }
        if units == "imperial":
            forecast_params.update(
                temperature_unit="fahrenheit",
                wind_speed_unit="mph",
                precipitation_unit="inch",
            )
        else:
            forecast_params.update(
                temperature_unit="celsius",
                wind_speed_unit="kmh",
                precipitation_unit="mm",
            )

        forecast_payload = await self._get_json(
            client,
            FORECAST_URL,
            params=forecast_params,
        )
        try:
            forecast = _ForecastResponse.model_validate(forecast_payload)
            return self._normalize(place, forecast)
        except ValidationError as exc:
            raise WeatherProviderError(_SAFE_PROVIDER_MESSAGE) from exc

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: Mapping[str, str | float | int],
    ) -> Any:
        try:
            response = await client.get(url, params=params, timeout=self._timeout)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WeatherProviderError(_SAFE_PROVIDER_MESSAGE) from exc

        if not isinstance(payload, Mapping):
            raise WeatherProviderError(_SAFE_PROVIDER_MESSAGE)
        return cast(Mapping[str, Any], payload)

    @staticmethod
    def _normalize(
        place: _GeocodingPlace,
        forecast: _ForecastResponse,
    ) -> WeatherResult:
        current = forecast.current
        unit_data = forecast.current_units
        return WeatherResult(
            location=WeatherLocation(
                name=place.name,
                country=place.country,
                region=place.admin1,
                latitude=place.latitude,
                longitude=place.longitude,
            ),
            observed_at=current.time,
            timezone=forecast.timezone,
            condition=describe_wmo_code(current.weather_code),
            weather_code=current.weather_code,
            temperature=current.temperature_2m,
            apparent_temperature=current.apparent_temperature,
            relative_humidity=current.relative_humidity_2m,
            precipitation=current.precipitation,
            cloud_cover=current.cloud_cover,
            wind_speed=current.wind_speed_10m,
            wind_direction_degrees=current.wind_direction_10m,
            is_day=bool(current.is_day),
            units=WeatherUnits(
                temperature=unit_data.temperature_2m,
                relative_humidity=unit_data.relative_humidity_2m,
                precipitation=unit_data.precipitation,
                cloud_cover=unit_data.cloud_cover,
                wind_speed=unit_data.wind_speed_10m,
                wind_direction=unit_data.wind_direction_10m,
            ),
            source="Open-Meteo",
            source_url=OPEN_METEO_URL,
        )
