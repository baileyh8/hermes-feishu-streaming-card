"""Requester evidence for card delivery after a sidecar restart."""
import asyncio
import hashlib
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import server
from hermes_feishu_card.feishu_client import FeishuAPIError
from tests.integration.test_reopened_delivery_identity import DeduplicatingClient
from tests.integration.test_server import FakeFeishuClient, create_app, event_payload, wait_for_card_update


def _sidecar(transport, directory):
    return TestClient(TestServer(create_app(
        transport, session_store_directory=directory, card_config={"flush_interval_ms": 0},
    )))


def _group_event(kind, sequence, message_id, tenant_user, *, thread="om_root", profile="work", **content):
    return event_payload(
        kind, sequence, {
            "chat_type": "group", "profile_id": profile, "admission_user_id": tenant_user,
            "reply_to_message_id": "om_shared", **content,
        },
        message_id=message_id, conversation_id="omt_topic", thread_id=thread,
    )


async def _post(client, payload, status=200):
    response = await client.post("/events", json=payload)
    result = await response.json()
    assert response.status == status, result
    return result


@pytest.mark.parametrize("shared_start_id", [False, True], ids=["distinct-starts", "shared-start-id"])
async def test_tenant_only_requesters_complete_their_original_cards_after_restart(tmp_path, shared_start_id):
    transport = FakeFeishuClient()
    tenants = ("fixture-alice", "fixture-bob")

    async with _sidecar(transport, tmp_path) as client:
        for index, tenant in enumerate(tenants):
            message_id = "om_shared" if shared_start_id else f"om_start_{index}"
            started = await _post(client, _group_event("message.started", 0, message_id, tenant))
            assert started["delivery"]["outcome"] == "delivered"
        assert len(transport.sent) == 2

    terminals = [
        _group_event("message.completed", 1, "om_internal", tenant, answer=f"FINAL ANSWER {index}")
        for index, tenant in enumerate(tenants)
    ]
    async with _sidecar(transport, tmp_path) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        assert client.app[server.DIAGNOSTICS_KEY]["card_checkpoints_restored"] == 2
        assert all(not session.sender_user_id for session in client.app[server.SESSIONS_KEY].values())

        results = await asyncio.gather(*(_post(client, terminal) for terminal in terminals))
        assert all(result["applied"] for result in results)
        assert len(transport.sent) == 2
        for index in range(2):
            message_id, _ = await wait_for_card_update(transport, f"FINAL ANSWER {index}")
            assert message_id == f"feishu-message-{index + 1}"

    async with _sidecar(transport, tmp_path) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        before = list(transport.updated)
        results = await asyncio.gather(*(_post(client, terminal) for terminal in reversed(terminals)))
        assert all(result["applied"] for result in results)
        assert transport.updated == before
        assert len(transport.sent) == 2


@pytest.mark.parametrize(("message_id", "tenant_user", "sender", "thread", "profile"), [
    ("om_internal", "", "", "om_root", "work"),
    ("om_internal", "fixture-bob", "ou_alice", "om_root", "work"),
    ("om_original", "fixture-bob", "", "om_root", "work"),
    ("om_internal", "fixture-alice", "ou_bob", "om_root", "work"),
    ("om_internal", "fixture-alice", "ou_alice", "om_other", "work"),
    ("om_internal", "fixture-alice", "ou_alice", "om_root", "personal"),
], ids=["missing", "tenant-conflict", "direct-tenant-conflict", "open-id-conflict", "topic", "profile"])
async def test_unrelated_legacy_terminal_leaves_restored_card_untouched(
    tmp_path, message_id, tenant_user, sender, thread, profile,
):
    transport = FakeFeishuClient()
    async with _sidecar(transport, tmp_path) as client:
        await _post(client, _group_event(
            "message.started", 0, "om_original", "fixture-alice", sender_open_id="ou_alice",
        ))

    async with _sidecar(transport, tmp_path) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        result = await _post(client, _group_event(
            "message.completed", 1, message_id, tenant_user, thread=thread, profile=profile,
            sender_open_id=sender, answer="UNRELATED ANSWER",
        ))
        assert result["applied"]
        assert len(transport.sent) == 2
        assert "UNRELATED ANSWER" in str(transport.sent[-1][1])
        original = client.app[server.SESSIONS_KEY]["work:om_original"]
        assert original.status == "thinking"
        assert original.answer_text == ""
        assert all("UNRELATED ANSWER" not in str(card) for _, card in transport.updated)


