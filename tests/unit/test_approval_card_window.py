"""An approval card must stop accepting decisions before the agent stops waiting for them.

A card whose window outlives the waiting agent is the worst shape: it still looks clickable, but no
runtime is left to receive the decision (#314 follow-up). The card window therefore derives from
the agent's own approval wait, minus a margin, so the paused/resume cycle always happens while the
waiter is alive.
"""
from __future__ import annotations

import pytest

from hermes_feishu_card import hook_runtime


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    monkeypatch.delenv("HERMES_FEISHU_CARD_INTERACTION_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("HERMES_FEISHU_CARD_ENABLED", "true")
    monkeypatch.delenv("HERMES_FEISHU_CARD_PROFILE_ID", raising=False)
    hook_runtime.reset_runtime_state()
    # Window propagation must not depend on a locally running sidecar's policy.
    # Exercise the real gate with an explicit card disposition, without network I/O.
    monkeypatch.setattr(
        hook_runtime,
        "_fetch_delivery_policy_sync",
        lambda *_args, **_kwargs: {"ok": True, "disposition": "card", "ttl_ms": 1000},
    )
    yield
    hook_runtime.reset_runtime_state()


def test_window_ends_before_the_agent_stops_waiting(monkeypatch):
    monkeypatch.setattr(hook_runtime, "_core_approval_timeout_seconds", lambda: 300.0)
    assert hook_runtime._approval_card_window(None) == (
        300.0 - hook_runtime._APPROVAL_CARD_WINDOW_MARGIN_SECONDS
    )


def test_short_agent_waits_still_get_a_usable_window(monkeypatch):
    monkeypatch.setattr(hook_runtime, "_core_approval_timeout_seconds", lambda: 45.0)
    assert hook_runtime._approval_card_window(None) == 30.0


def test_explicit_window_wins(monkeypatch):
    monkeypatch.setattr(hook_runtime, "_core_approval_timeout_seconds", lambda: 300.0)
    assert hook_runtime._approval_card_window(12.5) == 12.5


def test_env_override_wins_over_the_derived_window(monkeypatch):
    monkeypatch.setattr(hook_runtime, "_core_approval_timeout_seconds", lambda: 300.0)
    monkeypatch.setenv("HERMES_FEISHU_CARD_INTERACTION_TIMEOUT_SECONDS", "90")
    assert hook_runtime._approval_card_window(None) == 90.0


def test_core_wait_is_read_with_a_sane_fallback():
    # Runs against whichever core is installed: the value must always be a positive window.
    assert hook_runtime._core_approval_timeout_seconds() > 0


def test_approval_request_carries_the_derived_window(monkeypatch):
    """The derivation has to reach the wire, or the card keeps outliving its agent."""
    from types import SimpleNamespace

    captured = {}

    def fake_build(local_vars, **kwargs):
        captured.update(kwargs)
        return None  # short-circuits before any transport

    monkeypatch.setattr(hook_runtime, "build_interaction_event", fake_build)
    monkeypatch.setattr(hook_runtime, "_core_approval_timeout_seconds", lambda: 300.0)
    hook_runtime.request_interaction_from_hermes_locals(
        {
            "source": SimpleNamespace(platform="feishu"),
            "chat_id": "oc_abc",
            "conversation_id": "oc_abc",
            "message_id": "om_abc",
        },
        kind="approval",
        interaction_id="approval_window_test",
        prompt="approve?",
    )
    assert captured["timeout_seconds"] == (
        300.0 - hook_runtime._APPROVAL_CARD_WINDOW_MARGIN_SECONDS
    )


def test_non_approval_interactions_keep_their_own_window(monkeypatch):
    """Only approvals derive it: a picker's window is the caller's business."""
    from types import SimpleNamespace

    captured = {}

    def fake_build(local_vars, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(hook_runtime, "build_interaction_event", fake_build)
    monkeypatch.setattr(hook_runtime, "_core_approval_timeout_seconds", lambda: 300.0)
    hook_runtime.request_interaction_from_hermes_locals(
        {
            "source": SimpleNamespace(platform="feishu"),
            "chat_id": "oc_abc",
            "conversation_id": "oc_abc",
            "message_id": "om_abc",
        },
        kind="picker",
        interaction_id="picker_window_test",
        prompt="pick one",
        timeout_seconds=45.0,
    )
    assert captured["timeout_seconds"] == 45.0
