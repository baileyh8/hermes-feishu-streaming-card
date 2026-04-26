from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict

SUPPORTED_EVENTS = {
    "message.started",
    "thinking.delta",
    "tool.updated",
    "answer.delta",
    "message.completed",
    "message.failed",
}


class EventValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SidecarEvent:
    schema_version: str
    event: str
    conversation_id: str
    message_id: str
    chat_id: str
    platform: str
    sequence: int
    created_at: float
    data: Dict[str, Any]

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SidecarEvent":
        if not isinstance(payload, dict):
            raise EventValidationError("payload must be an object")

        required = (
            "schema_version",
            "event",
            "conversation_id",
            "message_id",
            "chat_id",
            "platform",
            "sequence",
            "created_at",
            "data",
        )
        for key in required:
            if key not in payload:
                raise EventValidationError(f"missing required field: {key}")
        if payload["schema_version"] != "1":
            raise EventValidationError("unsupported schema_version")
        event = payload["event"]
        if not isinstance(event, str) or not event.strip():
            raise EventValidationError("event must be a non-empty string")
        if event not in SUPPORTED_EVENTS:
            raise EventValidationError(f"unknown event: {event}")
        if payload["platform"] != "feishu":
            raise EventValidationError("platform must be feishu")
        if (
            isinstance(payload["sequence"], bool)
            or not isinstance(payload["sequence"], int)
            or payload["sequence"] < 0
        ):
            raise EventValidationError("sequence must be a non-negative integer")
        for key in ("conversation_id", "message_id", "chat_id"):
            value = payload[key]
            if not isinstance(value, str) or not value.strip():
                raise EventValidationError(f"{key} must be a non-empty string")
        try:
            created_at = float(payload["created_at"])
        except (TypeError, ValueError) as exc:
            raise EventValidationError("created_at must be a number") from exc
        if not math.isfinite(created_at):
            raise EventValidationError("created_at must be finite")
        data = payload["data"]
        if not isinstance(data, dict):
            raise EventValidationError("data must be an object")
        return cls(
            schema_version=payload["schema_version"],
            event=event,
            conversation_id=payload["conversation_id"],
            message_id=payload["message_id"],
            chat_id=payload["chat_id"],
            platform=payload["platform"],
            sequence=payload["sequence"],
            created_at=created_at,
            data=data,
        )
