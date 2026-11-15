"""Issue #529 QA baseline: duplicated final answer after terminal ACK loss.

Harness provenance and scope
----------------------------

Plugin-side Hermes functions (Base adapter, session ingress, session
authority, turn persistence) are the pinned V0226 fixture sources running
through the harness fixture of test_session_authority_completion.py, imported
with pytest.param(..., indirect=True); that module's fixture setup (private
HOME/state, fake ledger, fake native send counters) applies unchanged. This
module only re-points load_runtime_config and _post_json_response at the real
loopback sidecar. No fixture internals are re-implemented here.

The sidecar is the real hermes_feishu_card.server.create_app /events handler
on aiohttp TestServer loopback TCP with a real on-disk NativeHandoffStore.
Fake ports remain the sidecar's Feishu I/O adapter and Hermes' ledger/DB
ports inside the harness.

Not claimed: a complete v0.23.1 Gateway. The harness cohort tops out at
v0.22.6; a 0231 full-Gateway control is a separate follow-up layer.

Independence from root's test_root_ack.py
----------------------------------------

Root covers connect-refused, handler-then-close (middleware) and a real 10s
timeout, asserting duplication via terminal_applied plus the native list.
This module adds three complementary controls with its own trace fields
(applied flag, card-patch counter, response sequence):

1. lost first response after application: the handler first applies the
   terminal, then closes its transport; the replay remains reachable.
   At baseline this duplicates natively (red); after the fix it must not;
2. late replay: a duplicate terminal POST arriving after the turn settled
   hits the sidecar's applied cache (applied=True, no new card I/O, native
   stays suppressed);
3. terminal rejection before acceptance: a real-HTTP 403 {"ok": false}
   refusal (handler never runs) is final, is not retried into success, and
   the native body delivers exactly once with no card.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from hermes_feishu_card import hook_runtime as h
from hermes_feishu_card.server import SESSIONS_KEY, create_app
from hermes_feishu_card.native_handoff import NativeHandoffStore

from test_session_authority_completion import (  # noqa: F401
    Event,
    Source,
    V0226_FIXTURE,
    deliver,
    harness,
)


# No pytest-timeout plugin: each test bounds itself (deliver timeout + shrunk
# terminal retry budget) and the whole invocation runs under a subprocess
# timeout enforced by the runner command.


class CardCounterPort:
    """Sidecar Feishu I/O adapter that counts terminal card operations."""

    def __init__(self):
        self.card_sends = 0
        self.card_updates = 0

    async def send_card(self, *a, **k):
        self.card_sends += 1
        return "om_issue529-card"

    async def update_card_message(self, *a, **k):
        self.card_updates += 1

    async def send_text_message(self, *a, **k):
        raise AssertionError("sidecar must not send raw text for a card turn")


class SidecarHarness:
    """Real /events sidecar on loopback TCP with instrumented middleware."""

    def __init__(self, tmp_path, monkeypatch, harness_ns, timeout_seconds):
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.harness_ns = harness_ns
        self.timeout_seconds = timeout_seconds
        self.port = CardCounterPort()
        self.app = None
        self.server = None
        self.url = ""
        self.trace = {
            "terminal_requests": 0,
            "terminal_responses": [],
            "terminal_payloads": [],
            "terminal_event_ids": [],
            "terminal_applied": False,
            "delay_gate": None,
            "reject_terminal": False,
            "drop_first_terminal_response": False,
            "terminal_dropped": 0,
            "hold_all_terminal_responses": False,
        }

    async def start(self):
        self.app = create_app(
            self.port,
            native_handoff_store=NativeHandoffStore(self.tmp_path / "handoff"),
            operations_transport_root_secret=b"x" * 32,
        )
        sidecar = self

        @web.middleware
        async def instrument(request, handler):
            payload = await request.json() if request.path == "/events" else {}
            is_terminal = payload.get("event") == "message.completed"
            if is_terminal and sidecar.trace.get("reject_terminal") is True:
                # Refuse before the handler runs: the request is never
                # accepted, which is the fail-open precondition.
                sidecar.trace["terminal_requests"] += 1
                sidecar.trace["terminal_responses"].append(
                    {"origin": "rejected", "status": 403, "ok": False}
                )
                return web.json_response(
                    {"ok": False, "error": "issue529 explicit refusal"},
                    status=403,
                )
            if is_terminal:
                sidecar.trace["terminal_requests"] += 1
                sidecar.trace["last_terminal_payload"] = payload
                sidecar.trace["terminal_payloads"].append(payload)
                sidecar.trace["terminal_event_ids"].append(payload.get("event_id"))
            response = await handler(request)
            if is_terminal:
                ordinal = sidecar.trace["terminal_requests"]
                drop = (
                    sidecar.trace.get("hold_all_terminal_responses") is True
                    or (
                        sidecar.trace.get("drop_first_terminal_response") is True
                        and ordinal == 1
                    )
                )
                if drop:
                    # Real receipt loss: the handler has already applied the
                    # terminal (effects committed server-side); closing the
                    # transport drops the response bytes on the wire.
                    sidecar.trace["terminal_dropped"] += 1
                    request.transport.close()
                    return response
                gate = sidecar.trace.get("delay_gate")
                if gate is not None:
                    # Legacy hold-everything knob (kept for the probe).
                    await gate["gate"].wait()
                body = {}
                raw = response.body
                if raw:
                    try:
                        body = json.loads(bytes(raw))
                    except (ValueError, TypeError):
                        body = {}
                sidecar.trace["terminal_responses"].append(
                    {
                        "origin": "handler",
                        "status": response.status,
                        "ok": body.get("ok"),
                        "applied": body.get("applied"),
                    }
                )
                if any(s.status == "completed" for s in sidecar.app[SESSIONS_KEY].values()):
                    sidecar.trace["terminal_applied"] = True
            return response

        self.app.middlewares.append(instrument)
        self.server = TestServer(self.app, host="127.0.0.1")
        await self.server.start_server()
        self.url = str(self.server.make_url("/events"))
        self.monkeypatch.setattr(
            h,
            "load_runtime_config",
            lambda: SimpleNamespace(
                enabled=True,
                event_url=self.url,
                timeout_seconds=self.timeout_seconds,
            ),
        )
        self.monkeypatch.setattr(h, "_post_json_response", self.harness_ns.real_post)

    async def stop(self):
        if self.server is not None:
            await self.server.close()

    def started_event(self, event):
        return h.build_event(
            "message.started",
            {
                "event": event,
                "source": event.source,
                "message_id": event.message_id,
                "answer": "",
            },
        )

    async def post_started(self, event):
        payload = self.started_event(event)
        async with ClientSession() as client:
            async with client.post(self.url, json=payload) as response:
                return response.status, await response.json()

    def sessions(self):
        return list(self.app[SESSIONS_KEY].values())


@pytest.fixture
def retry_budget(monkeypatch):
    """Shrink the terminal retry budget so scenarios stay inside 60s."""
    monkeypatch.setattr(h, "TERMINAL_DELIVERY_RETRY_BUDGET_SECONDS", 8.0)
    monkeypatch.setattr(h, "TERMINAL_DELIVERY_RETRY_DELAYS", (0.05, 0.1))


@pytest.mark.parametrize(
    "harness", [(V0226_FIXTURE, True)], ids=["0226-real-owner"], indirect=True,
)
async def test_applied_terminal_suppresses_native(
    harness,  # noqa: F811
    monkeypatch,
    tmp_path,
):
    """Normal success: real-HTTP applied terminal ACKs, native sends nothing."""
    sidecar = SidecarHarness(tmp_path, monkeypatch, harness, timeout_seconds=5)
    await sidecar.start()
    try:
        event = Event()
        status, body = await sidecar.post_started(event)
        assert status == 200 and body["applied"] is True
        await deliver(harness, waiter=True, event=event, timeout=30)
        assert sidecar.trace["terminal_applied"] is True
        assert harness.native == []
        assert harness.ledger[-1] == ("final", "ingress-a", True)
        assert sidecar.port.card_updates >= 1
    finally:
        await sidecar.stop()


@pytest.mark.parametrize(
    "harness", [(V0226_FIXTURE, True)], ids=["0226-real-owner"], indirect=True,
)
async def test_first_terminal_response_lost_replay_still_reaches_plugin(
    harness, monkeypatch, tmp_path, retry_budget  # noqa: F811
):
    """Scenario A (first-only loss): the handler applies the terminal, the
    first response is dropped, and the same-event replay still reaches the
    plugin through the sidecar cache. The card stays applied and NO native
    duplicate is sent. RED at the pre-fix baseline (native duplicate was
    observed there), GREEN with the integrated #529 fix."""
    from hermes_feishu_card import hook_runtime as hr

    monkeypatch.setattr(hr, "TERMINAL_TIMEOUT_SECONDS", 0.3)
    sidecar = SidecarHarness(tmp_path, monkeypatch, harness, timeout_seconds=0.3)
    await sidecar.start()
    try:
        event = Event()
        status, body = await sidecar.post_started(event)
        assert status == 200 and body["applied"] is True
        # Drop ONLY the first terminal response (ordinal == 1). Later
        # attempts/replays return normally through the real handler.
        sidecar.trace["drop_first_terminal_response"] = True
        await deliver(harness, waiter=True, event=event, timeout=40)
        assert sidecar.trace["terminal_requests"] == 2, (
            "first attempt plus exactly one replay must reach the sidecar"
        )
        assert sidecar.trace["terminal_dropped"] == 1, (
            "exactly the first terminal response must be dropped"
        )
        assert sidecar.trace["terminal_applied"] is True
        assert harness.native == [], (
            "#529: the sidecar applied this exact terminal and the replay"
            " reached the plugin; a native body duplicate is the defect"
        )
        assert harness.ledger[-1] == ("final", "ingress-a", True)
    finally:
        await sidecar.stop()


