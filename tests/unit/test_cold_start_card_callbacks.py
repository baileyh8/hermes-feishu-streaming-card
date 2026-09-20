"""Issue #335: the first interaction must not need a slash-card warmup."""

import asyncio
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime


class QueuedWsLoop:
    def __init__(self):
        self.callbacks = []

    def is_closed(self):
        return False

    def call_soon_threadsafe(self, callback):
        self.callbacks.append(callback)

    def drain(self):
        while self.callbacks:
            self.callbacks.pop(0)()


def make_adapter(handler_factory=None):
    # Each test gets a fresh class: installation mutates adapter methods.
    class Adapter:
        name = "feishu"

        def __init__(self):
            self.native_calls = []
            self._client = object()
            self._ws_thread_loop = QueuedWsLoop()
            self.seen_tokens = set()
            self.replace_dispatcher()

        def _on_card_action_trigger(self, data):
            self.native_calls.append(data)
            return "native /card fallback"

        async def _handle_card_action_event(self, data):
            self.native_calls.append(data)

        def _is_card_action_duplicate(self, token):
            duplicate = token in self.seen_tokens
            self.seen_tokens.add(token)
            return duplicate

        def replace_dispatcher(self, callback=None):
            callback = callback or self._on_card_action_trigger
            if handler_factory:
                handler = handler_factory(callback)
            else:
                handler = SimpleNamespace(_callback_processor_map={
                    "p2.card.action.trigger": SimpleNamespace(f=callback),
                })
            self._event_handler = handler
            self._ws_client = SimpleNamespace(_event_handler=handler)
            return handler

    return Adapter()


class Runner:
    def __init__(self, adapter, work_adapter=None):
        self.adapters = {"feishu": adapter}
        self._profile_adapters = ({"work": {"feishu": work_adapter}}
                                  if work_adapter is not None else {})


def locals_for(runner, *, profile="default", platform="feishu"):
    source = SimpleNamespace(platform=platform, profile=profile, chat_id=f"chat-{profile}",
                             message_id=f"message-{profile}", thread_id="")
    return {"self": runner, "source": source, "message_id": source.message_id}


def click(*, profile="default", choice="yes", token="click-1"):
    return SimpleNamespace(event=SimpleNamespace(
        token=token,
        action=SimpleNamespace(value={"hfc_action": "interaction.select",
                                      "interaction_id": "interaction-first",
                                      "choice": choice, "token": "callback-proof",
                                      "profile_id": profile}),
        context=SimpleNamespace(open_chat_id=f"chat-{profile}"),
        operator=SimpleNamespace(open_id="user-example", user_name="Example"),
    ))


def callback(adapter):
    return adapter._ws_client._event_handler._callback_processor_map["p2.card.action.trigger"].f


@pytest.fixture
def runtime(monkeypatch):
    state = SimpleNamespace(posts=[], events=[])
    config = hook_runtime.RuntimeConfig(True, "http://127.0.0.1:8765/events", 0.1, 0, 0, 0)
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: config)
    monkeypatch.setattr(hook_runtime, "_GATEWAY_RUNNER_REF", None)
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda *args: True)
    monkeypatch.setattr(hook_runtime, "_install_delivery_ledger_mark_delivered_wrapper", lambda: None)
    monkeypatch.setattr(hook_runtime, "_policy_gate_sync",
                        lambda *args: hook_runtime._PolicyGateResult(True, None))

    def post(url, payload, timeout):
        state.posts.append(payload["event"])
        return {"ok": True, "card": {"elements": []}}

    async def emit(url, payload, timeout):
        state.events.append(payload)

    monkeypatch.setattr(hook_runtime, "_post_json_sync_response", post)
    monkeypatch.setattr(hook_runtime, "_send_fail_open_ordered", emit)
    return state


@pytest.mark.parametrize("threadsafe", [False, True])
@pytest.mark.asyncio
async def test_first_turn_click_reaches_sidecar_without_slash_warmup(runtime, threadsafe):
    adapter = make_adapter()
    runner = Runner(adapter)
    handler = adapter._event_handler
    emitter = (hook_runtime.emit_from_hermes_locals_threadsafe if threadsafe
               else hook_runtime.emit_from_hermes_locals)
    assert emitter(locals_for(runner)) is True
    await asyncio.sleep(0)
    adapter._ws_thread_loop.drain()
    callback(adapter)(click())
    assert [event["action"]["value"]["choice"] for event in runtime.posts] == ["yes"]
    assert adapter.native_calls == []
    assert adapter._event_handler is handler is adapter._ws_client._event_handler
    assert len(runtime.events) == 1


