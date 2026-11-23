"""Run the generated legacy completion hooks through the signed event endpoint."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from hashlib import sha256
import json
import time
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from hermes_feishu_card import hook_runtime, server
from hermes_feishu_card.install import patcher
from hermes_feishu_card.native_handoff import NativeHandoffStore
from tests.integration.test_issue550_direct_terminal import _long_answer
from tests.integration.test_reopened_delivery_identity import DeduplicatingClient
from tests.integration.test_server import TRANSPORT_ROOT_SECRET, FakeFeishuClient


@pytest.fixture
def gateway_runtime(monkeypatch):
    hook_runtime.reset_runtime_state()
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda _: None)
    monkeypatch.setattr(hook_runtime, "_exact_base_delivery_hook_available", lambda: False)
    monkeypatch.setattr(hook_runtime, "read_transport_root_secret", lambda: TRANSPORT_ROOT_SECRET)
    yield
    hook_runtime.reset_runtime_state()


def _generated_completion(renderer):
    namespace = {}
    exec(
        "async def complete(self, source, event, event_message_id, response, created_at):\n"
        "    _response_time = 0.0\n"
        "    agent_result = {}\n"
        + "".join(renderer("    ", "\n"))
        + "    return response\n",
        namespace,
    )
    return namespace["complete"]


def _sidecar_app(transport, directory, **options):
    return server.create_app(
        transport,
        card_config={"flush_interval_ms": 0, **options},
        session_store_directory=directory,
        native_handoff_store=NativeHandoffStore(directory / "handoff"),
        operations_transport_root_secret=TRANSPORT_ROOT_SECRET,
        event_auth_required=True,
    )


@pytest.fixture
async def restored_v1_fixture(tmp_path, gateway_runtime, monkeypatch):
    transport = DeduplicatingClient()
    source = SimpleNamespace(platform="feishu", chat_id="oc_abc", thread_id="omt_topic")
    app = _sidecar_app(transport, tmp_path)
    complete = _generated_completion(patcher._render_complete_hook_block)
    with monkeypatch.context() as patch:
        async with TestServer(app) as sidecar:
            patch.setenv("HERMES_FEISHU_CARD_EVENT_URL", str(sidecar.make_url("/events")))
            assert await complete(
                None, source, SimpleNamespace(), "om_reused", "Original v1 answer", time.time(),
            ) is None
            path, = app[server.SESSION_STORE_KEY].root.glob("*.json")
            envelope = json.loads(path.read_bytes())
            record = envelope["record"]
            assert record["version"] == 1 and record["key"] == "default:om_reused"
            # Reproduce the public v1 format without requiring the old package
            # or retaining its timestamped checkpoint as a static test asset.
            for field in ("legacy_terminal_fingerprint", "legacy_route_thread_id",
                          "confirmed_legacy_terminal_receipts"):
                record.pop(field, None)
            body = json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
            envelope["digest"] = sha256(body).hexdigest()
            path.write_text(json.dumps(envelope, ensure_ascii=False, allow_nan=False))
    hook_runtime.reset_runtime_state()
    return transport, source


@pytest.mark.parametrize(
    "renderer",
    [
        patcher._render_complete_hook_block,
        patcher._render_complete_hook_block_with_reply_anchor,
        patcher._render_decomposed_complete_hook_block,
    ],
    ids=["ordinary", "mainline-reply-anchor", "mainline-decomposed"],
)
@pytest.mark.parametrize("timing", ["producer", "ingress"])
async def test_native_long_then_new_short_through_generated_hook(
    tmp_path, restored_v1_fixture, monkeypatch, renderer, timing,
):
    transport, source = restored_v1_fixture
    native = FakeFeishuClient()
    long_body = _long_answer()
    short_body = "Short answer from a different generation"
    late_body = "Late terminal with earlier ingress"
    unordered_body = "Terminal whose generation has no ordering evidence"
    unbound_body = "Terminal without a Hermes event carrier"
    conflicting_body = "A callback with different content on a known generation"
    requests = []

    @web.middleware
    async def record_events(request, handler):
        if request.path != "/events":
            return await handler(request)
        event_body = await request.read()
        payload = json.loads(event_body)
        response = await handler(request)
        requests.append((payload, event_body, response.status, json.loads(response.body)))
        return response

    app = _sidecar_app(transport, tmp_path, over_limit="native")
    app.middlewares.insert(0, record_events)
    complete = _generated_completion(renderer)
    runner = SimpleNamespace(_reply_anchor_for_event=lambda event: event.reply_anchor)
    def turn(at=None):
        event = SimpleNamespace(reply_anchor="om_reused")
        if timing == "ingress" and at is not None:
            event.timestamp = datetime.fromtimestamp(at, tz=timezone.utc)
        return event

    async def deliver(event, answer, at=None):
        native_body = await complete(
            runner, source, event, "om_reused", answer,
            at if timing == "producer" else None,
        )
        if native_body is not None:
            await native.send_text_message(source.chat_id, native_body, thread_id=source.thread_id)

    async with TestServer(app) as sidecar:
        monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", str(sidecar.make_url("/events")))
        await app[server.SESSION_RESTORE_TASK_KEY]
        assert app[server.SESSIONS_KEY]["default:om_reused"].restored_legacy_terminal
        old_card = copy.deepcopy(transport.cards["card-0"])
        old_updates = list(transport.updated)
        first_at = time.time()
        first = turn(first_at)
        second = turn(first_at + 1)
        await deliver(first, long_body, first_at)
        await deliver(first, long_body, first_at)
        await deliver(second, short_body, first_at + 1)
        # The old callbacks arrive with a fresher wall time. Hermes still owns
        # the original event object for each generation.
        await deliver(first, long_body, first_at + 3)
        await deliver(second, short_body, first_at + 4)
        await deliver(second, conflicting_body, first_at + 4)
        for event_name in ("message.started", "answer.delta"):
            assert not await hook_runtime.emit_from_hermes_locals_async(
                {"source": source, "event": first, "message_id": first.reply_anchor,
                 "created_at": first_at + 5, "text": "late delta"},
                event_name,
            )
        # Neither an unseen older generation nor an unorderable one may
        # recycle the new card or lose its raw native body.
        await deliver(turn(first_at - 1), late_body, first_at - 1)
        await deliver(turn(), unordered_body)
        await deliver(None, unbound_body, first_at + 6)

    payloads, event_bodies, statuses, replies = zip(*requests)
    generations = [payload["data"]["native_handoff"]["generation"] for payload in payloads]
    markers = [payload["data"].get("policy_new_turn") is True for payload in payloads]
    visible = [message_id for message_id, card in transport.cards.items() if short_body in str(card)]
    print(
        f"{renderer.__name__}/{timing}: markers={markers}, statuses={statuses}, replies={replies}, "
        f"same_first_generation={generations[0] == generations[1]}, "
        f"distinct_second_generation={generations[1] != generations[2]}, "
        f"native_long_count={sum(row[1] == long_body for row in native.texts)}, "
        f"native_short_count={sum(row[1] == short_body for row in native.texts)}, "
        f"short_cards={visible}"
    )
    assert {payload["event"] for payload in payloads} == {"message.completed"}
    assert {payload["message_id"] for payload in payloads} == {"om_reused"}
    assert not any(payload.get("turn_id") for payload in payloads)
    assert all(set(payload["data"]["native_handoff"]) == {"generation"} for payload in payloads)
    assert generations[0] == generations[1] == generations[3] != generations[2] == generations[4]
    assert event_bodies[0] == event_bodies[1] == event_bodies[3]
    assert event_bodies[2] == event_bodies[4]
    assert markers == [True] * 5
    assert statuses == (200,) * 5
    assert replies == (
        {"ok": True, "applied": False, "disposition": "native"},
        {"ok": True, "applied": True},
        {"ok": True, "applied": True, "delivery": {"outcome": "delivered"}},
        {"ok": True, "applied": True},
        {"ok": True, "applied": True, "delivery": {"outcome": "delivered"}},
    )
    assert native.texts == [
        ("oc_abc", body, "omt_topic", None)
        for body in (long_body, conflicting_body, late_body, unordered_body, unbound_body)
    ]
    assert visible == ["card-1"]
    assert transport.cards["card-0"] == old_card
    assert transport.updated == old_updates