@pytest.mark.parametrize(
    "harness", [(V0226_FIXTURE, True)], ids=["0226-real-owner"], indirect=True,
)
async def test_all_terminal_acks_unreachable_keeps_native_once(
    harness, monkeypatch, tmp_path, retry_budget  # noqa: F811
):
    """Scenario B (all-ACK-unknown): every terminal response is lost.
    After bounded replay/recovery attempts, the plugin must send
    the native body exactly once and must not blindly suppress. This is
    the network-unknown boundary: card/native duplication is possible
    here and is NOT a fix target."""
    from hermes_feishu_card import hook_runtime as hr

    monkeypatch.setattr(hr, "TERMINAL_TIMEOUT_SECONDS", 0.3)
    sidecar = SidecarHarness(tmp_path, monkeypatch, harness, timeout_seconds=0.3)
    await sidecar.start()
    try:
        event = Event()
        status, body = await sidecar.post_started(event)
        assert status == 200 and body["applied"] is True
        sidecar.trace["hold_all_terminal_responses"] = True
        await deliver(harness, waiter=True, event=event, timeout=40)
        assert any(s.status == "completed" for s in sidecar.sessions()), (
            "the sidecar must have applied the terminal server-side"
        )
        assert harness.native == ["complete answer"], (
            "with every ACK unreachable the native body must deliver exactly once"
        )
        assert harness.native.count("complete answer") == 1
        assert harness.ledger[-1] == ("final", "ingress-a", True)
    finally:
        await sidecar.stop()

