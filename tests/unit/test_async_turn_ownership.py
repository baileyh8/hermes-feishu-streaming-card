"""Internal follow-ups must never inherit the previous card's delivery UUID."""
import copy
import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import hook_runtime as runtime
from hermes_feishu_card.install import patcher
from hermes_feishu_card.server import create_app


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    runtime.reset_runtime_state()
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(tmp_path / "state"))
    yield
    runtime.reset_runtime_state()


def source():
    return SimpleNamespace(platform="feishu", chat_id="oc_fixture", message_id="om_anchor",
                           thread_id="omt_fixture", _hfc_turn_id="om_parent")


async def start(monkeypatch, parent, mode, message_id="", fail=False):
    event = SimpleNamespace(message_id=message_id, internal=not bool(message_id),
                            source=parent, reply_to_message_id="om_anchor", text="follow-up")
    seen = []

    def emit(local_vars, **kwargs):
        if fail:
            raise RuntimeError("startup failed before building the event")
        seen.append(runtime.build_event("message.started", local_vars))
        return True

    async def emit_async(local_vars, **kwargs):
        return emit(local_vars, **kwargs)

    monkeypatch.setattr(runtime, "emit_from_hermes_locals", emit)
    monkeypatch.setattr(runtime, "emit_from_hermes_locals_async", emit_async)
    monkeypatch.setattr(runtime, "handle_hfc_command_from_hermes_locals", lambda *a, **k: False)
    if mode == "queued":
        block = "".join(patcher._render_queued_followup_hook_block("    ", "\n"))
        text = ("async def run(source, event):\n"
                "    next_source = source\n    pending_event = event\n    result = {}\n"
                + block + "    return next_source\n")
    else:
        block = "".join(patcher._render_hook_block("    ", "\n", strategy="gateway_run_013_plus"))
        text = "async def run(source, event):\n" + block + "    return source\n"
    namespace = {}
    exec(text, namespace)
    return await namespace["run"](parent, event), seen


@pytest.mark.parametrize("mode", ["queued", "idle"])
@pytest.mark.parametrize("fail", [False, True])
async def test_generated_hook_binds_before_startup_and_preserves_reply_anchor(monkeypatch, mode, fail):
    parent = source()
    child, seen = await start(monkeypatch, parent, mode, fail=fail)
    assert child is not parent
    assert parent._hfc_turn_id == "om_parent"
    assert child._hfc_turn_id.startswith("hfc-internal:")
    assert child.message_id == "om_anchor"
    terminal = runtime.build_event("message.completed", {"source": child, "response": "result"})
    assert terminal["turn_id"] == child._hfc_turn_id
    assert terminal["message_id"] == child._hfc_turn_id
    assert len(seen) == (0 if fail else 1)
    human, _ = await start(monkeypatch, child, mode, message_id="om_human", fail=True)
    assert human._hfc_turn_id == "om_human"
    assert human._hfc_internal_turn_id == ""
    assert child._hfc_turn_id != human._hfc_turn_id


@pytest.mark.parametrize("accepted", [False, True])
async def test_queued_final_retains_native_fallback(monkeypatch, accepted):
    seen = []

    async def emit(local_vars, **kwargs):
        seen.append(runtime.build_event(kwargs["event_name"], local_vars))
        return accepted

    monkeypatch.setattr(runtime, "emit_from_hermes_locals_async", emit)
    child = runtime.queued_followup_source(source(), SimpleNamespace(internal=True, message_id=""))
    block = "".join(patcher._render_queued_complete_hook_block("    ", "\n"))
    namespace = {}
    exec("async def run(turn_ctx):\n"
         "    first_response = 'finished'\n    _already_streamed = False\n    result = {}\n"
         + block + "    return _already_streamed, first_response\n", namespace)
    streamed, response = await namespace["run"](SimpleNamespace(source=child, event_message_id=""))
    assert streamed is accepted
    assert response == "finished"
    assert seen[0]["turn_id"] == child._hfc_internal_turn_id


def test_internal_turns_do_not_borrow_or_retire_another_fallback():
    parent = source()
    legacy = {"platform": "feishu", "chat_id": parent.chat_id, "thread_id": parent.thread_id}
    legacy_started = runtime.build_event("message.started", legacy)
    children = [runtime.queued_followup_source(parent, SimpleNamespace(internal=True, message_id=""))
                for _ in range(2)]
    assert children[0]._hfc_turn_id != children[1]._hfc_turn_id
    for child in children:
        payload = runtime.build_event("message.completed", {"source": child, "response": "done"})
        assert payload["message_id"] == child._hfc_turn_id
    legacy_final = runtime.build_event("message.completed", {**legacy, "response": "legacy done"})
    assert legacy_final["message_id"] == legacy_started["message_id"]


