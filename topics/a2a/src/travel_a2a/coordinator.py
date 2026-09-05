"""A defensive Trip Coordinator client for the remote Packing Advisor."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, urlsplit

import httpx
from a2a.client import (
    A2ACardResolver,
    A2AClientError,
    AgentCardResolutionError,
    ClientConfig,
    ClientFactory,
)
from a2a.helpers import get_data_parts, new_data_message
from a2a.types.a2a_pb2 import (
    AgentCard,
    Artifact,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.constants import TransportProtocol
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from travel_a2a.models import PackingPlan, PackingRequest
from travel_a2a.protocol import (
    A2A_PROTOCOL_VERSION,
    DEFAULT_AGENT_URL,
    JSON_MEDIA_TYPE,
    JSONRPC_BINDING,
    MARKDOWN_MEDIA_TYPE,
    PACKING_ARTIFACT_NAME,
    PACKING_SKILL_ID,
    TEXT_MEDIA_TYPE,
)

HttpClientFactory = Callable[[], httpx.AsyncClient]
EventCallback = Callable[["CoordinatorEvent"], None]

_DEFAULT_HTTP_TIMEOUT = httpx.Timeout(
    connect=5.0,
    read=120.0,
    write=10.0,
    pool=5.0,
)
_MAX_ERROR_DETAIL_LENGTH = 240


class CoordinatorError(Exception):
    """An expected discovery, transport, or remote-contract error."""


@dataclass(frozen=True, slots=True)
class CoordinatorEvent:
    """Small, presentation-neutral description of one protocol event."""

    kind: str
    task_id: str
    context_id: str
    state: str | None = None
    message: str | None = None
    artifact_name: str | None = None


@dataclass(frozen=True, slots=True)
class PlanOutcome:
    """Validated task state plus the packing artifact, when completed."""

    task: Task
    events: tuple[CoordinatorEvent, ...]
    plan: PackingPlan | None = None
    markdown: str | None = None

    @property
    def state(self) -> str:
        """Return a concise state name such as ``COMPLETED``."""

        return task_state_name(self.task.status.state)

    @property
    def status_message(self) -> str | None:
        """Return the remote agent's current status message, if present."""

        if not self.task.status.HasField("message"):
            return None
        return _validated_status_text(
            self.task.status,
            task_id=self.task.id,
            context_id=self.task.context_id,
        )


class TripCoordinator:
    """Discover an A2A agent and delegate a packing-plan task to it."""

    def __init__(
        self,
        agent_url: str = DEFAULT_AGENT_URL,
        *,
        http_client_factory: HttpClientFactory | None = None,
        timeout: httpx.Timeout | float | None = None,
    ) -> None:
        self.agent_url = normalize_agent_url(agent_url)
        resolved_timeout = _DEFAULT_HTTP_TIMEOUT if timeout is None else timeout
        self._http_client_factory = http_client_factory or (
            lambda: httpx.AsyncClient(timeout=resolved_timeout, follow_redirects=False)
        )

    async def discover_card(self) -> AgentCard:
        """Fetch and validate the public Agent Card as untrusted input."""

        http_client = self._http_client_factory()
        try:
            return await _discover_card(http_client, self.agent_url)
        except CoordinatorError:
            raise
        except (AgentCardResolutionError, httpx.HTTPError, ValueError) as exc:
            raise CoordinatorError(
                f"Could not discover an A2A agent at {self.agent_url}: "
                f"{_safe_exception_detail(exc)}"
            ) from exc
        finally:
            await http_client.aclose()

    async def request_plan(
        self,
        request: PackingRequest,
        *,
        stream: bool = False,
        task_id: str | None = None,
        context_id: str | None = None,
        on_event: EventCallback | None = None,
    ) -> PlanOutcome:
        """Send or continue a task and validate every returned event/artifact."""

        if bool(task_id) != bool(context_id):
            raise CoordinatorError("Task continuation requires both task_id and context_id.")

        http_client = self._http_client_factory()
        client = None
        try:
            card = await _discover_card(http_client, self.agent_url)
            config = ClientConfig(
                streaming=stream,
                httpx_client=http_client,
                supported_protocol_bindings=[TransportProtocol.JSONRPC],
                use_client_preference=True,
                accepted_output_modes=[JSON_MEDIA_TYPE, MARKDOWN_MEDIA_TYPE],
            )
            client = ClientFactory(config).create(card)
            message = new_data_message(
                request.model_dump(mode="json"),
                media_type=JSON_MEDIA_TYPE,
                task_id=task_id,
                context_id=context_id,
                role=Role.ROLE_USER,
            )
            send_request = SendMessageRequest(
                message=message,
                configuration=SendMessageConfiguration(
                    accepted_output_modes=[JSON_MEDIA_TYPE, MARKDOWN_MEDIA_TYPE]
                ),
            )
            collector = _TaskCollector(
                streaming=stream,
                expected_task_id=task_id,
                expected_context_id=context_id,
            )
            async for response in client.send_message(send_request):
                event = collector.consume(response)
                if on_event is not None:
                    on_event(event)
            return collector.finish()
        except CoordinatorError:
            raise
        except (AgentCardResolutionError, A2AClientError, httpx.HTTPError, ValueError) as exc:
            raise CoordinatorError(
                f"A2A request to {self.agent_url} failed: {_safe_exception_detail(exc)}"
            ) from exc
        finally:
            if client is not None:
                await client.close()
            else:
                await http_client.aclose()


