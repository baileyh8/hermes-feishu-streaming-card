from importlib.metadata import version
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime


lark_oapi = pytest.importorskip("lark_oapi")
pytest.importorskip("websockets")


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
