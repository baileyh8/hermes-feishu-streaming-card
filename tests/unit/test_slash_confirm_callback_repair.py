"""A stale installation marker must not route HFC confirmations to /card."""

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda *a: True)
    monkeypatch.setattr(hook_runtime, "_install_delivery_ledger_mark_delivered_wrapper", lambda: None)
    monkeypatch.setattr(hook_runtime, "_GATEWAY_RUNNER_REF", None)
    resolved, updated = [], []
    module = ModuleType("tools.slash_confirm")

    async def resolve(*args):
        resolved.append(args)
        return "New session started."

    async def update(adapter, message_id, card):
        updated.append((message_id, card))
        return True

    module.resolve = resolve
    tools = ModuleType("tools")
    tools.slash_confirm = module
    monkeypatch.setitem(sys.modules, "tools", tools)
    monkeypatch.setitem(sys.modules, "tools.slash_confirm", module)
    monkeypatch.setattr(hook_runtime, "_hfc_update_native_command_card", update)
    return SimpleNamespace(resolved=resolved, updated=updated)


def adapter_type():
    class Adapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self._loop = asyncio.get_running_loop()
            self.tasks = []
            self.native = []
            self.tokens = set()
            self._hfc_slash_confirm_state = {"confirm-1": {
                "session_key": "feishu:test", "chat_id": "oc_test",
                "message_id": "om_real_confirmation",
            }}

        def _on_card_action_trigger(self, data):
            self._submit_on_loop(self._loop, self._handle_card_action_event(data))

        async def _handle_card_action_event(self, data):
            self.native.append(data)

        def _submit_on_loop(self, loop, coroutine):
            self.tasks.append(loop.create_task(coroutine))
            return True

        def _is_card_action_duplicate(self, token):
            duplicate = token in self.tokens
            self.tokens.add(token)
            return duplicate

    return Adapter


def click(choice="once", *, token="c-callback-token", chat="oc_test", user="ou_test"):
    return SimpleNamespace(event=SimpleNamespace(
        token=token,
        action=SimpleNamespace(tag="button", value={
            "hfc_action": "slash_confirm", "hfc_confirm_id": "confirm-1",
            "hfc_choice": choice,
        }),
        context=SimpleNamespace(open_chat_id=chat, open_message_id="c-not-a-message"),
        operator=SimpleNamespace(open_id=user),
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["class-rebind", "subclass"])
async def test_reinstall_repairs_actual_callbacks_despite_old_marker(runtime, replacement):
    Base = adapter_type()
    native_sync, native_async = Base._on_card_action_trigger, Base._handle_card_action_event
    first = Base()
    assert hook_runtime.install_feishu_command_card_adapter_methods(SimpleNamespace(adapters={"feishu": first}))
    if replacement == "class-rebind":
        Base._on_card_action_trigger = native_sync
        Base._handle_card_action_event = native_async
        adapter = first
    else:
        class Reconnected(Base):
            _on_card_action_trigger = native_sync
            _handle_card_action_event = native_async
        adapter = Reconnected()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    adapter._on_card_action_trigger(click())
    await asyncio.gather(*adapter.tasks)
    assert adapter.native == []
    assert runtime.resolved == [("feishu:test", "confirm-1", "once")]
    assert [message_id for message_id, _ in runtime.updated] == ["om_real_confirmation"]
    # Preserve the real native handler for unrelated actions, exactly once.
    other = SimpleNamespace(event=SimpleNamespace(action=SimpleNamespace(value={"third_party": True})))
    adapter._on_card_action_trigger(other)
    await asyncio.gather(*adapter.tasks)
    assert adapter.native == [other]


@pytest.mark.asyncio
async def test_first_turn_repairs_async_callback_even_when_sync_hook_survived(runtime):
    Adapter = adapter_type()
    native_async = Adapter._handle_card_action_event
    adapter = Adapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    Adapter._handle_card_action_event = native_async
    assert hook_runtime._hfc_eager_ensure_command_card_hooks({
        "self": runner, "source": SimpleNamespace(platform="feishu", chat_id="oc_test"),
    })
    await adapter._handle_card_action_event(click())
    assert adapter.native == []
    assert runtime.resolved == [("feishu:test", "confirm-1", "once")]


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["once", "always", "cancel"])
async def test_stale_dispatcher_updates_real_message_once(runtime, choice):
    adapter = adapter_type()()
    stale_callback = adapter._on_card_action_trigger
    assert hook_runtime.install_feishu_command_card_adapter_methods(SimpleNamespace(adapters={"feishu": adapter}))
    stale_callback(click(choice))
    await asyncio.gather(*adapter.tasks)
    stale_callback(click(choice))
    stale_callback(click(choice, token="c-second-click"))
    await asyncio.gather(*adapter.tasks)
    assert adapter.native == []
    assert runtime.resolved == [("feishu:test", "confirm-1", choice)]
    assert [message_id for message_id, _ in runtime.updated] == ["om_real_confirmation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{"chat": "oc_other"}, {"user": ""}, {"choice": "invalid"}])
async def test_rejected_confirmation_never_routes_as_slash_command(runtime, fields):
    adapter = adapter_type()()
    stale_callback = adapter._on_card_action_trigger
    assert hook_runtime.install_feishu_command_card_adapter_methods(SimpleNamespace(adapters={"feishu": adapter}))
    stale_callback(click(**fields))
    await asyncio.gather(*adapter.tasks)
    assert not adapter.native and not runtime.resolved and not runtime.updated
    assert "confirm-1" in adapter._hfc_slash_confirm_state
