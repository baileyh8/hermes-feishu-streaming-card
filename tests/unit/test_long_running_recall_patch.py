from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.install import patcher

HEARTBEAT = "⏳ Working — 12 min — iteration 42/150, receiving stream response"
REDIRECT = "↪ Redirected current run. I'll adjust using your correction."
# The two ⌛ strings a Feishu user can actually receive — an approval card clicked too late, and an
# approval nobody answered in time. Verbatim heads of the core senders.
APPROVAL_CLICKED_TOO_LATE = (
    "⌛ That approval had already expired — the command was not run "
    "(it timed out or was resolved elsewhere)."
)
APPROVAL_TIMED_OUT = (
    "⌛ Approval timed out after 5 minutes — the command was NOT run. "
    "Ask me to try again if you still want it, or raise approvals.timeout in config.yaml."
)
PROVIDER_FAILURE = (
    "⚠️ The model provider failed after retries. I kept raw provider details out of chat; "
    "check gateway logs for diagnostics."
)

SOURCE = '''class Gateway:
    async def _notify_long_running(self, adapter, source):
        while True:
            try:
                _notify_res = None
                if _heartbeat_msg_id:
                    _notify_res = await adapter.edit_message(
                        source.chat_id, _heartbeat_msg_id, _heartbeat_text,
                    )
                if not (_notify_res and getattr(_notify_res, "success", False)):
                    _notify_res = await adapter.send(source.chat_id, _heartbeat_text)
                    if getattr(_notify_res, "success", False) and getattr(_notify_res, "message_id", None):
                        _heartbeat_msg_id = str(_notify_res.message_id)
                        if cleanup:
                            cleanup_ids.append(_heartbeat_msg_id)
            except Exception as exc:
                pass
'''


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_long_running_recall_restores_original_byte_for_byte(newline):
    original = SOURCE.replace("\n", newline)
    patched = patcher._apply_long_running_recall_patch(original)
    assert patched != original
    assert patcher._apply_long_running_recall_patch(patched) == patched
    assert patcher.remove_patch(patched) == original
    assert patcher.remove_patch_lenient(patched) == original


def test_long_running_recall_hook_is_outside_the_edit_path():
    """The hook must run once per FRESH send, never for the in-place edit (which arms nothing)."""
    patched = patcher._apply_long_running_recall_patch(SOURCE)
    lines = patched.splitlines()

    def indent(index):
        return len(lines[index]) - len(lines[index].lstrip())

    hook = next(i for i, line in enumerate(lines)
                if patcher.LONG_RUNNING_RECALL_PATCH_BEGIN in line)
    host = next(i for i, line in enumerate(lines)
                if line.lstrip().startswith('if getattr(_notify_res, "success"'))
    send = next(i for i, line in enumerate(lines) if "adapter.send(" in line)
    edit = next(i for i, line in enumerate(lines) if "adapter.edit_message(" in line)
    recorded = next(i for i, line in enumerate(lines) if "_heartbeat_msg_id = str(" in line)
    assert edit < hook, "must not sit on the edit path"
    assert send < hook, "must run after the send it withdraws"
    assert indent(hook) == indent(host), "same level as the branch it closes"
    assert indent(hook) < indent(recorded), "outside the message-id branch, so it also fires without one"


def test_long_running_recall_leaves_source_without_the_anchor_unchanged():
    without_anchor = SOURCE.replace("                        _heartbeat_msg_id = str(_notify_res.message_id)\n", "")
    assert patcher._apply_long_running_recall_patch(without_anchor) == without_anchor


