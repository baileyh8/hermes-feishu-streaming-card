"""An approval card must stay usable and auditable.

Two invariants for an approval that can no longer take a decision (window closed, expired):
every option leads back to a fresh card instead of a dead button, and the card body keeps the
question, the exact command and the outcome together so the decision can be audited afterwards.
"""
import time
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.render import render_card, render_legacy_interaction_callback_card
from hermes_feishu_card.session import CardSession, InteractionOption, InteractionState

COMMAND = "rm -rf /tmp/hfc-real-approval-test"


def _approval(status="pending", *, requested_at=0.0, kind="approval"):
    return InteractionState(
        interaction_id="i",
        kind=kind,
        prompt="需要授权后继续执行",
        description=f"**完整命令**\n{COMMAND}",
        status=status,
        error="交互已过期" if status == "failed" else "",
        choice="once" if status == "completed" else "",
        choice_label="允许一次" if status == "completed" else "",
        user_name="牟勇" if status == "completed" else "",
        timeout_seconds=300.0,
        requested_at=requested_at,
        options=[
            InteractionOption(label="允许一次", value="once"),
            InteractionOption(label="拒绝", value="deny"),
        ],
    )


@pytest.mark.parametrize(
    "status,requested_at,expected",
    [
        ("pending", 0.0, True),  # the window closed while the card was still on screen
        ("paused", 0.0, True),
        ("failed", 0.0, True),
        ("completed", 0.0, False),  # a taken decision must not be re-asked by a stray click
    ],
)
def test_only_an_approval_that_can_no_longer_decide_is_reissued(status, requested_at, expected):
    from hermes_feishu_card.server import _approval_card_needs_reissue

    assert _approval_card_needs_reissue(_approval(status, requested_at=requested_at)) is expected


def test_a_live_pending_approval_decides_normally_and_a_clarify_never_reissues():
    from hermes_feishu_card.server import _approval_card_needs_reissue

    assert _approval_card_needs_reissue(_approval("pending", requested_at=time.time())) is False
    assert _approval_card_needs_reissue(_approval("paused", kind="clarify")) is False


@pytest.mark.parametrize(
    "status,outcome",
    [
        ("completed", "已选择：允许一次"),
        ("failed", "交互已过期"),
        ("paused", "审批窗口已过期"),
    ],
)
def test_approval_card_keeps_question_command_and_outcome_together(status, outcome):
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.active_interaction = _approval(status)
    if status == "paused":
        session.active_interaction.error = "审批窗口已过期，任务已暂停。请查看完整操作后继续审批。"

    text = str(render_legacy_interaction_callback_card(session, title="Hermes Agent"))

    assert "需要授权后继续执行" in text  # what was asked
    assert COMMAND in text  # what would run — kept after the decision, not swapped out
    assert "1. 允许一次" in text  # the options it offered
    assert outcome in text  # the outcome, appended below them


def test_streaming_session_card_keeps_the_command_after_the_decision():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.active_interaction = _approval("completed")

    text = str(render_card(session))

    assert COMMAND in text
    assert "已选择：允许一次" in text


def test_sidecar_verdict_is_used_instead_of_a_generic_guess():
    assert hook_runtime._hfc_sidecar_notice(
        {
            "ok": False,
            "status": "failed",
            "error": "interaction expired",
            "toast": {"type": "warning", "content": "交互已过期"},
            "card": {"elements": []},
        }
    ) == ("交互已过期", "warning")
    assert hook_runtime._hfc_sidecar_notice(
        {"ok": False, "error": "interaction already completed"}
    ) == ("该选择已处理，请查看最新卡片。", "warning")
    assert hook_runtime._hfc_sidecar_notice(
        {"ok": True, "toast": {"type": "info", "content": "审批已过期，请重新审批"}}
    ) == ("审批已过期，请重新审批", "info")
    assert hook_runtime._hfc_sidecar_notice({"ok": True, "card": {"elements": []}}) is None
    assert hook_runtime._hfc_sidecar_notice(None) is None


