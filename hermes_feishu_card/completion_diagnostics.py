"""Fixed, bounded diagnostics for admission-completion handoff.

This module owns log categories and throttling only. It has no execution,
authorization, claim, or delivery state.
"""
from __future__ import annotations

import logging
import sys
import threading
import time


logger = logging.getLogger(__name__)
STAGE_INTERVAL_SECONDS = 60.0

STAGE_REASONS = frozenset({
    "base-hook-unavailable",
    "config-disabled",
    "empty-answer",
    "already-delivered",
    "policy-identity-unavailable",
    "policy-native",
    "payload-unavailable",
    "exception",
})
ATTEMPT_REASONS = frozenset({
    "ack-base-seam-unverified",
    "ack-not-applied",
    "ack-not-capable",
    "ack-not-exact-scope",
    "ack-stage-invalidated",
    "ack-transport-error",
    "consume-adapter-mismatch",
    "consume-authority-stale",
    "consume-base-seam-unverified",
    "consume-expired",
    "consume-response-mismatch",
    "consume-route-mismatch",
    "consume-session-mismatch",
    "consume-source-unverified",
    "consume-state-mismatch",
    "consume-target-mismatch",
    "deliver-native-error",
})

_NORMAL_STAGE_REASONS = frozenset({
    "config-disabled",
    "empty-answer",
    "already-delivered",
    "policy-native",
})
_NORMAL_ATTEMPT_REASONS = frozenset({"ack-not-capable", "ack-not-exact-scope"})
_last_stage: dict[str, float] = {}
_last_attempt: dict[str, float] = {}
_lock = threading.Lock()


def _emit(message: str, *, debug: bool = False) -> None:
    if debug:
        try:
            logger.debug("[hermes-feishu-card] %s", message)
        except Exception:
            pass
        return
    try:
        logger.warning("[hermes-feishu-card] %s", message)
        return
    except Exception:
        pass
    try:
        print(f"[hermes-feishu-card] {message}", file=sys.stderr)
    except Exception:
        pass


def stage_skipped(reason: str) -> None:
    """Emit at most one fixed stage category per minute."""
    try:
        if reason not in STAGE_REASONS:
            return
        now = time.monotonic()
        with _lock:
            previous = _last_stage.get(reason)
            if previous is not None and now - previous < STAGE_INTERVAL_SECONDS:
                return
            _last_stage[reason] = now
        _emit(
            f"admission completion stage skipped: {reason}",
            debug=reason in _NORMAL_STAGE_REASONS,
        )
    except Exception:
        return


def attempt_failed(reason: str) -> None:
    """Rate-limit fixed categories; callers also limit each admission attempt."""
    try:
        if reason in ATTEMPT_REASONS:
            now = time.monotonic()
            with _lock:
                previous = _last_attempt.get(reason)
                if previous is not None and now - previous < STAGE_INTERVAL_SECONDS:
                    return
                _last_attempt[reason] = now
            _emit(
                f"admission completion attempt: {reason}",
                debug=reason in _NORMAL_ATTEMPT_REASONS,
            )
    except Exception:
        return


def reset() -> None:
    with _lock:
        _last_stage.clear()
        _last_attempt.clear()
