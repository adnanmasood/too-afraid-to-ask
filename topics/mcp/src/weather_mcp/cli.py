"""Command-line client for the weather MCP learning server.

The direct commands use the MCP Python SDK themselves, which makes tool
discovery and invocation visible without putting a language model in the
middle.  The ``ask`` command demonstrates the second layer: OpenAI's Responses
API connects to the same MCP server and decides whether to call its weather
tool.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, TextIO
from urllib.parse import urlsplit

from mcp import Client
from openai import OpenAI

DEFAULT_SERVER_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_MODEL = "gpt-5.6"
WEATHER_TOOL = "get_weather"
SERVER_LABEL = "weather"
SERVER_DESCRIPTION = "Read-only current weather data for cities, sourced from Open-Meteo."


class CLIError(Exception):
    """An expected command-line error that should not produce a traceback."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    """Build the command parser used by the ``weather-mcp`` entry point."""

    parser = argparse.ArgumentParser(
        prog="weather-mcp",
        description="Discover and call the weather MCP server.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    tools_parser = subparsers.add_parser(
        "tools",
        help="Discover the tools exposed by an MCP server.",
    )
    tools_parser.add_argument(
        "--server-url",
        help=f"MCP endpoint (default: MCP_SERVER_URL or {DEFAULT_SERVER_URL}).",
    )

    get_parser = subparsers.add_parser(
        "get",
        help="Call get_weather directly, without a language model.",
    )
    get_parser.add_argument("city", help='City to look up, for example "Paris, France".')
    get_parser.add_argument(
        "--units",
        choices=("metric", "imperial"),
        default="metric",
        help="Unit system for the result (default: metric).",
    )
    get_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the structured MCP result as JSON.",
    )
    get_parser.add_argument(
        "--server-url",
        help=f"MCP endpoint (default: MCP_SERVER_URL or {DEFAULT_SERVER_URL}).",
    )

    ask_parser = subparsers.add_parser(
        "ask",
        help="Let an OpenAI model choose and call the remote weather tool.",
    )
    ask_parser.add_argument("prompt", help="Question for the model.")
    ask_parser.add_argument(
        "--server-url",
        help="Public MCP endpoint (or set MCP_SERVER_URL).",
    )
    ask_parser.add_argument(
        "--model",
        help=f"OpenAI model (default: OPENAI_MODEL or {DEFAULT_MODEL}).",
    )

    return parser


def resolve_server_url(
    cli_value: str | None,
    environ: Mapping[str, str],
    *,
    allow_local_default: bool,
) -> str:
    """Resolve and validate a server URL using CLI > environment > default."""

    value = (cli_value or "").strip() or environ.get("MCP_SERVER_URL", "").strip()
    if not value and allow_local_default:
        value = DEFAULT_SERVER_URL
    if not value:
        raise CLIError(
            "ask requires a public MCP URL; pass --server-url or set MCP_SERVER_URL.",
            exit_code=2,
        )

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CLIError(
            f"invalid MCP server URL {value!r}; use an http:// or https:// URL.",
            exit_code=2,
        )
    return value


def resolve_model(cli_value: str | None, environ: Mapping[str, str]) -> str:
    """Resolve the Responses API model using CLI > environment > default."""

    return (cli_value or "").strip() or environ.get("OPENAI_MODEL", "").strip() or DEFAULT_MODEL


def is_loopback_url(url: str) -> bool:
    """Return whether a URL names the local host or a loopback IP address."""

    hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _jsonable(value: Any) -> Any:
    """Convert SDK/Pydantic values to data accepted by ``json.dumps``."""

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _result_text(result: Any) -> str:
    lines: list[str] = []
    for block in _field(result, "content", []) or []:
        text = _field(block, "text")
        if text:
            lines.append(str(text))
    return "\n".join(lines).strip()


def structured_tool_result(result: Any) -> dict[str, Any]:
    """Extract a successful structured tool result from an MCP SDK value."""

    is_error = bool(_field(result, "is_error", _field(result, "isError", False)))
    if is_error:
        detail = _result_text(result) or "the MCP server reported a tool error"
        raise CLIError(detail)

    structured = _field(
        result,
        "structured_content",
        _field(result, "structuredContent"),
    )
    if structured is None:
        text = _result_text(result)
        try:
            structured = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            structured = None

    converted = _jsonable(structured)
    if not isinstance(converted, dict):
        raise CLIError("the MCP server did not return structured weather data")
    return converted


def _display_number(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _location_label(location: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("name", "region", "country"):
        value = location.get(key)
        if value is None or not str(value).strip():
            continue
        text = str(value).strip()
        if text.casefold() not in {part.casefold() for part in parts}:
            parts.append(text)
    return ", ".join(parts) or "Unknown location"


def format_weather(weather: Mapping[str, Any]) -> str:
    """Format the structured weather result for a terminal."""

    location = weather.get("location")
    if not isinstance(location, Mapping):
        location = {}
    units = weather.get("units")
    if not isinstance(units, Mapping):
        units = {}

    temperature_unit = units.get("temperature", "")
    humidity_unit = units.get("relative_humidity", "%")
    wind_unit = units.get("wind_speed", "")
    wind_direction_unit = units.get("wind_direction", "°")
    precipitation_unit = units.get("precipitation", "")
    cloud_cover_unit = units.get("cloud_cover", "%")
    daytime = "daytime" if weather.get("is_day") else "nighttime"

    lines = [
        f"Weather for {_location_label(location)}",
        (
            f"Condition: {weather.get('condition', 'Unknown')} "
            f"(WMO {weather.get('weather_code', 'unknown')}; {daytime})"
        ),
        (
            f"Temperature: {_display_number(weather.get('temperature', 'unknown'))} "
            f"{temperature_unit} (feels like "
            f"{_display_number(weather.get('apparent_temperature', 'unknown'))} "
            f"{temperature_unit})"
        ).rstrip(),
        (
            f"Humidity: {_display_number(weather.get('relative_humidity', 'unknown'))} "
            f"{humidity_unit}"
        ).rstrip(),
        (
            f"Precipitation: {_display_number(weather.get('precipitation', 'unknown'))} "
            f"{precipitation_unit}"
        ).rstrip(),
        (
            f"Cloud cover: {_display_number(weather.get('cloud_cover', 'unknown'))} "
            f"{cloud_cover_unit}"
        ).rstrip(),
        (
            f"Wind: {_display_number(weather.get('wind_speed', 'unknown'))} {wind_unit} at "
            f"{_display_number(weather.get('wind_direction_degrees', 'unknown'))} "
            f"{wind_direction_unit}"
        ).rstrip(),
    ]

    observed_at = weather.get("observed_at", "unknown")
    timezone = weather.get("timezone")
    observed_suffix = f" ({timezone})" if timezone else ""
    lines.append(f"Observed: {observed_at}{observed_suffix}")

    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
        lines.append(f"Coordinates: {latitude:.4f}, {longitude:.4f}")

    source = weather.get("source", "Open-Meteo")
    source_url = weather.get("source_url")
    lines.append(f"Source: {source}" + (f" — {source_url}" if source_url else ""))
    return "\n".join(lines)


def _format_schema(schema: Any) -> list[str]:
    rendered = json.dumps(_jsonable(schema or {}), indent=2, ensure_ascii=False, sort_keys=True)
    return [f"      {line}" for line in rendered.splitlines()]


async def discover_tools(server_url: str, client_factory: Callable[[str], Any] = Client) -> str:
    """Connect to an MCP server and return a human-readable discovery report."""

    async with client_factory(server_url) as client:
        result = await client.list_tools()
        server_info = _field(client, "server_info")
        protocol_version = _field(client, "protocol_version")

    server_name = _field(server_info, "name", "unknown")
    server_version = _field(server_info, "version")
    heading = f"Server: {server_name}"
    if server_version:
        heading += f" {server_version}"
    lines = [heading]
    if protocol_version:
        lines.append(f"Protocol: {protocol_version}")
    lines.append("Tools:")

    tools = _field(result, "tools", []) or []
    if not tools:
        lines.append("  (none)")
    for tool in tools:
        name = _field(tool, "name", "unknown")
        title = _field(tool, "title")
        lines.append(f"  - {title} ({name})" if title and title != name else f"  - {name}")
        description = _field(tool, "description")
        if description:
            lines.append(f"    {description}")
        lines.append("    Input schema:")
        schema = _field(tool, "input_schema", _field(tool, "inputSchema", {}))
        lines.extend(_format_schema(schema))
    return "\n".join(lines)


async def call_weather(
    server_url: str,
    city: str,
    units: str,
    client_factory: Callable[[str], Any] = Client,
) -> dict[str, Any]:
    """Call ``get_weather`` directly and return its structured result."""

    normalized_city = city.strip()
    if not 2 <= len(normalized_city) <= 100:
        raise CLIError("city must contain between 2 and 100 characters", exit_code=2)

    async with client_factory(server_url) as client:
        result = await client.call_tool(
            WEATHER_TOOL,
            {"city": normalized_city, "units": units},
        )
    return structured_tool_result(result)


def _compact_json(value: Any) -> str:
    converted = _jsonable(value)
    return json.dumps(converted, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _compact_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
    if arguments is None:
        arguments = {}
    return _compact_json(arguments)


def format_mcp_trace(output: Sequence[Any] | None) -> str:
    """Format MCP calls from a Responses API output array."""

    calls = [item for item in output or [] if _field(item, "type") == "mcp_call"]
    if not calls:
        return "  (the model did not call the MCP tool)"

    lines: list[str] = []
    for item in calls:
        server_label = _field(item, "server_label", SERVER_LABEL)
        name = _field(item, "name", "unknown")
        arguments = _compact_arguments(_field(item, "arguments"))
        error = _field(item, "error")
        status = f"error={_compact_json(error)}" if error else "ok"
        lines.append(f"  {server_label}.{name} {arguments} -> {status}")
    return "\n".join(lines)


def ask_weather(
    prompt: str,
    server_url: str,
    model: str,
    openai_client_factory: Callable[[], Any] = OpenAI,
) -> str:
    """Ask an OpenAI model to use the public weather MCP server."""

    if is_loopback_url(server_url):
        raise CLIError(
            "OpenAI cannot reach a loopback MCP URL. Publish the server or use a tunnel, "
            "then pass its public /mcp URL to --server-url.",
            exit_code=2,
        )

    response = openai_client_factory().responses.create(
        model=model,
        input=prompt,
        tools=[
            {
                "type": "mcp",
                "server_label": SERVER_LABEL,
                "server_description": SERVER_DESCRIPTION,
                "server_url": server_url,
                "allowed_tools": [WEATHER_TOOL],
                "require_approval": "never",
            }
        ],
    )
    answer = _field(response, "output_text") or "(The model returned no final text.)"
    trace = format_mcp_trace(_field(response, "output", []))
    return f"MCP call trace:\n{trace}\n\nAnswer:\n{answer}"


def run_command(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str],
    mcp_client_factory: Callable[[str], Any] = Client,
    openai_client_factory: Callable[[], Any] = OpenAI,
) -> str:
    """Execute parsed arguments and return text for stdout."""

    if args.command == "tools":
        server_url = resolve_server_url(
            args.server_url,
            environ,
            allow_local_default=True,
        )
        return asyncio.run(discover_tools(server_url, mcp_client_factory))

    if args.command == "get":
        server_url = resolve_server_url(
            args.server_url,
            environ,
            allow_local_default=True,
        )
        weather = asyncio.run(
            call_weather(
                server_url,
                args.city,
                args.units,
                mcp_client_factory,
            )
        )
        if args.json:
            return json.dumps(weather, indent=2, ensure_ascii=False, sort_keys=True)
        return format_weather(weather)

    if args.command == "ask":
        server_url = resolve_server_url(
            args.server_url,
            environ,
            allow_local_default=False,
        )
        model = resolve_model(args.model, environ)
        return ask_weather(
            args.prompt,
            server_url,
            model,
            openai_client_factory,
        )

    raise CLIError(f"unknown command: {args.command}", exit_code=2)  # pragma: no cover


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    mcp_client_factory: Callable[[str], Any] = Client,
    openai_client_factory: Callable[[], Any] = OpenAI,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the command-line application and return a process exit code."""

    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr
    args = build_parser().parse_args(argv)
    try:
        output = run_command(
            args,
            environ=os.environ if environ is None else environ,
            mcp_client_factory=mcp_client_factory,
            openai_client_factory=openai_client_factory,
        )
    except CLIError as exc:
        print(f"error: {exc}", file=error_stream)
        return exc.exit_code
    except KeyboardInterrupt:
        print("error: interrupted", file=error_stream)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary intentionally removes tracebacks
        print(f"error: {exc}", file=error_stream)
        return 1

    print(output, file=output_stream)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
