"""PR #331: only a confirmed later delivery may retire same-route restart text."""
import asyncio
import copy
import json
import time
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import server
from hermes_feishu_card import hook_runtime as runtime
from hermes_feishu_card.bots import RouteResult
from hermes_feishu_card.feishu_client import FeishuAPIError
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.notice_lifecycle import RestartNoticeRegistry
from hermes_feishu_card.session import CardSession, InteractionState


class Client:
    def __init__(self):
        self.sent = []
        self.updated = []
        self.deleted = []
        self.fail_send = False
        self.fail_delete = False
        self.before_send = None
        self.fail_update = False
        self.before_update = None
        self.texts = []

    async def send_card(self, chat_id, card, **kwargs):
        if self.before_send:
            await self.before_send()
        if self.fail_send:
            raise FeishuAPIError("fixture refusal", outcome="not_sent")
        self.sent.append((chat_id, card))
        return f"om_card_{len(self.sent)}"

    async def update_card_message(self, message_id, card):
        if self.before_update:
            await self.before_update()
        if self.fail_update:
            raise FeishuAPIError("fixture update refusal")
        self.updated.append((message_id, copy.deepcopy(card)))
        return None

    async def send_text_message(self, chat_id, text, **kwargs):
        if self.fail_send:
            raise FeishuAPIError("fixture text refusal", outcome="not_sent")
        self.texts.append((chat_id, text))
        return f"om_text_{len(self.texts)}"

    async def delete_message(self, message_id):
        if self.fail_delete:
            raise FeishuAPIError("fixture delete refusal")
        self.deleted.append(message_id)
        return True


class Factory:
    def __init__(self, clients):
        self.clients = clients

    def get_client(self, bot_id):
        return self.clients[bot_id]


async def register(http, message_id, *, profile="default", thread="", bot="alpha"):
    return await http.post("/recall/schedule", json={
        "message_id": message_id,
        "notice_family": "restart",
        "record_only": True,
        "route": {"profile_id": profile, "chat_id": "oc_fixture",
                  "conversation_id": thread, "bot_id": bot},
    })


def app_with_clients():
    clients = {(profile, bot): Client() for profile in ("default", "work")
               for bot in ("alpha", "beta")}
    app = server.create_app(
        {p: Factory({b: clients[p, b] for b in ("alpha", "beta")})
         for p in ("default", "work")},
        bot_router=lambda e: RouteResult(e.data.get("bot_id", "alpha"), "fixture"),
    )
    return app, clients


async def drain():
    for _ in range(4):
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [None, "work"])
@pytest.mark.parametrize("first_event", ["message.started", "answer.delta"])
@pytest.mark.parametrize("restore", [False, True])
async def test_opaque_turn_id_uses_event_profile_for_notice_cleanup(
    tmp_path, monkeypatch, profile, first_event, restore,
):
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(tmp_path / "state"))
    fake = Client()

    def make_app():
        return server.create_app(fake, card_config={"flush_interval_ms": 0},
                                 session_store_directory=tmp_path / "checkpoints")

    def event(kind, sequence, **data):
        if profile is not None:
            data["profile_id"] = profile
        return dict(schema_version="1", event=kind, platform="feishu",
                    conversation_id="oc_fixture", message_id="om_source",
                    turn_id="opaque:turn", chat_id="oc_fixture", sequence=sequence,
                    created_at=time.time(), data=data)

    async def assert_update_scope(http, app):
        assert (await register(http, "om_own", profile=profile or "default")).status == 200
        assert (await register(http, "om_other_profile", profile="opaque")).status == 200
        response = await http.post("/events", json=event("answer.delta", 1, text="VISIBLE_RESULT"))
        assert response.status == 200, await response.text()
        await asyncio.sleep(.03)
        key = f"{profile}:opaque:turn" if profile else "opaque:turn"
        assert "VISIBLE_RESULT" in app[server.SESSIONS_KEY][key].answer_text
        assert "VISIBLE_RESULT" in str(fake.updated[-1][1])
        assert fake.deleted == ["om_own"]
        assert len(fake.sent) == 1

    app = make_app()
    async with TestClient(TestServer(app)) as http:
        response = await http.post("/events", json=event(first_event, 0, text="INITIAL"))
        assert response.status == 200, await response.text()
        if not restore:
            await assert_update_scope(http, app)
    if restore:
        app = make_app()
        async with TestClient(TestServer(app)) as http:
            await app[server.SESSION_RESTORE_TASK_KEY]
            await assert_update_scope(http, app)

    # The profile stays in the existing checkpoint envelope, without adding a
    # new session field that would invalidate older display-checkpoint readers.
    for path in (tmp_path / "checkpoints").rglob("*.json"):
        assert "route_profile_id" not in json.loads(path.read_text())["record"]["session"]

