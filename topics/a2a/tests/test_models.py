"""Tests for strict application contracts and plan rendering."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from travel_a2a.models import (
    DailyForecast,
    ForecastPeriod,
    ForecastUnits,
    PackingAdvice,
    PackingPlan,
    PackingRequest,
    SourceAttribution,
    WeatherForecast,
    WeatherLocation,
)


def _location() -> WeatherLocation:
    return WeatherLocation(
        name="München",
        country="Germany",
        region="Bavaria",
        latitude=48.137,
        longitude=11.575,
    )


def _units() -> ForecastUnits:
    return ForecastUnits(
        temperature="°C",
        precipitation="mm",
        precipitation_probability="%",
        wind_speed="km/h",
    )


def _day(day: int, *, low: float = 10.0, high: float = 20.0) -> DailyForecast:
    return DailyForecast(
        date=date(2026, 9, day),
        condition="Partly cloudy",
        weather_code=2,
        temperature_min=low,
        temperature_max=high,
        precipitation_sum=0.2,
        precipitation_probability=15,
        wind_speed_max=12.0,
    )


def _plan() -> PackingPlan:
    days = [_day(4), _day(5)]
    return PackingPlan(
        location=_location(),
        forecast_period=ForecastPeriod(
            start_date=days[0].date,
            end_date=days[-1].date,
            timezone="Europe/Berlin",
            units=_units(),
        ),
        daily_summary=days,
        essentials=["Phone and charger"],
        weather_specific_items=["Light jacket"],
        style_specific_items=["Comfortable walking shoes"],
        cautions=[],
        attribution=SourceAttribution(),
    )


def test_packing_request_normalizes_unicode_destination_and_applies_defaults() -> None:
    request = PackingRequest(destination="  München  ")

    assert request.model_dump() == {
        "destination": "München",
        "days": None,
        "units": "metric",
        "style": "general",
    }


@pytest.mark.parametrize("destination", ["", " ", "A", "x" * 101])
def test_packing_request_rejects_invalid_destination(destination: str) -> None:
    with pytest.raises(ValidationError):
        PackingRequest(destination=destination)


@pytest.mark.parametrize("days", [0, 8, True, 3.0, "3"])
def test_packing_request_rejects_invalid_or_coerced_days(days: object) -> None:
    with pytest.raises(ValidationError):
        PackingRequest(destination="Berlin", days=days)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [("units", "kelvin"), ("style", "formal")],
)
def test_packing_request_rejects_unsupported_literals(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        PackingRequest.model_validate({"destination": "Berlin", field: value})


def test_strict_models_reject_unknown_fields_and_non_finite_numbers() -> None:
    with pytest.raises(ValidationError):
        PackingRequest.model_validate({"destination": "Berlin", "extra": True})

    with pytest.raises(ValidationError):
        WeatherLocation(
            name="Berlin",
            country="Germany",
            latitude=float("nan"),
            longitude=13.405,
        )


@pytest.mark.parametrize(
    "destination",
    ["Berlin\n# forged heading", "Berlin\tGermany", "Ber\u200blin"],
)
def test_destination_rejects_line_breaks_and_control_characters(destination: str) -> None:
    with pytest.raises(ValidationError, match="control or line-break"):
        PackingRequest(destination=destination)


@pytest.mark.parametrize(
    "item",
    ["Passport\n# forged heading", "Passport\tand wallet", "Pass\u200bport"],
)
def test_model_generated_list_items_reject_control_characters(item: str) -> None:
    with pytest.raises(ValidationError, match="control or line-break"):
        PackingAdvice(
            essentials=[item],
            weather_specific_items=[],
            style_specific_items=[],
            cautions=[],
        )


def test_location_display_name_avoids_repeated_components() -> None:
    assert _location().display_name == "München, Bavaria, Germany"
    assert (
        WeatherLocation(
            name="Berlin",
            country="Germany",
            region="Berlin",
            latitude=52.52,
            longitude=13.405,
        ).display_name
        == "Berlin, Germany"
    )


def test_daily_forecast_rejects_reversed_temperature_range() -> None:
    with pytest.raises(ValidationError, match="temperature_min"):
        _day(4, low=21.0, high=20.0)


def test_packing_plan_rejects_period_that_does_not_match_daily_summary() -> None:
    with pytest.raises(ValidationError, match="must start"):
        PackingPlan(
            location=_location(),
            forecast_period=ForecastPeriod(
                start_date=date(2026, 9, 3),
                end_date=date(2026, 9, 5),
                timezone="Europe/Berlin",
                units=_units(),
            ),
            daily_summary=[_day(4), _day(5)],
            essentials=["Phone and charger"],
            weather_specific_items=[],
            style_specific_items=[],
            cautions=[],
            attribution=SourceAttribution(),
        )


def test_weather_forecast_rejects_non_consecutive_daily_dates() -> None:
    with pytest.raises(ValidationError, match="consecutive"):
        WeatherForecast(
            location=_location(),
            timezone="Europe/Berlin",
            units=_units(),
            daily=[_day(4), _day(6)],
        )


def test_packing_plan_rejects_non_consecutive_daily_summary() -> None:
    with pytest.raises(ValidationError, match="consecutive"):
        PackingPlan(
            location=_location(),
            forecast_period=ForecastPeriod(
                start_date=date(2026, 9, 4),
                end_date=date(2026, 9, 6),
                timezone="Europe/Berlin",
                units=_units(),
            ),
            daily_summary=[_day(4), _day(6)],
            essentials=["Phone and charger"],
            weather_specific_items=[],
            style_specific_items=[],
            cautions=[],
            attribution=SourceAttribution(),
        )


def test_packing_plan_json_and_markdown_preserve_contract_and_attribution() -> None:
    plan = _plan()

    payload = plan.model_dump(mode="json")
    markdown = plan.to_markdown()

    assert payload["location"]["name"] == "München"
    assert payload["forecast_period"]["start_date"] == "2026-09-04"
    assert payload["daily_summary"][1]["date"] == "2026-09-05"
    assert payload["attribution"] == {
        "name": "Open-Meteo",
        "url": "https://open-meteo.com/",
    }
    assert "# Packing plan for München, Bavaria, Germany" in markdown
    assert "2026-09-04 | Partly cloudy | 10 / 20 °C" in markdown
    assert "- None" in markdown
    assert "[Open-Meteo](https://open-meteo.com/)" in markdown


def test_markdown_escapes_all_dynamic_structure_characters() -> None:
    daily = DailyForecast(
        date=date(2026, 9, 4),
        condition="Rain | **storm**",
        weather_code=95,
        temperature_min=10.0,
        temperature_max=20.0,
        precipitation_sum=2.0,
        precipitation_probability=80,
        wind_speed_max=20.0,
    )
    plan = PackingPlan(
        location=WeatherLocation(
            name="# Berlin [link]",
            country="Germany",
            latitude=52.52,
            longitude=13.405,
        ),
        forecast_period=ForecastPeriod(
            start_date=daily.date,
            end_date=daily.date,
            timezone="> Europe/Berlin",
            units=_units(),
        ),
        daily_summary=[daily],
        essentials=["- forged item **bold** [link](https://invalid.example)"],
        weather_specific_items=[],
        style_specific_items=[],
        cautions=[],
        attribution=SourceAttribution(),
    )

    markdown = plan.to_markdown()

    assert r"\# Berlin \[link\], Germany" in markdown
    assert r"\> Europe/Berlin" in markdown
    assert r"Rain \| \*\*storm\*\*" in markdown
    assert r"\- forged item \*\*bold\*\* \[link\]\(https://invalid\.example\)" in markdown
