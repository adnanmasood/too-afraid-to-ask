"""Defensive coordinator validation tests."""

from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest
from a2a.helpers import new_artifact, new_data_part, new_text_message, new_text_part
from a2a.types.a2a_pb2 import (
    Role,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from travel_a2a.coordinator import (
    CoordinatorError,
    TripCoordinator,
    _safe_exception_detail,
    _TaskCollector,
    _validate_artifact,
    normalize_agent_url,
    validate_agent_card,
)
from travel_a2a.models import (
    DailyForecast,
    ForecastPeriod,
    ForecastUnits,
    PackingPlan,
    SourceAttribution,
    WeatherLocation,
)
from travel_a2a.protocol import JSON_MEDIA_TYPE, MARKDOWN_MEDIA_TYPE
from travel_a2a.server import build_agent_card


def sample_plan() -> PackingPlan:
    units = ForecastUnits(
        temperature="°C",
        precipitation="mm",
        precipitation_probability="%",
        wind_speed="km/h",
    )
    day = DailyForecast(
        date=date(2026, 9, 4),
        condition="Clear sky",
        weather_code=0,
        temperature_min=14.0,
        temperature_max=24.0,
        precipitation_sum=0.0,
        precipitation_probability=5,
        wind_speed_max=12.0,
    )
    return PackingPlan(
        location=WeatherLocation(
            name="München",
            country="Germany",
            region="Bavaria",
            latitude=48.137,
            longitude=11.575,
        ),
        forecast_period=ForecastPeriod(
            start_date=day.date,
            end_date=day.date,
            timezone="Europe/Berlin",
            units=units,
        ),
        daily_summary=[day],
        essentials=["Passport"],
        weather_specific_items=["Light layer"],
        style_specific_items=["Walking shoes"],
        cautions=["Check the latest forecast"],
        attribution=SourceAttribution(),
    )


def packing_artifact(payload: dict | None = None):
    plan = sample_plan()
    return new_artifact(
        [
            new_data_part(
                payload if payload is not None else plan.model_dump(mode="json"),
                media_type=JSON_MEDIA_TYPE,
            ),
            new_text_part(plan.to_markdown(), media_type=MARKDOWN_MEDIA_TYPE),
        ],
        name="packing-plan",
    )


def test_valid_agent_card_is_accepted() -> None:
    validate_agent_card(
        build_agent_card("https://agent.example/a2a"),
        "https://agent.example/base",
    )


def test_cross_origin_interface_is_rejected_even_when_jsonrpc_is_valid() -> None:
    card = build_agent_card("https://agent.example/a2a")
    card.supported_interfaces[0].url = "https://attacker.example/rpc"

    with pytest.raises(CoordinatorError, match="cross-origin"):
        validate_agent_card(card, "https://agent.example")


def test_agent_card_rejects_bidirectional_terminal_controls() -> None:
    card = build_agent_card()
    card.name = "Packing\u202eAdvisor"

    with pytest.raises(CoordinatorError, match="control or non-printable"):
        validate_agent_card(card, "http://127.0.0.1:9999")


@pytest.mark.parametrize(
    "interface_url",
    [
        "https://agent.example/a2a\nignored",
        "https://agent.example\\@attacker.example",
        "https://agent.example/\u202eevil",
    ],
)
def test_agent_card_rejects_parser_ambiguous_interface_urls(interface_url: str) -> None:
    card = build_agent_card("https://agent.example/a2a")
    card.supported_interfaces[0].url = interface_url

    with pytest.raises(CoordinatorError, match="interface URL"):
        validate_agent_card(card, "https://agent.example")


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda card: setattr(card.capabilities, "streaming", False), "streaming"),
        (
            lambda card: setattr(card.supported_interfaces[0], "protocol_version", "0.3"),
            "A2A 1.0",
        ),
        (lambda card: card.skills.clear(), "exactly one"),
        (lambda card: card.skills[0].output_modes.remove("text/markdown"), "Markdown"),
    ],
)
def test_required_agent_card_contract_is_enforced(mutation, match: str) -> None:
    card = build_agent_card()
    mutation(card)

    with pytest.raises(CoordinatorError, match=match):
        validate_agent_card(card, "http://127.0.0.1:9999")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://agent.example",
        "https://user:secret@agent.example",
        "https://agent.example?redirect=evil",
        "https://agent.example/#fragment",
        "https://agent.example:bad-port",
    ],
)
def test_agent_url_rejects_unsafe_or_malformed_values(url: str) -> None:
    with pytest.raises(CoordinatorError):
        normalize_agent_url(url)