@pytest.mark.asyncio
async def test_only_transient_notices_are_withdrawn(monkeypatch):
    calls = []

    async def schedule(message_id, *, delay_seconds=15.0, bot_id="", route=None):
        calls.append((message_id, delay_seconds))
        return True

    monkeypatch.setattr(hook_runtime, "schedule_message_recall_async", schedule)
    feishu = SimpleNamespace(platform="feishu")
    sent = SimpleNamespace(success=True, message_id="om_notice")

    assert await hook_runtime.recall_transient_thread_notice_async(feishu, HEARTBEAT, sent)
    assert calls == [("om_notice", 15.0)]
    assert await hook_runtime.recall_transient_thread_notice_async(feishu, REDIRECT, sent)

    # The approval-expiry receipts are withdrawn too: a click that lands after the wait ended, and
    # the timeout notice nobody answered. Both describe a decision that is already over, and the
    # user asked for them to stop cluttering the thread
    # (「如果用户点了审批的交互，那么应该撤销 … 让会话流干净一点」).
    calls.clear()
    assert await hook_runtime.recall_transient_thread_notice_async(
        feishu, APPROVAL_CLICKED_TOO_LATE, sent)
    assert await hook_runtime.recall_transient_thread_notice_async(feishu, APPROVAL_TIMED_OUT, sent)
    assert calls == [("om_notice", 15.0)] * 2

    calls.clear()
    # Content the user still needs, a failed send, a non-Feishu platform and a missing id all stay.
    assert not await hook_runtime.recall_transient_thread_notice_async(feishu, PROVIDER_FAILURE, sent)
    assert not await hook_runtime.recall_transient_thread_notice_async(feishu, "改完了。", sent)
    # The prefix is the head of the string only: a user QUOTING a ⌛ line back at the bot, or any
    # answer that merely mentions an expired approval, is a message in its own right and must stay.
    assert not await hook_runtime.recall_transient_thread_notice_async(
        feishu, "我看了这段 ⌛ Approval timed out 日志，帮我分析", sent)
    assert not await hook_runtime.recall_transient_thread_notice_async(
        feishu, "The approval expired before I clicked.", sent)
    assert not await hook_runtime.recall_transient_thread_notice_async(
        feishu, HEARTBEAT, SimpleNamespace(success=False, message_id="om_x"))
    assert not await hook_runtime.recall_transient_thread_notice_async(
        SimpleNamespace(platform="telegram"), HEARTBEAT, sent)
    assert not await hook_runtime.recall_transient_thread_notice_async(
        feishu, HEARTBEAT, SimpleNamespace(success=True, message_id=""))
    assert calls == []


@pytest.mark.parametrize("source", [
    SOURCE + SOURCE.replace("class Gateway:", "class OtherGateway:"),
    SOURCE.replace("_notify_long_running", "_send_ordinary_answer"),
    SOURCE.replace("adapter.send(source.chat_id, _heartbeat_text)", "adapter.send(source.chat_id, answer)"),
    SOURCE.replace("str(_notify_res.message_id)", "str(_notify_res.message_id, encoding='utf8')"),
])
def test_long_running_recall_rejects_ambiguous_or_drifted_source(source):
    assert patcher._apply_long_running_recall_patch(source) == source


@pytest.mark.asyncio
@pytest.mark.parametrize('edit_success,send_success,expected', [(True, True, ['notice-1']), (False, True, ['notice-1', 'notice-2']), (False, False, [])])
async def test_generated_heartbeat_hook_only_recalls_successful_fresh_sends(monkeypatch, edit_success, send_success, expected):
    calls = []
    async def schedule(message_id, **kwargs):
        calls.append(message_id)
        return True
    monkeypatch.setattr(hook_runtime, 'schedule_message_recall_async', schedule)
    class Adapter:
        sends = 0
        async def send(self, chat_id, text):
            self.sends += 1
            return SimpleNamespace(success=send_success, message_id=f'notice-{self.sends}')
        async def edit_message(self, *args):
            return SimpleNamespace(success=edit_success, message_id='notice-1')
    source = SOURCE.replace('        while True:',
        f'        _heartbeat_msg_id = None\n        _heartbeat_text = {HEARTBEAT!r}\n        cleanup = False\n        cleanup_ids = []\n        for _ in range(2):')
    patched = patcher._apply_long_running_recall_patch(source)
    namespace = {}
    exec(compile(patched, '<heartbeat-flow>', 'exec'), namespace)
    await namespace['Gateway']()._notify_long_running(Adapter(), SimpleNamespace(platform='feishu', chat_id='test-chat'))
    assert calls == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', ['notfeishu', 'feishu-preview'])
async def test_notice_recall_does_not_guess_unknown_platform_names(monkeypatch, platform):
    calls = []
    async def schedule(*args, **kwargs):
        calls.append(args)
        return True
    monkeypatch.setattr(hook_runtime, 'schedule_message_recall_async', schedule)
    assert not await hook_runtime.recall_transient_thread_notice_async(
        SimpleNamespace(platform=platform), HEARTBEAT,
        SimpleNamespace(success=True, message_id='notice-fixture'))
    assert calls == []