@pytest.mark.parametrize(
    "harness", [(V0226_FIXTURE, True)], ids=["0226-real-owner"], indirect=True,
)
async def test_explicit_ok_false_terminal_is_final_not_retried_into_success(
    harness, monkeypatch, tmp_path, retry_budget  # noqa: F811
):
    """Real-HTTP 403 ok:false refusal before acceptance: native exactly once."""
    sidecar = SidecarHarness(tmp_path, monkeypatch, harness, timeout_seconds=0.3)
    await sidecar.start()
    try:
        event = Event()
        status, body = await sidecar.post_started(event)
        assert status == 200 and body["applied"] is True
        sidecar.trace["reject_terminal"] = True
        await deliver(harness, waiter=True, event=event, timeout=30)
        assert sidecar.trace["terminal_requests"] == 1, (
            "explicit ok=false must not be retried as if never ruled"
        )
        assert all(
            r["status"] == 403 and r["ok"] is False
            for r in sidecar.trace["terminal_responses"]
        )
        assert harness.native == ["complete answer"]
        assert harness.ledger[-1] == ("final", "ingress-a", True)
        assert all(s.status != "completed" for s in sidecar.sessions())
        # message.started legitimately sends the first card; the refused
        # terminal must not add a card update or text.
        assert sidecar.port.card_updates == 0
    finally:
        await sidecar.stop()


