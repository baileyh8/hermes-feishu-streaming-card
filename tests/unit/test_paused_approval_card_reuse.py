"""A timed-out approval refreshes its own card — it must not post a second paused card.

The paused notice used to be a brand-new message, so the user looked at the same approval twice:
the pending card with its option buttons AND a copy of it with "查看并继续审批" (#314). The card the
approval already lives on is the one to refresh; only an approval whose card was never recorded
delivered keeps the standalone send (that path must stay working — it is the recovery path for
interactions whose card delivery was lost).
"""
from __future__ import annotations

import time

import pytest

from hermes_feishu_card import server
from hermes_feishu_card.session import CardSession, InteractionOption, InteractionState

SESSION_KEY = "om_1"


def _paused_session(*, card_id: str) -> CardSession:
    session = CardSession(conversation_id="oc_1", message_id=SESSION_KEY, chat_id="oc_1")
    session.active_interaction = InteractionState(
        interaction_id="approval_1",
        kind="approval",
        prompt="需要授权后继续执行",
        description="**完整命令**\nrm -rf /tmp/probe",
        status="paused",
        options=[InteractionOption(label="允许一次", value="once")],
        pause_on_timeout=True,
        pause_generation=1,  # the pause that is being announced
        pause_notified_generation=0,
        feishu_message_id=card_id,
    )
    return session


async def _run_scheduled(app):
    for task in list(app[server.PAUSED_APPROVAL_TASKS_KEY]):
        await task


@pytest.fixture
def delivery_spies(monkeypatch):
    edits: list[str] = []
    sends: list[str] = []

    async def _edit(_app, message_id, _card, _bot_id, **_kwargs):
        edits.append(message_id)
        return True

    async def _send(_app, chat_id, _card, _bot_id, **_kwargs):
        sends.append(chat_id)
        return server.CardDeliveryResult(message_id="om_new", outcome="delivered")

    monkeypatch.setattr(server, "_update_card_for_app", _edit)
    monkeypatch.setattr(server, "_send_card_for_app", _send)
    return edits, sends


@pytest.mark.asyncio
async def test_paused_notice_refreshes_the_approval_card(delivery_spies):
    edits, sends = delivery_spies
    app = server.create_app(object())
    session = _paused_session(card_id="om_approval")
    app[server.SESSIONS_KEY][SESSION_KEY] = session

    server._schedule_paused_approval_card(app, SESSION_KEY, session, session.active_interaction)
    await _run_scheduled(app)

    assert edits == ["om_approval"]  # refreshed where the approval already is
    assert sends == []  # ...so the user does not get a second paused card


@pytest.mark.asyncio
async def test_paused_notice_still_sends_when_no_card_was_recorded(delivery_spies):
    edits, sends = delivery_spies
    app = server.create_app(object())
    session = _paused_session(card_id="")
    app[server.SESSIONS_KEY][SESSION_KEY] = session

    server._schedule_paused_approval_card(app, SESSION_KEY, session, session.active_interaction)
    await _run_scheduled(app)

    assert edits == []
    assert sends == ["oc_1"]


@pytest.mark.asyncio
async def test_failed_refresh_retries_rather_than_sending_a_second_card(monkeypatch):
    """A transient outage on the edit must not turn into the duplicate card we are removing."""
    app = server.create_app(object())
    session = _paused_session(card_id="om_approval")
    interaction = session.active_interaction
    app[server.SESSIONS_KEY][SESSION_KEY] = session
    sends: list[str] = []

    async def _failing_edit(*_args, **_kwargs):
        return False

    async def _send(_app, chat_id, *_args, **_kwargs):
        sends.append(chat_id)
        return server.CardDeliveryResult(message_id="om_new", outcome="delivered")

    monkeypatch.setattr(server, "_update_card_for_app", _failing_edit)
    monkeypatch.setattr(server, "_send_card_for_app", _send)

    server._schedule_paused_approval_card(app, SESSION_KEY, session, interaction)
    await _run_scheduled(app)

    assert sends == []
    assert interaction.pause_retry_after > time.time()
    assert interaction.pause_notified_generation < interaction.pause_generation