@pytest.mark.asyncio
async def test_plain_text_egress_clears_the_restart_group_behind_a_normal_message(monkeypatch):
    """An ordinary message retires the restart group in front of it, on the plain-text door.

    The sidecar clears the group on its own card sends; this arm exists for the sends that never
    reach it — the home channel's plain-text notices. It must NOT fire for the restart notices
    themselves (those register instead), and a failure to reach the sidecar must not disturb the send.
    """
    posted = []

    async def post(url, payload, timeout):
        posted.append((url, payload))
        return {"ok": True, "withdrawn": 2}

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", post)
    monkeypatch.setattr(hook_runtime, "_transient_notice_recall_seconds", lambda content: None)

    assert await hook_runtime._hfc_recall_plain_text_status_notice(
        "oc_home", "本轮回复结束", {"thread_id": "omt_x"}, SimpleNamespace(
            success=True, message_id="om_plain")
    )
    assert len(posted) == 1
    url, payload = posted[0]
    assert url.endswith("/recall/supersede")
    assert payload["route"]["chat_id"] == "oc_home"
    assert payload["route"]["conversation_id"] == "omt_x"

    # A restart notice registers itself instead of clearing what stands in front of it, and carries the
    # same 15s deadline as every other restart line («我觉得都应该挂 15 秒清就好了»).
    posted.clear()
    assert await hook_runtime._hfc_recall_plain_text_status_notice(
        "oc_home",
        "♻️ Gateway online — Hermes is back and ready.",
        {"thread_id": "omt_x"},
        SimpleNamespace(success=True, message_id="om_online"),
    )
    assert [p[0].rsplit("/", 1)[-1] for p in posted] == ["schedule"]
    assert posted[0][1].get("record_only") is not True
    assert posted[0][1]["delay_seconds"] == hook_runtime.RESTART_NOTICE_RECALL_SECONDS

    # Best-effort: an unreachable sidecar yields 0 and never raises.
    async def boom(*args, **kwargs):
        raise RuntimeError("sidecar down")

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", boom)
    assert await hook_runtime.supersede_restart_group_async(
        SimpleNamespace(platform="feishu", chat_id="oc_home", thread_id="omt_x"), "oc_home") == 0


def test_an_interrupted_turn_reports_its_metrics_not_just_its_error():
    """A stopped turn sends duration/model/tokens/context, so its card can say where it stopped.

    Regression: the interruption envelope carried the error text ALONE, so the card drew
    「已停止」 · 工具 #1 · 0s · Unknown — the numbers were never sent, not merely unread — while the
    sibling path (a queued follow-up ending in failure) already carried them. The reader's question
    about a stopped run is where it got to.
    """
    result = {
        "_hfc_turn_seconds": 42.5,
        "model": "deepseek-flash",
        "input_tokens": 1234,
        "output_tokens": 567,
        "last_prompt_tokens": 301_000,
        "context_length": 1_000_000,
    }
    locals_ = hook_runtime.interrupted_turn_locals(
        SimpleNamespace(platform="feishu", chat_id="oc_x"), "om_user", result
    )

    assert locals_["error"] == "用户已打断当前任务"
    payload = hook_runtime.build_event("message.failed", dict(locals_))
    data = payload["data"]
    assert data["error"] == "用户已打断当前任务"
    assert data["duration"] == 42.5
    assert data["model"] == "deepseek-flash"
    assert data["tokens"] == {"input_tokens": 1234, "output_tokens": 567}
    assert data["context"] == {"used_tokens": 301_000, "max_tokens": 1_000_000}


def test_an_interrupted_turn_without_a_result_still_sends_its_error():
    """No result to measure → the envelope still reports the interruption, with no invented metrics.

    The session keeps whatever it already measured rather than adopting a placeholder, so this must
    NOT put zeros or "Unknown" into the payload.
    """
    locals_ = hook_runtime.interrupted_turn_locals(
        SimpleNamespace(platform="feishu", chat_id="oc_x"), "om_user", None
    )
    assert locals_["error"] == "用户已打断当前任务"
    assert "duration" not in locals_
    assert "model" not in locals_


def test_only_the_restart_family_is_treated_as_self_retiring():
    """The restart pair retires itself; nothing else is caught by the prefix check.

    Both phrasings are accepted because a gateway updated mid-flight can still have the older line
    ("⚠️ Gateway restarting — Your current task …") sitting in a thread to be superseded.
    """
    for text in (
        "⚠️ Hermes is restarting — your current task will be interrupted. Send any message after "
        "the restart and I'll try to resume where you left off.",
        "⚠️ Hermes is shutting down — your current task will be interrupted. When it is back "
        "online, send any message and I'll try to pick up where we left off.",
        "⚠️ Gateway restarting — Your current task will be interrupted.",
        "⚠️ Gateway shutting down — Your current task will be interrupted.",
        "♻️ Gateway online — Hermes is back and ready.",
        "♻ Gateway restarted successfully. Your session continues.",
    ):
        assert hook_runtime._hfc_is_restart_notice(text), text

    for text in (
        # A user quoting a notice back at the bot is a message in its own right.
        "⚠️ Hermes is restarting 这条我看不懂，解释一下",
        "Gateway online",
        HEARTBEAT,
        "改完了。",
        "",
        None,
    ):
        assert not hook_runtime._hfc_is_restart_notice(text), text


