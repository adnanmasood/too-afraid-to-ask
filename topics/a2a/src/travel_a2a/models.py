"""Strict application models shared by the A2A server and coordinator."""

from __future__ import annotations

import unicodedata
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Units = Literal["metric", "imperial"]
TravelStyle = Literal["general", "business", "outdoors"]


def _reject_control_characters(value: str) -> str:
    """Keep protocol/provider/model text on one visible, printable line."""

    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for character in value
    ):
        raise ValueError("text must not contain control or line-break characters")
    return value


Destination = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=100),
    AfterValidator(_reject_control_characters),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
    AfterValidator(_reject_control_characters),
]
ListItem = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_reject_control_characters),
]

_MARKDOWN_SPECIAL_CHARACTERS = frozenset("\\`*_{}[]<>()#+-.!|>&~")


def _escape_markdown(value: str) -> str:
    """Escape untrusted dynamic text before inserting it into Markdown."""

    return "".join(
        f"\\{character}" if character in _MARKDOWN_SPECIAL_CHARACTERS else character
        for character in value
    )


def _dates_are_consecutive(values: list[date]) -> bool:
    return all(
        current == previous + timedelta(days=1)
        for previous, current in zip(values, values[1:], strict=False)
    )


class StrictModel(BaseModel):
    """Base class that rejects coercion, unknown fields, and non-finite numbers."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class PackingRequest(StrictModel):
    """Application-level input sent to the Packing Advisor agent."""

    destination: Destination
    days: int | None = Field(default=None, ge=1, le=7)
    units: Units = "metric"
    style: TravelStyle = "general"


class WeatherLocation(StrictModel):
    """The first location matched by Open-Meteo's geocoder."""

    name: ShortText
    country: ShortText
    region: ShortText | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    @property
    def display_name(self) -> str:
        """Return a concise, normalized label for human-readable output."""

        parts = [self.name]
        if self.region and self.region not in {self.name, self.country}:
            parts.append(self.region)
        if self.country != self.name:
            parts.append(self.country)
        return ", ".join(parts)


class ForecastUnits(StrictModel):
    """Provider-supplied unit labels for the normalized daily forecast."""

    temperature: ShortText
    precipitation: ShortText
    precipitation_probability: ShortText
    wind_speed: ShortText


class DailyForecast(StrictModel):
    """One normalized day from an Open-Meteo forecast."""

    date: date
    condition: ShortText
    weather_code: int
    temperature_min: float
    temperature_max: float
    precipitation_sum: float = Field(ge=0)
    precipitation_probability: int = Field(ge=0, le=100)
    wind_speed_max: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_temperature_range(self) -> DailyForecast:
        """Reject an internally inconsistent provider response."""

        if self.temperature_min > self.temperature_max:
            raise ValueError("temperature_min must not exceed temperature_max")
        return self


class WeatherForecast(StrictModel):
    """Provider-independent forecast consumed by packing planners."""

    location: WeatherLocation
    timezone: ShortText
    units: ForecastUnits
    daily: list[DailyForecast] = Field(min_length=1, max_length=7)
    source: Literal["Open-Meteo"] = "Open-Meteo"
    source_url: Literal["https://open-meteo.com/"] = "https://open-meteo.com/"

    @model_validator(mode="after")
    def validate_daily_dates(self) -> WeatherForecast:
        """Require unique, chronological, consecutive forecast days."""

        dates = [item.date for item in self.daily]
        if not _dates_are_consecutive(dates):
            raise ValueError("daily forecast dates must be unique, chronological, and consecutive")
        return self


class PackingAdvice(StrictModel):
    """Planner-owned recommendations; forecast facts deliberately live elsewhere."""

    essentials: list[ListItem] = Field(min_length=1, max_length=20)
    weather_specific_items: list[ListItem] = Field(max_length=20)
    style_specific_items: list[ListItem] = Field(max_length=20)
    cautions: list[ListItem] = Field(max_length=20)


class ForecastPeriod(StrictModel):
    """Range and units attached to the forecast embedded in a packing plan."""

    start_date: date
    end_date: date
    timezone: ShortText
    units: ForecastUnits

    @model_validator(mode="after")
    def validate_date_range(self) -> ForecastPeriod:
        """Reject a reversed period."""

        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class SourceAttribution(StrictModel):
    """Attribution retained when weather data crosses the A2A boundary."""

    name: Literal["Open-Meteo"] = "Open-Meteo"
    url: Literal["https://open-meteo.com/"] = "https://open-meteo.com/"


class PackingPlan(StrictModel):
    """Trusted forecast data combined with planner-owned packing advice."""

    location: WeatherLocation
    forecast_period: ForecastPeriod
    daily_summary: list[DailyForecast] = Field(min_length=1, max_length=7)
    essentials: list[ListItem] = Field(min_length=1, max_length=20)
    weather_specific_items: list[ListItem] = Field(max_length=20)
    style_specific_items: list[ListItem] = Field(max_length=20)
    cautions: list[ListItem] = Field(max_length=20)
    attribution: SourceAttribution

    @model_validator(mode="after")
    def validate_forecast_period(self) -> PackingPlan:
        """Keep the advertised period synchronized with the daily data."""

        dates = [item.date for item in self.daily_summary]
        if not _dates_are_consecutive(dates):
            raise ValueError("daily summary dates must be unique, chronological, and consecutive")
        if dates[0] != self.forecast_period.start_date:
            raise ValueError("forecast period must start on the first daily summary date")
        if dates[-1] != self.forecast_period.end_date:
            raise ValueError("forecast period must end on the last daily summary date")
        return self

    def to_markdown(self) -> str:
        """Render a compact human-readable companion to the JSON artifact part."""

        period = self.forecast_period
        units = period.units
        location = _escape_markdown(self.location.display_name)
        timezone = _escape_markdown(period.timezone)
        temperature_unit = _escape_markdown(units.temperature)
        wind_speed_unit = _escape_markdown(units.wind_speed)
        lines = [
            f"# Packing plan for {location}",
            "",
            f"Forecast period: {period.start_date.isoformat()} to "
            f"{period.end_date.isoformat()} ({timezone})",
            "",
            "## Daily forecast",
            "",
            "| Date | Conditions | Low / high | Rain | Max wind |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
        for day in self.daily_summary:
            condition = _escape_markdown(day.condition)
            lines.append(
                f"| {day.date.isoformat()} | {condition} | "
                f"{day.temperature_min:g} / {day.temperature_max:g} "
                f"{temperature_unit} | {day.precipitation_probability}% | "
                f"{day.wind_speed_max:g} {wind_speed_unit} |"
            )

        for heading, items in (
            ("Essentials", self.essentials),
            ("Weather-specific items", self.weather_specific_items),
            ("Style-specific items", self.style_specific_items),
            ("Cautions", self.cautions),
        ):
            lines.extend(("", f"## {heading}", ""))
            if items:
                lines.extend(f"- {_escape_markdown(item)}" for item in items)
            else:
                lines.append("- None")

        lines.extend(
            (
                "",
                f"Weather data: [{self.attribution.name}]({self.attribution.url}).",
            )
        )
        return "\n".join(lines)
