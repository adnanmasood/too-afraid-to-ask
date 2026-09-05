"""Offline command-line tests for the Trip Coordinator and server entrypoint."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from a2a.helpers import new_text_message
from a2a.types.a2a_pb2 import Role, Task, TaskState, TaskStatus

from travel_a2a.cli import build_parser, main, resolve_agent_url
from travel_a2a.coordinator import CoordinatorEvent, PlanOutcome
from travel_a2a.models import (
    DailyForecast,
    ForecastPeriod,
    ForecastUnits,
    PackingPlan,
    PackingRequest,
    SourceAttribution,
    WeatherLocation,
)
from travel_a2a.server import build_agent_card, resolve_planner_name
from travel_a2a.server import main as server_main


def sample_plan() -> PackingPlan:
    units = ForecastUnits(
        temperature="°C",
        precipitation="mm",
        precipitation_probability="%",
        wind_speed="km/h",
    )
    daily = DailyForecast(
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
            name="Berlin",
            country="Germany",
            region="Berlin",
            latitude=52.52,
            longitude=13.405,
        ),
        forecast_period=ForecastPeriod(
            start_date=daily.date,
            end_date=daily.date,
            timezone="Europe/Berlin",
            units=units,
        ),
        daily_summary=[daily],
        essentials=["Passport"],
        weather_specific_items=["Light layer"],
        style_specific_items=["Walking shoes"],
        cautions=["Check the latest forecast"],
        attribution=SourceAttribution(),
    )


def task_outcome(
    state: int,
    *,
    task_id: str = "server-task",
    context_id: str = "server-context",
    message: str | None = None,
) -> PlanOutcome:
    status = TaskStatus(state=state)
    if message is not None:
        status.message.CopyFrom(
            new_text_message(
                message,
                media_type="text/plain",
                task_id=task_id,
                context_id=context_id,
                role=Role.ROLE_AGENT,
            )
        )
    task = Task(id=task_id, context_id=context_id, status=status)
    if state == TaskState.TASK_STATE_COMPLETED:
        plan = sample_plan()
        return PlanOutcome(task, (), plan, plan.to_markdown())
    return PlanOutcome(task, ())


@dataclass
class FakeCoordinator:
    outcomes: list[PlanOutcome]
    events: list[list[CoordinatorEvent]] = field(default_factory=list)
    calls: list[tuple[PackingRequest, dict[str, Any]]] = field(default_factory=list)

    async def discover_card(self):
        return build_agent_card("https://agent.example/a2a")

    async def request_plan(self, request: PackingRequest, **kwargs: Any) -> PlanOutcome:
        self.calls.append((request, kwargs))
        call_events = self.events[len(self.calls) - 1] if self.events else []
        callback = kwargs.get("on_event")
        for event in call_events:
            if callback is not None:
                callback(event)
        return self.outcomes.pop(0)


def test_parser_captures_plan_options() -> None:
    args = build_parser().parse_args(
        [
            "plan",
            "Kyoto, Japan",
            "--days",
            "4",
            "--style",
            "outdoors",
            "--units",
            "imperial",
            "--stream",
            "--json",
            "--agent-url",
            "https://agent.example/a2a",
        ]
    )

    assert args.command == "plan"
    assert args.destination == "Kyoto, Japan"
    assert args.days == 4
    assert args.style == "outdoors"
    assert args.units == "imperial"
    assert args.stream is True
    assert args.json is True
    assert args.agent_url == "https://agent.example/a2a"


def test_agent_url_precedence_and_default() -> None:
    environ = {"A2A_AGENT_URL": "https://env.example/a2a"}

    assert resolve_agent_url("https://cli.example/a2a", environ) == ("https://cli.example/a2a")
    assert resolve_agent_url(None, environ) == "https://env.example/a2a"
    assert resolve_agent_url(None, {}) == "http://127.0.0.1:9999"


def test_card_command_prints_discovered_card_json() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    urls: list[str] = []
    fake = FakeCoordinator([])

    exit_code = main(
        ["card", "--agent-url", "https://agent.example/a2a"],
        coordinator_factory=lambda url: urls.append(url) or fake,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert urls == ["https://agent.example/a2a"]
    payload = json.loads(stdout.getvalue())
    assert payload["name"] == "Packing Advisor"
    assert payload["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert stderr.getvalue() == ""


def test_missing_days_prompts_and_resumes_same_task() -> None:
    fake = FakeCoordinator(
        [
            task_outcome(
                TaskState.TASK_STATE_INPUT_REQUIRED,
                message="How many days is the trip?",
            ),
            task_outcome(TaskState.TASK_STATE_COMPLETED),
        ]
    )
    prompts: list[str] = []
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        ["plan", "Berlin"],
        coordinator_factory=lambda _url: fake,
        input_fn=lambda prompt: prompts.append(prompt) or "3",
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert prompts == [""]
    assert fake.calls[0][0].days is None
    assert fake.calls[0][1]["task_id"] is None
    assert fake.calls[1][0].days == 3
    assert fake.calls[1][1]["task_id"] == "server-task"
    assert fake.calls[1][1]["context_id"] == "server-context"
    assert "# Packing plan for Berlin, Germany" in stdout.getvalue()
    assert "INPUT_REQUIRED — How many days is the trip?" in stderr.getvalue()
    assert stderr.getvalue().endswith("Days (1-7): ")


def test_unknown_location_prompts_for_more_specific_destination() -> None:
    fake = FakeCoordinator(
        [
            task_outcome(
                TaskState.TASK_STATE_INPUT_REQUIRED,
                message="Please provide a more specific destination.",
            ),
            task_outcome(TaskState.TASK_STATE_COMPLETED),
        ]
    )
    prompts: list[str] = []

    exit_code = main(
        ["plan", "Springfield", "--days", "1"],
        coordinator_factory=lambda _url: fake,
        input_fn=lambda prompt: prompts.append(prompt) or "Springfield, Illinois, USA",
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert prompts == [""]
    assert fake.calls[1][0].destination == "Springfield, Illinois, USA"
    assert fake.calls[1][1]["task_id"] == "server-task"


def test_streaming_json_keeps_stdout_machine_readable() -> None:
    fake = FakeCoordinator(
        [task_outcome(TaskState.TASK_STATE_COMPLETED)],
        events=[
            [
                CoordinatorEvent("task", "task", "context", state="SUBMITTED"),
                CoordinatorEvent(
                    "status",
                    "task",
                    "context",
                    state="WORKING",
                    message="Checking forecast.",
                ),
                CoordinatorEvent(
                    "artifact",
                    "task",
                    "context",
                    artifact_name="packing-plan",
                ),
                CoordinatorEvent("status", "task", "context", state="COMPLETED"),
            ]
        ],
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        ["plan", "Berlin", "--days", "1", "--stream", "--json"],
        coordinator_factory=lambda _url: fake,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["location"]["name"] == "Berlin"
    assert stderr.getvalue().splitlines() == [
        "SUBMITTED",
        "WORKING — Checking forecast.",
        "ARTIFACT packing-plan",
        "COMPLETED",
    ]


def test_json_continuation_prompt_stays_off_stdout() -> None:
    fake = FakeCoordinator(
        [
            task_outcome(
                TaskState.TASK_STATE_INPUT_REQUIRED,
                message="How many days is the trip?",
            ),
            task_outcome(TaskState.TASK_STATE_COMPLETED),
        ]
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        ["plan", "Berlin", "--json"],
        coordinator_factory=lambda _url: fake,
        input_fn=lambda _prompt: "1",
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["location"]["name"] == "Berlin"
    assert "Days (1-7): " in stderr.getvalue()


def test_failed_task_returns_nonzero_without_private_traceback() -> None:
    fake = FakeCoordinator(
        [
            task_outcome(
                TaskState.TASK_STATE_FAILED,
                message="The weather service is temporarily unavailable.",
            )
        ]
    )
    stderr = io.StringIO()

    exit_code = main(
        ["plan", "Berlin", "--days", "1"],
        coordinator_factory=lambda _url: fake,
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert exit_code == 1
    assert stderr.getvalue() == (
        "error: Task failed: The weather service is temporarily unavailable.\n"
    )


def test_server_planner_and_public_url_precedence() -> None:
    assert resolve_planner_name("openai", {"PACKING_PLANNER": "deterministic"}) == "openai"
    assert resolve_planner_name(None, {"PACKING_PLANNER": "openai"}) == "openai"
    assert resolve_planner_name(None, {}) == "deterministic"

    calls: list[tuple[Any, str, int]] = []

    def uvicorn_run(app: Any, *, host: str, port: int) -> None:
        calls.append((app, host, port))

    exit_code = server_main(
        ["--port", "8765"],
        environ={"A2A_PUBLIC_URL": "https://env.example/a2a"},
        uvicorn_run=uvicorn_run,
    )

    assert exit_code == 0
    app, host, port = calls[0]
    assert host == "127.0.0.1"
    assert port == 8765
    assert app.state.agent_card.supported_interfaces[0].url == "https://env.example/a2a"


def test_server_cli_public_url_wins_and_invalid_url_is_rejected() -> None:
    apps: list[Any] = []
    server_main(
        ["--public-url", "https://cli.example/a2a"],
        environ={"A2A_PUBLIC_URL": "https://env.example/a2a"},
        uvicorn_run=lambda app, **_kwargs: apps.append(app),
    )
    assert apps[0].state.agent_card.supported_interfaces[0].url == ("https://cli.example/a2a")

    with pytest.raises(SystemExit) as exc_info:
        server_main(
            [],
            environ={"A2A_PUBLIC_URL": "file:///tmp/agent"},
            uvicorn_run=lambda *_args, **_kwargs: None,
        )
    assert exc_info.value.code == 2
