"""MCP server exposing a single read-only weather tool."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import StringConstraints

from weather_mcp import __version__
from weather_mcp.models import Units, WeatherResult
from weather_mcp.weather_service import (
    LocationNotFoundError,
    OpenMeteoWeatherService,
    WeatherProviderError,
    WeatherService,
)

City = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=100),
]


def create_server(weather_service: WeatherService | None = None) -> MCPServer:
    """Build a weather MCP server, optionally using an injected service."""
    service = weather_service if weather_service is not None else OpenMeteoWeatherService()
    server = MCPServer(
        name="weather-mcp",
        title="Weather MCP Server",
        description="A learning server that returns current weather from Open-Meteo.",
        instructions="Call get_weather with a city name to retrieve current weather.",
        version=__version__,
    )

    @server.tool(
        name="get_weather",
        title="Get current weather",
        description=(
            "Get current weather for a city. Add a country or region to the city name "
            "when it needs disambiguation."
        ),
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
        structured_output=True,
    )
    async def get_weather(city: City, units: Units = "metric") -> WeatherResult:
        try:
            return await service.get_weather(city, units)
        except LocationNotFoundError as exc:
            raise ToolError(str(exc)) from exc
        except WeatherProviderError as exc:
            raise ToolError(
                "Weather service is temporarily unavailable. Please try again later."
            ) from exc

    return server


mcp = create_server()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="weather-mcp-server",
        description="Run the Weather MCP server over Streamable HTTP.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Address to bind (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind (default: %(default)s)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run the module-global server on the configured HTTP address."""
    args = _parser().parse_args(argv)
    try:
        mcp.run(
            "streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
        )
    except KeyboardInterrupt:
        # MCP/AnyIO may re-raise Ctrl+C after Uvicorn has shut down cleanly.
        pass


if __name__ == "__main__":
    main()
