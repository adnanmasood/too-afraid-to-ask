"""Offline tests for the direct and model-mediated command-line clients."""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest

from weather_mcp.cli import (
    DEFAULT_MODEL,
    DEFAULT_SERVER_URL,
    SERVER_DESCRIPTION,
    build_parser,
    format_mcp_trace,
    format_weather,
    is_loopback_url,
    main,
    resolve_model,
    resolve_server_url,
)


def sample_weather() -> dict[str, Any]:
    return {
        "location": {
            "name": "Berlin",
            "country": "Germany",
            "region": "Berlin",
            "latitude": 52.52,
            "longitude": 13.405,
        },
        "observed_at": "2026-09-02T16:00",
        "timezone": "Europe/Berlin",
        "condition": "Clear sky",
        "weather_code": 0,
        "temperature": 21.0,
        "apparent_temperature": 20.4,
        "relative_humidity": 55.0,
        "precipitation": 0.0,
        "cloud_cover": 5.0,
        "wind_speed": 8.5,
        "wind_direction_degrees": 225.0,
        "is_day": True,
        "units": {
            "temperature": "°C",
            "relative_humidity": "%",
            "precipitation": "mm",
            "cloud_cover": "%",
            "wind_speed": "km/h",
            "wind_direction": "°",
        },
        "source": "Open-Meteo",
        "source_url": "https://open-meteo.com/",
    }


class FakeMCPClient:
    def __init__(
        self,
        owner: FakeMCPFactory,
        *,
        call_result: Any | None = None,
    ) -> None:
        self.owner = owner
        self.call_result = call_result
        self.server_info = SimpleNamespace(name="weather-mcp", version="0.1.0")
        self.protocol_version = "2026-07-28"

    async def __aenter__(self) -> FakeMCPClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def list_tools(self) -> Any:
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="get_weather",
                    title="Get current weather",
                    description="Get current weather for a city.",
                    input_schema={
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                )
            ]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.owner.calls.append((name, arguments))
        if self.call_result is None:
            return SimpleNamespace(
                is_error=False,
                structured_content=sample_weather(),
                content=[],
            )
        return self.call_result


class FakeMCPFactory:
    def __init__(self, call_result: Any | None = None) -> None:
        self.call_result = call_result
        self.urls: list[str] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str) -> FakeMCPClient:
        self.urls.append(url)
        return FakeMCPClient(self, call_result=self.call_result)


class FakeResponses:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return SimpleNamespace(
            output=[
                {"type": "mcp_list_tools", "server_label": "weather"},
                {
                    "type": "mcp_call",
                    "server_label": "weather",
                    "name": "get_weather",
                    "arguments": '{"units":"metric","city":"Berlin"}',
                    "output": '{"temperature":21}',
                    "error": None,
                },
            ],
            output_text="It is clear and 21 °C in Berlin.",
        )


class FakeOpenAI:
    def __init__(self) -> None:
        self.responses = FakeResponses()
        self.constructed = 0

    def factory(self) -> FakeOpenAI:
        self.constructed += 1
        return self


def test_parser_captures_get_options() -> None:
    args = build_parser().parse_args(
        [
            "get",
            "Paris, France",
            "--units",
            "imperial",
            "--json",
            "--server-url",
            "https://weather.example/mcp",
        ]
    )

    assert args.command == "get"
    assert args.city == "Paris, France"
    assert args.units == "imperial"
    assert args.json is True
    assert args.server_url == "https://weather.example/mcp"


def test_server_url_precedence_and_local_default() -> None:
    environ = {"MCP_SERVER_URL": "https://env.example/mcp"}

    assert (
        resolve_server_url(
            "https://cli.example/mcp",
            environ,
            allow_local_default=True,
        )
        == "https://cli.example/mcp"
    )
    assert resolve_server_url(None, environ, allow_local_default=True) == "https://env.example/mcp"
    assert resolve_server_url(None, {}, allow_local_default=True) == DEFAULT_SERVER_URL


def test_model_precedence_and_default() -> None:
    assert resolve_model("gpt-cli", {"OPENAI_MODEL": "gpt-env"}) == "gpt-cli"
    assert resolve_model(None, {"OPENAI_MODEL": "gpt-env"}) == "gpt-env"
    assert resolve_model(None, {}) == DEFAULT_MODEL


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000/mcp",
        "http://localhost.:8000/mcp",
        "http://api.localhost/mcp",
        "http://127.0.0.1:8000/mcp",
        "http://127.99.3.4/mcp",
        "http://[::1]:8000/mcp",
    ],
)
def test_loopback_url_detection(url: str) -> None:
    assert is_loopback_url(url)


def test_public_url_is_not_mistaken_for_loopback() -> None:
    assert not is_loopback_url("https://weather.example/mcp")
    assert not is_loopback_url("https://localhost.example.com/mcp")