@pytest.mark.parametrize(
    "harness", [(V0226_FIXTURE, True)], ids=["0226-real-owner"], indirect=True,
)
async def test_invalidated_stage_cannot_consume_applied_ack(
    harness, monkeypatch, tmp_path  # noqa: F811
):
    """Cancel path: a stage cleared mid-POST cannot consume the applied ACK."""
    sidecar = SidecarHarness(tmp_path, monkeypatch, harness, timeout_seconds=5)
    await sidecar.start()
    try:
        event = Event()
        status, body = await sidecar.post_started(event)
        assert status == 200 and body["applied"] is True

        async def post_and_invalidate(url, payload, timeout):
            stage = h._HFC_EXACT_COMPLETION_STAGE.get()
            assert stage is not None
            h._HFC_EXACT_COMPLETION_STAGE.set(None)
            return {"ok": True, "applied": True}

        monkeypatch.setattr(h, "_post_json_response", post_and_invalidate)
        await deliver(harness, waiter=True, event=event, timeout=30)
        assert harness.native == ["complete answer"], (
            "invalidated stage must not suppress the native body"
        )
        assert harness.ledger[-1] == ("final", "ingress-a", True)
    finally:
        await sidecar.stop()


@pytest.mark.parametrize(
    "harness", [(V0226_FIXTURE, True)], ids=["0226-real-owner"], indirect=True,
)
async def test_changed_frozen_payload_rejects_ack_keeps_native(
    harness, monkeypatch, tmp_path  # noqa: F811
):
    """Binding: mutating the frozen request after acceptance rejects the ACK."""
    sidecar = SidecarHarness(tmp_path, monkeypatch, harness, timeout_seconds=5)
    await sidecar.start()
    try:
        event = Event()
        status, body = await sidecar.post_started(event)
        assert status == 200 and body["applied"] is True

        async def post_and_mutate(url, payload, timeout):
            assert payload["data"]["answer"] == "complete answer"
            payload["data"]["answer"] = "changed"
            return {"ok": True, "applied": True}

        monkeypatch.setattr(h, "_post_json_response", post_and_mutate)
        await deliver(harness, waiter=True, event=event, timeout=30)
        assert harness.native == ["complete answer"], (
            "a mutated frozen request must not consume the applied ACK"
        )
        assert harness.ledger[-1] == ("final", "ingress-a", True)
    finally:
        await sidecar.stop()


