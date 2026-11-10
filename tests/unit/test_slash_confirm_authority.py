"""Exact slash-confirm seams preserve requester authority and upstream bytes."""
from __future__ import annotations

import ast
import threading
from types import ModuleType, SimpleNamespace

import pytest

from hermes_feishu_card.install import hybrid_renderers, patcher
from hermes_feishu_card.install.ast_contracts import find_slash_confirm_register


LEGACY = "_slash_confirm_mod.register(session_key, confirm_id, command, handler)"
AUTHORITY = (
    "prompt_authority(str(event.sender_id() or ''), str(actor.user_name or ''), "
    "str(getattr(source, 'chat_type', None) or ''))"
)
MODERN = LEGACY[:-1] + ", authority=" + AUTHORITY + ")"
DESCRIPTOR = next(d for d in patcher.HYBRID_PATCH_REGISTRY.descriptors
                  if d.group == "slash_confirm")


def _source(call, newline="\n"):
    body = (
        "class GatewayRunner:\n"
        "    async def _request_slash_confirm(self, event, command, title, message, handler):\n"
        "        source = event.source\n"
        "        session_key = 'session'\n"
        "        confirm_id = 'confirm'\n"
        + "\n".join("        " + line for line in call.splitlines()) + "\n"
        "        return message\n"
    )
    return body.replace("\n", newline)


@pytest.mark.parametrize("call", [
    LEGACY,
    MODERN,
    MODERN.replace("register(", "register(\n    ").replace(", authority=", ",\n    authority=") + " # retain comment",
])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_exact_forms_insert_after_whole_statement_and_restore_bytes(call, newline):
    source = _source(call, newline)
    statement = find_slash_confirm_register(source)
    assert statement is not None
    original_call = ast.dump(statement.value)
    legacy = patcher._apply_slash_confirm_patch(source)
    assert patcher.SLASH_CONFIRM_PATCH_BEGIN in legacy
    assert patcher._apply_slash_confirm_patch(legacy) == legacy
    assert patcher.remove_patch(legacy) == source
    rendered = hybrid_renderers._render_slash_confirm(source.encode(), DESCRIPTOR)
    assert hybrid_renderers._remove_fragments(rendered, DESCRIPTOR) == source.encode()
    for value in (legacy, rendered.decode()):
        compile(value, "<slash-contract>", "exec")
        assert ast.dump(find_slash_confirm_register(value).value) == original_call
        lines = value.splitlines()
        assert "HERMES_FEISHU_CARD" in lines[statement.end_lineno]
    if newline == "\r\n":
        assert b"\n" not in rendered.replace(b"\r\n", b"")


@pytest.mark.parametrize("call", [
    LEGACY.replace("session_key,", "other_session,"),
    LEGACY.replace("confirm_id,", "other_confirm,"),
    LEGACY.replace("command,", "other_command,"),
    LEGACY.replace("handler)", "other_handler)"),
    LEGACY[:-1] + ", extra=True)",
    LEGACY[:-1] + ", **kwargs)",
    "_slash_confirm_mod.register(*args)",
    LEGACY[:-1] + ", authority=None)",
    MODERN.replace("event.sender_id()", "other_event.sender_id()"),
    MODERN.replace("actor.user_name", "other_actor.user_name"),
    MODERN.replace("'chat_type'", "'chat_id'"),
    MODERN[:-1] + ", extra=True)",
    "result = " + LEGACY,
    "await " + LEGACY,
    LEGACY + "; other_action()",
    "other_action(); " + LEGACY,
    LEGACY + "\n" + LEGACY,
    "if condition:\n    " + LEGACY,
    "def nested():\n    " + LEGACY,
    "text = " + repr(LEGACY),
])
def test_unknown_or_ambiguous_register_seams_remain_unsupported(call):
    source = _source(call)
    assert find_slash_confirm_register(source) is None
    assert patcher._apply_slash_confirm_patch(source) == source
    with pytest.raises(ValueError, match="slash confirm register contract changed"):
        hybrid_renderers._render_slash_confirm(source.encode(), DESCRIPTOR)


def test_duplicate_handler_functions_are_not_an_anchor():
    source = _source(LEGACY) + _source(MODERN).replace("GatewayRunner", "OtherRunner")
    assert find_slash_confirm_register(source) is None


@pytest.mark.asyncio
async def test_rendered_call_preserves_upstream_authority_and_handler(monkeypatch):
    authority = object()
    observed = []
    pending = {}

    def prompt_authority(*args):
        observed.append(args)
        return authority

    def register(session_key, confirm_id, command, handler, *, authority):
        pending[session_key] = {
            "confirm_id": confirm_id, "command": command,
            "handler": handler, "authority": authority,
        }

    hook = ModuleType("hermes_feishu_card.hook_runtime")
    hook.admit_pending_interaction_from_hermes_locals = lambda *args: False
    monkeypatch.setitem(__import__("sys").modules, hook.__name__, hook)
    namespace = {
        "prompt_authority": prompt_authority,
        "actor": SimpleNamespace(user_name="requester"),
        "_slash_confirm_mod": SimpleNamespace(
            register=register, _pending=pending, _lock=threading.RLock(),
        ),
    }
    source = hybrid_renderers._render_slash_confirm(_source(MODERN).encode(), DESCRIPTOR)
    exec(compile(source, "<slash-authority>", "exec"), namespace)
    handler = object()
    event = SimpleNamespace(sender_id=lambda: "sender", source=SimpleNamespace(chat_type="group"))
    result = await namespace["GatewayRunner"]()._request_slash_confirm(
        event, "command", "title", "message", handler,
    )
    assert result == "message"
    assert observed == [("sender", "requester", "group")]
    assert pending["session"]["authority"] is authority
    assert pending["session"]["handler"] is handler