@pytest.mark.asyncio
async def test_restart_scope_is_exact_and_card_delivery_supersedes_only_its_scope():
    app, clients = app_with_clients()
    async with TestClient(TestServer(app)) as http:
        for profile, bot, thread, message in [
            ("default", "alpha", "", "om_default"),
            ("work", "alpha", "", "om_home"),
            ("work", "alpha", "omt_target", "om_target"),
            ("work", "alpha", "omt_other", "om_other"),
            ("work", "beta", "omt_target", "om_other_bot"),
        ]:
            assert (await register(http, message, profile=profile, bot=bot, thread=thread)).status == 200
        result = await server._send_card_for_app(
            app, "oc_fixture", {}, "work:alpha", thread_id="omt_target"
        )
        assert result.delivered
        await drain()
        assert clients["work", "alpha"].deleted == ["om_target"]
        assert clients["work", "beta"].deleted == []
        assert clients["default", "alpha"].deleted == []
        # An empty topic is an exact home scope, never a wildcard.
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        await drain()
        assert clients["work", "alpha"].deleted == ["om_target", "om_home"]


@pytest.mark.asyncio
async def test_failed_replacement_preserves_notice_until_confirmed_delivery():
    app, clients = app_with_clients()
    fake = clients["work", "alpha"]
    async with TestClient(TestServer(app)) as http:
        assert (await register(http, "om_restart", profile="work")).status == 200
        fake.fail_send = True
        result = await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        assert not result.delivered
        await drain()
        assert fake.deleted == []
        fake.fail_send = False
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        await drain()
        assert fake.deleted == ["om_restart"]


@pytest.mark.asyncio
async def test_late_success_does_not_retire_a_newer_restart_notice():
    app, clients = app_with_clients()
    fake = clients["work", "alpha"]
    async with TestClient(TestServer(app)) as http:
        assert (await register(http, "om_old", profile="work")).status == 200
        async def concurrent_restart():
            assert (await register(http, "om_new", profile="work")).status == 200
        fake.before_send = concurrent_restart
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        await drain()
        assert fake.deleted == ["om_old"]
        fake.before_send = None
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        await drain()
        assert fake.deleted == ["om_old", "om_new"]


@pytest.mark.asyncio
async def test_registry_capacity_preserves_prior_notices_and_duplicate_registration():
    app, clients = app_with_clients()
    app[server.RESTART_NOTICES_KEY] = RestartNoticeRegistry(max_scopes=1, max_members=2)
    async with TestClient(TestServer(app)) as http:
        assert (await register(http, "om_first", profile="work")).status == 200
        assert (await register(http, "om_first", profile="work")).status == 200
        # Reusing a real message id cannot transfer its ownership to another scope.
        assert (await register(http, "om_first", profile="default")).status == 429
        assert (await register(http, "om_second", profile="work")).status == 200
        assert (await register(http, "om_third", profile="work")).status == 429
        assert (await register(http, "om_elsewhere", profile="default")).status == 429
        assert app[server.EPHEMERAL_RECALL_TASKS_KEY] == {}  # no new 15-second default
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        await drain()
        assert clients["work", "alpha"].deleted == ["om_first", "om_second"]
        assert (await register(http, "om_elsewhere", profile="default")).status == 200


