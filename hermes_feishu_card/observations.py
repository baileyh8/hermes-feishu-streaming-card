"""Local event timing, never model TTFT, generation speed or delivery latency."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math
from typing import Any


def _milliseconds(start: float | None, end: float | None) -> int | None:
    if start is None or end is None or end < start:
        return None
    value = (end - start) * 1000
    return int(value) if math.isfinite(value) and value <= 2**53 - 1 else None


@dataclass(repr=False)
class TurnObservation:
    """One accepted session generation; intentionally excluded from checkpoints."""
    started: float | None = None
    first_answer: float | None = None
    finished: float | None = None
    last_observed: float | None = None
    clock_valid: bool = True

    def observe(self, event: str, *, now: float, answer_changed: bool = False) -> None:
        if self.finished is not None or not self.clock_valid:
            return
        if (not math.isfinite(now) or now < 0
                or self.last_observed is not None and now < self.last_observed):
            self.clock_valid = False
            return
        if event == "message.started" and self.last_observed is None:
            self.started = now
        self.last_observed = now
        if event == "answer.delta" and answer_changed and self.first_answer is None:
            self.first_answer = now
        if event in {"message.completed", "message.failed"}:
            self.finished = now

    def snapshot(self) -> dict[str, Any]:
        if not self.clock_valid:
            return {"status": "unavailable", "reason": "invalid_monotonic_clock"}
        if self.started is None:
            return {"status": "unavailable", "reason": "start_not_observed"}
        result: dict[str, Any] = {
            "status": "complete" if self.finished is not None else "observing",
            "scope": "sidecar_accepted_events",
        }
        first = _milliseconds(self.started, self.first_answer)
        duration = _milliseconds(self.started, self.finished)
        if first is not None:
            result["first_answer_ms"] = first
        if duration is not None:
            result["turn_ms"] = duration
        return result


def timing_summary(observations: Iterable[TurnObservation], *, rate_samples: int = 0) -> dict[str, Any]:
    """Sidecar event timing; ``rate_samples`` counts cards with an exact per-invocation rate."""
    first: list[int] = []
    completed: list[int] = []
    starts = 0
    for observation in observations:
        snapshot = observation.snapshot()
        if snapshot["status"] == "unavailable":
            continue
        starts += 1
        if "first_answer_ms" in snapshot:
            first.append(snapshot["first_answer_ms"])
        if "turn_ms" in snapshot:
            completed.append(snapshot["turn_ms"])
    return {
        "scope": "sidecar_accepted_events",
        "window": "retained_session_memory",
        "clock": "monotonic",
        "started_samples": starts,
        "first_answer_samples": len(first),
        "first_answer_total_ms": sum(first),
        "completed_samples": len(completed),
        "observed_turn_total_ms": sum(completed),
        **token_rate_status(rate_samples),
    }


def token_rate_status(rate_samples: object) -> dict[str, Any]:
    if type(rate_samples) is int and rate_samples > 0:
        return {"token_rate_status": "available", "token_rate_reason": "per_invocation",
                "token_rate_samples": rate_samples}
    return {"token_rate_status": "unavailable", "token_rate_reason": "model_invocation_scope_missing"}