def test_expired_click_repaints_the_card_together_with_the_sidecar_message(monkeypatch):
    """The field report: the card never changed and every click answered the same vague line."""

    class FakeP2Response:
        def __init__(self):
            self.card = None
            self.toast = None

    class FakeCard:
        def __init__(self):
            self.type = None
            self.data = None

    class FakeToast:
        pass

    class DummyFeishuAdapter:
        pass

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False)
    monkeypatch.setattr(hook_runtime, "CallBackCard", FakeCard, raising=False)
    monkeypatch.setattr(hook_runtime, "CallBackToast", FakeToast, raising=False)
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: SimpleNamespace(event_url="http://127.0.0.1:8765/events"),
    )
    repainted = {"elements": [{"tag": "markdown", "content": "窗口已过期"}]}
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_sync_response",
        lambda url, payload, timeout: {
            "ok": False,
            "status": "failed",
            "error": "interaction expired",
            "toast": {"type": "warning", "content": "交互已过期"},
            "card": repainted,
        },
    )
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "interaction.select",
                    "interaction_id": "int-1",
                    "choice": "once",
                    "choice_label": "允许一次",
                    "token": "tok-1",
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user"),
        )
    )

    response = hook_runtime._hfc_on_feishu_card_action_trigger(DummyFeishuAdapter(), data)

    assert response.card is not None and response.card.data == repainted
    assert response.toast.content == "交互已过期"


@pytest.mark.parametrize(
    "command,secret,kept",
    [
        ("mysql --password=hunter2 -h db.internal", "hunter2", "--password="),
        ("API_KEY=abc123 ./deploy.sh prod", "abc123", "./deploy.sh prod"),
        ("PGPASSWORD='s3cr3t pass' psql -c 'select 1'", "s3cr3t pass", "psql -c 'select 1'"),
        (
            'curl -H "Authorization: Bearer ghp_abcdefghijklmnopqrst" https://api.internal',
            "ghp_abcdefghijklmnopqrst",
            "https://api.internal",
        ),
        ("docker login -u bot https://bot:s3cret@registry.internal", "s3cret", "registry.internal"),
        ('curl "https://x.internal/api?token=abc123&page=2"', "abc123", "page=2"),
        ("deploy --api-key sk-live-abcdefghijklmnop", "sk-live-abcdefghijklmnop", "deploy"),
    ],
)
def test_the_scope_is_masked_but_the_command_stays_identifiable(command, secret, kept):
    """Retaining the command must not put a credential into a group-visible card."""
    from hermes_feishu_card.render import mask_approval_scope

    masked = mask_approval_scope(command)

    assert secret not in masked
    assert "[REDACTED]" in masked
    assert kept in masked  # the command and its flags remain readable: that is the audit value
    assert mask_approval_scope(masked) == masked  # idempotent


def test_a_decided_approval_keeps_the_scope_with_credentials_masked():
    """Both halves at once: auditable afterwards, and no secret left in the chat history."""
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    interaction = _approval("completed")
    interaction.description = "**完整命令**\nAPI_KEY=abc123 ./deploy.sh prod"
    session.active_interaction = interaction

    text = str(render_card(session))

    assert "需要授权后继续执行" in text  # 1. what was asked
    assert "已选择：允许一次" in text  # 2. what the user chose
    assert "./deploy.sh prod" in text  # 3. what would run, still identifiable
    assert "[REDACTED]" in text
    assert "abc123" not in text


def test_a_pending_approval_is_masked_too():
    """The open card is readable by every chat member, so it hides the credential as well."""
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    interaction = _approval("pending")
    interaction.description = "**完整命令**\nAPI_KEY=abc123 ./deploy.sh prod"
    session.active_interaction = interaction

    text = str(render_legacy_interaction_callback_card(session, title="Hermes Agent"))

    assert "./deploy.sh prod" in text
    assert "abc123" not in text


def test_mobile_approval_explains_expand_before_full_scope_and_consent():
    session = CardSession(conversation_id='c', message_id='m', chat_id='oc')
    session.active_interaction = _approval(requested_at=time.time())
    session.active_interaction.description = '完整命令\n' + ('echo review-scope\n' * 40)
    card = render_legacy_interaction_callback_card(session)
    assert '展开仅查看内容，不会提交授权' in card['elements'][0]['content']
    assert 'echo review-scope' in str(card)
    scope_index = next(i for i,e in enumerate(card['elements']) if 'echo review-scope' in str(e))
    action_index = next(i for i,e in enumerate(card['elements']) if e['tag'] == 'column_set')
    assert scope_index < action_index