@pytest.mark.asyncio
async def test_update_uses_actual_message_owner_and_isolated_profile_and_topic():
    app, clients = app_with_clients()
    fake = clients["work", "alpha"]
    session = CardSession("omt_target", "om_user", "oc_fixture")
    app[server.SESSIONS_KEY]["work:om_user"] = session
    app[server.FEISHU_MESSAGE_IDS_KEY]["work:om_user"] = "om_owner"
    app[server.MESSAGE_BOT_IDS_KEY]["work:om_user"] = "work:alpha"
    async with TestClient(TestServer(app)) as http:
        await register(http, "om_old", profile="work", thread="omt_target")
        await register(http, "om_other", thread="omt_target")
        fake.fail_update = True
        assert not await server._update_card_for_app(app, "om_owner", {}, "work:alpha")
        await drain()
        assert fake.deleted == []
        fake.fail_update = False
        async def newer_notice():
            await register(http, "om_new", profile="work", thread="omt_target")
        fake.before_update = newer_notice
        assert await server._update_card_for_app(app, "om_owner", {}, "work:alpha")
        await drain()
        assert fake.deleted == ["om_old"]
        fake.before_update = None
        assert await server._update_card_for_app(app, "om_owner", {}, "work:alpha")
        await drain()
        assert fake.deleted == ["om_old", "om_new"]
        assert clients["default", "alpha"].deleted == []


@pytest.mark.asyncio
async def test_completion_notification_waits_for_success_and_keeps_the_receipt():
    app, clients = app_with_clients()
    fake = clients["work", "alpha"]
    session = CardSession("omt_target", "om_user", "oc_fixture", status="completed")
    app[server.MESSAGE_BOT_IDS_KEY]["work:om_user"] = "work:alpha"
    app[server.SESSION_CARD_CONFIGS_KEY]["work:om_user"] = {
        "completion_notify": {"enabled": True, "mention": False},
    }
    event = SidecarEvent.from_dict({
        "schema_version": "1", "event": "message.completed", "platform": "feishu",
        "conversation_id": "omt_target", "message_id": "om_user", "chat_id": "oc_fixture",
        "sequence": 2, "created_at": time.time(), "data": {"profile_id": "work"},
    })
    async with TestClient(TestServer(app)) as http:
        await register(http, "om_old", profile="work", thread="omt_target")
        await register(http, "om_other", thread="omt_target")
        fake.fail_send = True
        await server._maybe_send_completion_notify(app, "work:om_user", session, event)
        await drain()
        assert fake.deleted == []
        assert session.completion_notify_state == "idle"
        fake.fail_send = False
        await server._maybe_send_completion_notify(app, "work:om_user", session, event)
        await drain()
        assert fake.deleted == ["om_old"]
        assert len(fake.texts) == 1
        assert "本轮回复结束" in fake.texts[0][1]
        assert clients["default", "alpha"].deleted == []


@pytest.mark.asyncio
async def test_registered_notice_that_becomes_answer_or_interaction_is_retained():
    app, clients = app_with_clients()
    session = CardSession("oc_fixture", "om_user", "oc_fixture")
    app[server.SESSIONS_KEY]["work:om_user"] = session
    async with TestClient(TestServer(app)) as http:
        await register(http, "om_answer", profile="work")
        await register(http, "om_interaction", profile="work")
        app[server.FEISHU_MESSAGE_IDS_KEY]["work:om_user"] = "om_answer"
        session.active_interaction = InteractionState("question", "approval", "scope")
        session.active_interaction.feishu_message_id = "om_interaction"
        assert (await register(http, "om_answer", profile="work")).status == 409
        assert (await register(http, "om_interaction", profile="work")).status == 409
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        await drain()
        assert clients["work", "alpha"].deleted == []
        # Normal retention cleanup must never make that old answer disposable again.
        app[server.SESSIONS_KEY].clear()
        app[server.FEISHU_MESSAGE_IDS_KEY].clear()
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        await drain()
        assert clients["work", "alpha"].deleted == []


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["unknown", ["work"], "../work"])
async def test_notice_registration_does_not_fallback_to_other_profiles(profile):
    app, clients = app_with_clients()
    async with TestClient(TestServer(app)) as http:
        response = await register(http, "om_notice", profile=profile)
        assert response.status in (400, 409)
        assert app[server.EPHEMERAL_RECALL_TASKS_KEY] == {}


@pytest.mark.asyncio
async def test_first_thread_reply_anchor_does_not_clear_home_notice():
    app, clients = app_with_clients()
    async with TestClient(TestServer(app)) as http:
        await register(http, "om_home", profile="work")
        await server._send_card_for_app(
            app, "oc_fixture", {}, "work:alpha", reply_to_message_id="om_anchor", reply_in_thread=True,
        )
        await drain()
        assert clients["work", "alpha"].deleted == []