@pytest.mark.parametrize(
    "harness", [(V0226_FIXTURE, True)], ids=["0226-real-owner"], indirect=True,
)
async def test_recovery_runs_in_same_task_and_sets_context(
    harness, monkeypatch, tmp_path  # noqa: F811
):
    """The recovery helper must await recover in the SAME task: a fake
    recover that records its task and sets HFC_NATIVE_HANDOFF_CONTEXT must
    be observable from the direct caller (a wait_for child task would
    hide both the task identity and the ContextVar update)."""
    from hermes_feishu_card import hook_runtime as hr

    sidecar = SidecarHarness(tmp_path, monkeypatch, harness, timeout_seconds=5)
    token_holder = {}
    await sidecar.start()
    try:
        event = Event()
        status, body = await sidecar.post_started(event)
        assert status == 200 and body["applied"] is True
        await deliver(harness, waiter=True, event=event, timeout=30)
        assert harness.native == []

        stage = {
            "payload": {},
            "event_url": sidecar.url,
            "timeout_seconds": 5,
            "_terminal_recovery_deadline": None,
        }
        seen = {}
        async def fake_recover(payload, *, event_url, timeout):
            seen["task"] = asyncio.current_task()
            token_holder["token"] = h._HFC_NATIVE_HANDOFF_CONTEXT.set(
                {"descriptor": "fake"}
            )
            return True

        monkeypatch.setattr(
            hr, "_recover_exact_terminal_native_handoff", fake_recover
        )
        monkeypatch.setattr(
            hr, "_native_handoff_binding_from_payload", lambda payload: {}
        )

        caller = asyncio.current_task()
        result = await hr._recover_terminal_with_remaining_budget(
            stage, {"event_id": "hfc-terminal:probe"},
            event_url=sidecar.url, timeout=5,
        )
        assert result is True
        assert seen["task"] is caller, (
            "recover must run in the caller task, not a wait_for child"
        )
        assert h._HFC_NATIVE_HANDOFF_CONTEXT.get() == {"descriptor": "fake"}, (
            "ContextVar set inside recover must be readable by the caller"
        )
    finally:
        if "token" in token_holder:
            h._HFC_NATIVE_HANDOFF_CONTEXT.reset(token_holder["token"])
        await sidecar.stop()


@pytest.mark.parametrize(
    "harness", [(V0226_FIXTURE, True)], ids=["0226-real-owner"], indirect=True,
)
async def test_spent_recovery_budget_skips_recover(
    harness, monkeypatch, tmp_path  # noqa: F811
):
    """A recovery deadline already in the past must skip recover entirely."""
    from hermes_feishu_card import hook_runtime as hr

    sidecar = SidecarHarness(tmp_path, monkeypatch, harness, timeout_seconds=5)
    await sidecar.start()
    try:
        event = Event()
        status, body = await sidecar.post_started(event)
        assert status == 200 and body["applied"] is True
        await deliver(harness, waiter=True, event=event, timeout=30)
        assert harness.native == []

        calls = []

        async def fake_recover(payload, *, event_url, timeout):
            calls.append(dict(payload))
            return True

        monkeypatch.setattr(
            hr, "_recover_exact_terminal_native_handoff", fake_recover
        )

        stage = {
            "payload": {},
            "event_url": sidecar.url,
            "timeout_seconds": 5,
            "_terminal_recovery_deadline": 0.0,
        }
        result = await hr._recover_terminal_with_remaining_budget(
            stage, {"event_id": "hfc-terminal:probe"},
            event_url=sidecar.url, timeout=5,
        )
        assert result is False, "spent budget must return False"
        assert calls == [], "spent budget must not call recover"
    finally:
        await sidecar.stop()


