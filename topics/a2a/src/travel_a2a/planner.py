"""Deterministic and optional OpenAI packing planners behind one interface."""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from travel_a2a.models import (
    ForecastPeriod,
    PackingAdvice,
    PackingPlan,
    PackingRequest,
    SourceAttribution,
    WeatherForecast,
)

DEFAULT_OPENAI_MODEL = "gpt-5.6"
_SAFE_PLANNER_MESSAGE = (
    "The packing planner is temporarily unavailable or returned invalid data. "
    "Please try again later."
)

_RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
_SNOW_CODES = {71, 73, 75, 77, 85, 86}
_THUNDERSTORM_CODES = {95, 96, 99}


class PlannerError(RuntimeError):
    """Raised with a safe message when a planner cannot return valid advice."""


class PackingPlanner(Protocol):
    """Planner contract used by the A2A executor."""

    async def plan(
        self,
        request: PackingRequest,
        forecast: WeatherForecast,
    ) -> PackingAdvice:
        """Generate only recommendations; forecast facts remain application-owned."""


def _append_once(items: list[str], item: str) -> None:
    if item not in items:
        items.append(item)


def _to_celsius(value: float, *, imperial: bool) -> float:
    converted = (value - 32) * 5 / 9 if imperial else value
    return round(converted, 6)


def _to_millimeters(value: float, *, imperial: bool) -> float:
    converted = value * 25.4 if imperial else value
    return round(converted, 6)


def _to_kilometers_per_hour(value: float, *, imperial: bool) -> float:
    converted = value * 1.609344 if imperial else value
    return round(converted, 6)


class DeterministicPackingPlanner:
    """Apply transparent, repeatable thresholds without an API key."""

    async def plan(
        self,
        request: PackingRequest,
        forecast: WeatherForecast,
    ) -> PackingAdvice:
        if request.days is None:
            raise PlannerError("A trip length is required before packing advice can be created.")
        if request.days != len(forecast.daily):
            raise PlannerError("The forecast does not match the requested trip length.")

        day_word = "day" if request.days == 1 else "days"
        essentials = [
            "Travel documents and wallet",
            "Phone and charger",
            f"Medication for {request.days} {day_word}",
            "Toiletries",
            f"Underwear and socks for {request.days} {day_word}",
        ]
        weather_items: list[str] = []
        cautions: list[str] = []

        imperial = request.units == "imperial"
        minimum_c = min(
            _to_celsius(day.temperature_min, imperial=imperial) for day in forecast.daily
        )
        maximum_c = max(
            _to_celsius(day.temperature_max, imperial=imperial) for day in forecast.daily
        )
        precipitation_mm = max(
            _to_millimeters(day.precipitation_sum, imperial=imperial) for day in forecast.daily
        )
        rain_probability = max(day.precipitation_probability for day in forecast.daily)
        wind_kmh = max(
            _to_kilometers_per_hour(day.wind_speed_max, imperial=imperial) for day in forecast.daily
        )
        weather_codes = {day.weather_code for day in forecast.daily}

        if minimum_c <= 0:
            _append_once(weather_items, "Insulated coat")
            _append_once(weather_items, "Warm hat and gloves")
        elif minimum_c <= 10:
            _append_once(weather_items, "Warm layers")
            _append_once(weather_items, "Light jacket")

        if maximum_c >= 27:
            _append_once(weather_items, "Lightweight breathable clothing")
            _append_once(weather_items, "Sun protection and sunglasses")
            _append_once(weather_items, "Reusable water bottle")

        rain_expected = bool(weather_codes & _RAIN_CODES) or (
            precipitation_mm >= 1 or rain_probability >= 40
        )
        if rain_expected:
            _append_once(weather_items, "Compact umbrella")
            _append_once(weather_items, "Water-resistant outer layer")

        snow_expected = bool(weather_codes & _SNOW_CODES)
        if snow_expected:
            _append_once(weather_items, "Waterproof boots with good traction")

        if wind_kmh >= 30:
            _append_once(weather_items, "Windproof layer")

        if not weather_items:
            weather_items.append("Versatile light layer for changing conditions")

        if weather_codes & _THUNDERSTORM_CODES:
            cautions.append(
                "Thunderstorms are forecast; monitor local alerts and avoid exposed areas."
            )
        if snow_expected:
            cautions.append("Snow or ice may affect footing and transportation.")
        if precipitation_mm >= 10 or rain_probability >= 70:
            cautions.append("Heavy or likely precipitation may disrupt outdoor plans.")
        if wind_kmh >= 40:
            cautions.append("Strong winds may affect exposed routes and transportation.")
        if maximum_c - minimum_c >= 12:
            cautions.append("Temperatures vary widely; dress in adjustable layers.")
        if not cautions:
            cautions.append("Check the latest local forecast before departure.")

        style_items = {
            "general": ["Comfortable walking shoes", "Versatile casual outfits"],
            "business": [
                "Business attire",
                "Dress shoes",
                "Garment bag or wrinkle-release spray",
            ],
            "outdoors": ["Sturdy footwear", "Daypack", "Reusable water bottle"],
        }[request.style]

        return PackingAdvice(
            essentials=essentials,
            weather_specific_items=weather_items,
            style_specific_items=style_items,
            cautions=cautions,
        )