@pytest.mark.asyncio
async def test_notice_delivery_does_not_claim_the_gateway_has_resumed():
    app, clients = app_with_clients()
    async with TestClient(TestServer(app)) as http:
        await register(http, "om_restart", profile="work")
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha", delivery_kind="notice")
        await drain()
        assert clients["work", "alpha"].deleted == []


@pytest.mark.asyncio
async def test_hook_registration_and_event_delivery_share_the_single_client_profile(monkeypatch):
    fake = Client()
    app = server.create_app(fake, card_config={"flush_interval_ms": 0})
    async with TestClient(TestServer(app)) as http:
        monkeypatch.setattr(runtime, "load_runtime_config", lambda: SimpleNamespace(
            enabled=True, event_url=str(http.make_url("/events")), timeout_seconds=2,
        ))
        token = runtime._HFC_FEISHU_DELIVERY_CONTEXT.set({
            "chat_id": "oc_fixture", "profile_id": "work", "thread_id": "omt_target",
        })
        try:
            class Adapter:
                async def _hfc_original_send(self, *args, **kwargs):
                    return SimpleNamespace(success=True, message_id="om_online")
            result = await runtime._hfc_send_system_notice_card(
                Adapter(), chat_id="oc_fixture",
                content="♻ Gateway restarted successfully. Your session continues.",
            )
            assert result.success is True
        finally:
            runtime._HFC_FEISHU_DELIVERY_CONTEXT.reset(token)
        assert fake.deleted == []
        event = {
            "schema_version": "1", "event": "message.started", "platform": "feishu",
            "conversation_id": "omt_target", "message_id": "om_user", "chat_id": "oc_fixture",
            "sequence": 1, "created_at": time.time(),
            "data": {"profile_id": "work", "reply_to_message_id": "om_anchor"},
        }
        assert (await http.post("/events", json=event)).status == 200
        await drain()
        assert len(fake.sent) == 1
        assert fake.deleted == ["om_online"]
        # Subsequent notices are retired by actual updates to that message's owner.
        assert (await register(http, "om_online_again", profile="work", thread="omt_target")).status == 200
        event.update(event="message.completed", sequence=2)
        event["data"]["answer"] = "完整结果保留"
        assert (await http.post("/events", json=event)).status == 200
        await drain()
        assert fake.deleted == ["om_online", "om_online_again"]
        assert app[server.SESSIONS_KEY]["work:om_user"].answer_text == "完整结果保留"
        assert len(fake.sent) == 1


@pytest.mark.asyncio
async def test_capacity_and_delete_failure_retain_the_notice_for_a_later_retry(monkeypatch):
    app, clients = app_with_clients()
    app[server.RESTART_NOTICES_KEY] = RestartNoticeRegistry(retry_delay=0)
    fake = clients["work", "alpha"]
    async with TestClient(TestServer(app)) as http:
        assert (await register(http, "om_old", profile="work")).status == 200
        monkeypatch.setattr(server, "EPHEMERAL_RECALL_MAX_PENDING", 0)
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        assert (await register(http, "om_new", profile="work")).status == 200
        monkeypatch.setattr(server, "EPHEMERAL_RECALL_MAX_PENDING", 1024)
        fake.fail_delete = True
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        await drain()
        assert fake.deleted == []
        fake.fail_delete = False
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        await drain()
        assert fake.deleted == ["om_old", "om_new"]


@pytest.mark.asyncio
async def test_animation_updates_do_not_retry_failed_recalls_without_a_cooldown():
    app, clients = app_with_clients()
    now = [10.0]
    app[server.RESTART_NOTICES_KEY] = RestartNoticeRegistry(clock=lambda: now[0])
    fake = clients["work", "alpha"]
    async with TestClient(TestServer(app)) as http:
        await register(http, "om_notice", profile="work")
        fake.fail_delete = True
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        await drain()
        assert app[server.METRICS_KEY].ephemeral_recall_failures == 1
        fake.fail_delete = False
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        await drain()
        assert fake.deleted == []
        now[0] += 30
        await server._send_card_for_app(app, "oc_fixture", {}, "work:alpha")
        await drain()
        assert fake.deleted == ["om_notice"]
