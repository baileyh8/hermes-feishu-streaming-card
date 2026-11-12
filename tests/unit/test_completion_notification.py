"""Unit tests for completion_notification clock semantics.

Each test anchors a defect or review finding from PR #502: the fallback clock
was untestable (direct time.time()), checkpoint-restored sessions had no test
for unknown-origin anchors, and replayed 'message.completed' events had no
idempotence anchor. The module under test imports no 'server' or 'aiohttp';
these tests keep that contract.
"""
from __future__ import annotations

import math
import time

from hermes_feishu_card.completion_notification import elapsed_seconds, meets_threshold

TURN_START = 1000.0


def test_duration_preferred_over_anchor_age():
    # Hermes' own measurement wins even when the session record is older:
    # resumed sessions otherwise report wall-clock age as turn duration.
    assert elapsed_seconds(125.0, TURN_START, now=TURN_START + 3.0) == 125.0


def test_missing_duration_falls_back_to_anchor_age():
    # Some terminal paths omit 'duration'; the gate must not mistake a long
    # turn for a three-second one (the defect that motivated the fallback).
    assert elapsed_seconds(None, TURN_START, now=TURN_START + 42.5) == 42.5
    assert elapsed_seconds(0.0, TURN_START, now=TURN_START + 42.5) == 42.5


def test_non_positive_duration_falls_back():
    # session.apply_event coerces a missing/invalid duration to 0.0, and a
    # negative value is equally unusable as a measurement.
    assert elapsed_seconds(0, TURN_START, now=TURN_START + 10.0) == 10.0
    assert elapsed_seconds(-5.0, TURN_START, now=TURN_START + 10.0) == 10.0


def test_invalid_duration_types_fall_back():
    for bad_duration in ("90", True, float("nan"), float("inf")):
        assert elapsed_seconds(bad_duration, TURN_START, now=TURN_START + 8.0) == 8.0


def test_no_gate_thresholds_pass_everything():
    # Shipped semantics: absent/None/invalid/<=0 threshold means no gate, so
    # the default config keeps notifying on every completed turn.
    for threshold in (None, 0, 0.0, -30, float("nan"), float("inf"), "abc"):
        assert meets_threshold(3.0, threshold) is True


def test_boundary_elapsed_equal_threshold_notifies():
    assert meets_threshold(60.0, 60) is True
    assert meets_threshold(59.999, 60) is False
    assert meets_threshold(60.001, 60) is True


def test_active_gate_blocks_short_turn():
    assert meets_threshold(3.0, 60) is False
    assert meets_threshold(0.0, 60) is False


def test_replayed_completed_event_keeps_verdict():
    # The same 'message.completed' replayed moments later must not flip a
    # decision: the skip path records "sent" so a replay can never notify.
    first = meets_threshold(elapsed_seconds(0.0, TURN_START, now=TURN_START + 70), 60)
    replay = meets_threshold(elapsed_seconds(0.0, TURN_START, now=TURN_START + 70.4), 60)
    assert first is True
    assert replay is True

    short_first = meets_threshold(elapsed_seconds(0.0, TURN_START, now=TURN_START + 3), 60)
    short_replay = meets_threshold(elapsed_seconds(0.0, TURN_START, now=TURN_START + 3.4), 60)
    assert short_first is False
    assert short_replay is False


def test_out_of_order_clock_clamps_to_zero():
    # 'now' older than the anchor (clock skew, an event carrying a stale
    # timestamp): never a negative duration; under an active gate this holds
    # the notice instead of firing one off a corrupted timeline.
    assert elapsed_seconds(None, TURN_START + 10, now=TURN_START) == 0.0
    assert meets_threshold(elapsed_seconds(None, TURN_START + 10, now=TURN_START), 60) is False


def test_unknown_anchor_is_unmeasurable_not_zero_turn():
    # Checkpoint restore hands over the original created_at. Any other
    # origin (None, garbage, a future stamp) is unknown: 0.0, never fabricated.
    for bad_anchor in (None, "abc", 0, -1.0, True, float("nan"), float("inf")):
        assert elapsed_seconds(None, bad_anchor, now=TURN_START + 5.0) == 0.0


def test_restored_session_with_faithful_anchor_measures_correctly():
    # The turn ran 90s with 'duration' omitted and the checkpoint preserved
    # created_at: the restored session still clears the gate. Display
    # checkpoints restore presentation, not a fresh execution timeline.
    elapsed = elapsed_seconds(0.0, TURN_START, now=TURN_START + 90.0)
    assert elapsed == 90.0
    assert meets_threshold(elapsed, 60) is True


def test_invalid_now_is_unmeasurable():
    assert elapsed_seconds(None, TURN_START, now=float("nan")) == 0.0
    assert elapsed_seconds(None, TURN_START, now="bad") == 0.0
    assert elapsed_seconds(None, TURN_START, now=0) == 0.0


def test_injected_now_determines_result_exactly():
    # The real clock may only enter as the default value of 'now'.
    assert elapsed_seconds(None, TURN_START, now=TURN_START + 1.0) == 1.0
    assert elapsed_seconds(None, TURN_START, now=TURN_START + 300.0) == 300.0


def test_default_now_uses_real_clock():
    elapsed = elapsed_seconds(None, time.time() - 5.0)
    assert math.isfinite(elapsed)
    assert 4.0 <= elapsed < 60.0


def test_string_threshold_behaves_like_production():
    # Production config validation rejects non-numeric thresholds before the
    # gate runs, but the shipped gate coerces via float(): mirror that rather
    # than inventing a new policy here.
    assert meets_threshold(70.0, "60") is True
    assert meets_threshold(3.0, "60") is False
