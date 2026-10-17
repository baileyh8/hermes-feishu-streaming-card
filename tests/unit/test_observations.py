"""Event timings are measured only inside one accepted in-memory turn."""
import copy
import json
import math
import time
from types import SimpleNamespace

import pytest

from hermes_feishu_card import session as session_module
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.observations import TurnObservation, timing_summary
from hermes_feishu_card.session import CardSession
from hermes_feishu_card.session_store import SessionStore
from hermes_feishu_card.support_report import build_support_report, serialize_support_report


def event(kind, sequence, data=None, **overrides):
    payload = dict(schema_version="1", event=kind, sequence=sequence, conversation_id="fixture-chat", message_id="fixture-turn", chat_id="fixture-chat", platform="feishu", created_at=1777017600.0 + sequence, data=data or {})
    payload.update(overrides)
    return SidecarEvent.from_dict(payload)


@pytest.fixture
def observed_session(monkeypatch):
    clock = SimpleNamespace(now=10.0)
    monkeypatch.setattr(session_module, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock.now))
    return CardSession("fixture-chat", "fixture-turn", "fixture-chat"), clock


@pytest.mark.parametrize("models", [["model-one"], ["model-one", "model-two"]])
def test_accepted_events_include_tool_wait_but_never_infer_model_rate(observed_session, models):
    value, clock = observed_session
    assert value.apply(event("message.started", 0, {"model": models[0]}))
    clock.now = 11.0
    assert value.apply(event("answer.delta", 1, {"text": "FIRST", "model": models[0]}))
    clock.now = 12.0
    assert value.apply(event("tool.updated", 2, {"tool_id": "tool-one", "name": "terminal", "status": "running"}))
    clock.now = 102.0
    assert value.apply(event("tool.updated", 3, {"tool_id": "tool-one", "name": "terminal", "status": "completed"}))
    clock.now = 103.0
    assert value.apply(event("answer.delta", 4, {"text": "SECOND", "model": models[-1]}))
    clock.now = 110.0
    assert value.apply(event("message.completed", 5, {"answer": "FINAL", "tokens": {"input_tokens": 1000, "output_tokens": 200}, "duration": 99999, "model": models[-1]}))
    assert value.observation.snapshot() == {"status": "complete", "scope": "sidecar_accepted_events", "first_answer_ms": 1000, "turn_ms": 100000}
    summary = timing_summary([value.observation])
    assert summary["observed_turn_total_ms"] == 100000
    assert summary["first_answer_total_ms"] == 1000
    assert summary["token_rate_status"] == "unavailable"
    assert summary["token_rate_reason"] == "model_invocation_scope_missing"
    assert all(model not in json.dumps(summary) for model in models)


def test_only_visible_answer_change_starts_first_answer_clock(observed_session):
    value, clock = observed_session
    assert value.apply(event("message.started", 0))
    for sequence, text in enumerate(["", "  ", "<think>", "</think>"], 1):
        clock.now += 1
        assert value.apply(event("answer.delta", sequence, {"text": text}))
        assert "first_answer_ms" not in value.observation.snapshot()
    clock.now = 15
    assert value.apply(event("answer.delta", 5, {"text": "VISIBLE"}))
    assert value.observation.snapshot()["first_answer_ms"] == 5000


@pytest.mark.parametrize("kind,data", [("message.completed", {"answer": "NON_STREAMED"}), ("message.failed", {"error": "fixture error"})])
def test_non_streamed_terminal_has_turn_duration_without_inventing_first_delta(observed_session, kind, data):
    value, clock = observed_session
    value.apply(event("message.started", 0))
    clock.now = 12
    value.apply(event(kind, 1, data))
    assert value.observation.snapshot() == {"status": "complete", "scope": "sidecar_accepted_events", "turn_ms": 2000}


@pytest.mark.parametrize("late_start", [False, True])
def test_missing_or_late_start_remains_unavailable(observed_session, late_start):
    value, clock = observed_session
    assert value.apply(event("answer.delta", 0, {"text": "BEFORE_START"}))
    if late_start:
        clock.now += 1
        assert value.apply(event("message.started", 1))
    clock.now += 1
    assert value.apply(event("message.completed", 2, {"answer": "FINAL"}))
    assert value.observation.snapshot() == {"status": "unavailable", "reason": "start_not_observed"}
    assert timing_summary([value.observation])["started_samples"] == 0


def test_rejected_event_and_duplicate_terminal_do_not_change_observation(observed_session):
    value, clock = observed_session
    value.apply(event("message.started", 0))
    before = copy.deepcopy(value.observation)
    clock.now = -1
    assert not value.apply(event("answer.delta", 0, {"text": "duplicate"}))
    assert not value.apply(event("answer.delta", 1, {"text": "wrong"}, message_id="other-turn"))
    assert value.observation == before
    clock.now = 15
    assert value.apply(event("message.completed", 1, {"answer": "FINAL"}))
    finished = copy.deepcopy(value.observation)
    clock.now = 30
    assert not value.apply(event("message.completed", 2, {"answer": "duplicate"}))
    assert value.observation == finished
    assert timing_summary([value.observation])["completed_samples"] == 1