async def _discover_card(http_client: httpx.AsyncClient, agent_url: str) -> AgentCard:
    resolver = A2ACardResolver(http_client, agent_url)
    try:
        card = await resolver.get_agent_card()
    except AgentCardResolutionError as exc:
        raise CoordinatorError(
            f"Could not discover an A2A agent at {agent_url}: {_safe_exception_detail(exc)}"
        ) from exc
    validate_agent_card(card, agent_url)
    return card


def normalize_agent_url(value: str) -> str:
    """Validate a user-controlled base URL and remove only trailing slashes."""

    if not isinstance(value, str):
        raise CoordinatorError("Agent URL must be text.")
    if any(
        not character.isprintable()
        or unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for character in value
    ):
        raise CoordinatorError("Agent URL must not contain control or non-printable characters.")
    # Permit ordinary copy/paste padding, but reject whitespace or parser-
    # ambiguous backslashes inside the URL itself.
    cleaned = value.strip(" ")
    if any(character.isspace() for character in cleaned) or "\\" in cleaned:
        raise CoordinatorError("Agent URL must not contain whitespace or backslashes.")
    try:
        parsed = urlsplit(cleaned)
    except ValueError as exc:
        raise CoordinatorError("Agent URL is malformed.") from exc
    _validate_http_url(parsed, label="agent URL")
    if parsed.query or parsed.fragment:
        raise CoordinatorError("Agent URL must not contain a query string or fragment.")
    try:
        normalized = httpx.URL(cleaned)
    except httpx.InvalidURL as exc:
        raise CoordinatorError("Agent URL is malformed.") from exc
    path = normalized.path.rstrip("/")
    return str(normalized.copy_with(path=path))


def validate_agent_card(card: AgentCard, requested_url: str) -> None:
    """Require the expected skill, modes, protocol, and same-origin interfaces."""

    _reject_unsafe_card_text(MessageToDict(card))
    if not card.name.strip() or not card.description.strip():
        raise CoordinatorError("Agent Card is missing its name or description.")
    if not card.capabilities.streaming:
        raise CoordinatorError("Agent Card does not advertise streaming support.")
    if not card.supported_interfaces:
        raise CoordinatorError("Agent Card does not advertise an interface.")

    requested_origin = _origin(urlsplit(normalize_agent_url(requested_url)))
    matching_jsonrpc = False
    for interface in card.supported_interfaces:
        try:
            normalized_interface = normalize_agent_url(interface.url)
        except CoordinatorError as exc:
            raise CoordinatorError(f"Agent Card interface URL is invalid: {exc}") from exc
        # ClientFactory consumes this same card after validation, so retain the
        # canonical value rather than handing it the parser-ambiguous original.
        interface.url = normalized_interface
        parsed_interface = urlsplit(normalized_interface)
        if _origin(parsed_interface) != requested_origin:
            raise CoordinatorError(
                "Agent Card advertised a cross-origin interface; refusing to connect."
            )
        if (
            interface.protocol_binding == JSONRPC_BINDING
            and interface.protocol_version == A2A_PROTOCOL_VERSION
        ):
            matching_jsonrpc = True

    if not matching_jsonrpc:
        raise CoordinatorError("Agent Card does not advertise A2A 1.0 over JSON-RPC.")

    skills = [skill for skill in card.skills if skill.id == PACKING_SKILL_ID]
    if len(skills) != 1:
        raise CoordinatorError(f"Agent Card must advertise exactly one {PACKING_SKILL_ID!r} skill.")
    skill = skills[0]
    input_modes = set(skill.input_modes or card.default_input_modes)
    output_modes = set(skill.output_modes or card.default_output_modes)
    if JSON_MEDIA_TYPE not in input_modes:
        raise CoordinatorError("Packing skill does not accept application/json.")
    if not {JSON_MEDIA_TYPE, MARKDOWN_MEDIA_TYPE}.issubset(output_modes):
        raise CoordinatorError("Packing skill does not advertise both JSON and Markdown output.")