@pytest.mark.parametrize("kind", ["approval", "clarify"])
def test_first_interaction_without_started_event_returns_first_button_choice(runtime, monkeypatch, kind):
    adapter = make_adapter()
    runner = Runner(adapter)

    def publish(local_vars, url, payload, timeout):
        # The first user-visible card is immediately clicked, before any slash or model UI.
        adapter._ws_thread_loop.drain()
        callback(adapter)(click(choice="once" if kind == "approval" else "Option A"))
        return {"ok": True, "applied": True}

    def result(*args):
        if not runtime.posts:
            return {"status": "failed"}
        return {"status": "completed", "choice": runtime.posts[0]["action"]["value"]["choice"]}

    monkeypatch.setattr(hook_runtime, "_post_interaction_event", publish)
    monkeypatch.setattr(hook_runtime, "_get_json_sync", result)
    choice = hook_runtime.request_interaction_from_hermes_locals(
        locals_for(runner), kind=kind, interaction_id="interaction-first", prompt="Choose",
        options=[{"label": "Yes", "value": "once"}], timeout_seconds=0.1,
    )
    assert choice == {"status": "completed", "choice": "once" if kind == "approval" else "Option A"}
    assert adapter.native_calls == []


@pytest.mark.asyncio
async def test_reconnect_rechecks_live_processor_and_preserves_new_dispatcher(runtime):
    adapter = make_adapter()
    runner = Runner(adapter)
    stale_callback = callback(adapter)
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    adapter._ws_thread_loop.drain()
    # A restarted transport retained a callback snapshot from before installation.
    new_handler = adapter.replace_dispatcher(stale_callback)
    assert hook_runtime.emit_from_hermes_locals(locals_for(runner))
    await asyncio.sleep(0)
    adapter._ws_thread_loop.drain()
    callback(adapter)(click())
    assert len(runtime.posts) == 1
    assert adapter.native_calls == []
    assert adapter._event_handler is new_handler is adapter._ws_client._event_handler


@pytest.mark.asyncio
async def test_profiles_and_duplicate_click_remain_isolated(runtime):
    default, work = make_adapter(), make_adapter()
    runner = Runner(default, work)
    for _ in range(3):
        assert hook_runtime.emit_from_hermes_locals(locals_for(runner, profile="work"))
    await asyncio.sleep(0)
    for adapter in (default, work):
        assert len(adapter._ws_thread_loop.callbacks) == 1
        adapter._ws_thread_loop.drain()
    callback(work)(click(profile="work"))
    callback(work)(click(profile="work"))
    callback(default)(click())
    assert [event["context"]["profile_id"] for event in runtime.posts] == ["work", "default"]
    assert default.native_calls == work.native_calls == []


@pytest.mark.asyncio
async def test_non_feishu_emit_leaves_feishu_native_callback_untouched(runtime):
    adapter = make_adapter()
    runner = Runner(adapter)
    original = callback(adapter)
    hook_runtime.emit_from_hermes_locals(locals_for(runner, platform="telegram"))
    await asyncio.sleep(0)
    assert callback(adapter) is original
    assert adapter._ws_thread_loop.callbacks == []


@pytest.mark.asyncio
async def test_sdk_dispatcher_first_click_after_cold_start(runtime):
    pytest.importorskip("lark_oapi")
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger

    adapter = make_adapter(lambda f: EventDispatcherHandler.builder("", "").register_p2_card_action_trigger(f).build())
    handler = adapter._event_handler
    assert hook_runtime.emit_from_hermes_locals(locals_for(Runner(adapter)))
    await asyncio.sleep(0)
    adapter._ws_thread_loop.drain()
    event = P2CardActionTrigger({"event": {
        "token": "sdk-click", "action": {"value": {"hfc_action": "interaction.select",
        "interaction_id": "interaction-first", "choice": "once", "token": "callback-proof"}},
        "context": {"open_chat_id": "chat-default"}, "operator": {"open_id": "user-example"},
    }})
    handler._callback_processor_map["p2.card.action.trigger"].do(event)
    assert runtime.posts[0]["action"]["value"]["choice"] == "once"
    assert adapter.native_calls == []
    assert adapter._event_handler is handler is adapter._ws_client._event_handler


