"""Typed domain models returned by the weather MCP tool."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Units = Literal["metric", "imperial"]


class WeatherLocation(BaseModel):
    """The first location matched by Open-Meteo's geocoder."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str
    country: str
    region: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class WeatherUnits(BaseModel):
    """Unit labels supplied by Open-Meteo for each numeric measurement."""

    model_config = ConfigDict(extra="forbid")

    temperature: str
    relative_humidity: str
    precipitation: str
    cloud_cover: str
    wind_speed: str
    wind_direction: str


class WeatherResult(BaseModel):
    """Provider-independent weather data exposed as MCP structured output."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    location: WeatherLocation
    observed_at: str
    timezone: str
    condition: str
    weather_code: int
    temperature: float
    apparent_temperature: float
    relative_humidity: float = Field(ge=0, le=100)
    precipitation: float = Field(ge=0)
    cloud_cover: float = Field(ge=0, le=100)
    wind_speed: float = Field(ge=0)
    wind_direction_degrees: float = Field(ge=0, le=360)
    is_day: bool
    units: WeatherUnits
    source: str = "Open-Meteo"
    source_url: str = "https://open-meteo.com/"
