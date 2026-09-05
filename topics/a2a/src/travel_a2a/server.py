"""Starlette application for the Packing Advisor A2A agent."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from starlette.applications import Starlette

from travel_a2a import __version__
from travel_a2a.coordinator import CoordinatorError, normalize_agent_url
from travel_a2a.executor import PackingAgentExecutor
from travel_a2a.planner import PackingPlanner, create_planner
from travel_a2a.protocol import (
    A2A_PROTOCOL_VERSION,
    DEFAULT_AGENT_URL,
    JSON_MEDIA_TYPE,
    JSONRPC_BINDING,
    MARKDOWN_MEDIA_TYPE,
    PACKING_SKILL_ID,
)
from travel_a2a.weather_service import ForecastService, OpenMeteoForecastService

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9999


def build_agent_card(agent_url: str = DEFAULT_AGENT_URL) -> AgentCard:
    """Describe the one skill and JSON-RPC interface exposed by this agent."""

    normalized_url = normalize_agent_url(agent_url)
    return AgentCard(
        name="Packing Advisor",
        description=(
            "Creates a weather-aware packing plan for trips of one to seven days, "
            "using forecast data from Open-Meteo."
        ),
        supported_interfaces=[
            AgentInterface(
                url=normalized_url,
                protocol_binding=JSONRPC_BINDING,
                protocol_version=A2A_PROTOCOL_VERSION,
            )
        ],
        version=__version__,
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=[JSON_MEDIA_TYPE],
        default_output_modes=[JSON_MEDIA_TYPE, MARKDOWN_MEDIA_TYPE],
        skills=[
            AgentSkill(
                id=PACKING_SKILL_ID,
                name="Plan weather-aware packing",
                description=(
                    "Create a structured and human-readable packing plan from a "
                    "destination, trip length, unit system, and travel style."
                ),
                tags=["travel", "weather", "packing"],
                examples=['{"destination":"Berlin, Germany","days":3,"style":"business"}'],
                input_modes=[JSON_MEDIA_TYPE],
                output_modes=[JSON_MEDIA_TYPE, MARKDOWN_MEDIA_TYPE],
            )
        ],
    )


def create_app(
    forecast_service: ForecastService | None = None,
    planner: PackingPlanner | None = None,
    *,
    agent_url: str = DEFAULT_AGENT_URL,
) -> Starlette:
    """Create an injectable Starlette app with Agent Card and JSON-RPC routes."""

    card = build_agent_card(agent_url)
    selected_planner = planner or create_planner("deterministic")
    executor = PackingAgentExecutor(
        forecast_service or OpenMeteoForecastService(),
        selected_planner,
    )
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        try:
            yield
        finally:
            try:
                await handler.aclose()
            finally:
                close_planner = getattr(selected_planner, "aclose", None)
                if close_planner is not None:
                    await close_planner()

    routes = [
        *create_agent_card_routes(card),
        *create_jsonrpc_routes(handler, rpc_url="/"),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.agent_card = card
    app.state.request_handler = handler
    return app


def build_parser() -> argparse.ArgumentParser:
    """Build the ``travel-a2a-server`` command parser."""

    parser = argparse.ArgumentParser(
        prog="travel-a2a-server",
        description="Run the Packing Advisor using A2A 1.0 over JSON-RPC 2.0.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Address to bind (default: %(default)s).",
    )
    parser.add_argument(
        "--port",
        default=DEFAULT_PORT,
        type=int,
        help="Port to bind (default: %(default)s).",
    )
    parser.add_argument(
        "--public-url",
        help=(
            "URL placed in the Agent Card (default: A2A_PUBLIC_URL or loopback URL using --port)."
        ),
    )
    parser.add_argument(
        "--planner",
        choices=("deterministic", "openai"),
        help="Planner backend (default: PACKING_PLANNER or deterministic).",
    )
    return parser


def resolve_planner_name(cli_value: str | None, environ: Mapping[str, str]) -> str:
    """Resolve the planner using command line, environment, then default."""

    name = (cli_value or "").strip() or environ.get("PACKING_PLANNER", "").strip()
    name = name or "deterministic"
    if name not in {"deterministic", "openai"}:
        raise ValueError("PACKING_PLANNER must be 'deterministic' or 'openai'")
    return name


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    uvicorn_run: Any = uvicorn.run,
) -> int:
    """Run the Packing Advisor on the configured loopback address."""

    parser = build_parser()
    args = parser.parse_args(argv)
    environment = os.environ if environ is None else environ
    try:
        planner_name = resolve_planner_name(args.planner, environment)
    except ValueError as exc:
        parser.error(str(exc))

    public_url_value = (
        (args.public_url or "").strip()
        or environment.get("A2A_PUBLIC_URL", "").strip()
        or f"http://127.0.0.1:{args.port}"
    )
    try:
        public_url = normalize_agent_url(public_url_value)
    except CoordinatorError as exc:
        parser.error(str(exc))
    planner = create_planner(
        planner_name,
        openai_model=environment.get("OPENAI_MODEL") or None,
    )
    app = create_app(planner=planner, agent_url=public_url)
    print(f"Packing Advisor Agent Card: {public_url.rstrip('/')}/.well-known/agent-card.json")
    print(f"Packing Advisor JSON-RPC: {public_url.rstrip('/') or public_url}/")
    try:
        uvicorn_run(app, host=args.host, port=args.port)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
