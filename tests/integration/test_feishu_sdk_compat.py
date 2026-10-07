from importlib.metadata import version
from types import SimpleNamespace

import pytest
import pytest_asyncio

from hermes_feishu_card import hook_runtime


lark_oapi = pytest.importorskip("lark_oapi")
pytest.importorskip("websockets")



@pytest_asyncio.fixture(autouse=True)
async def close_sdk_cache_tasks(monkeypatch):
    import asyncio
    from lark_oapi.ws.client import Client
    original = Client.__init__
    tasks = []
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        tasks.append(self._cache._cron)
    monkeypatch.setattr(Client, "__init__", initialize)
    yield
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

def test_lark_168_card_callback_refresh_preserves_live_ws_handler_identity():
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

    assert version("lark-oapi") == "1.6.8"
    assert version("websockets") == "15.0.1"

    def original_callback(data):
        return data

    handler = (
        EventDispatcherHandler.builder("", "")
        .register_p2_card_action_trigger(original_callback)
        .build()
    )

    class FakeWsLoop:
        def __init__(self):
            self.callbacks = []

        def is_closed(self):
            return False

        def call_soon_threadsafe(self, callback):
            self.callbacks.append(callback)

    class DummyFeishuAdapter:
        name = "feishu"

        def _on_card_action_trigger(self, data):
            return original_callback(data)

        async def _handle_card_action_event(self, data):
            return None

    class DummyRunner:
        def __init__(self, adapter):
            self.adapters = {"feishu": adapter}

    adapter = DummyFeishuAdapter()
    adapter._client = object()
    adapter._event_handler = handler
    adapter._ws_client = SimpleNamespace(_event_handler=handler)
    adapter._ws_thread_loop = FakeWsLoop()
    live_handler_identity = id(handler)

    assert hook_runtime.install_feishu_command_card_adapter_methods(
        DummyRunner(adapter)
    )

    assert id(adapter._event_handler) == live_handler_identity
    assert id(adapter._ws_client._event_handler) == live_handler_identity
    assert len(adapter._ws_thread_loop.callbacks) == 1

    adapter._ws_thread_loop.callbacks[0]()

    processor = handler._callback_processor_map["p2.card.action.trigger"]
    assert processor.f.__func__ is hook_runtime._hfc_on_feishu_card_action_trigger


@pytest.mark.asyncio
@pytest.mark.parametrize("fragmented", [False, True])
async def test_card_frame_reaches_callback_and_ack_preserves_wire_headers(fragmented):
    import json
    import base64
    from lark_oapi.ws.client import Client
    from lark_oapi.ws.pb.pbbp2_pb2 import Frame
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
    from hermes_feishu_card.feishu_ws_compat import repair_card_frame_dispatch
    seen, sent = [], []
    def callback(data):
        seen.append(data.event.action.value)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "confirmed"}})
    handler = EventDispatcherHandler.builder("", "").register_p2_card_action_trigger(callback).build()
    client = Client("test-app", "test-secret", event_handler=handler)
    async def write(payload):
        sent.append(Frame.FromString(payload))
    client._write_message = write
    assert repair_card_frame_dispatch(client) is True
    assert repair_card_frame_dispatch(client) is False
    payload = json.dumps({"schema": "2.0", "header": {"event_type": "card.action.trigger", "event_id": "synthetic-event"},
        "event": {"action": {"tag": "button", "value": {"hfc_action": "slash_confirm"}}}}).encode()
    chunks = [payload[:20], payload[20:]] if fragmented else [payload]
    for seq, chunk in enumerate(chunks):
        frame = Frame(SeqID=17, LogID=23, service=1, method=1, payload=chunk)
        for key, value in {"message_id": "synthetic-message", "trace_id": "synthetic-trace", "sum": str(len(chunks)), "seq": str(seq), "type": "card"}.items():
            h = frame.headers.add(); h.key = key; h.value = value
        await client._handle_message(frame.SerializeToString())
    assert seen == [{"hfc_action": "slash_confirm"}]
    assert len(sent) == 1
    response = sent[0]
    assert (response.SeqID, response.LogID) == (17, 23)
    assert {h.key: h.value for h in response.headers}["type"] == "card"
    body = json.loads(response.payload)
    assert body["code"] == 200
    assert json.loads(base64.b64decode(body["data"]))["toast"]["content"] == "confirmed"


@pytest.mark.asyncio
async def test_card_frame_repair_does_not_change_sdk_class_or_unknown_client():
    from lark_oapi.ws.client import Client
    from hermes_feishu_card.feishu_ws_compat import repair_card_frame_dispatch
    original = Client._handle_data_frame
    a = Client("test", "test")
    b = Client("test", "test")
    assert repair_card_frame_dispatch(a)
    assert Client._handle_data_frame is original
    assert b._handle_data_frame.__func__ is original
    class Unknown:
        async def _handle_data_frame(self, frame):
            raise RuntimeError("unknown implementation")
    unknown = Unknown()
    assert not repair_card_frame_dispatch(unknown)
    assert unknown._handle_data_frame.__func__ is Unknown._handle_data_frame


@pytest.mark.asyncio
async def test_card_frame_repair_keeps_event_dispatch_and_error_ack():
    import json
    from lark_oapi.ws.client import Client
    from lark_oapi.ws.pb.pbbp2_pb2 import Frame
    from hermes_feishu_card.feishu_ws_compat import repair_card_frame_dispatch
    seen, sent = [], []
    def dispatch(payload):
        seen.append(payload)
        raise ValueError("synthetic callback failure")
    client = Client("test", "test", event_handler=SimpleNamespace(_do_without_validation=dispatch))
    async def write(payload):
        sent.append(Frame.FromString(payload))
    client._write_message = write
    assert repair_card_frame_dispatch(client)
    for kind in ("event", "card"):
        frame = Frame(SeqID=1, LogID=2, service=1, method=1, payload=b"{}")
        for key, value in {"message_id": "synthetic", "trace_id": "synthetic", "sum": "1", "seq": "0", "type": kind}.items():
            h = frame.headers.add(); h.key = key; h.value = value
        await client._handle_message(frame.SerializeToString())
    assert seen == [b"{}", b"{}"]
    assert [json.loads(f.payload)["code"] for f in sent] == [500, 500]


@pytest.mark.asyncio
async def test_transport_repair_runs_on_live_loop_and_reconnects():
    from lark_oapi.ws.client import Client
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
    from hermes_feishu_card.feishu_ws_compat import card_frame_dispatch_needs_repair
    scheduled = []
    loop = SimpleNamespace(is_closed=lambda: False, call_soon_threadsafe=scheduled.append)
    def callback(data):
        return data
    handler = EventDispatcherHandler.builder("", "").register_p2_card_action_trigger(callback).build()
    first = Client("test", "test", event_handler=handler)
    adapter = SimpleNamespace(_event_handler=handler, _ws_client=first,
                              _ws_thread_loop=loop, _on_card_action_trigger=callback)
    assert hook_runtime._hfc_refresh_feishu_event_handler(adapter)
    assert card_frame_dispatch_needs_repair(first)
    second = Client("test", "test", event_handler=handler)
    adapter._ws_client = second
    scheduled.pop(0)()
    assert card_frame_dispatch_needs_repair(first)  # stale closure did not mutate
    assert hook_runtime._hfc_refresh_feishu_event_handler(adapter)
    scheduled.pop(0)()
    assert not card_frame_dispatch_needs_repair(second)
    assert card_frame_dispatch_needs_repair(first)
    assert handler._callback_processor_map["p2.card.action.trigger"].f is callback
