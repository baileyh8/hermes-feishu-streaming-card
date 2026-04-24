from __future__ import annotations

from dataclasses import dataclass
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
        if payload["event"] not in SUPPORTED_EVENTS:
            raise EventValidationError(f"unknown event: {payload['event']}")
        if payload["platform"] != "feishu":
            raise EventValidationError("platform must be feishu")
        if not isinstance(payload["sequence"], int) or payload["sequence"] < 0:
            raise EventValidationError("sequence must be a non-negative integer")
        data = payload["data"]
        if not isinstance(data, dict):
            raise EventValidationError("data must be an object")
        return cls(
            schema_version=payload["schema_version"],
            event=payload["event"],
            conversation_id=str(payload["conversation_id"]),
            message_id=str(payload["message_id"]),
            chat_id=str(payload["chat_id"]),
            platform=payload["platform"],
            sequence=payload["sequence"],
            created_at=float(payload["created_at"]),
            data=data,
        )