class DeduplicatingFeishu:
    """Like Feishu, repeated create UUIDs return the original message ID."""
    def __init__(self):
        self.ids = {}
        self.cards = {}
        self.routes = []

    async def send_card_delivery(self, chat_id, card, **kwargs):
        key = kwargs["delivery_uuid"]
        if key not in self.ids:
            self.ids[key] = "om_card_" + str(len(self.ids))
            self.cards[self.ids[key]] = copy.deepcopy(card)
            self.routes.append((chat_id, kwargs))
        return SimpleNamespace(message_id=self.ids[key], retry_count=0)

    async def update_card_message(self, message_id, card, **kwargs):
        self.cards[message_id] = copy.deepcopy(card)
        return True


@pytest.mark.parametrize("mode", ["queued", "idle"])
@pytest.mark.parametrize("fail_started", [False, True])
async def test_distinct_cards_with_uuid_dedup_and_late_final(monkeypatch, mode, fail_started):
    transport = DeduplicatingFeishu()
    client = TestClient(TestServer(create_app(transport, operations_transport_root_secret=b"t" * 32)))
    await client.start_server()
    try:
        async def send(payload):
            assert payload is not None
            response = await client.post("/events", json=payload)
            assert response.status == 200, await response.text()

        parent = source()
        original = runtime.build_event("message.completed", {"source": parent, "message_id": "om_parent", "response": "PARENT ANSWER"})
        await send(original)
        first_id = next(iter(transport.cards))
        first_card = copy.deepcopy(transport.cards[first_id])
        child, started = await start(monkeypatch, parent, mode, fail=fail_started)
        for payload in started:
            await send(payload)
        final = runtime.build_event("message.completed", {"source": child, "response": "FOLLOWUP ANSWER"})
        await send(final)
        assert len(transport.cards) == 2
        assert transport.cards[first_id] == first_card
        assert "PARENT ANSWER" in json.dumps(first_card)
        second_id = next(key for key in transport.cards if key != first_id)
        assert "FOLLOWUP ANSWER" in json.dumps(transport.cards[second_id])
        second_card = copy.deepcopy(transport.cards[second_id])
        human, started = await start(monkeypatch, child, mode, message_id="om_human")
        for payload in started:
            await send(payload)
        await send(runtime.build_event("message.completed", {"source": human, "message_id": "om_human", "response": "HUMAN ANSWER"}))
        await send(final)  # exact duplicate after the next real inbound turn
        await send(runtime.build_event("message.completed", {"source": child, "response": "FOLLOWUP ANSWER"}))
        assert len(transport.cards) == 3
        assert transport.cards[first_id] == first_card
        assert transport.cards[second_id] == second_card
        assert all("hfc-internal:" not in str(route[1].get("reply_to_message_id", "")) for route in transport.routes)
    finally:
        await client.close()


def test_old_and_current_start_templates_roundtrip():
    original = ("async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
                "    response = await self._run_agent(event, source)\n"
                "    await self.hooks.emit('agent:end', {'response': response})\n"
                "    return response\n")
    installed = patcher.apply_patch(original, strategy="gateway_run_013_plus")
    current = "".join(patcher._render_hook_block("    ", "\n", strategy="gateway_run_013_plus"))
    previous = "".join(patcher._render_hook_block("    ", "\n", strategy="gateway_run_013_plus", isolate_turn=False))
    assert current in installed
    old = installed.replace(current, previous)
    assert patcher.remove_patch(old) == original
    assert patcher.remove_patch(installed) == original
    assert patcher.apply_patch(installed, strategy="gateway_run_013_plus") == installed


def test_old_queued_template_can_be_removed():
    original = "async def run():\n    pass\n"
    for render in (patcher._render_v462_queued_followup_hook_block,
                   patcher._render_v468_queued_followup_hook_block,
                   patcher._render_queued_followup_hook_block):
        installed = original.replace("    pass\n", "".join(render("    ", "\n")) + "    pass\n")
        assert patcher.remove_patch(installed) == original
