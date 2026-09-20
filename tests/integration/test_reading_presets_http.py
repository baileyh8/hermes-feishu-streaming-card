"""Exercise preset resolution through HTTP and the real multi-bot factory."""
import asyncio
import copy

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card.bots import BotRegistry, FeishuClientFactory, RouteResult
from hermes_feishu_card.card_limits import inspect_card_limits
from hermes_feishu_card.config import load_config
from hermes_feishu_card.server import create_app


class CardCapture:
    def __init__(self):
        self.sent = []
        self.updated = []

    async def send_card(self, chat_id, card, **kwargs):
        self.sent.append(copy.deepcopy(card))
        return "preset-card"

    async def update_card_message(self, message_id, card):
        self.updated.append((message_id, copy.deepcopy(card)))


def event(kind, seq, data=None):
    return {
        "schema_version": "1", "event": kind, "sequence": seq,
        "conversation_id": "preset-conversation", "message_id": "preset-message",
        "chat_id": "preset-chat", "platform": "feishu", "created_at": 1777017600 + seq,
        "data": data or {},
    }


async def wait_for(capture, text):
    for _ in range(100):
        if capture.updated and text in str(capture.updated[-1][1]):
            return capture.updated[-1][1]
        await asyncio.sleep(0.01)
    raise AssertionError(f"No rendered card containing {text!r}")


@pytest.mark.parametrize("terminal", ["completed", "failed"])
@pytest.mark.parametrize("explicit_hide", [None, True, False])
@pytest.mark.parametrize("streaming", [False, True])
async def test_focused_http_keeps_failed_activity_and_legacy_overrides(terminal, explicit_hide, streaming):
    capture = CardCapture()
    settings = {"reading_preset": "focused", "flush_interval_ms": 0, "streaming_mode": streaming}
    if explicit_hide is not None:
        settings["hide_completed_tool_activity"] = explicit_hide
    app = create_app(capture, card_config=settings)
    async with TestClient(TestServer(app)) as http:
        assert (await http.post("/events", json=event("message.started", 0))).status == 200
        assert (await http.post("/events", json=event("thinking.delta", 1, {"text": "LIVE_THINKING " + "思考" * 15000}))).status == 200
        live = await wait_for(capture, "LIVE_THINKING")
        assert inspect_card_limits(live).safe
        assert "LIVE_THINKING" not in "\n".join(e.get("content", "") for e in live["body"]["elements"])
        panel = next(e for e in live["body"]["elements"] if e.get("element_id") == "auxiliary_timeline")
        assert panel["expanded"] is False
        assert (await http.post("/events", json=event("tool.updated", 2, {
            "tool_id": "terminal-1", "name": "terminal", "status": "running", "detail": "verify latest result",
        }))).status == 200
        await wait_for(capture, "verify latest result")
        assert (await http.post("/events", json=event("answer.delta", 3, {"text": "PRESERVED_PARTIAL"}))).status == 200
        data = {"answer": "PRESERVED_PARTIAL FINAL_RESULT"} if terminal == "completed" else {"error": "FAILURE_REASON"}
        assert (await http.post("/events", json=event(f"message.{terminal}", 4, data))).status == 200
        card = await wait_for(capture, "FINAL_RESULT" if terminal == "completed" else "FAILURE_REASON")
        assert "PRESERVED_PARTIAL" in str(card)
        tool_rows = [e for e in card["body"]["elements"] if e.get("element_id", "").startswith("tool_activity_")]
        # The missing tool terminal becomes interrupted; compaction may hide
        # successful work but must preserve evidence of an unfinished call.
        assert tool_rows and '已中断' in tool_rows[0]['content']
        assert inspect_card_limits(card).safe
        assert len(capture.sent) == 1
        assert all(message_id == "preset-card" for message_id, _ in capture.updated)
        before = copy.deepcopy(card)
        assert (await http.post("/events", json=event("thinking.delta", 5, {"text": "LATE_THINKING"}))).status == 200
        assert capture.updated[-1][1] == before


async def test_http_real_factory_uses_bot_preset_over_profile_but_keeps_explicit_keys(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("""
card:
  reading_preset: classic
  flush_interval_ms: 0
profiles:
  work:
    card:
      reading_preset: detailed
    bots:
      default: worker
      items:
        worker:
          app_id: fixture-app
          app_secret: fixture-secret
          card:
            reading_preset: focused
            timeline_expanded: true
""")
    config = load_config(path)
    profile = config["profiles"]["work"]
    capture = CardCapture()
    factory = FeishuClientFactory(BotRegistry.from_config(profile), client_builder=lambda _: capture, profile_card=profile["card"])
    app = create_app({"work": factory}, card_config=config["card"], bot_router=lambda _: RouteResult("worker", "fixture"))
    async with TestClient(TestServer(app)) as http:
        assert (await http.post("/events", json=event("message.started", 0, {"profile_id": "work"}))).status == 200
        assert (await http.post("/events", json=event("thinking.delta", 1, {"text": "BOT_THINKING", "profile_id": "work"}))).status == 200
        card = await wait_for(capture, "BOT_THINKING")
        assert "BOT_THINKING" not in "\n".join(e.get("content", "") for e in card["body"]["elements"])
        panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "auxiliary_timeline")
        assert panel["expanded"] is True
