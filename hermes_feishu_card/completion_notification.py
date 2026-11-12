"""Completion-notify clock semantics as pure decisions.

Extracted from 'server.py' so the elapsed/threshold decision (an event and
state policy) lives apart from delivery I/O. time.time() appears only as a
default-argument value: callers that need determinism (tests, replay handling)
inject 'now' explicitly, so a replayed 'message.completed' re-derives the
same verdict instead of mutating one.
"""
from __future__ import annotations

import math
import time
from typing import Any


def _finite_seconds(value: Any) -> float | None:
    """Return 'value' as a finite positive float, or None when unknown.

    Booleans are rejected even though bool subclasses int: a truthy flag is
    not a timestamp. Non-numeric, non-finite and non-positive values are all
    "unknown origin", never fabricated into a measurement.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def elapsed_seconds(duration: Any, anchor: Any, now: float | None = None) -> float:
    """Seconds the turn actually took, for the min-duration gate.

    'duration' is the turn's own measurement
    ('message.completed.data.duration'). Hermes omits it on some terminal
    paths (resumed sessions, some interrupted completions) and a long turn
    with duration == 0 would otherwise be indistinguishable from a
    three-second one; the age of the session record then stands in, with
    'anchor' being CardSession.created_at.

    'now' defaults to the real clock; inject it to make replayed or
    out-of-order events deterministic. An 'anchor' newer than 'now'
    (clock skew, a re-stamped checkpoint) is an unknown origin, not negative
    time: the result clamps to 0.0 so an active gate can only hold a
    notification, never fire one off a corrupted timeline.
    """
    measured = _finite_seconds(duration)
    if measured is not None:
        return measured
    reference = _finite_seconds(anchor)
    if reference is None:
        return 0.0
    effective_now = time.time() if now is None else _finite_seconds(now)
    if effective_now is None:
        return 0.0
    return max(effective_now - reference, 0.0)


def meets_threshold(elapsed: Any, threshold: Any) -> bool:
    """Apply 'completion_notify.min_duration_seconds' (absent/0 = no gate).

    Mirrors the shipped gate: None, non-numeric, non-finite and <= 0
    thresholds disable the gate entirely, and a turn notifies when
    elapsed >= threshold. Under an active gate an unmeasurable 'elapsed'
    counts as short: an unknown duration must not impersonate "long enough
    to interrupt the user".
    """
    if threshold is None:
        return True
    try:
        gate = float(threshold)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(gate) or gate <= 0:
        return True
    measured = _finite_seconds(elapsed)
    if measured is None:
        return False
    return measured >= gate