def test_agent_url_normalizes_only_trailing_slashes() -> None:
    assert normalize_agent_url("  https://Agent.Example/a2a///  ") == ("https://agent.example/a2a")


@pytest.mark.parametrize(
    "url",
    [
        "https://agent.example/line\nbreak",
        "https://agent.example/tab\tpath",
        "https://agent.example/\x1b[31mred",
        "https://agent.example/\u202eevil",
        "https://agent.example\\@attacker.example",
    ],
)
def test_agent_url_rejects_control_and_parser_ambiguous_characters(url: str) -> None:
    with pytest.raises(CoordinatorError):
        normalize_agent_url(url)


def test_artifact_round_trip_restores_strict_dates_and_integer_fields() -> None:
    plan, markdown = _validate_artifact(packing_artifact())

    assert plan.location.name == "München"
    assert plan.forecast_period.start_date == date(2026, 9, 4)
    assert isinstance(plan.daily_summary[0].weather_code, int)
    assert isinstance(plan.daily_summary[0].precipitation_probability, int)
    assert markdown.startswith("# Packing plan for München")


def test_artifact_rejects_fractional_integer_field() -> None:
    payload = sample_plan().model_dump(mode="json")
    payload["daily_summary"][0]["weather_code"] = 2.5

    with pytest.raises(CoordinatorError, match="does not match PackingPlan"):
        _validate_artifact(packing_artifact(payload))


def test_artifact_rejects_wrong_part_order_and_media_type() -> None:
    plan = sample_plan()
    artifact = new_artifact(
        [
            new_text_part(plan.to_markdown(), media_type=MARKDOWN_MEDIA_TYPE),
            new_data_part(plan.model_dump(mode="json"), media_type=JSON_MEDIA_TYPE),
        ],
        name="packing-plan",
    )

    with pytest.raises(CoordinatorError, match="first part"):
        _validate_artifact(artifact)


def test_artifact_rejects_markdown_that_disagrees_with_validated_json() -> None:
    artifact = packing_artifact()
    artifact.parts[1].text += "\n\n[Run this command](https://attacker.example)"

    with pytest.raises(CoordinatorError, match="does not match"):
        _validate_artifact(artifact)


