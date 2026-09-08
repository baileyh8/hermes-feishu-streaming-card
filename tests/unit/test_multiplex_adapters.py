"""Multiplex transport registration and executable upstream ingress regression."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from hermes_feishu_card import hook_runtime


@pytest.fixture(autouse=True)
def clean_runtime(monkeypatch):
    hook_runtime.reset_runtime_state()
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda *a: True)
    monkeypatch.setattr(hook_runtime, "_install_delivery_ledger_mark_delivered_wrapper", lambda: None)
    yield
    hook_runtime.reset_runtime_state()


def adapter_type():
    class Adapter:
        name = "feishu"
        _client = object()

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            return SimpleNamespace(success=True, message_id="test-native")

        async def _feishu_send_with_retry(self, *args, **kwargs):
            return None
    return Adapter


def test_named_only_registry_installs_and_refreshes_every_connected_instance(monkeypatch):
    Adapter = adapter_type()
    first, second = Adapter(), Adapter()
    refreshed = []
    monkeypatch.setattr(hook_runtime, "_hfc_refresh_feishu_event_handler", lambda a: refreshed.append(a))
    class Runner:
        adapters = {}
        _profile_adapters = {"secretary": {"feishu": first}, "engineering": {"feishu": second}, "duplicate": {"feishu": first}}
    runner = Runner()
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    assert refreshed == [first, second]
    assert first.send.__func__ is hook_runtime._hfc_send_with_native_command_result_card
    assert callable(second.send_slash_confirm)
    replacement = Adapter()
    runner._profile_adapters["secretary"]["feishu"] = replacement
    refreshed.clear()
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    assert refreshed == [replacement, second, first]


@pytest.mark.parametrize("profile,expected", [(None, "primary"), ("default", "primary"), ("secretary", "secondary"), ("missing", None), ("disconnected", None)])
def test_legacy_selection_never_borrows_another_profiles_bot(profile, expected):
    Adapter = adapter_type()
    primary, secondary = Adapter(), Adapter()
    runner = SimpleNamespace(adapters={"feishu": primary}, _profile_adapters={"secretary": {"feishu": secondary}, "disconnected": {}})
    selected = hook_runtime._hfc_feishu_adapter_from_runner(runner, SimpleNamespace(platform="feishu", profile=profile))
    assert selected is {"primary": primary, "secondary": secondary, None: None}[expected]


@pytest.mark.parametrize("result", [None, RuntimeError("unavailable"), SimpleNamespace(name="telegram")])
def test_current_resolver_rejection_never_falls_back_to_primary(result):
    def resolve(source):
        if isinstance(result, Exception):
            raise result
        return result
    runner = SimpleNamespace(adapters={"feishu": adapter_type()()}, _adapter_for_source=resolve)
    assert hook_runtime._hfc_feishu_adapter_from_runner(runner, SimpleNamespace(platform="feishu")) is None


def test_current_resolver_preserves_validated_shared_transport():
    transport = adapter_type()()
    source = SimpleNamespace(platform="feishu", profile="routed-profile")
    observed = []
    def resolve(value):
        observed.append(value)
        return transport
    runner = SimpleNamespace(adapters={"feishu": transport}, _adapter_for_source=resolve)
    assert hook_runtime._hfc_feishu_adapter_from_runner(runner, source) is transport
    assert observed == [source]


@pytest.mark.asyncio
async def test_upstream_multiplex_ingress_installs_named_adapter_and_emits_profile_event(monkeypatch):
    # Source excerpt taken verbatim from the pinned upstream commit. Execute its
    # real handler: it owns stamping source.profile before the HFC ingress hook.
    ns = {}
    fixture = Path(__file__).parents[1] / "fixtures/hermes_latest/profile_ingress.py"
    exec(compile(fixture.read_text(), str(fixture), "exec"), ns)
    @asynccontextmanager
    async def scope(home):
        yield
    monkeypatch.setitem(sys.modules, "gateway.run", SimpleNamespace(_async_profile_runtime_scope=scope))
    monkeypatch.setenv("HERMES_FEISHU_CARD_PROFILE_ID", "default")
    sent = []
    async def send(url, payload, timeout):
        sent.append(payload)
        return True
    monkeypatch.setattr(hook_runtime, "_send_fail_open_ordered", send)
    monkeypatch.setattr(hook_runtime, "_fetch_delivery_policy_sync", lambda *a, **kw: {"ok": True, "disposition": "card", "ttl_ms": 1000})
    class Runner(ns["HermesProfileIngress"]):
        config = SimpleNamespace(multiplex_profiles=True)
        adapters = {}
        _profile_adapters = {"secretary": {"feishu": adapter_type()()}}
        def _profile_home_or_none(self, name):
            return "/test/profiles/" + name
        async def _handle_message(self, event):
            assert hook_runtime.install_feishu_command_card_adapter_methods(self, event=event)
            assert hook_runtime._hfc_native_feishu_command_cards_available({"self": self, "source": event.source})
            assert hook_runtime.emit_from_hermes_locals({"self": self, "event": event, "source": event.source}, "message.started")
    runtime = Runner()
    event = SimpleNamespace(source=SimpleNamespace(platform="feishu", profile=None, chat_id="test-chat", message_id="test-message"), message_id="test-message", text="hello")
    await runtime._make_profile_message_handler("secretary")(event)
    await asyncio.sleep(0)
    assert event.source.profile == "secretary"
    assert len(sent) == 1
    assert sent[0]["data"]["profile_id"] == "secretary"


@pytest.mark.asyncio
async def test_two_named_bots_keep_distinct_profile_and_topic_identity(monkeypatch):
    ns = {}
    fixture = Path(__file__).parents[1] / "fixtures/hermes_latest/profile_ingress.py"
    exec(compile(fixture.read_text(), str(fixture), "exec"), ns)

    @asynccontextmanager
    async def scope(home):
        yield

    monkeypatch.setitem(
        sys.modules,
        "gateway.run",
        SimpleNamespace(_async_profile_runtime_scope=scope),
    )
    monkeypatch.setenv("HERMES_FEISHU_CARD_PROFILE_ID", "default")
    sent = []

    async def send(url, payload, timeout):
        sent.append(payload)
        return True

    monkeypatch.setattr(hook_runtime, "_send_fail_open_ordered", send)
    monkeypatch.setattr(
        hook_runtime,
        "_fetch_delivery_policy_sync",
        lambda *a, **kw: {"ok": True, "disposition": "card", "ttl_ms": 1000},
    )

    class Runner(ns["HermesProfileIngress"]):
        config = SimpleNamespace(multiplex_profiles=True)
        adapters = {}
        _profile_adapters = {
            "secretary": {"feishu": adapter_type()()},
            "engineering": {"feishu": adapter_type()()},
        }

        def _profile_home_or_none(self, name):
            return "/test/profiles/" + name

        async def _handle_message(self, event):
            assert hook_runtime.install_feishu_command_card_adapter_methods(
                self, event=event
            )
            assert hook_runtime.emit_from_hermes_locals(
                {"self": self, "event": event, "source": event.source},
                "message.started",
            )

    runtime = Runner()
    events = [
        SimpleNamespace(
            source=SimpleNamespace(
                platform="feishu",
                profile=None,
                chat_id="test-chat-secretary",
                thread_id="omt_secretary",
            ),
            message_id="om_secretary",
            text="secretary",
        ),
        SimpleNamespace(
            source=SimpleNamespace(
                platform="feishu",
                profile=None,
                chat_id="test-chat-engineering",
                thread_id="omt_engineering",
            ),
            message_id="om_engineering",
            text="engineering",
        ),
    ]
    await runtime._make_profile_message_handler("secretary")(events[0])
    await runtime._make_profile_message_handler("engineering")(events[1])
    await asyncio.sleep(0)

    assert [payload["data"]["profile_id"] for payload in sent] == [
        "secretary",
        "engineering",
    ]
    assert [payload["thread_id"] for payload in sent] == [
        "omt_secretary",
        "omt_engineering",
    ]
    assert [payload["conversation_id"] for payload in sent] == [
        "omt_secretary",
        "omt_engineering",
    ]
