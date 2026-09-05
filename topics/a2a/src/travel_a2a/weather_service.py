"""Injectable Open-Meteo adapter for one-to-seven-day forecasts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Annotated, Any, Protocol, cast

import httpx
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from travel_a2a.models import (
    DailyForecast,
    ForecastUnits,
    Units,
    WeatherForecast,
    WeatherLocation,
)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_URL = "https://open-meteo.com/"
DEFAULT_TIMEOUT_SECONDS = 10.0

_DAILY_FIELDS = (
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "precipitation_probability_max",
    "wind_speed_10m_max",
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

_EXPECTED_PROVIDER_UNITS: dict[Units, dict[str, str]] = {
    "metric": {
        "time": "iso8601",
        "weather_code": "wmo code",
        "temperature_2m_max": "°C",
        "temperature_2m_min": "°C",
        "precipitation_sum": "mm",
        "precipitation_probability_max": "%",
        "wind_speed_10m_max": "km/h",
    },
    "imperial": {
        "time": "iso8601",
        "weather_code": "wmo code",
        "temperature_2m_max": "°F",
        "temperature_2m_min": "°F",
        "precipitation_sum": "inch",
        "precipitation_probability_max": "%",
        "wind_speed_10m_max": "mp/h",
    },
}


def _provider_float(value: Any) -> float:
    """Accept only genuine JSON numbers, never booleans or numeric strings."""

    if type(value) not in {int, float}:
        raise ValueError("value must be a JSON number")
    try:
        return float(value)
    except OverflowError as exc:
        raise ValueError("numeric value is outside the supported range") from exc


def _provider_int(value: Any) -> int:
    """Accept JSON integers and integral floats while rejecting coercion."""

    if type(value) is int:
        return value
    if type(value) is float and value.is_integer():
        return int(value)
    raise ValueError("value must be an integer JSON number")


ProviderFloat = Annotated[float, BeforeValidator(_provider_float)]
ProviderInt = Annotated[int, BeforeValidator(_provider_int)]


class LocationNotFoundError(ValueError):
    """Raised when the geocoder cannot match a requested destination."""


class WeatherProviderError(RuntimeError):
    """Raised with a safe message when Open-Meteo cannot provide valid data."""


class ForecastService(Protocol):
    """Small interface consumed by the A2A executor and replaceable in tests."""

    async def get_forecast(
        self,
        destination: str,
        days: int,
        units: Units,
    ) -> WeatherForecast:
        """Return a normalized daily forecast for a destination."""


class _ProviderModel(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class _GeocodingPlace(_ProviderModel):
    name: str = Field(min_length=1)
    country: str = Field(min_length=1)
    admin1: str | None = None
    latitude: ProviderFloat = Field(ge=-90, le=90)
    longitude: ProviderFloat = Field(ge=-180, le=180)


class _GeocodingResponse(_ProviderModel):
    results: list[_GeocodingPlace] = Field(default_factory=list)


class _DailyData(_ProviderModel):
    time: list[str] = Field(min_length=1, max_length=7)
    weather_code: list[ProviderInt]
    temperature_2m_max: list[ProviderFloat]
    temperature_2m_min: list[ProviderFloat]
    precipitation_sum: list[ProviderFloat]
    precipitation_probability_max: list[ProviderInt]
    wind_speed_10m_max: list[ProviderFloat]

    @model_validator(mode="after")
    def validate_parallel_arrays(self) -> _DailyData:
        expected_length = len(self.time)
        values = (
            self.weather_code,
            self.temperature_2m_max,
            self.temperature_2m_min,
            self.precipitation_sum,
            self.precipitation_probability_max,
            self.wind_speed_10m_max,
        )
        if any(len(items) != expected_length for items in values):
            raise ValueError("daily forecast arrays must have equal lengths")
        return self


class _DailyUnits(_ProviderModel):
    time: str = Field(min_length=1)
    weather_code: str = Field(min_length=1)
    temperature_2m_max: str = Field(min_length=1)
    temperature_2m_min: str = Field(min_length=1)
    precipitation_sum: str = Field(min_length=1)
    precipitation_probability_max: str = Field(min_length=1)
    wind_speed_10m_max: str = Field(min_length=1)


class _ForecastResponse(_ProviderModel):
    timezone: str = Field(min_length=1)
    daily: _DailyData
    daily_units: _DailyUnits


def describe_wmo_code(code: int) -> str:
    """Return a readable WMO interpretation, including a useful fallback."""

    return _WMO_DESCRIPTIONS.get(code, f"Unknown weather condition (WMO code {code})")


class OpenMeteoForecastService:
    """Fetch forecasts from Open-Meteo's unauthenticated APIs.

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

    async def get_forecast(
        self,
        destination: str,
        days: int,
        units: Units = "metric",
    ) -> WeatherForecast:
        """Geocode ``destination``, then fetch and normalize its daily forecast."""

        if not isinstance(destination, str):
            raise ValueError("destination must be a string")
        normalized_destination = destination.strip()
        if not 2 <= len(normalized_destination) <= 100:
            raise ValueError("destination must contain between 2 and 100 characters")
        if not normalized_destination.isprintable():
            raise ValueError("destination must not contain control or line-break characters")
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 7:
            raise ValueError("days must be an integer between 1 and 7")
        if units not in ("metric", "imperial"):
            raise ValueError("units must be either 'metric' or 'imperial'")

        try:
            if self._client is not None:
                return await self._get_forecast(
                    self._client,
                    normalized_destination,
                    days,
                    units,
                )

            async with httpx.AsyncClient(
                timeout=self._timeout,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "travel-a2a-learning/0.1.0",
                },
            ) as client:
                return await self._get_forecast(client, normalized_destination, days, units)
        except (LocationNotFoundError, WeatherProviderError):
            raise
        except Exception as exc:
            raise WeatherProviderError(_SAFE_PROVIDER_MESSAGE) from exc

    async def _get_forecast(
        self,
        client: httpx.AsyncClient,
        destination: str,
        days: int,
        units: Units,
    ) -> WeatherForecast:
        geocoding_payload = await self._get_json(
            client,
            GEOCODING_URL,
            params={
                "name": destination,
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
                f"No location found for {destination!r}. Try a more specific value, "
                "such as 'Paris, France'."
            )
        place = geocoding.results[0]

        forecast_params: dict[str, str | float | int] = {
            "latitude": place.latitude,
            "longitude": place.longitude,
            "daily": ",".join(_DAILY_FIELDS),
            "forecast_days": days,
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
            if len(forecast.daily.time) != days:
                raise ValueError("provider returned an unexpected number of days")
            self._validate_provider_units(forecast.daily_units, units)
            return self._normalize(place, forecast)
        except (ValidationError, ValueError) as exc:
            raise WeatherProviderError(_SAFE_PROVIDER_MESSAGE) from exc

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: Mapping[str, str | float | int],
    ) -> Mapping[str, Any]:
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
    def _validate_provider_units(daily_units: _DailyUnits, units: Units) -> None:
        expected = _EXPECTED_PROVIDER_UNITS[units]
        actual = {
            "time": daily_units.time,
            "weather_code": daily_units.weather_code,
            "temperature_2m_max": daily_units.temperature_2m_max,
            "temperature_2m_min": daily_units.temperature_2m_min,
            "precipitation_sum": daily_units.precipitation_sum,
            "precipitation_probability_max": daily_units.precipitation_probability_max,
            "wind_speed_10m_max": daily_units.wind_speed_10m_max,
        }
        if actual != expected:
            raise ValueError("provider units do not match the requested unit system")

    @staticmethod
    def _normalize(
        place: _GeocodingPlace,
        forecast: _ForecastResponse,
    ) -> WeatherForecast:
        daily = forecast.daily
        units = forecast.daily_units
        normalized_days = [
            DailyForecast(
                date=date.fromisoformat(day),
                condition=describe_wmo_code(daily.weather_code[index]),
                weather_code=daily.weather_code[index],
                temperature_min=float(daily.temperature_2m_min[index]),
                temperature_max=float(daily.temperature_2m_max[index]),
                precipitation_sum=float(daily.precipitation_sum[index]),
                precipitation_probability=daily.precipitation_probability_max[index],
                wind_speed_max=float(daily.wind_speed_10m_max[index]),
            )
            for index, day in enumerate(daily.time)
        ]
        return WeatherForecast(
            location=WeatherLocation(
                name=place.name,
                country=place.country,
                region=place.admin1,
                latitude=float(place.latitude),
                longitude=float(place.longitude),
            ),
            timezone=forecast.timezone,
            units=ForecastUnits(
                temperature=units.temperature_2m_max,
                precipitation=units.precipitation_sum,
                precipitation_probability=units.precipitation_probability_max,
                wind_speed=units.wind_speed_10m_max,
            ),
            daily=normalized_days,
            source="Open-Meteo",
            source_url=OPEN_METEO_URL,
        )