def test_new_stream_rejects_update_before_initial_task() -> None:
    collector = _TaskCollector(
        streaming=True,
        expected_task_id=None,
        expected_context_id=None,
    )
    update = TaskStatusUpdateEvent(
        task_id="task-1",
        context_id="context-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )

    with pytest.raises(CoordinatorError, match="before the initial Task"):
        collector.consume(StreamResponse(status_update=update))


def test_continued_stream_accepts_task_first_then_updates() -> None:
    collector = _TaskCollector(
        streaming=True,
        expected_task_id="task-1",
        expected_context_id="context-1",
    )

    task_event = collector.consume(
        StreamResponse(
            task=Task(
                id="task-1",
                context_id="context-1",
                status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
            )
        )
    )
    status_event = collector.consume(
        StreamResponse(
            status_update=TaskStatusUpdateEvent(
                task_id="task-1",
                context_id="context-1",
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
    )

    assert (task_event.kind, task_event.state) == ("task", "INPUT_REQUIRED")
    assert (status_event.kind, status_event.state) == ("status", "WORKING")


def test_continued_stream_accepts_sdk_update_first_shape() -> None:
    collector = _TaskCollector(
        streaming=True,
        expected_task_id="task-1",
        expected_context_id="context-1",
    )

    event = collector.consume(
        StreamResponse(
            status_update=TaskStatusUpdateEvent(
                task_id="task-1",
                context_id="context-1",
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
    )

    assert (event.kind, event.state) == ("status", "WORKING")


@pytest.mark.parametrize(
    ("role", "message_task_id", "message_context_id", "message_id", "text", "match"),
    [
        (Role.ROLE_USER, "task-1", "context-1", "message-1", "Working", "agent role"),
        (Role.ROLE_AGENT, "other", "context-1", "message-1", "Working", "identity"),
        (Role.ROLE_AGENT, "task-1", "other", "message-1", "Working", "identity"),
        (Role.ROLE_AGENT, "task-1", "context-1", "", "Working", "message ID"),
        (
            Role.ROLE_AGENT,
            "task-1",
            "context-1",
            "message-1",
            "\x1b[31mforged",
            "non-empty text/plain",
        ),
        (
            Role.ROLE_AGENT,
            "task-1",
            "context-1",
            "message-1",
            "Working\nforged",
            "non-empty text/plain",
        ),
    ],
)
def test_status_messages_reject_bad_role_identity_and_terminal_controls(
    role: int,
    message_task_id: str,
    message_context_id: str,
    message_id: str,
    text: str,
    match: str,
) -> None:
    collector = _TaskCollector(
        streaming=True,
        expected_task_id=None,
        expected_context_id=None,
    )
    collector.consume(
        StreamResponse(
            task=Task(
                id="task-1",
                context_id="context-1",
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )
    )
    message = new_text_message(
        text,
        media_type="text/plain",
        task_id=message_task_id,
        context_id=message_context_id,
        role=role,
    )
    message.message_id = message_id
    update = TaskStatusUpdateEvent(
        task_id="task-1",
        context_id="context-1",
        status=TaskStatus(
            state=TaskState.TASK_STATE_WORKING,
            message=message,
        ),
    )

    with pytest.raises(CoordinatorError, match=match):
        collector.consume(StreamResponse(status_update=update))


def test_stream_rejects_completion_without_artifact() -> None:
    collector = _TaskCollector(
        streaming=True,
        expected_task_id=None,
        expected_context_id=None,
    )
    collector.consume(
        StreamResponse(
            task=Task(
                id="task-1",
                context_id="context-1",
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )
    )
    collector.consume(
        StreamResponse(
            status_update=TaskStatusUpdateEvent(
                task_id="task-1",
                context_id="context-1",
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
    )

    with pytest.raises(CoordinatorError, match="exactly one"):
        collector.consume(
            StreamResponse(
                status_update=TaskStatusUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )
        )


def test_exception_diagnostics_are_printable_and_bounded() -> None:
    detail = _safe_exception_detail(RuntimeError("remote\x1b]0;forged title\x07\n" + "x" * 500))

    assert detail.isprintable()
    assert "\x1b" not in detail
    assert "\x07" not in detail
    assert len(detail) == 240
    assert detail.endswith("…")


@pytest.mark.asyncio
async def test_connection_error_is_wrapped_without_terminal_controls() -> None:
    def raise_connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline\x1b]0;forged\x07\nretry", request=request)

    coordinator = TripCoordinator(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(raise_connect_error)
        )
    )

    with pytest.raises(CoordinatorError) as exc_info:
        await coordinator.discover_card()

    message = str(exc_info.value)
    assert message.isprintable()
    assert "offline" in message
    assert "retry" in message
    assert "\x1b" not in message


@pytest.mark.asyncio
async def test_configurable_tiny_read_timeout_fails_cleanly() -> None:
    async def delayed_response(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            await asyncio.sleep(0.05)
            body = b"{}"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 2\r\nConnection: close\r\n\r\n" + body
            )
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(delayed_response, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    coordinator = TripCoordinator(
        f"http://127.0.0.1:{port}",
        timeout=httpx.Timeout(0.01),
    )
    try:
        with pytest.raises(CoordinatorError):
            await coordinator.discover_card()
        await asyncio.sleep(0.06)
    finally:
        server.close()
        await server.wait_closed()