@pytest.mark.asyncio
async def test_extracted_turn_runner_first_callback_uses_own_gateway(runtime):
    adapter = make_adapter()
    runner = Runner(adapter)
    values = locals_for(runner)
    values["self"] = SimpleNamespace(_runner=runner)
    assert hook_runtime.emit_from_hermes_locals_threadsafe(values)
    await asyncio.sleep(0)
    adapter._ws_thread_loop.drain()
    callback(adapter)(click())
    assert len(runtime.posts) == 1
    assert adapter.native_calls == []


@pytest.mark.asyncio
async def test_reconnect_after_install_captures_patched_callback_without_new_emit(runtime):
    adapter = make_adapter()
    runner = Runner(adapter)
    assert hook_runtime.emit_from_hermes_locals(locals_for(runner))
    await asyncio.sleep(0)
    adapter._ws_thread_loop.drain()
    original = callback(adapter)
    # Hermes reconnect builds the dispatcher with self._on_card_action_trigger.
    new_handler = adapter.replace_dispatcher()
    callback(adapter)(click())
    assert callback(adapter) == original
    assert len(runtime.posts) == 1
    assert adapter._event_handler is new_handler


@pytest.mark.asyncio
async def test_reconnect_before_scheduled_refresh_keeps_old_loop_off_new_transport(runtime):
    adapter = make_adapter()
    runner = Runner(adapter)
    stale = callback(adapter)
    old_handler, old_loop = adapter._event_handler, adapter._ws_thread_loop
    assert hook_runtime.emit_from_hermes_locals(locals_for(runner))
    adapter._ws_thread_loop = QueuedWsLoop()
    new_handler = adapter.replace_dispatcher(stale)
    assert hook_runtime.emit_from_hermes_locals(locals_for(runner))
    await asyncio.sleep(0)
    old_loop.drain()
    assert old_handler._callback_processor_map["p2.card.action.trigger"].f is stale
    assert callback(adapter) is stale
    adapter._ws_thread_loop.drain()
    callback(adapter)(click())
    assert len(runtime.posts) == 1
    assert adapter._event_handler is new_handler


@pytest.mark.asyncio
async def test_missing_profile_never_falls_back_to_default_adapter(runtime):
    adapter = make_adapter()
    original = callback(adapter)
    assert hook_runtime.emit_from_hermes_locals(locals_for(Runner(adapter), profile="missing"))
    await asyncio.sleep(0)
    assert callback(adapter) is original
    assert not adapter._ws_thread_loop.callbacks


@pytest.mark.parametrize("failure", ["unknown-dispatcher", "closed-loop", "schedule-error"])
@pytest.mark.asyncio
async def test_incompatible_transport_fails_open_without_rebuild(runtime, failure):
    adapter = make_adapter()
    handler = adapter._event_handler
    original = callback(adapter)
    if failure == "unknown-dispatcher":
        handler._callback_processor_map = []
    elif failure == "closed-loop":
        adapter._ws_thread_loop.is_closed = lambda: True
    else:
        def reject(callback):
            raise RuntimeError("test schedule rejection")
        adapter._ws_thread_loop.call_soon_threadsafe = reject
    assert hook_runtime.emit_from_hermes_locals(locals_for(Runner(adapter)))
    await asyncio.sleep(0)
    adapter._ws_thread_loop.drain()
    assert len(runtime.events) == 1
    assert adapter._event_handler is handler is adapter._ws_client._event_handler
    if failure != "unknown-dispatcher":
        assert callback(adapter) is original
    # Unknown/non-HFC actions must still reach the original Hermes handler.
    assert adapter._on_card_action_trigger(SimpleNamespace()) == "native /card fallback"


@pytest.mark.asyncio
async def test_eager_install_failure_does_not_drop_turn_event(runtime, monkeypatch):
    adapter = make_adapter()
    original = callback(adapter)

    def fail(*args):
        raise RuntimeError("test installer failure")

    monkeypatch.setattr(hook_runtime, "install_feishu_command_card_adapter_methods", fail)
    assert hook_runtime.emit_from_hermes_locals(locals_for(Runner(adapter)))
    await asyncio.sleep(0)
    assert len(runtime.events) == 1
    assert callback(adapter) is original


def test_disabled_runtime_does_not_patch_adapter(runtime, monkeypatch):
    adapter = make_adapter()
    original = callback(adapter)
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: SimpleNamespace(enabled=False))
    assert hook_runtime.emit_from_hermes_locals(locals_for(Runner(adapter))) is False
    assert callback(adapter) is original
    assert not adapter._ws_thread_loop.callbacks