@pytest.mark.parametrize("invalid_digest", [None, ["bad-metadata"]], ids=["old-v1", "malformed-digest"])
async def test_checkpoint_without_requester_evidence_does_not_claim_a_quote(tmp_path, invalid_digest):
    transport = FakeFeishuClient()
    async with _sidecar(transport, tmp_path) as client:
        started = await _post(client, _group_event("message.started", 0, "om_original", "fixture-alice"))
        assert started["delivery"]["outcome"] == "delivered"

    path = next((tmp_path / "card-checkpoints-v1").glob("*.json"))
    envelope = json.loads(path.read_bytes())
    record = envelope["record"]
    if invalid_digest is None:
        del record["display_requester_digest"]
    else:
        record["display_requester_digest"] = invalid_digest
    material = json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True)
    envelope["digest"] = hashlib.sha256(material.encode()).hexdigest()
    path.write_text(json.dumps(envelope))

    async with _sidecar(transport, tmp_path) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        assert client.app[server.DIAGNOSTICS_KEY]["card_checkpoints_restored"] == 1
        result = await _post(client, _group_event(
            "message.completed", 1, "om_internal", "fixture-alice", answer="UNVERIFIED ANSWER",
        ))
        assert result["delivery"]["outcome"] == "delivered"
        assert len(transport.sent) == 2
        assert client.app[server.SESSIONS_KEY]["work:om_original"].status == "thinking"


async def test_rebound_quote_retains_restored_owner_when_an_event_omits_tenant_user(tmp_path):
    transport = FakeFeishuClient()
    async with _sidecar(transport, tmp_path) as client:
        await _post(client, _group_event(
            "message.started", 0, "om_original", "fixture-alice", sender_open_id="ou_alice",
        ))

    async with _sidecar(transport, tmp_path) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        delta = await _post(client, _group_event(
            "answer.delta", 1, "om_original", "", sender_open_id="ou_alice",
            reply_to_message_id="om_new_quote", text="ALICE DRAFT",
        ))
        assert delta["applied"]
        assert not client.app[server.SESSIONS_KEY]["work:om_original"].sender_user_id
        await _post(client, _group_event(
            "message.started", 0, "om_bob", "fixture-bob", reply_to_message_id="om_new_quote",
        ))
        terminal = await _post(client, _group_event(
            "message.completed", 2, "om_internal", "fixture-alice",
            reply_to_message_id="om_new_quote", answer="ALICE FINAL",
        ))
        assert terminal["applied"]
        assert len(transport.sent) == 2
        assert (await wait_for_card_update(transport, "ALICE FINAL"))[0] == "feishu-message-1"


async def test_restored_reopen_requires_the_pending_requester_when_supplied(tmp_path, monkeypatch):
    transport = DeduplicatingClient()
    send = transport.send_card_delivery

    def payload(kind, sequence, tenant_user, *, created_at, **content):
        return event_payload(
            kind, sequence, {"chat_type": "group", "admission_user_id": tenant_user, **content},
            turn_id="reopen-turn", created_at=created_at,
        )

    async def lose_ack(*args, **kwargs):
        await send(*args, **kwargs)
        raise FeishuAPIError("fixture lost ACK", outcome="unknown")

    start_at = 1777017600.0
    async with _sidecar(transport, tmp_path) as client:
        await _post(client, payload(
            "message.completed", 1, "fixture-predecessor", created_at=start_at, answer="OLD BODY",
        ))
        with monkeypatch.context() as scope:
            scope.setattr(transport, "send_card_delivery", lose_ack)
            reopened = await _post(client, payload(
                "message.started", 0, "fixture-reopened", created_at=start_at + 10,
            ), status=502)
        assert reopened["delivery"]["outcome"] == "unknown"
        reopen_uuid = transport.attempted_uuids[-1]

    async with _sidecar(transport, tmp_path) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        predecessor = client.app[server.SESSIONS_KEY]["reopen-turn"]
        pending = predecessor.pending_reopen_create
        assert pending is not None
        assert not predecessor.sender_user_id and not pending.candidate.sender_user_id
        original = transport.cards["card-0"]
        attempts = list(transport.attempted_uuids)
        rejected = await _post(client, payload(
            "message.completed", 1, "fixture-predecessor", created_at=start_at + 11, answer="WRONG OWNER",
        ))
        assert rejected == {"ok": True, "applied": False, "disposition": "native"}
        assert transport.attempted_uuids == attempts
        assert predecessor.pending_reopen_create is pending

        recovered = await _post(client, payload(
            "message.completed", 1, "fixture-reopened", created_at=start_at + 12, answer="REOPEN BODY",
        ))
        assert recovered["delivery"]["outcome"] == "delivered"
        assert transport.attempted_uuids == [*attempts, reopen_uuid]
        assert len(transport.cards) == 2
        assert transport.cards["card-0"] == original
        assert "REOPEN BODY" in str(transport.cards["card-1"])
