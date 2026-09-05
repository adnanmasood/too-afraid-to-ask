"""Command-line Trip Coordinator for the Packing Advisor A2A agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import TextIO

from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from travel_a2a.coordinator import (
    CoordinatorError,
    CoordinatorEvent,
    PlanOutcome,
    TripCoordinator,
    normalize_agent_url,
)
from travel_a2a.models import PackingRequest
from travel_a2a.protocol import DEFAULT_AGENT_URL

CoordinatorFactory = Callable[[str], TripCoordinator]
InputFunction = Callable[[str], str]


def build_parser() -> argparse.ArgumentParser:
    """Build the ``travel-a2a`` command parser."""

    parser = argparse.ArgumentParser(
        prog="travel-a2a",
        description="Discover and delegate to the Packing Advisor A2A agent.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    card_parser = commands.add_parser("card", help="Discover and print the Agent Card.")
    _add_agent_url(card_parser)

    plan_parser = commands.add_parser("plan", help="Request a weather-aware packing plan.")
    plan_parser.add_argument(
        "destination",
        help='Destination, for example "Berlin, Germany".',
    )
    plan_parser.add_argument(
        "--days",
        type=_days_value,
        metavar="1..7",
        help="Trip length. When omitted, the agent will ask for it.",
    )
    plan_parser.add_argument(
        "--style",
        choices=("general", "business", "outdoors"),
        default="general",
        help="Packing style (default: %(default)s).",
    )
    plan_parser.add_argument(
        "--units",
        choices=("metric", "imperial"),
        default="metric",
        help="Forecast units (default: %(default)s).",
    )
    plan_parser.add_argument(
        "--stream",
        action="store_true",
        help="Show task, status, and artifact events as they arrive.",
    )
    plan_parser.add_argument(
        "--json",
        action="store_true",
        help="Print only the structured PackingPlan artifact on stdout.",
    )
    _add_agent_url(plan_parser)
    return parser


def _add_agent_url(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent-url",
        help=f"Agent base URL (default: A2A_AGENT_URL or {DEFAULT_AGENT_URL}).",
    )


def _days_value(value: str) -> int:
    try:
        days = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("days must be a whole number from 1 to 7") from exc
    if not 1 <= days <= 7:
        raise argparse.ArgumentTypeError("days must be a whole number from 1 to 7")
    return days


def resolve_agent_url(cli_value: str | None, environ: Mapping[str, str]) -> str:
    """Resolve and validate URL using command line, environment, then default."""

    value = (cli_value or "").strip() or environ.get("A2A_AGENT_URL", "").strip()
    return normalize_agent_url(value or DEFAULT_AGENT_URL)


def format_event(event: CoordinatorEvent) -> str:
    """Render a compact streaming lifecycle line."""

    if event.kind == "artifact":
        return f"ARTIFACT {event.artifact_name}"
    label = event.state or "UNKNOWN"
    if event.message:
        return f"{label} — {event.message}"
    return label


def format_card(card: object) -> str:
    """Render the validated protobuf Agent Card as canonical JSON."""

    return json.dumps(
        MessageToDict(card),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )


async def _run_card(coordinator: TripCoordinator, stdout: TextIO) -> None:
    card = await coordinator.discover_card()
    print(format_card(card), file=stdout)


async def _run_plan(
    coordinator: TripCoordinator,
    args: argparse.Namespace,
    *,
    input_fn: InputFunction,
    stdout: TextIO,
    stderr: TextIO,
) -> None:
    try:
        request = PackingRequest(
            destination=args.destination,
            days=args.days,
            units=args.units,
            style=args.style,
        )
    except ValidationError as exc:
        raise CoordinatorError(_validation_message(exc)) from exc

    trace_output = stderr if args.json else stdout

    def on_event(event: CoordinatorEvent) -> None:
        print(format_event(event), file=trace_output, flush=True)

    task_id: str | None = None
    context_id: str | None = None
    while True:
        outcome = await coordinator.request_plan(
            request,
            stream=args.stream,
            task_id=task_id,
            context_id=context_id,
            on_event=on_event if args.stream else None,
        )
        if outcome.state != "INPUT_REQUIRED":
            break

        if not args.stream and outcome.status_message:
            print(f"INPUT_REQUIRED — {outcome.status_message}", file=stderr)
        task_id = outcome.task.id
        context_id = outcome.task.context_id
        request = _prompt_for_continuation(request, input_fn, stderr)

    _print_outcome(outcome, as_json=args.json, stdout=stdout)


def _prompt_for_continuation(
    request: PackingRequest,
    input_fn: InputFunction,
    prompt_output: TextIO,
) -> PackingRequest:
    try:
        if request.days is None:
            print("Days (1-7): ", end="", file=prompt_output, flush=True)
            raw_days = input_fn("").strip()
            days = _days_value(raw_days)
            return request.model_copy(update={"days": days})

        print(
            "More specific destination (city, country): ",
            end="",
            file=prompt_output,
            flush=True,
        )
        destination = input_fn("").strip()
        return PackingRequest(
            destination=destination,
            days=request.days,
            units=request.units,
            style=request.style,
        )
    except (EOFError, KeyboardInterrupt) as exc:
        raise CoordinatorError("Input was canceled before the task could continue.") from exc
    except argparse.ArgumentTypeError as exc:
        raise CoordinatorError(str(exc)) from exc
    except ValidationError as exc:
        raise CoordinatorError(_validation_message(exc)) from exc


def _print_outcome(outcome: PlanOutcome, *, as_json: bool, stdout: TextIO) -> None:
    if outcome.state in {"FAILED", "REJECTED"}:
        detail = outcome.status_message or "the remote agent did not provide details"
        raise CoordinatorError(f"Task {outcome.state.lower()}: {detail}")
    if outcome.state != "COMPLETED" or outcome.plan is None or outcome.markdown is None:
        raise CoordinatorError(f"Task stopped in unexpected state {outcome.state}.")

    if as_json:
        print(
            json.dumps(
                outcome.plan.model_dump(mode="json"),
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=stdout,
        )
    else:
        print(outcome.markdown, file=stdout)


def _validation_message(exc: ValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in error["loc"]) or "request"
        messages.append(f"{location}: {error['msg']}")
    return "Invalid request: " + "; ".join(messages)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    coordinator_factory: CoordinatorFactory = TripCoordinator,
    input_fn: InputFunction = input,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Run a card discovery or packing delegation command."""

    args = build_parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    try:
        agent_url = resolve_agent_url(args.agent_url, environment)
        coordinator = coordinator_factory(agent_url)
        if args.command == "card":
            asyncio.run(_run_card(coordinator, stdout))
        else:
            asyncio.run(
                _run_plan(
                    coordinator,
                    args,
                    input_fn=input_fn,
                    stdout=stdout,
                    stderr=stderr,
                )
            )
    except CoordinatorError as exc:
        print(f"error: {exc}", file=stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
