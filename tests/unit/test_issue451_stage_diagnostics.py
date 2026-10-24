"""Bounded stage diagnostics for #451; fixed categories only."""

from __future__ import annotations

import asyncio
import builtins
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime as h
from hermes_feishu_card import admission_completion as ac
from hermes_feishu_card import completion_diagnostics as diag


def _config(enabled: bool = True):
    return SimpleNamespace(enabled=enabled, event_url="http://127.0.0.1:1/events", timeout_seconds=0.2)


def test_stage_diagnostic_is_fixed_and_rate_limited(monkeypatch):
    warnings: list[str] = []
    ticks = iter((100.0, 101.0, 200.0))
    diag.reset()
    monkeypatch.setattr(diag, "_emit", lambda message, **_kwargs: warnings.append(message))
    monkeypatch.setattr(diag.time, "monotonic", lambda: next(ticks))

    diag.stage_skipped("config-disabled")
    diag.stage_skipped("config-disabled")
    diag.stage_skipped("unknown-private-id")
    diag.stage_skipped("config-disabled")

    assert warnings == [
        "admission completion stage skipped: config-disabled",
        "admission completion stage skipped: config-disabled",
    ]


def test_attempt_diagnostic_is_once_per_offer_and_requires_offer(monkeypatch):
    diag.reset()
    warnings: list[str] = []
    monkeypatch.setattr(diag, "_emit", lambda message, **_kwargs: warnings.append(message))
    offer = object.__new__(ac.Completion)
    offer.diagnostic_emitted = False

    ac._warn_attempt(offer, "ack-not-applied")
    ac._warn_attempt(offer, "ack-transport-error")
    ac.diagnose_attempt({}, "consume-route-mismatch")

    assert warnings == ["admission completion attempt: ack-not-applied"]


def test_attempt_categories_are_independently_rate_limited(monkeypatch):
    emitted = []
    ticks = iter((100.0, 101.0, 102.0, 160.0))
    diag.reset()
    monkeypatch.setattr(diag.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(diag, "_emit", lambda message, **kwargs: emitted.append(message))
    diag.attempt_failed("ack-not-applied")
    diag.attempt_failed("ack-not-applied")
    diag.attempt_failed("ack-transport-error")
    diag.attempt_failed("unknown-private-id")
    diag.attempt_failed("ack-not-applied")
    assert emitted == [
        "admission completion attempt: ack-not-applied",
        "admission completion attempt: ack-transport-error",
        "admission completion attempt: ack-not-applied",
    ]


def test_emit_uses_logger_once_and_only_falls_back_when_logger_fails(monkeypatch):
    logged: list[str] = []
    printed: list[str] = []
    monkeypatch.setattr(diag.logger, "warning", lambda fmt, message: logged.append(message))
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: printed.append(args[0]))

    diag._emit("one line")
    assert logged == ["one line"]
    assert printed == []

    def broken_warning(*args, **kwargs):
        raise RuntimeError("handler failed")

    monkeypatch.setattr(diag.logger, "warning", broken_warning)
    diag._emit("fallback line")
    assert printed == ["[hermes-feishu-card] fallback line"]

    logged.clear()
    printed.clear()
    monkeypatch.setattr(diag.logger, "debug", broken_warning)
    diag._emit("debug line", debug=True)
    assert printed == []


def test_runtime_warning_does_not_duplicate_successful_log(monkeypatch):
    printed = []
    logged = []
    monkeypatch.setattr(h.logger, "warning", lambda fmt, message: logged.append(message))
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: printed.append(args[0]))
    h._hfc_warn("fixed category")
    assert logged == ["fixed category"]
    assert printed == []


def test_diagnostics_fail_best_effort_without_changing_delivery(monkeypatch):
    diag.reset()
    diag.stage_skipped([])  # unhashable reason
    monkeypatch.setattr(diag.time, "monotonic", lambda: (_ for _ in ()).throw(RuntimeError()))
    diag.stage_skipped("exception")
    diag.attempt_failed({})
    monkeypatch.setattr(diag.logger, "warning", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(builtins, "print", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    diag._emit("must not raise")


@pytest.mark.asyncio
async def test_stage_preconditions_emit_fixed_categories(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(diag, "_emit", lambda message, **_kwargs: warnings.append(message))
    monkeypatch.setattr(h, "_ensure_runtime_control_started", lambda *_: None)

    async def call(values=None):
        return await h.stage_message_completed_from_hermes_locals_async(values or {})

    h.reset_runtime_state()
    monkeypatch.setattr(h, "load_runtime_config", lambda: _config(False))
    assert not await call()
    assert warnings[-1].endswith("config-disabled")

    h.reset_runtime_state()
    monkeypatch.setattr(h, "load_runtime_config", lambda: _config())
    monkeypatch.setattr(h, "_policy_identity", lambda *_: None)
    assert not await call()
    assert warnings[-1].endswith("policy-identity-unavailable")

    h.reset_runtime_state()
    monkeypatch.setattr(h, "_policy_identity", lambda *_: object())
    monkeypatch.setattr(h, "_policy_async_event_lock", lambda _: asyncio.Lock())

    async def native_gate(*_):
        return h._PolicyGateResult(False, None)

    monkeypatch.setattr(h, "_policy_gate_async", native_gate)
    assert not await call()
    assert warnings[-1].endswith("policy-native")

    h.reset_runtime_state()

    async def card_gate(*_):
        return h._PolicyGateResult(True, None)

    async def no_pending(*_):
        return None

    monkeypatch.setattr(h, "_policy_gate_async", card_gate)
    monkeypatch.setattr(h, "_flush_pending_deltas_for_local_vars", no_pending)
    monkeypatch.setattr(h, "build_event", lambda *a, **k: None)
    assert not await call()
    assert warnings[-1].endswith("payload-unavailable")

    h.reset_runtime_state()

    def broken_config():
        raise RuntimeError("private failure detail")

    monkeypatch.setattr(h, "load_runtime_config", broken_config)
    assert not await call()
    assert warnings[-1].endswith("exception")
    assert "private failure detail" not in "\n".join(warnings)