@pytest.mark.parametrize("bad_clock", [9.0, -1.0, math.nan, math.inf, -math.inf])
def test_invalid_or_backwards_monotonic_clock_never_creates_timing(observed_session, bad_clock):
    value, clock = observed_session
    value.apply(event("message.started", 0))
    clock.now = bad_clock
    value.apply(event("answer.delta", 1, {"text": "ANSWER"}))
    clock.now = 30
    value.apply(event("message.completed", 2, {"answer": "FINAL"}))
    assert value.observation.snapshot() == {"status": "unavailable", "reason": "invalid_monotonic_clock"}
    assert timing_summary([value.observation])["completed_samples"] == 0


def test_unrepresentable_duration_does_not_export_an_infinite_or_unsafe_number():
    observation = TurnObservation()
    observation.observe("message.started", now=0)
    observation.observe("message.completed", now=1e300)
    assert "turn_ms" not in observation.snapshot()
    assert timing_summary([observation])["completed_samples"] == 0


def test_checkpoint_restores_content_but_never_process_monotonic_measurements(observed_session, tmp_path):
    value, clock = observed_session
    value.apply(event("message.started", 0))
    clock.now = 11
    value.apply(event("answer.delta", 1, {"text": "PRESERVE_ANSWER"}))
    store = SessionStore(tmp_path)
    store.save("fixture-turn", value, "fixture-card", None, "", {}, "fixture-client")
    checkpoint = next(store.root.glob("*.json")).read_text()
    assert "observation" not in checkpoint and "first_answer_ms" not in checkpoint
    restored = store.load()[0]["session"]
    assert restored.answer_text == value.answer_text
    assert restored.observation.snapshot() == {"status": "unavailable", "reason": "start_not_observed"}
    clock.now = 100
    restored.apply(event("message.completed", 2, {"answer": "FINAL"}))
    assert restored.observation.snapshot()["reason"] == "start_not_observed"


@pytest.mark.parametrize("wrong_scope", [False, True])
def test_support_timing_allowlist_keeps_counts_without_secret_or_model_rate(wrong_scope):
    timing = {"scope": "sidecar_accepted_events", "window": "retained_session_memory", "clock": "PRIVATE_TIMING_SECRET", "started_samples": 3, "first_answer_samples": 2, "first_answer_total_ms": 1200, "completed_samples": 1, "observed_turn_total_ms": 5000, "token_rate_status": "available", "token_rate_reason": "PRIVATE_TIMING_SECRET", "tokens_per_second": 99, "prompt": "PRIVATE_TIMING_SECRET", "models": ["PRIVATE_TIMING_SECRET"]}
    if wrong_scope:
        timing["scope"] = "model_invocation"
    source = {"runtime": {"timing": timing}}
    before = copy.deepcopy(source)
    report = build_support_report(source)
    encoded = serialize_support_report(report)
    assert source == before
    assert "PRIVATE_TIMING_SECRET" not in encoded and "tokens_per_second" not in encoded
    exported = report["diagnostics"]["runtime"].get("timing")
    if wrong_scope:
        assert exported is None
    else:
        assert exported["first_answer_total_ms"] == 1200
        assert exported["token_rate_status"] == "unavailable"
        assert exported["token_rate_reason"] == "model_invocation_scope_missing"
        assert exported["clock"] == "monotonic"


@pytest.mark.parametrize("bad_count", [True, -1, "PRIVATE_TIMING_SECRET", math.inf, math.nan, 1.5])
def test_support_timing_rejects_non_integer_and_negative_counts(bad_count):
    source = {"runtime": {"timing": {"scope": "sidecar_accepted_events", "window": "retained_session_memory", "first_answer_total_ms": bad_count}}}
    report = build_support_report(source)
    assert "first_answer_total_ms" not in report["diagnostics"]["runtime"]["timing"]


def test_timing_summary_reports_per_invocation_rate_only_with_samples():
    assert timing_summary([], rate_samples=0)["token_rate_reason"] == "model_invocation_scope_missing"
    for bad in (True, -1, 1.5, "2"):
        assert timing_summary([], rate_samples=bad)["token_rate_status"] == "unavailable"
    summary = timing_summary([], rate_samples=2)
    assert summary["token_rate_status"] == "available"
    assert summary["token_rate_reason"] == "per_invocation"
    assert summary["token_rate_samples"] == 2
    report = build_support_report({"runtime": {"timing": summary}})
    exported = report["diagnostics"]["runtime"]["timing"]
    assert exported["token_rate_status"] == "available"
    assert exported["token_rate_reason"] == "per_invocation"
    assert exported["token_rate_samples"] == 2


@pytest.mark.parametrize("samples", [0, -1, True, "PRIVATE_TIMING_SECRET", None])
def test_support_timing_rejects_unproven_rate_availability(samples):
    timing = {"scope": "sidecar_accepted_events", "window": "retained_session_memory",
              "token_rate_status": "available", "token_rate_reason": "per_invocation",
              "token_rate_samples": samples}
    exported = build_support_report({"runtime": {"timing": timing}})["diagnostics"]["runtime"]["timing"]
    assert exported["token_rate_status"] == "unavailable"
    assert "token_rate_samples" not in exported