def test_tools_command_prints_server_tool_and_schema() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    factory = FakeMCPFactory()

    exit_code = main(
        ["tools", "--server-url", "https://cli.example/mcp"],
        environ={"MCP_SERVER_URL": "https://env.example/mcp"},
        mcp_client_factory=factory,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert factory.urls == ["https://cli.example/mcp"]
    assert "Server: weather-mcp 0.1.0" in stdout.getvalue()
    assert "Protocol: 2026-07-28" in stdout.getvalue()
    assert "Get current weather (get_weather)" in stdout.getvalue()
    assert '"required": [' in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_get_command_calls_tool_and_prints_structured_json() -> None:
    stdout = io.StringIO()
    factory = FakeMCPFactory()

    exit_code = main(
        ["get", "  Berlin  ", "--units", "imperial", "--json"],
        environ={"MCP_SERVER_URL": "https://env.example/mcp"},
        mcp_client_factory=factory,
        stdout=stdout,
    )

    assert exit_code == 0
    assert factory.urls == ["https://env.example/mcp"]
    assert factory.calls == [("get_weather", {"city": "Berlin", "units": "imperial"})]
    assert '"condition": "Clear sky"' in stdout.getvalue()
    assert '"source": "Open-Meteo"' in stdout.getvalue()


def test_human_weather_format_includes_all_measurements_and_attribution() -> None:
    rendered = format_weather(sample_weather())

    assert "Weather for Berlin, Germany" in rendered
    assert "Condition: Clear sky (WMO 0; daytime)" in rendered
    assert "Temperature: 21 °C (feels like 20.4 °C)" in rendered
    assert "Humidity: 55 %" in rendered
    assert "Precipitation: 0 mm" in rendered
    assert "Cloud cover: 5 %" in rendered
    assert "Wind: 8.5 km/h at 225 °" in rendered
    assert "Observed: 2026-09-02T16:00 (Europe/Berlin)" in rendered
    assert "Coordinates: 52.5200, 13.4050" in rendered
    assert "Source: Open-Meteo — https://open-meteo.com/" in rendered


def test_get_command_turns_mcp_tool_error_into_clean_failure() -> None:
    result = SimpleNamespace(
        is_error=True,
        structured_content=None,
        content=[SimpleNamespace(type="text", text="No location found for 'Atlantis'.")],
    )
    factory = FakeMCPFactory(call_result=result)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        ["get", "Atlantis"],
        environ={},
        mcp_client_factory=factory,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "error: No location found for 'Atlantis'.\n"


def test_ask_rejects_loopback_before_constructing_openai_client() -> None:
    fake_openai = FakeOpenAI()
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        ["ask", "What is the weather?", "--server-url", "http://127.0.0.1:8000/mcp"],
        environ={},
        openai_client_factory=fake_openai.factory,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert fake_openai.constructed == 0
    assert "OpenAI cannot reach a loopback MCP URL" in stderr.getvalue()
    assert "public /mcp URL" in stderr.getvalue()
    assert stdout.getvalue() == ""


def test_ask_requires_public_url_when_no_cli_or_environment_value() -> None:
    stderr = io.StringIO()

    exit_code = main(
        ["ask", "What is the weather?"],
        environ={},
        openai_client_factory=FakeOpenAI().factory,
        stderr=stderr,
    )

    assert exit_code == 2
    assert "pass --server-url or set MCP_SERVER_URL" in stderr.getvalue()


def test_ask_builds_remote_mcp_payload_and_prints_call_trace() -> None:
    fake_openai = FakeOpenAI()
    stdout = io.StringIO()

    exit_code = main(
        [
            "ask",
            "How is Berlin today?",
            "--server-url",
            "https://weather.example/mcp",
            "--model",
            "gpt-cli",
        ],
        environ={
            "MCP_SERVER_URL": "https://env.example/mcp",
            "OPENAI_MODEL": "gpt-env",
        },
        openai_client_factory=fake_openai.factory,
        stdout=stdout,
    )

    assert exit_code == 0
    assert fake_openai.constructed == 1
    assert fake_openai.responses.requests == [
        {
            "model": "gpt-cli",
            "input": "How is Berlin today?",
            "tools": [
                {
                    "type": "mcp",
                    "server_label": "weather",
                    "server_description": SERVER_DESCRIPTION,
                    "server_url": "https://weather.example/mcp",
                    "allowed_tools": ["get_weather"],
                    "require_approval": "never",
                }
            ],
        }
    ]
    rendered = stdout.getvalue()
    assert "MCP call trace:" in rendered
    assert 'weather.get_weather {"city":"Berlin","units":"metric"} -> ok' in rendered
    assert "Answer:\nIt is clear and 21 °C in Berlin." in rendered


def test_trace_shows_absent_and_failed_calls() -> None:
    assert format_mcp_trace([]) == "  (the model did not call the MCP tool)"

    trace = format_mcp_trace(
        [
            SimpleNamespace(
                type="mcp_call",
                server_label="weather",
                name="get_weather",
                arguments={"city": "Atlantis"},
                error={"message": "not found"},
            )
        ]
    )
    assert trace == ('  weather.get_weather {"city":"Atlantis"} -> error={"message":"not found"}')
