from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.install import patcher

HEARTBEAT = "⏳ Working — 12 min — iteration 42/150, receiving stream response"
REDIRECT = "↪ Redirected current run. I'll adjust using your correction."
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

    async def schedule(message_id, *, delay_seconds=15.0, bot_id=""):
        calls.append((message_id, delay_seconds))
        return True

    monkeypatch.setattr(hook_runtime, "schedule_message_recall_async", schedule)
    feishu = SimpleNamespace(platform="feishu")
    sent = SimpleNamespace(success=True, message_id="om_notice")

    assert await hook_runtime.recall_transient_thread_notice_async(feishu, HEARTBEAT, sent)
    assert calls == [("om_notice", 15.0)]
    assert await hook_runtime.recall_transient_thread_notice_async(feishu, REDIRECT, sent)

    calls.clear()
    # Content the user still needs, a failed send, a non-Feishu platform and a missing id all stay.
    assert not await hook_runtime.recall_transient_thread_notice_async(feishu, PROVIDER_FAILURE, sent)
    assert not await hook_runtime.recall_transient_thread_notice_async(feishu, "改完了。", sent)
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
