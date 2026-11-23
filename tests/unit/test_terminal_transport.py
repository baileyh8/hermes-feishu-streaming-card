"""Terminal adapter ownership checks, with no HTTP or application runtime."""
from types import SimpleNamespace

import pytest

from hermes_feishu_card import server
from hermes_feishu_card.terminal_delivery import (
    TerminalDeliveryPlan, TerminalTarget, deliver_terminal,
)


@pytest.mark.parametrize("disposition", ["card", "native"])
async def test_backoff_generation_change_must_not_patch_obsolete_owner(monkeypatch, disposition):
    owner = SimpleNamespace(terminal_delivery_state="pending", generation=1)
    accepted_generation = owner.generation
    calls, obsolete_writes = [], []

    async def simulated_update(app, message_id, card, bot_id, *, is_current=None, **kwargs):
        calls.append((message_id, is_current, owner.generation))
        if is_current is not None and not is_current():
            return False
        if len(calls) == 1:
            return False
        if owner.generation != accepted_generation:
            obsolete_writes.append(message_id)
        return True

    async def advance_generation_during_backoff(delay):
        owner.generation = 2

    class Transport(server._AppTerminalTransport):
        async def recover(self, plan):
            pytest.fail("Superseded owner must not start recovery")

    monkeypatch.setattr(server, "_update_card_for_app", simulated_update)
    monkeypatch.setattr(server.asyncio, "sleep", advance_generation_during_backoff)
    plan = TerminalDeliveryPlan(
        TerminalTarget("synthetic-turn", "om_synthetic", "oc_synthetic", None,
                       None, None, False, None, "message.completed", 2),
        disposition, {"answer": "accepted final answer"},
    )
    result = await deliver_terminal(
        plan, owner, Transport({}, owner, replay=False),
        is_current=lambda: owner.generation == accepted_generation,
    )
    assert obsolete_writes == []
    assert len(calls) == 1
    assert calls[0][1] is not None
    assert owner.terminal_delivery_state == "pending"
    assert result.superseded and not result.updated and not result.commit_native


async def test_retry_rechecks_owner_after_failed_network_update(monkeypatch):
    owner = SimpleNamespace(generation=1)
    accepted_generation = owner.generation
    current = lambda: owner.generation == accepted_generation
    calls, delays = [], []

    async def record_delay(delay):
        delays.append(delay)

    async def fail_and_replace_owner(app, message_id, card, bot_id, *, is_current=None):
        calls.append((message_id, is_current))
        owner.generation = 2
        return False

    monkeypatch.setattr(server, "_update_card_for_app", fail_and_replace_owner)
    monkeypatch.setattr(server.asyncio, "sleep", record_delay)
    updated = await server._retry_terminal_update({}, "om_synthetic", {}, None, current)
    assert calls == [("om_synthetic", current)]
    assert delays == [1.0]
    assert not updated


@pytest.mark.parametrize("event,notice_failures", [("system.notice", 1), ("message.completed", 0)])
async def test_terminal_notice_patch_preserves_failure_metrics(monkeypatch, event, notice_failures):
    class FailingClient:
        async def update_card_message(self, message_id, card):
            raise OSError("synthetic update failure")

    monkeypatch.setattr(server, "_client_for_bot", lambda *args: FailingClient())
    monkeypatch.setattr(server, "_restart_scope_for_message", lambda *args: None)
    monkeypatch.setattr(server, "_restart_notice_snapshot", lambda *args: None)
    metrics = server.SidecarMetrics()
    app = {server.METRICS_KEY: metrics, server.DIAGNOSTICS_KEY: {}}
    plan = TerminalDeliveryPlan(
        TerminalTarget("synthetic-turn", "om_synthetic", "oc_synthetic", None,
                       None, None, False, None, event, 2),
        "card", {},
    )
    owner = server.CardSession("conversation", "synthetic-turn", "oc_synthetic")
    transport = server._AppTerminalTransport(app, owner, replay=False)
    assert not await transport.patch(plan, is_current=lambda: True)
    assert metrics.feishu_update_failures == server.UPDATE_MAX_ATTEMPTS
    assert metrics.notice_update_failures == notice_failures
