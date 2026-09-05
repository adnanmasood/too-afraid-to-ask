"""Tests for deterministic and optional OpenAI packing planners."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from travel_a2a.models import (
    DailyForecast,
    ForecastUnits,
    PackingAdvice,
    PackingRequest,
    WeatherForecast,
    WeatherLocation,
)
from travel_a2a.planner import (
    DeterministicPackingPlanner,
    OpenAIPackingPlanner,
    PlannerError,
    build_packing_plan,
    create_planner,
)


def _day(
    day: int,
    *,
    code: int = 2,
    low: float = 14.0,
    high: float = 22.0,
    precipitation: float = 0.0,
    rain_probability: int = 10,
    wind: float = 12.0,
) -> DailyForecast:
    return DailyForecast(
        date=date(2026, 9, day),
        condition="Partly cloudy",
        weather_code=code,
        temperature_min=low,
        temperature_max=high,
        precipitation_sum=precipitation,
        precipitation_probability=rain_probability,
        wind_speed_max=wind,
    )


def _forecast(
    daily: list[DailyForecast] | None = None,
    *,
    imperial: bool = False,
) -> WeatherForecast:
    return WeatherForecast(
        location=WeatherLocation(
            name="München",
            country="Germany",
            region="Bavaria",
            latitude=48.137,
            longitude=11.575,
        ),
        timezone="Europe/Berlin",
        units=ForecastUnits(
            temperature="°F" if imperial else "°C",
            precipitation="inch" if imperial else "mm",
            precipitation_probability="%",
            wind_speed="mph" if imperial else "km/h",
        ),
        daily=daily or [_day(4), _day(5), _day(6)],
    )


@pytest.mark.asyncio
async def test_mild_forecast_uses_stable_baseline_recommendations() -> None:
    request = PackingRequest(destination="München", days=3)

    advice = await DeterministicPackingPlanner().plan(request, _forecast())

    assert advice.essentials == [
        "Travel documents and wallet",
        "Phone and charger",
        "Medication for 3 days",
        "Toiletries",
        "Underwear and socks for 3 days",
    ]
    assert advice.weather_specific_items == ["Versatile light layer for changing conditions"]
    assert advice.style_specific_items == [
        "Comfortable walking shoes",
        "Versatile casual outfits",
    ]
    assert advice.cautions == ["Check the latest local forecast before departure."]


@pytest.mark.asyncio
async def test_extreme_forecast_triggers_weather_thresholds_and_cautions() -> None:
    forecast = _forecast(
        [
            _day(
                4,
                code=99,
                low=-4,
                high=30,
                precipitation=12,
                rain_probability=90,
                wind=46,
            ),
            _day(5, code=75, low=-2, high=8, precipitation=4, wind=35),
        ]
    )
    request = PackingRequest(destination="München", days=2, style="outdoors")

    advice = await DeterministicPackingPlanner().plan(request, forecast)

    assert advice.weather_specific_items == [
        "Insulated coat",
        "Warm hat and gloves",
        "Lightweight breathable clothing",
        "Sun protection and sunglasses",
        "Reusable water bottle",
        "Compact umbrella",
        "Water-resistant outer layer",
        "Waterproof boots with good traction",
        "Windproof layer",
    ]
    assert advice.style_specific_items == [
        "Sturdy footwear",
        "Daypack",
        "Reusable water bottle",
    ]
    assert advice.cautions == [
        "Thunderstorms are forecast; monitor local alerts and avoid exposed areas.",
        "Snow or ice may affect footing and transportation.",
        "Heavy or likely precipitation may disrupt outdoor plans.",
        "Strong winds may affect exposed routes and transportation.",
        "Temperatures vary widely; dress in adjustable layers.",
    ]


@pytest.mark.asyncio
async def test_imperial_thresholds_match_metric_thresholds() -> None:
    metric = _forecast([_day(4, low=0, high=27, precipitation=10, rain_probability=70, wind=40)])
    imperial = _forecast(
        [
            _day(
                4,
                low=32,
                high=80.6,
                precipitation=10 / 25.4,
                rain_probability=70,
                wind=40 / 1.609344,
            )
        ],
        imperial=True,
    )
    planner = DeterministicPackingPlanner()

    metric_advice = await planner.plan(
        PackingRequest(destination="Berlin", days=1, units="metric"),
        metric,
    )
    imperial_advice = await planner.plan(
        PackingRequest(destination="Berlin", days=1, units="imperial"),
        imperial,
    )

    assert imperial_advice == metric_advice


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("general", ["Comfortable walking shoes", "Versatile casual outfits"]),
        (
            "business",
            ["Business attire", "Dress shoes", "Garment bag or wrinkle-release spray"],
        ),
        ("outdoors", ["Sturdy footwear", "Daypack", "Reusable water bottle"]),
    ],
)
async def test_style_specific_rules(style: str, expected: list[str]) -> None:
    request = PackingRequest.model_validate({"destination": "Berlin", "days": 3, "style": style})

    advice = await DeterministicPackingPlanner().plan(request, _forecast())

    assert advice.style_specific_items == expected


@pytest.mark.asyncio
async def test_planner_requires_days_and_matching_forecast_length() -> None:
    planner = DeterministicPackingPlanner()

    with pytest.raises(PlannerError, match="trip length"):
        await planner.plan(PackingRequest(destination="Berlin"), _forecast())
    with pytest.raises(PlannerError, match="does not match"):
        await planner.plan(PackingRequest(destination="Berlin", days=2), _forecast())


def test_build_packing_plan_keeps_forecast_facts_application_owned() -> None:
    request = PackingRequest(destination="München", days=3, style="business")
    forecast = _forecast()
    advice = PackingAdvice(
        essentials=["Passport"],
        weather_specific_items=["Umbrella"],
        style_specific_items=["Business attire"],
        cautions=["Check local alerts"],
    )

    plan = build_packing_plan(request, forecast, advice)

    assert plan.location is forecast.location
    assert plan.daily_summary == forecast.daily
    assert plan.forecast_period.start_date == date(2026, 9, 4)
    assert plan.forecast_period.end_date == date(2026, 9, 6)
    assert plan.forecast_period.units is forecast.units
    assert plan.essentials == advice.essentials
    assert plan.attribution.model_dump() == {
        "name": "Open-Meteo",
        "url": "https://open-meteo.com/",
    }
    assert "München, Bavaria, Germany" in plan.to_markdown()


@dataclass
class FakeResponses:
    parsed: object
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def parse(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output_parsed=self.parsed)


@dataclass
class FakeOpenAIClient:
    responses: FakeResponses
    close_calls: int = 0

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_openai_planner_uses_responses_parse_and_validates_schema() -> None:
    parsed = {
        "essentials": ["Passport"],
        "weather_specific_items": ["Light jacket"],
        "style_specific_items": ["Comfortable walking shoes"],
        "cautions": ["Check the latest forecast"],
    }
    responses = FakeResponses(parsed)
    planner = OpenAIPackingPlanner(FakeOpenAIClient(responses), model="test-model")
    request = PackingRequest(destination="München", days=3)
    forecast = _forecast()

    advice = await planner.plan(request, forecast)

    assert advice == PackingAdvice.model_validate(parsed)
    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call["model"] == "test-model"
    assert call["text_format"] is PackingAdvice
    assert call["store"] is False
    assert "trusted application data" in call["instructions"]
    payload = json.loads(call["input"])
    assert payload["request"]["destination"] == "München"
    assert payload["forecast"]["daily"][0]["temperature_min"] == 14.0
    assert payload["forecast"]["source"] == "Open-Meteo"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "responses",
    [
        FakeResponses(None),
        FakeResponses({"essentials": ["Passport"], "unexpected": "secret"}),
        FakeResponses(
            {
                "essentials": ["Passport\n# forged heading"],
                "weather_specific_items": [],
                "style_specific_items": [],
                "cautions": [],
            }
        ),
        FakeResponses(None, RuntimeError("private API diagnostic")),
    ],
)
async def test_openai_failures_are_sanitized(responses: FakeResponses) -> None:
    planner = OpenAIPackingPlanner(FakeOpenAIClient(responses), model="test-model")

    with pytest.raises(PlannerError) as exc_info:
        await planner.plan(PackingRequest(destination="Berlin", days=3), _forecast())

    message = str(exc_info.value)
    assert "temporarily unavailable" in message
    assert "private API diagnostic" not in message
    assert "unexpected" not in message


@pytest.mark.asyncio
async def test_openai_planner_does_not_close_injected_client() -> None:
    client = FakeOpenAIClient(FakeResponses(None))
    planner = OpenAIPackingPlanner(client, model="test-model")

    await planner.aclose()
    await planner.aclose()

    assert client.close_calls == 0


@pytest.mark.asyncio
async def test_openai_planner_closes_lazily_created_client_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeOpenAIClient(FakeResponses(None))
    fake_openai = ModuleType("openai")
    fake_openai.AsyncOpenAI = lambda: client
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    planner = OpenAIPackingPlanner(model="test-model")

    assert planner._get_client() is client
    await planner.aclose()
    await planner.aclose()

    assert client.close_calls == 1
    with pytest.raises(PlannerError, match="closed"):
        planner._get_client()


def test_create_planner_resolves_explicit_name_then_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PACKING_PLANNER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")
    fake = FakeOpenAIClient(FakeResponses(None))

    explicit = create_planner("deterministic")
    from_environment = create_planner(openai_client=fake)

    assert isinstance(explicit, DeterministicPackingPlanner)
    assert isinstance(from_environment, OpenAIPackingPlanner)
    assert from_environment._model == "environment-model"


def test_create_planner_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="deterministic.*openai"):
        create_planner("rules-plus")