@pytest.mark.parametrize(
    "harness", [(V0226_FIXTURE, True)], ids=["0226-real-owner"], indirect=True,
)
async def test_retry_body_identity_and_single_replay(
    harness, monkeypatch, tmp_path, retry_budget  # noqa: F811
):
    """Identity: the retried terminal body equals the first attempt body
    byte-for-byte (same frozen event_id) and the sidecar counts exactly one
    event_id replay (cache hit, no second card I/O)."""
    from hermes_feishu_card import hook_runtime as hr

    monkeypatch.setattr(hr, "TERMINAL_TIMEOUT_SECONDS", 0.3)
    sidecar = SidecarHarness(tmp_path, monkeypatch, harness, timeout_seconds=0.3)
    await sidecar.start()
    try:
        event = Event()
        status, body = await sidecar.post_started(event)
        assert status == 200 and body["applied"] is True
        sidecar.trace["drop_first_terminal_response"] = True
        await deliver(harness, waiter=True, event=event, timeout=40)
        payloads = sidecar.trace["terminal_payloads"]
        assert len(payloads) == 2, (
            "exactly the first attempt plus one replay may reach the sidecar"
        )
        first, second = payloads[0], payloads[1]
        assert first == second, (
            "retry body must equal the first attempt byte-for-byte"
        )
        event_ids = sidecar.trace["terminal_event_ids"]
        assert event_ids == [first.get("event_id"), second.get("event_id")]
        assert first.get("event_id"), "terminal payload must carry event_id"
        assert first.get("event_id") == second.get("event_id"), (
            "the replay must reuse the frozen event identity"
        )
        assert first.get("producer") == "patch"
        assert first.get("phase") == "terminal"
        from hermes_feishu_card.server import METRICS_KEY

        metrics = sidecar.app[METRICS_KEY]
        assert metrics.event_id_replays == 1, (
            "exactly one cache replay must be counted"
        )
        assert sidecar.trace["terminal_applied"] is True
        assert harness.native == []
    finally:
        await sidecar.stop()

@pytest.mark.parametrize(
    "harness", [(V0226_FIXTURE, True)], ids=["0226-real-owner"], indirect=True,
)
async def test_late_replay_of_terminal_after_settle_is_cached_not_reapplied(
    harness, monkeypatch, tmp_path  # noqa: F811
):
    """Late duplicate terminal POST hits the applied cache and stays applied."""
    sidecar = SidecarHarness(tmp_path, monkeypatch, harness, timeout_seconds=5)
    await sidecar.start()
    try:
        event = Event()
        status, body = await sidecar.post_started(event)
        assert status == 200 and body["applied"] is True
        await deliver(harness, waiter=True, event=event, timeout=30)
        assert harness.native == [] and sidecar.trace["terminal_applied"] is True
        updates_after_first = sidecar.port.card_updates
        terminal_payload = sidecar.trace["last_terminal_payload"]
        assert terminal_payload["event"] == "message.completed"
        async with ClientSession() as client:
            async with client.post(sidecar.url, json=terminal_payload) as response:
                replay = await response.json()
        assert response.status == 200
        assert replay.get("ok") is True and replay.get("applied") is True
        assert all(s.status == "completed" for s in sidecar.sessions())
        assert sidecar.port.card_updates == updates_after_first, (
            "late replay must not perform new card I/O"
        )
    finally:
        await sidecar.stop()


async def test_cancelled_admission_post_revokes_once_and_releases_lock(monkeypatch):
    """Task cancellation must propagate without replaying or leaking the claim/lock."""
    from hermes_feishu_card import admission_completion as ac

    stage = {}
    lock = asyncio.Lock()
    revoked = []
    posts = []
    attempt = SimpleNamespace(revoke=lambda: revoked.append(True))
    monkeypatch.setattr(ac, "begin_applied_attempt", lambda *args: attempt)
    monkeypatch.setattr(h, "_send_lock", lambda *args: lock)

    async def cancelled_post(*args):
        posts.append(True)
        raise asyncio.CancelledError

    monkeypatch.setattr(h, "_post_json_response", cancelled_post)
    token = h._HFC_EXACT_COMPLETION_STAGE.set(stage)
    try:
        with pytest.raises(asyncio.CancelledError):
            await h._post_admission_terminal(
                stage, {"event_id": "hfc-terminal:cancel"}, {}, None,
                "http://127.0.0.1:1/events", 1,
            )
        assert posts == [True]
        assert revoked == [True]
        assert not lock.locked()
        assert "_terminal_recovery_deadline" not in stage
    finally:
        h._HFC_EXACT_COMPLETION_STAGE.reset(token)
