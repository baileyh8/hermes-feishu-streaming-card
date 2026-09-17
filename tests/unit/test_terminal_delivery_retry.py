"""A terminal event must be retried while its POST looks lost.

A terminal event is the last write a card will ever receive, and the sidecar keeps sessions in
memory only, so an event that never lands leaves that card permanently wrong (issue 320: the event
was POSTed while the sidecar was 28s into a stop/start window, the send failed, the exception was
swallowed, and the card stayed on "执行中").

These tests pin the two halves of the contract: only failures that mean "no verdict was reached"
are repeated, and only terminal events take the retrying path.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib import error as urlerror

import pytest

from hermes_feishu_card import hook_runtime


@pytest.fixture
def fast_backoff(monkeypatch):
    """Keep the schedule's shape but not its duration (explicit: the window test reads the real
    defaults, so this must not be autouse)."""
    monkeypatch.setattr(
        hook_runtime, "TERMINAL_DELIVERY_RETRY_DELAYS", (0.01, 0.01, 0.01, 0.01, 0.01)
    )
    monkeypatch.setattr(hook_runtime, "TERMINAL_DELIVERY_RETRY_BUDGET_SECONDS", 5.0)


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True, event_url="http://sidecar.test/events", timeout_seconds=0.8
    )


PAYLOAD = {
    "schema_version": "1",
    "event": "message.completed",
    "conversation_id": "omt_probe",
    "message_id": "om_probe",
    "chat_id": "oc_probe",
    "platform": "feishu",
    "sequence": 3,
    "created_at": 0.0,
    "data": {},
}


def _failing_post(monkeypatch, errors: list[BaseException], result=None):
    """Make the transport fail with `errors[i]` per attempt, returning `result` once exhausted."""
    calls: list[int] = []

    async def post(url, payload, timeout):
        calls.append(1)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        if result is None:
            raise AssertionError("stubbed transport ran out of failures")
        return result

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", post)
    return calls


def test_a_lost_request_is_retried_until_it_lands(monkeypatch, fast_backoff):
    calls = _failing_post(
        monkeypatch,
        [urlerror.URLError("connection refused")],
        result={"ok": True, "applied": True},
    )
    got = asyncio.run(hook_runtime._post_terminal_with_retry(_config(), PAYLOAD, "message.completed"))
    assert got == {"ok": True, "applied": True}
    assert len(calls) == 2


def test_a_server_side_failure_is_retried(monkeypatch, fast_backoff):
    def boom(code):
        return urlerror.HTTPError("http://sidecar.test/events", code, "err", {}, None)

    calls = _failing_post(monkeypatch, [boom(500), boom(503)], result={"ok": True, "applied": True})
    got = asyncio.run(hook_runtime._post_terminal_with_retry(_config(), PAYLOAD, "message.failed"))
    assert got == {"ok": True, "applied": True}
    assert len(calls) == 3


def test_a_deliberate_refusal_is_not_retried(monkeypatch):
    """A 4xx is a verdict from the sidecar, so repeating it would only delay the turn."""
    error = urlerror.HTTPError("http://sidecar.test/events", 400, "bad request", {}, None)
    calls = _failing_post(monkeypatch, [error] * 9)
    with pytest.raises(urlerror.HTTPError):
        asyncio.run(hook_runtime._post_terminal_with_retry(_config(), PAYLOAD, "message.completed"))
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (urlerror.URLError("connection refused"), True),
        (urlerror.HTTPError("u", 500, "server error", {}, None), True),
        (urlerror.HTTPError("u", 503, "unavailable", {}, None), True),
        (urlerror.HTTPError("u", 400, "bad request", {}, None), False),
        (urlerror.HTTPError("u", 404, "not found", {}, None), False),
        (urlerror.HTTPError("u", 429, "too many requests", {}, None), False),
        (TimeoutError("timed out"), True),
        (OSError("connection reset"), True),
        (ValueError("not a transport failure"), False),
    ],
    # Explicit ids, not just for readability: pytest derives a parametrize id by calling
    # `getattr(value, "__name__")`, and `urllib.error.HTTPError` FORWARDS unknown attributes to its
    # `fp`. On Python 3.9 that forwarded lookup raises KeyError('file') instead of the AttributeError
    # pytest expects, which aborts COLLECTION of this whole file — the 3.9 CI matrix entry died with
    # `KeyError: 'file'` while every other interpreter was green. Passing ids skips id derivation
    # entirely. Same reason upstream's own HTTPError parametrize carries `ids=[...]`.
    ids=[
        "url-error",
        "http-500",
        "http-503",
        "http-400",
        "http-404",
        "http-429",
        "timeout",
        "oserror",
        "value-error",
    ],
)
def test_retry_classification_is_about_whether_a_verdict_was_reached(error, retryable):
    assert hook_runtime._terminal_delivery_retryable(error) is retryable


def test_retries_are_bounded_by_the_schedule(monkeypatch, fast_backoff):
    """A sidecar that never returns must not keep the turn waiting forever."""
    error = urlerror.URLError("connection refused")
    calls = _failing_post(monkeypatch, [error] * 50)
    with pytest.raises(urlerror.URLError):
        asyncio.run(hook_runtime._post_terminal_with_retry(_config(), PAYLOAD, "message.completed"))
    assert len(calls) == len(hook_runtime.TERMINAL_DELIVERY_RETRY_DELAYS) + 1


def test_the_total_budget_caps_the_wait(monkeypatch):
    monkeypatch.setattr(hook_runtime, "TERMINAL_DELIVERY_RETRY_DELAYS", (30.0,) * 8)
    monkeypatch.setattr(hook_runtime, "TERMINAL_DELIVERY_RETRY_BUDGET_SECONDS", 0.05)
    error = urlerror.URLError("connection refused")
    calls = _failing_post(monkeypatch, [error] * 50)
    with pytest.raises(urlerror.URLError):
        asyncio.run(hook_runtime._post_terminal_with_retry(_config(), PAYLOAD, "message.completed"))
    assert len(calls) == 2


def test_the_window_covers_a_sidecar_restart():
    """The schedule must outlast a real stop/start cycle, not just the 28s one incident hit.

    Measured live: `stop` to a ready /health is 34-42s (the start half alone spends ~30s on
    integrity checks), so a window sized to that single 28s incident would give up while the
    sidecar was still starting.
    """
    assert sum(hook_runtime.TERMINAL_DELIVERY_RETRY_DELAYS) >= 60.0
    assert (
        hook_runtime.TERMINAL_DELIVERY_RETRY_BUDGET_SECONDS
        > sum(hook_runtime.TERMINAL_DELIVERY_RETRY_DELAYS)
    )


def _stub_emission(monkeypatch):
    """Patch the policy layer so the real emit function can run without a sidecar."""
    seen: dict[str, int] = {"terminal": 0, "single": 0}

    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: _config())
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda config: None)
    monkeypatch.setattr(hook_runtime, "_policy_identity", lambda *a, **k: "ident")
    monkeypatch.setattr(hook_runtime, "_policy_async_event_lock", lambda key: asyncio.Lock())

    async def gate(config, local_vars, event_name):
        return SimpleNamespace(card=True)

    async def flush(local_vars):
        return None

    monkeypatch.setattr(hook_runtime, "_policy_gate_async", gate)
    monkeypatch.setattr(hook_runtime, "_policy_event_locals", lambda local_vars, gate: local_vars)
    monkeypatch.setattr(hook_runtime, "_flush_pending_deltas_for_local_vars", flush)
    monkeypatch.setattr(hook_runtime, "build_event", lambda name, local_vars: dict(PAYLOAD))
    monkeypatch.setattr(hook_runtime, "_register_native_handoff_descriptor", lambda *a, **k: None)
    monkeypatch.setattr(hook_runtime, "_event_was_applied", lambda result, strict=False: True)
    monkeypatch.setattr(
        hook_runtime, "_register_native_media_text_suppression", lambda *a, **k: None
    )

    async def terminal_with_retry(config, payload, event_name):
        seen["terminal"] += 1
        return {"ok": True, "applied": True}

    async def single_shot(url, payload, timeout):
        seen["single"] += 1
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_terminal_with_retry", terminal_with_retry)
    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", single_shot)
    return seen


@pytest.mark.parametrize("event_name", ["message.completed", "message.failed"])
def test_terminal_events_take_the_retrying_path(monkeypatch, event_name):
    seen = _stub_emission(monkeypatch)
    asyncio.run(hook_runtime.emit_from_hermes_locals_async({}, event_name=event_name))
    assert seen == {"terminal": 1, "single": 0}


def test_non_terminal_events_keep_the_single_shot_path(monkeypatch):
    """Only terminal events can strand a card, so nothing else should pay for a retry."""
    seen = _stub_emission(monkeypatch)
    asyncio.run(hook_runtime.emit_from_hermes_locals_async({}, event_name="answer.delta"))
    assert seen == {"terminal": 0, "single": 1}


def _log_text(caplog) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


def test_a_retry_is_visible_in_the_log(monkeypatch, fast_backoff, caplog):
    """A retry means a card was about to be stranded, so each attempt and the recovery are logged.

    Without this the outage is indistinguishable from a healthy run: the event lands either way.
    """
    _failing_post(
        monkeypatch,
        [urlerror.URLError("connection refused"), urlerror.URLError("connection refused")],
        result={"ok": True, "applied": True},
    )
    with caplog.at_level("WARNING"):
        asyncio.run(
            hook_runtime._post_terminal_with_retry(_config(), PAYLOAD, "message.completed")
        )
    text = _log_text(caplog)
    schedule = len(hook_runtime.TERMINAL_DELIVERY_RETRY_DELAYS)
    assert "message.completed om_probe: not delivered" in text
    assert f"retry 1/{schedule}" in text
    assert f"retry 2/{schedule}" in text
    assert "delivered after 2 retries" in text
    assert "stays unfinished" not in text


def test_giving_up_names_the_stranded_event(monkeypatch, caplog):
    """When the retries run out the log has to say which card was left behind."""
    monkeypatch.setattr(hook_runtime, "TERMINAL_DELIVERY_RETRY_DELAYS", (0.01, 0.01))
    monkeypatch.setattr(hook_runtime, "TERMINAL_DELIVERY_RETRY_BUDGET_SECONDS", 5.0)
    _failing_post(monkeypatch, [urlerror.URLError("connection refused")] * 6)
    with caplog.at_level("WARNING"):
        with pytest.raises(urlerror.URLError):
            asyncio.run(
                hook_runtime._post_terminal_with_retry(_config(), PAYLOAD, "message.failed")
            )
    text = _log_text(caplog)
    assert "message.failed om_probe: gave up after 2 retries" in text
    assert "the card stays unfinished" in text


def test_a_refusal_is_not_announced_as_a_retry(monkeypatch, caplog):
    """A 4xx never retried, so it must not look like an outage that was fought through."""
    error = urlerror.HTTPError("http://sidecar.test/events", 400, "bad request", {}, None)
    _failing_post(monkeypatch, [error] * 2)
    with caplog.at_level("WARNING"):
        with pytest.raises(urlerror.HTTPError):
            asyncio.run(
                hook_runtime._post_terminal_with_retry(_config(), PAYLOAD, "message.completed")
            )
    assert "retry" not in _log_text(caplog)
