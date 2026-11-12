"""Opt-in segmented terminal delivery over the live event flow (issue #511)."""
from __future__ import annotations

from aiohttp.test_utils import TestClient, TestServer
import pytest

from hermes_feishu_card.card_limits import inspect_card_limits
from hermes_feishu_card import server as sidecar_server
from hermes_feishu_card.native_handoff import NativeHandoffStore

from tests.integration.test_server import (
    FakeFeishuClient,
    create_app,
    event_payload,
    exact_handoff_metadata,
    identified,
    wait_for_condition,
)


def oversize_answer() -> str:
    return "# 调研报告\n\n" + "结论要点与分析内容，覆盖多段论证。\n\n" * 1_500


class SegmentFailureFeishuClient(FakeFeishuClient):
    """Fails every card send after the turn's initial card."""

    def __init__(self):
        super().__init__()
        self.fail_after_first_send = False

    async def send_card(self, chat_id, card, **kwargs):
        if self.fail_after_first_send and self.sent:
            raise RuntimeError("segment send unavailable")
        return await super().send_card(chat_id, card, **kwargs)


async def _start_turn(test_client) -> None:
    started = await test_client.post(
        "/events",
        json=identified(
            event_payload(
                "message.started",
                0,
                {
                    "sender_open_id": "ou_sender-01",
                    # Keep the same profile-prefixed session key the terminal
                    # event derives from its handoff metadata.
                    "profile_id": "default",
                    "profile_source": "fallback_default",
                },
            ),
            "turn:turn-seg-1:started",
        ),
    )
    assert started.status == 200


async def _completed_event(*, answer: str, sequence: int = 1, event_id: str = "turn:turn-seg-1:completed"):
    return identified(
        event_payload(
            "message.completed",
            sequence,
            {
                "answer": answer,
                "native_handoff": exact_handoff_metadata("3" * 32, "4" * 64, answer=answer),
            },
        ),
        event_id,
        phase="terminal",
    )


async def _make_client(tmp_path, feishu_client, **card_config):
    app = create_app(
        feishu_client,
        card_config={"over_limit": "segmented", **card_config},
        native_handoff_store=NativeHandoffStore(tmp_path / "handoff-state"),
    )
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        yield test_client, feishu_client
    finally:
        await test_client.close()


@pytest.fixture
async def segmented_client(tmp_path):
    async for client in _make_client(tmp_path, FakeFeishuClient()):
        yield client


async def test_streaming_never_sends_a_second_card_before_terminal(segmented_client):
    test_client, feishu_client = segmented_client
    await _start_turn(test_client)

    delta = await test_client.post(
        "/events",
        json=identified(
            event_payload(
                "answer.delta",
                1,
                {
                    "text": oversize_answer(),
                    "profile_id": "default",
                    "profile_source": "fallback_default",
                },
            ),
            "turn:turn-seg-1:delta-1",
        ),
    )

    assert delta.status == 200
    # Live turns keep the single-card invariant even with segmented enabled.
    assert len(feishu_client.sent) == 1


async def test_terminal_over_limit_delivers_numbered_continuation_cards(segmented_client):
    test_client, feishu_client = segmented_client
    await _start_turn(test_client)
    initial_cards = len(feishu_client.sent)

    completed = await test_client.post(
        "/events", json=await _completed_event(answer=oversize_answer())
    )
    body = await completed.json()

    assert completed.status == 200
    assert body["ok"] is True
    assert body["applied"] is True
    assert "disposition" not in body

    continuation_cards = feishu_client.sent[initial_cards:]
    assert len(continuation_cards) >= 2
    total = len(continuation_cards)
    for index, (_, card, _, _) in enumerate(continuation_cards, start=1):
        assert card["schema"] == "2.0"
        assert inspect_card_limits(card).safe is True
        assert card["header"]["title"]["content"].endswith(f"（续 {index}/{total}）")

    # The main card was patched in place and announces the continuation set.
    assert any(
        f"{total} 张续卡" in str(card) for _, card in feishu_client.updated
    )
    assert all(inspect_card_limits(card).safe for _, card in feishu_client.updated)

    metrics = test_client.app[sidecar_server.METRICS_KEY]
    assert metrics.card_segmented_deliveries == 1
    assert metrics.card_segmented_fallbacks == 0


