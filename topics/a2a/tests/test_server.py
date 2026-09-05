"""A2A server contract and lifecycle tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.helpers import new_data_message, new_text_message
from a2a.types.a2a_pb2 import (
    Role,
    SendMessageRequest,
    TaskState,
)

from travel_a2a.coordinator import TripCoordinator
from travel_a2a.models import (
    DailyForecast,
    ForecastUnits,
    PackingRequest,
    Units,
    WeatherForecast,
    WeatherLocation,
)
from travel_a2a.planner import DeterministicPackingPlanner, PlannerError
from travel_a2a.protocol import JSON_MEDIA_TYPE
from travel_a2a.server import build_agent_card, create_app
from travel_a2a.weather_service import LocationNotFoundError, WeatherProviderError


def sample_forecast(days: int = 2) -> WeatherForecast:
    return WeatherForecast(
        location=WeatherLocation(
            name="Berlin",
            country="Germany",
            region="Berlin",
            latitude=52.52,
            longitude=13.405,
        ),
        timezone="Europe/Berlin",
        units=ForecastUnits(
            temperature="°C",
            precipitation="mm",
            precipitation_probability="%",
            wind_speed="km/h",
        ),
        daily=[
            DailyForecast(
                date=date(2026, 9, 4) + timedelta(days=index),
                condition="Partly cloudy",
                weather_code=2,
                temperature_min=12.0,
                temperature_max=22.0,
                precipitation_sum=0.0,
                precipitation_probability=10,
                wind_speed_max=15.0,
            )
            for index in range(days)
        ],
    )


@dataclass
class FakeForecastService:
    error: Exception | None = None
    calls: list[tuple[str, int, Units]] = field(default_factory=list)

    async def get_forecast(self, destination: str, days: int, units: Units) -> WeatherForecast:
        self.calls.append((destination, days, units))
        if self.error is not None:
            raise self.error
        return sample_forecast(days)


class FailingPlanner:
    async def plan(self, request: PackingRequest, forecast: WeatherForecast) -> Any:
        del request, forecast
        raise PlannerError("private planner diagnostic")


class ClosablePlanner(DeterministicPackingPlanner):
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


def coordinator_for(app: Any) -> TripCoordinator:
    def client_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:9999",
        )

    return TripCoordinator(http_client_factory=client_factory)


@pytest.mark.asyncio
async def test_agent_card_and_routes_advertise_jsonrpc_1_0() -> None:
    app = create_app(FakeForecastService(), DeterministicPackingPlanner())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:9999",
    ) as client:
        response = await client.get("/.well-known/agent-card.json")
        missing = await client.get("/not-a-transport")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Packing Advisor"
    assert payload["capabilities"]["streaming"] is True
    assert payload["supportedInterfaces"] == [
        {
            "url": "http://127.0.0.1:9999",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }
    ]
    assert payload["skills"][0]["id"] == "plan_weather_aware_packing"
    assert payload["skills"][0]["inputModes"] == ["application/json"]
    assert payload["skills"][0]["outputModes"] == [
        "application/json",
        "text/markdown",
    ]
    assert missing.status_code == 404
    await app.state.request_handler.aclose()


@pytest.mark.asyncio
async def test_streaming_task_order_and_two_part_artifact() -> None:
    service = FakeForecastService()
    app = create_app(service, DeterministicPackingPlanner())
    events = []

    outcome = await coordinator_for(app).request_plan(
        PackingRequest(destination="  Berlin  ", days=2, style="business"),
        stream=True,
        on_event=events.append,
    )

    assert outcome.state == "COMPLETED"
    assert [(event.kind, event.state) for event in events] == [
        ("task", "SUBMITTED"),
        ("status", "WORKING"),
        ("artifact", None),
        ("status", "COMPLETED"),
    ]
    artifact = outcome.task.artifacts[0]
    assert artifact.name == "packing-plan"
    assert [part.media_type for part in artifact.parts] == [
        "application/json",
        "text/markdown",
    ]
    assert artifact.parts[0].HasField("data")
    assert artifact.parts[1].HasField("text")
    assert outcome.plan is not None
    assert outcome.plan.daily_summary[0].date == date(2026, 9, 4)
    assert outcome.plan.daily_summary[0].weather_code == 2
    assert service.calls == [("Berlin", 2, "metric")]
    await app.state.request_handler.aclose()


@pytest.mark.asyncio
async def test_non_streaming_success_returns_one_completed_task() -> None:
    service = FakeForecastService()
    app = create_app(service, DeterministicPackingPlanner())

    outcome = await coordinator_for(app).request_plan(
        PackingRequest(destination="Berlin", days=2),
        stream=False,
    )

    assert outcome.state == "COMPLETED"
    assert [(event.kind, event.state) for event in outcome.events] == [("task", "COMPLETED")]
    assert outcome.plan is not None
    assert service.calls == [("Berlin", 2, "metric")]
    await app.state.request_handler.aclose()


@pytest.mark.asyncio
async def test_missing_days_continues_with_same_server_issued_ids() -> None:
    service = FakeForecastService()
    app = create_app(service, DeterministicPackingPlanner())
    coordinator = coordinator_for(app)

    interrupted = await coordinator.request_plan(
        PackingRequest(destination="Berlin"),
        stream=True,
    )
    resumed = await coordinator.request_plan(
        PackingRequest(destination="Berlin", days=2),
        stream=True,
        task_id=interrupted.task.id,
        context_id=interrupted.task.context_id,
    )

    assert interrupted.state == "INPUT_REQUIRED"
    assert [event.state for event in interrupted.events] == ["SUBMITTED", "INPUT_REQUIRED"]
    assert resumed.state == "COMPLETED"
    assert resumed.task.id == interrupted.task.id
    assert resumed.task.context_id == interrupted.task.context_id
    assert [event.state for event in resumed.events if event.state] == [
        "WORKING",
        "COMPLETED",
    ]
    assert service.calls == [("Berlin", 2, "metric")]
    await app.state.request_handler.aclose()


@pytest.mark.asyncio
async def test_non_streaming_continuation_waits_for_completed_task() -> None:
    service = FakeForecastService()
    app = create_app(service, DeterministicPackingPlanner())
    coordinator = coordinator_for(app)

    interrupted = await coordinator.request_plan(
        PackingRequest(destination="Berlin"),
        stream=False,
    )
    resumed = await coordinator.request_plan(
        PackingRequest(destination="Berlin", days=2),
        stream=False,
        task_id=interrupted.task.id,
        context_id=interrupted.task.context_id,
    )

    assert interrupted.state == "INPUT_REQUIRED"
    assert resumed.state == "COMPLETED"
    assert resumed.task.id == interrupted.task.id
    assert resumed.task.context_id == interrupted.task.context_id
    assert service.calls == [("Berlin", 2, "metric")]
    await app.state.request_handler.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "state", "visible", "secret"),
    [
        (
            LocationNotFoundError("private geocoder detail"),
            "INPUT_REQUIRED",
            "more specific destination",
            "private geocoder detail",
        ),
        (
            WeatherProviderError("private provider detail"),
            "FAILED",
            "temporarily unavailable",
            "private provider detail",
        ),
        (
            RuntimeError("private adapter traceback detail"),
            "FAILED",
            "temporarily unavailable",
            "private adapter traceback detail",
        ),
    ],
)
async def test_location_and_provider_failures_are_safe(
    error: Exception,
    state: str,
    visible: str,
    secret: str,
) -> None:
    app = create_app(FakeForecastService(error), DeterministicPackingPlanner())

    outcome = await coordinator_for(app).request_plan(
        PackingRequest(destination="Atlantis", days=2)
    )

    assert outcome.state == state
    assert outcome.status_message is not None
    assert visible in outcome.status_message
    assert secret not in outcome.status_message
    await app.state.request_handler.aclose()


@pytest.mark.asyncio
async def test_planner_failure_is_failed_and_sanitized() -> None:
    app = create_app(FakeForecastService(), FailingPlanner())

    outcome = await coordinator_for(app).request_plan(PackingRequest(destination="Berlin", days=2))

    assert outcome.state == "FAILED"
    assert outcome.status_message is not None
    assert "could not create a valid plan" in outcome.status_message
    assert "private planner diagnostic" not in outcome.status_message
    await app.state.request_handler.aclose()


async def _send_unvalidated_message(app: Any, message: Any):
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:9999",
    )
    resolver = A2ACardResolver(http_client, "http://127.0.0.1:9999")
    card = await resolver.get_agent_card()
    client = ClientFactory(ClientConfig(streaming=False, httpx_client=http_client)).create(card)
    try:
        responses = [
            response async for response in client.send_message(SendMessageRequest(message=message))
        ]
        return responses[0].task
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        new_text_message("Berlin", media_type="text/plain", role=Role.ROLE_USER),
        new_data_message(
            {"destination": "Berlin", "days": 2.5},
            media_type=JSON_MEDIA_TYPE,
            role=Role.ROLE_USER,
        ),
        new_data_message(
            {"destination": "x", "days": 2},
            media_type=JSON_MEDIA_TYPE,
            role=Role.ROLE_USER,
        ),
        new_data_message(
            {"destination": "Berlin", "days": 2},
            media_type=JSON_MEDIA_TYPE,
            role=Role.ROLE_AGENT,
        ),
    ],
)
async def test_invalid_or_unsupported_messages_are_rejected(message: Any) -> None:
    service = FakeForecastService()
    app = create_app(service, DeterministicPackingPlanner())

    task = await _send_unvalidated_message(app, message)

    assert task.status.state == TaskState.TASK_STATE_REJECTED
    assert service.calls == []
    await app.state.request_handler.aclose()


@pytest.mark.asyncio
async def test_continuation_with_agent_role_is_rejected_with_same_ids() -> None:
    service = FakeForecastService()
    app = create_app(service, DeterministicPackingPlanner())
    interrupted = await _send_unvalidated_message(
        app,
        new_data_message(
            {"destination": "Berlin"},
            media_type=JSON_MEDIA_TYPE,
            role=Role.ROLE_USER,
        ),
    )

    rejected = await _send_unvalidated_message(
        app,
        new_data_message(
            {"destination": "Berlin", "days": 2},
            media_type=JSON_MEDIA_TYPE,
            task_id=interrupted.id,
            context_id=interrupted.context_id,
            role=Role.ROLE_AGENT,
        ),
    )

    assert interrupted.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert rejected.status.state == TaskState.TASK_STATE_REJECTED
    assert rejected.id == interrupted.id
    assert rejected.context_id == interrupted.context_id
    assert service.calls == []
    await app.state.request_handler.aclose()


@pytest.mark.asyncio
async def test_app_lifespan_closes_closeable_planner_once() -> None:
    planner = ClosablePlanner()
    app = create_app(FakeForecastService(), planner)

    async with app.router.lifespan_context(app):
        assert planner.close_calls == 0

    assert planner.close_calls == 1


def test_build_agent_card_rejects_malformed_public_url() -> None:
    with pytest.raises(Exception, match="http:// or https://"):
        build_agent_card("file:///tmp/a2a.sock")