@pytest.mark.asyncio
async def test_plain_text_egress_retires_restart_notices_and_expires_status_notices(monkeypatch):
    """On the plain-text door the two families are routed to different arms of the same hook.

    The restart notices must NOT go through the transient table: they have no deadline of their own
    (a restart warning is good until the gateway is back), so they are registered as self-retiring
    entries keyed per chat. The status notices keep their 15s clock.
    """
    scheduled = []

    async def schedule(message_id, **kwargs):
        scheduled.append((message_id, kwargs))
        return True

    monkeypatch.setattr(hook_runtime, 'schedule_message_recall_async', schedule)
    sent = SimpleNamespace(success=True, message_id='om_notice')

    assert await hook_runtime._hfc_recall_plain_text_status_notice(
        'oc_chat',
        "⚠️ Hermes is restarting — your current task will be interrupted. Send any message after "
        "the restart and I'll try to resume where you left off.",
        {'thread_id': 'omt_thread'},
        sent,
    )
    assert len(scheduled) == 1
    message_id, kwargs = scheduled[0]
    assert message_id == 'om_notice'
    # Registered under a per-chat key AND armed with the standard 15s deadline — one rule for the class.
    assert kwargs['record_only'] is not True
    assert kwargs['delay_seconds'] == hook_runtime.RESTART_NOTICE_RECALL_SECONDS
    assert kwargs['supersede_key'].startswith('restart-notice:')
    assert kwargs['supersede_key'].endswith('oc_chat:omt_thread')

    scheduled.clear()
    assert await hook_runtime._hfc_recall_plain_text_status_notice(
        'oc_chat', HEARTBEAT, {'thread_id': 'omt_thread'}, sent)
    assert len(scheduled) == 1
    _, kwargs = scheduled[0]
    assert kwargs.get('record_only') is not True
    assert not kwargs.get('supersede_key')
    assert kwargs['delay_seconds'] == 15.0


def test_the_background_task_receipt_is_withdrawn_after_fifteen_seconds():
    """The concise completion receipt self-erases; the FAILURE wording deliberately does not.

    Field report: 「Background task finished 不会 15s 撤回吗？」 — it did not. The receipt is built by
    the core (`_format_concise_process_notification`) with a wording no arm recognised, so every
    finished background task left a permanent line in the thread.

    The failure variant is asserted to stay: it appends the last output lines and an offer to rerun,
    which is the diagnostic the reader opens it for.
    """
    assert hook_runtime._transient_notice_recall_seconds("✅ Background task finished") == (
        hook_runtime.BACKGROUND_TASK_NOTICE_RECALL_SECONDS
    )
    assert hook_runtime._transient_notice_recall_seconds(
        "✅ Background task finished — `ssh -p 14234 root@host 'bash /tmp/x.sh'` (6s)"
    ) == hook_runtime.BACKGROUND_TASK_NOTICE_RECALL_SECONDS
    # Long runs print "m s" / "h m" instead of seconds.
    assert hook_runtime._transient_notice_recall_seconds(
        "✅ Background task finished — `pytest -q` (3m 21s)"
    ) == hook_runtime.BACKGROUND_TASK_NOTICE_RECALL_SECONDS

    # NOT the failure wording: it carries the error and the log tail.
    assert hook_runtime._transient_notice_recall_seconds(
        "❌ Background task failed — `pytest -q` (2s, exit 1). Last output:\n```\nboom\n```"
    ) is None
    # …and an hourglass/check alone is not evidence: arbitrary text is left alone.
    assert hook_runtime._transient_notice_recall_seconds("✅ Background task finished blah") is None


async def test_the_background_task_receipt_arms_the_recall_through_the_egress_door(monkeypatch):
    """End-to-end through the plain-text door: a receipt really schedules a withdrawal."""
    scheduled = []

    async def schedule(message_id, **kwargs):
        scheduled.append((message_id, kwargs))
        return True

    monkeypatch.setattr(hook_runtime, "schedule_message_recall_async", schedule)

    armed = await hook_runtime._hfc_recall_plain_text_status_notice(
        "oc_chat",
        "✅ Background task finished — `ls -la` (1s)",
        {"thread_id": "omt_thread"},
        SimpleNamespace(success=True, message_id="om_receipt"),
    )

    assert armed is True
    assert len(scheduled) == 1
    message_id, kwargs = scheduled[0]
    assert message_id == "om_receipt"
    assert kwargs["delay_seconds"] == hook_runtime.BACKGROUND_TASK_NOTICE_RECALL_SECONDS
