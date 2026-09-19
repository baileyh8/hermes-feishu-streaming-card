from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.install import patcher

SOURCE = '''class Gateway:
    async def _send_busy_reply(self, event, content):
        adapter = self.adapter
        await adapter._send_with_retry(
            chat_id=event.chat_id,
            content=content,
        )
'''

INDENT = "        "


def _legacy_patched(newline="\n"):
    """A file as an older release left it: the send captured under a marker block."""
    source = SOURCE.replace("\n", newline)
    lines = source.splitlines(keepends=True)
    start = next(
        index for index, line in enumerate(lines)
        if line.lstrip().startswith("await adapter._send_with_retry(")
    )
    captured = [
        f"{INDENT}_hfc_recall_result = await adapter._send_with_retry({newline}",
        f"{INDENT}    chat_id=event.chat_id,{newline}",
        f"{INDENT}    content=content,{newline}",
        f"{INDENT}){newline}",
    ]
    block = (
        [f"{INDENT}{patcher.BUSY_RECALL_PATCH_BEGIN}{newline}"]
        + captured
        + patcher._render_busy_recall_hook_block(INDENT, newline)
        + [f"{INDENT}{patcher.BUSY_RECALL_PATCH_END}{newline}"]
    )
    return "".join(lines[:start] + block + lines[start + 4:])


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_the_retired_fragment_can_still_be_removed_byte_for_byte(newline):
    """A file patched by an older release must come back exactly, send statement included."""
    old = _legacy_patched(newline)
    assert old != SOURCE.replace("\n", newline)
    assert patcher.remove_patch(old) == SOURCE.replace("\n", newline)
    assert patcher.remove_patch_lenient(old) == SOURCE.replace("\n", newline)


def test_the_busy_recall_is_no_longer_rendered_as_a_fragment():
    """The withdrawal moved into the send wrapper, so nothing has to capture an upstream send."""
    rendered = patcher.apply_gateway_fragment(SOURCE, "gateway/run_busy.py")
    assert patcher.BUSY_RECALL_PATCH_BEGIN not in rendered
    assert "_hfc_recall_result" not in rendered


@pytest.mark.asyncio
async def test_the_send_wrapper_withdraws_a_delivered_redirect_ack(monkeypatch):
    calls = []

    async def schedule(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(hook_runtime, "schedule_message_recall_async", schedule)
    adapter = SimpleNamespace(_response_succeeded=lambda response: True)
    ack = '{"text": "\\u21aa Redirected current run. I\'ll adjust using your correction."}'
    delivered = SimpleNamespace(message_id="om_ack")
    assert await hook_runtime.recall_delivered_notice_from_send_async(
        adapter, "oc_chat", ack, {"thread_id": "omt_thread"}, delivered,
    )
    assert len(calls) == 1
    assert calls[0][0][0] == "om_ack"
    assert calls[0][1]["delay_seconds"] == hook_runtime.BUSY_REDIRECT_ACK_RECALL_SECONDS
    assert calls[0][1]["route"]["chat_id"] == "oc_chat"
    assert calls[0][1]["route"]["conversation_id"] == "omt_thread"


@pytest.mark.asyncio
async def test_a_redirect_ack_delivered_through_the_retry_helper_is_withdrawn(monkeypatch):
    """The end-to-end door: the wrapper the gateway's sends actually pass through arms the recall."""
    calls = []

    async def schedule(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(hook_runtime, "schedule_message_recall_async", schedule)

    class Adapter:
        def _response_succeeded(self, response):
            return True

        async def _hfc_original_feishu_send_with_retry(self, **kwargs):
            return SimpleNamespace(success=True, message_id="om_ack")

    response = await hook_runtime._hfc_feishu_send_with_native_handoff_tracking(
        Adapter(),
        chat_id="oc_chat",
        msg_type="text",
        payload='{"text": "\\u21aa Redirected current run. I\'ll adjust using your correction."}',
        reply_to=None,
        metadata={"thread_id": "omt_thread"},
    )
    assert response.message_id == "om_ack"
    assert [call[0][0] for call in calls] == ["om_ack"]


@pytest.mark.asyncio
async def test_the_send_wrapper_only_touches_disposable_acknowledgements(monkeypatch):
    calls = []

    async def schedule(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(hook_runtime, "schedule_message_recall_async", schedule)
    adapter = SimpleNamespace(_response_succeeded=lambda response: True)
    delivered = SimpleNamespace(message_id="om_ack")
    payload = lambda text: '{"text": "%s"}' % text

    # A real answer, a card (no ``text`` field), a failed send, and a send with no id must all be
    # left alone — only acknowledgements the user reads once are withdrawn.
    assert not await hook_runtime.recall_delivered_notice_from_send_async(
        adapter, "oc_chat", payload("改完了。"), {}, delivered)
    assert not await hook_runtime.recall_delivered_notice_from_send_async(
        adapter, "oc_chat", '{"elements": []}', {}, delivered)
    assert not await hook_runtime.recall_delivered_notice_from_send_async(
        SimpleNamespace(_response_succeeded=lambda response: False),
        "oc_chat", payload("\\u21aa Redirected current run."), {}, delivered)
    assert not await hook_runtime.recall_delivered_notice_from_send_async(
        adapter, "oc_chat", payload("\\u21aa Redirected current run."), {},
        SimpleNamespace(message_id=""))
    assert calls == []