class OpenAIPackingPlanner:
    """Generate validated advice with the Responses API and a Pydantic schema."""

    def __init__(self, client: Any | None = None, *, model: str | None = None) -> None:
        configured_model = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        if not configured_model.strip():
            raise ValueError("model must not be empty")
        self._client = client
        self._owns_client = client is None
        self._closed = False
        self._model = configured_model.strip()

    def _get_client(self) -> Any:
        if self._closed:
            raise PlannerError("The OpenAI planner has been closed.")
        if self._client is not None:
            return self._client

        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise PlannerError(
                "The OpenAI planner requires the optional 'ai' dependencies."
            ) from exc
        self._client = AsyncOpenAI()
        return self._client

    async def aclose(self) -> None:
        """Close only a lazily created SDK client; injected clients stay caller-owned."""

        if self._closed:
            return
        self._closed = True
        if self._owns_client and self._client is not None:
            await self._client.close()

    async def plan(
        self,
        request: PackingRequest,
        forecast: WeatherForecast,
    ) -> PackingAdvice:
        if request.days is None:
            raise PlannerError("A trip length is required before packing advice can be created.")
        if request.days != len(forecast.daily):
            raise PlannerError("The forecast does not match the requested trip length.")

        payload = {
            "request": request.model_dump(mode="json"),
            "forecast": forecast.model_dump(mode="json"),
        }
        try:
            response = await self._get_client().responses.parse(
                model=self._model,
                instructions=(
                    "You are a concise travel packing advisor. The supplied forecast is "
                    "trusted application data. Do not restate, alter, or invent forecast "
                    "facts. Return practical recommendations in the PackingAdvice schema."
                ),
                input=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                text_format=PackingAdvice,
                store=False,
            )
            parsed = response.output_parsed
            if parsed is None:
                raise ValueError("the model returned no parsed output")
            return PackingAdvice.model_validate(parsed)
        except PlannerError:
            raise
        except Exception as exc:
            raise PlannerError(_SAFE_PLANNER_MESSAGE) from exc


def create_planner(
    name: str | None = None,
    *,
    openai_client: Any | None = None,
    openai_model: str | None = None,
) -> PackingPlanner:
    """Create the requested planner, falling back to ``PACKING_PLANNER``."""

    configured_name = name or os.getenv("PACKING_PLANNER") or "deterministic"
    normalized_name = configured_name.strip().lower()
    if normalized_name == "deterministic":
        return DeterministicPackingPlanner()
    if normalized_name == "openai":
        return OpenAIPackingPlanner(openai_client, model=openai_model)
    raise ValueError("planner must be either 'deterministic' or 'openai'")


def build_packing_plan(
    request: PackingRequest,
    forecast: WeatherForecast,
    advice: PackingAdvice,
) -> PackingPlan:
    """Combine trusted weather data and separately generated packing advice."""

    if request.days is None:
        raise ValueError("request.days is required to build a packing plan")
    if request.days != len(forecast.daily):
        raise ValueError("forecast length does not match request.days")
    first_day = forecast.daily[0].date
    last_day = forecast.daily[-1].date
    return PackingPlan(
        location=forecast.location,
        forecast_period=ForecastPeriod(
            start_date=first_day,
            end_date=last_day,
            timezone=forecast.timezone,
            units=forecast.units,
        ),
        daily_summary=forecast.daily,
        essentials=advice.essentials,
        weather_specific_items=advice.weather_specific_items,
        style_specific_items=advice.style_specific_items,
        cautions=advice.cautions,
        attribution=SourceAttribution(name=forecast.source, url=forecast.source_url),
    )
