"""Translate A2A Messages and Tasks to the packing application contract."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from a2a.helpers import (
    get_data_parts,
    new_data_part,
    new_task,
    new_task_from_user_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types.a2a_pb2 import Message, Role, TaskState
from a2a.utils.errors import UnsupportedOperationError
from pydantic import ValidationError

from travel_a2a.models import PackingRequest
from travel_a2a.planner import (
    PackingPlanner,
    PlannerError,
    build_packing_plan,
)
from travel_a2a.protocol import (
    JSON_MEDIA_TYPE,
    MARKDOWN_MEDIA_TYPE,
    PACKING_ARTIFACT_NAME,
    TEXT_MEDIA_TYPE,
)
from travel_a2a.weather_service import (
    ForecastService,
    LocationNotFoundError,
    WeatherProviderError,
)

logger = logging.getLogger(__name__)


class PackingAgentExecutor(AgentExecutor):
    """Run the Packing Advisor while preserving the A2A task lifecycle."""

    def __init__(
        self,
        forecast_service: ForecastService,
        planner: PackingPlanner,
    ) -> None:
        self._forecast_service = forecast_service
        self._planner = planner

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Validate one JSON message, plan, and publish a two-part artifact."""

        message = context.message
        task_id = context.task_id
        context_id = context.context_id
        if message is None or not task_id or not context_id:
            raise ValueError("A2A request context is missing its message or identifiers")

        updater = TaskUpdater(event_queue, task_id, context_id)
        is_new_task = context.current_task is None
        if message.role != Role.ROLE_USER:
            if is_new_task:
                # new_task_from_user_message intentionally refuses non-user
                # Messages, but a task-mode rejection still needs a SUBMITTED
                # Task before its terminal status event.
                await event_queue.enqueue_event(
                    new_task(
                        task_id,
                        context_id,
                        TaskState.TASK_STATE_SUBMITTED,
                    )
                )
            await updater.reject(
                _status_message(updater, "Packing requests must be sent with the user role.")
            )
            return

        if is_new_task:
            # A task-mode stream must begin with the Task itself. The SDK has
            # already assigned the server-owned task and context identifiers to
            # the incoming Message by the time the executor runs.
            await event_queue.enqueue_event(new_task_from_user_message(message))
        # With a continued task, a2a-sdk 1.1.2 must see the first modification
        # event before another Task event so it persists the new user Message.
        # Consequently, continuations intentionally begin update-first.

        payload, error = _request_payload(message.parts)
        if error is not None:
            await updater.reject(_status_message(updater, error))
            return

        try:
            request = PackingRequest.model_validate(payload)
        except ValidationError as exc:
            await updater.reject(
                _status_message(updater, f"Invalid packing request: {_validation_summary(exc)}")
            )
            return

        if request.days is None:
            await updater.requires_input(
                _status_message(
                    updater,
                    "How many days is the trip? Please provide a whole number from 1 to 7.",
                )
            )
            return

        await updater.start_work(
            _status_message(updater, f"Checking the forecast for {request.destination}.")
        )

        try:
            forecast = await self._forecast_service.get_forecast(
                request.destination,
                request.days,
                request.units,
            )
        except LocationNotFoundError:
            await updater.requires_input(
                _status_message(
                    updater,
                    "I could not find that location. Please provide a more specific "
                    "destination, such as a city and country.",
                )
            )
            return
        except WeatherProviderError:
            logger.exception("The forecast provider failed")
            await updater.failed(
                _status_message(
                    updater,
                    "The weather service is temporarily unavailable. Please try again later.",
                )
            )
            return
        except Exception:  # noqa: BLE001 - sanitize unexpected adapter details
            logger.exception("Unexpected forecast provider failure")
            await updater.failed(
                _status_message(
                    updater,
                    "The weather service is temporarily unavailable. Please try again later.",
                )
            )
            return

        try:
            advice = await self._planner.plan(request, forecast)
            plan = build_packing_plan(request, forecast, advice)
        except (PlannerError, ValidationError, ValueError):
            logger.exception("The packing planner failed")
            await updater.failed(
                _status_message(
                    updater,
                    "The packing planner could not create a valid plan. Please try again later.",
                )
            )
            return
        except Exception:  # noqa: BLE001 - sanitize unexpected backend details at the boundary
            logger.exception("Unexpected packing planner failure")
            await updater.failed(
                _status_message(
                    updater,
                    "The packing planner is temporarily unavailable. Please try again later.",
                )
            )
            return

        await updater.add_artifact(
            [
                new_data_part(plan.model_dump(mode="json"), media_type=JSON_MEDIA_TYPE),
                new_text_part(plan.to_markdown(), media_type=MARKDOWN_MEDIA_TYPE),
            ],
            name=PACKING_ARTIFACT_NAME,
            last_chunk=True,
        )
        await updater.complete(_status_message(updater, "The weather-aware packing plan is ready."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancellation is intentionally outside this introductory example."""

        del context, event_queue
        raise UnsupportedOperationError("Task cancellation is not supported by this agent.")


def _request_payload(parts: Any) -> tuple[dict[str, Any], str | None]:
    """Return a normalized mapping from exactly one JSON data Part."""

    if len(parts) != 1:
        return {}, "Send exactly one application/json data part."

    part = parts[0]
    if not part.HasField("data") or part.media_type != JSON_MEDIA_TYPE:
        return {}, "Send exactly one application/json data part."

    values = get_data_parts(parts)
    if len(values) != 1 or not isinstance(values[0], Mapping):
        return {}, "The application/json data part must contain a JSON object."

    payload = dict(values[0])
    # google.protobuf.Value represents every JSON number as a double. Restore
    # the application's integer type only when the value is exactly integral;
    # booleans, strings, and fractional values remain invalid.
    days = payload.get("days")
    if isinstance(days, float) and not isinstance(days, bool) and days.is_integer():
        payload["days"] = int(days)
    return payload, None


def _status_message(updater: TaskUpdater, text: str) -> Message:
    return updater.new_agent_message([new_text_part(text, media_type=TEXT_MEDIA_TYPE)])


def _validation_summary(exc: ValidationError) -> str:
    """Create a concise validation error without URLs or internal representations."""

    summaries: list[str] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in error["loc"]) or "request"
        summaries.append(f"{location}: {error['msg']}")
    return "; ".join(summaries)