async def test_continuation_send_failure_falls_back_to_native_handoff(tmp_path):
    feishu_client = SegmentFailureFeishuClient()
    async for test_client, _ in _make_client(tmp_path, feishu_client):
        await _start_turn(test_client)
        feishu_client.fail_after_first_send = True
        cards_before = len(feishu_client.sent)

        completed = await test_client.post(
            "/events", json=await _completed_event(answer=oversize_answer())
        )
        body = await completed.json()

        assert completed.status == 200
        assert body["applied"] is False
        assert body["disposition"] == "native"
        assert body["native_handoff"]["protocol"] == "hfc-native-handoff-v2"
        assert len(feishu_client.sent) == cards_before

        # The placeholder handoff card replaces the partial segmented result.
        await wait_for_condition(
            lambda: any(
                "完整内容已切换为 Hermes 原生消息发送" in str(card)
                for _, card in feishu_client.updated
            )
        )
        metrics = test_client.app[sidecar_server.METRICS_KEY]
        assert metrics.card_segmented_fallbacks == 1


async def test_duplicate_terminal_event_does_not_resend_continuations(segmented_client):
    test_client, feishu_client = segmented_client
    await _start_turn(test_client)

    first = await test_client.post(
        "/events", json=await _completed_event(answer=oversize_answer())
    )
    assert (await first.json())["applied"] is True
    cards_after_terminal = len(feishu_client.sent)

    duplicate = await test_client.post(
        "/events",
        json=await _completed_event(
            answer=oversize_answer(), sequence=2, event_id="turn:turn-seg-1:completed-2"
        ),
    )
    duplicate_body = await duplicate.json()

    assert duplicate.status == 200
    assert duplicate_body["ok"] is True
    assert duplicate_body["applied"] is True
    assert len(feishu_client.sent) == cards_after_terminal


async def test_cardless_terminal_creates_main_card_then_continuations(tmp_path):
    async for test_client, feishu_client in _make_client(tmp_path, FakeFeishuClient()):
        completed = await test_client.post(
            "/events", json=await _completed_event(answer=oversize_answer())
        )
        body = await completed.json()

        assert completed.status == 200
        assert body["ok"] is True
        assert body["applied"] is True

        cards = feishu_client.sent
        assert len(cards) >= 3  # main card plus at least two continuations
        total = len(cards) - 1
        for index, (_, card, _, _) in enumerate(cards[1:], start=1):
            assert inspect_card_limits(card).safe is True
            assert card["header"]["title"]["content"].endswith(f"（续 {index}/{total}）")

        metrics = test_client.app[sidecar_server.METRICS_KEY]
        assert metrics.card_segmented_deliveries == 1
        assert metrics.card_segmented_fallbacks == 0


async def test_cardless_terminal_segment_failure_falls_back_to_native(tmp_path):
    feishu_client = SegmentFailureFeishuClient()
    async for test_client, _ in _make_client(tmp_path, feishu_client):
        feishu_client.fail_after_first_send = True

        completed = await test_client.post(
            "/events", json=await _completed_event(answer=oversize_answer())
        )
        body = await completed.json()

        assert completed.status == 200
        assert body["applied"] is False
        assert body["disposition"] == "native"
        assert body["native_handoff"]["protocol"] == "hfc-native-handoff-v2"
        # The main card went out and became the handoff placeholder; no
        # continuation card reached the chat.
        assert len(feishu_client.sent) == 1
        await wait_for_condition(
            lambda: any(
                "完整内容已切换为 Hermes 原生消息发送" in str(card)
                for _, card in feishu_client.updated
            )
        )
        metrics = test_client.app[sidecar_server.METRICS_KEY]
        assert metrics.card_segmented_fallbacks == 1