def task_state_name(state: int) -> str:
    """Convert the protobuf enum value to a compact stable label."""

    try:
        name = TaskState.Name(state)
    except ValueError as exc:
        raise CoordinatorError(f"Agent returned an unknown task state: {state}") from exc
    return name.removeprefix("TASK_STATE_")


def _reject_unsafe_card_text(value: Any) -> None:
    """Reject terminal-spoofing controls anywhere in untrusted card metadata."""

    if isinstance(value, str):
        if any(
            not character.isprintable()
            or unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            for character in value
        ):
            raise CoordinatorError(
                "Agent Card interface URL or text must not contain control or "
                "non-printable characters."
            )
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_unsafe_card_text(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_unsafe_card_text(item)


def _validate_http_url(parsed: SplitResult, *, label: str) -> None:
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CoordinatorError(f"{label} must be an http:// or https:// URL.")
    if parsed.username is not None or parsed.password is not None:
        raise CoordinatorError(f"{label} must not contain credentials.")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise CoordinatorError(f"{label} has an invalid port.") from exc


def _origin(parsed: SplitResult) -> tuple[str, str, int]:
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port or default_port


def _safe_exception_detail(exc: BaseException) -> str:
    """Return a single printable, bounded diagnostic for untrusted failures."""

    try:
        raw = str(exc)
    except Exception:  # pragma: no cover - exceptionally defensive
        raw = type(exc).__name__
    printable = "".join(character if character.isprintable() else " " for character in raw)
    collapsed = " ".join(printable.split()) or type(exc).__name__
    if len(collapsed) <= _MAX_ERROR_DETAIL_LENGTH:
        return collapsed
    return collapsed[: _MAX_ERROR_DETAIL_LENGTH - 1].rstrip() + "…"


_ALLOWED_TRANSITIONS: dict[int, set[int]] = {
    TaskState.TASK_STATE_SUBMITTED: {
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_FAILED,
    },
    TaskState.TASK_STATE_WORKING: {
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_FAILED,
    },
    TaskState.TASK_STATE_INPUT_REQUIRED: {
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_FAILED,
    },
}


class _TaskCollector:
    def __init__(
        self,
        *,
        streaming: bool,
        expected_task_id: str | None,
        expected_context_id: str | None,
    ) -> None:
        self.streaming = streaming
        self.expected_task_id = expected_task_id
        self.expected_context_id = expected_context_id
        self.task: Task | None = None
        self.events: list[CoordinatorEvent] = []
        self._payload_count = 0
        self._state = TaskState.TASK_STATE_INPUT_REQUIRED if expected_task_id else None

    def consume(self, response: StreamResponse) -> CoordinatorEvent:
        payload = response.WhichOneof("payload")
        if payload is None:
            raise CoordinatorError("Agent returned an empty stream response.")
        if payload == "message":
            raise CoordinatorError("Packing Advisor returned a Message instead of a Task.")

        self._payload_count += 1
        if payload == "task":
            event = self._consume_task(response.task)
        elif payload == "status_update":
            event = self._consume_status(response.status_update)
        elif payload == "artifact_update":
            event = self._consume_artifact(response.artifact_update)
        else:  # pragma: no cover - protobuf oneof limits this branch
            raise CoordinatorError(f"Unsupported stream payload: {payload}")
        self.events.append(event)
        return event

    def _consume_task(self, task: Task) -> CoordinatorEvent:
        if self.task is not None or self._payload_count != 1:
            raise CoordinatorError("Agent returned an unexpected additional Task event.")
        self._validate_ids(task.id, task.context_id)
        if self.streaming and self.expected_task_id is None:
            if task.status.state != TaskState.TASK_STATE_SUBMITTED:
                raise CoordinatorError("A new task stream did not begin in SUBMITTED state.")
        elif self.streaming and self.expected_task_id is not None:
            # Some conforming servers replay the current Task before emitting
            # continuation updates. a2a-sdk 1.1.2 emits updates first. Accept
            # both shapes while requiring the known interrupted state and IDs.
            if task.status.state != TaskState.TASK_STATE_INPUT_REQUIRED:
                raise CoordinatorError(
                    "A continued task stream must replay INPUT_REQUIRED before updates."
                )

        self.task = Task()
        self.task.CopyFrom(task)
        self._state = task.status.state
        message = (
            _validated_status_text(
                task.status,
                task_id=task.id,
                context_id=task.context_id,
            )
            if task.status.HasField("message")
            else None
        )
        if task.status.state == TaskState.TASK_STATE_COMPLETED:
            _extract_packing_artifact(task)
        elif task.artifacts:
            raise CoordinatorError("A non-completed task unexpectedly contained artifacts.")
        return CoordinatorEvent(
            kind="task",
            task_id=task.id,
            context_id=task.context_id,
            state=task_state_name(task.status.state),
            message=message,
        )

    def _consume_status(self, update: Any) -> CoordinatorEvent:
        if not self.streaming:
            raise CoordinatorError("A non-streaming response contained a status update.")
        self._ensure_task(update.task_id, update.context_id)
        assert self.task is not None
        new_state = update.status.state
        allowed = _ALLOWED_TRANSITIONS.get(self._state or -1, set())
        if new_state not in allowed:
            raise CoordinatorError(
                f"Invalid task transition: {task_state_name(self._state or 0)} -> "
                f"{task_state_name(new_state)}."
            )
        message = (
            _validated_status_text(
                update.status,
                task_id=update.task_id,
                context_id=update.context_id,
            )
            if update.status.HasField("message")
            else None
        )
        self.task.status.CopyFrom(update.status)
        self._state = new_state
        if new_state == TaskState.TASK_STATE_COMPLETED:
            _extract_packing_artifact(self.task)
        return CoordinatorEvent(
            kind="status",
            task_id=update.task_id,
            context_id=update.context_id,
            state=task_state_name(new_state),
            message=message,
        )

    def _consume_artifact(self, update: Any) -> CoordinatorEvent:
        if not self.streaming:
            raise CoordinatorError("A non-streaming response contained an artifact update.")
        self._ensure_task(update.task_id, update.context_id)
        assert self.task is not None
        if self._state != TaskState.TASK_STATE_WORKING:
            raise CoordinatorError("Agent returned an artifact outside WORKING state.")
        if update.append or not update.last_chunk:
            raise CoordinatorError("Packing artifact must be sent once as a complete artifact.")
        if self.task.artifacts:
            raise CoordinatorError("Agent returned more than one packing artifact.")
        _validate_artifact(update.artifact)
        self.task.artifacts.add().CopyFrom(update.artifact)
        return CoordinatorEvent(
            kind="artifact",
            task_id=update.task_id,
            context_id=update.context_id,
            artifact_name=update.artifact.name,
        )

    def _ensure_task(self, task_id: str, context_id: str) -> None:
        self._validate_ids(task_id, context_id)
        if self.task is None:
            if self.expected_task_id is None:
                raise CoordinatorError("A task update arrived before the initial Task.")
            self.task = Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
            )

    def _validate_ids(self, task_id: str, context_id: str) -> None:
        if not task_id or not context_id:
            raise CoordinatorError("Agent returned an event without task/context identifiers.")
        if self.expected_task_id is not None and task_id != self.expected_task_id:
            raise CoordinatorError("Agent changed the task ID during continuation.")
        if self.expected_context_id is not None and context_id != self.expected_context_id:
            raise CoordinatorError("Agent changed the context ID during continuation.")
        if self.task is not None and (
            task_id != self.task.id or context_id != self.task.context_id
        ):
            raise CoordinatorError("Agent changed task/context identifiers within a stream.")

    def finish(self) -> PlanOutcome:
        if self.task is None or self._payload_count == 0:
            raise CoordinatorError("Agent returned no task result.")
        state = self.task.status.state
        if state not in {
            TaskState.TASK_STATE_COMPLETED,
            TaskState.TASK_STATE_FAILED,
            TaskState.TASK_STATE_REJECTED,
            TaskState.TASK_STATE_INPUT_REQUIRED,
        }:
            raise CoordinatorError(f"Agent stopped in non-final state {task_state_name(state)}.")
        if state == TaskState.TASK_STATE_COMPLETED:
            plan, markdown = _extract_packing_artifact(self.task)
            return PlanOutcome(self.task, tuple(self.events), plan, markdown)
        if self.task.artifacts:
            raise CoordinatorError("A non-completed task unexpectedly contained artifacts.")
        return PlanOutcome(self.task, tuple(self.events))


def _validated_status_text(
    status: TaskStatus,
    *,
    task_id: str,
    context_id: str,
) -> str:
    message = status.message
    if message.role != Role.ROLE_AGENT:
        raise CoordinatorError("Task status message must use the agent role.")
    if not message.message_id:
        raise CoordinatorError("Task status message is missing its message ID.")
    if message.task_id != task_id or message.context_id != context_id:
        raise CoordinatorError("Task status message identity does not match its task event.")
    if len(message.parts) != 1:
        raise CoordinatorError("Task status message must contain exactly one text part.")
    part = message.parts[0]
    if (
        not part.HasField("text")
        or part.media_type != TEXT_MEDIA_TYPE
        or not part.text
        or part.text != part.text.strip()
        or not part.text.isprintable()
    ):
        raise CoordinatorError("Task status message must be non-empty text/plain.")
    return part.text


def _extract_packing_artifact(task: Task) -> tuple[PackingPlan, str]:
    if len(task.artifacts) != 1:
        raise CoordinatorError("Completed task must contain exactly one packing-plan artifact.")
    return _validate_artifact(task.artifacts[0])


def _validate_artifact(artifact: Artifact) -> tuple[PackingPlan, str]:
    if artifact.name != PACKING_ARTIFACT_NAME or not artifact.artifact_id:
        raise CoordinatorError("Agent returned an invalid packing artifact identity.")
    if len(artifact.parts) != 2:
        raise CoordinatorError("Packing artifact must contain JSON followed by Markdown.")
    data_part, markdown_part = artifact.parts
    if not data_part.HasField("data") or data_part.media_type != JSON_MEDIA_TYPE:
        raise CoordinatorError("Packing artifact's first part must be application/json data.")
    if (
        not markdown_part.HasField("text")
        or markdown_part.media_type != MARKDOWN_MEDIA_TYPE
        or not markdown_part.text.strip()
    ):
        raise CoordinatorError("Packing artifact's second part must be non-empty text/markdown.")

    values = get_data_parts([data_part])
    if len(values) != 1 or not isinstance(values[0], Mapping):
        raise CoordinatorError("Packing artifact JSON must contain an object.")
    payload = _restore_integer_fields(dict(values[0]))
    try:
        # Strict Pydantic models intentionally reject Python strings for date
        # fields, while strict JSON validation accepts their ISO-8601 wire form.
        plan = PackingPlan.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        raise CoordinatorError("Packing artifact JSON does not match PackingPlan.") from exc
    expected_markdown = plan.to_markdown()
    if markdown_part.text != expected_markdown:
        raise CoordinatorError(
            "Packing artifact Markdown does not match the validated PackingPlan JSON."
        )
    return plan, markdown_part.text


def _restore_integer_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Restore integer fields lost through protobuf Value's double representation."""

    daily = payload.get("daily_summary")
    if not isinstance(daily, list):
        return payload
    for item in daily:
        if not isinstance(item, dict):
            continue
        for field in ("weather_code", "precipitation_probability"):
            value = item.get(field)
            if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
                item[field] = int(value)
    return payload
