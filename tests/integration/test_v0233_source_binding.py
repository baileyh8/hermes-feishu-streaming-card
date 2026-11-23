"""Hermes v0.23.3 source-binding and Persistence Session Authority contracts (#537).

Exact upstream checkouts may be supplied with HFC_UPSTREAM_0232_ROOT and
HFC_UPSTREAM_0233_ROOT. Negative controls use a private copy of the relevant
sources and never mutate the pinned checkout.
"""
from __future__ import annotations

import asyncio
import builtins
from contextlib import contextmanager, suppress
import dataclasses
from datetime import datetime
from functools import partial
import hashlib
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import FunctionType, ModuleType, SimpleNamespace

import pytest

from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card.install.patcher import _approval_callback_name, apply_gateway_fragment
from hermes_feishu_card.install.source_layout import (
    DECOMPOSED_PEER_SOURCES,
    DECOMPOSED_SOURCE_TARGETS,
    read_peer_contract_sources,
)
from tests.integration.test_issue_500_notice_suppression import notice_path as notice_path

ENV_0232 = "HFC_UPSTREAM_0232_ROOT"
ENV_0233 = "HFC_UPSTREAM_0233_ROOT"
REQUIRE_ENV = "HFC_REQUIRE_0233_PIN"
COMMIT_0232 = "ae2a81ccc00e9b7c06301523e5bb09608397149c"
COMMIT_0233 = "caafe5389ae585ef77387a962aee05b1bcf17029"
ANCHORS = (
    "message_handler",
    "tool_callback",
    "answer_delta_callback",
    "thinking_delta_callback",
    "clarify_callback",
    "approval_callback",
)
FILE_HASHES = {
    "v0.23.2": {
        "gateway/run_turn.py": "834c924b1b5b83816ba228800f4b2daff39d10e9ef046ba32f28f2133752284b",
        "gateway/run_turn_runner.py": "c7d0ffc2bc87d128a854b29844397006bf1fef990454fae7c325de22c9e4b1b6",
    },
    "v0.23.3": {
        "gateway/run_turn.py": "834c924b1b5b83816ba228800f4b2daff39d10e9ef046ba32f28f2133752284b",
        "gateway/run_turn_runner.py": "3843440af2cd02c925e6adebd093090d35dbec5df79d8bb627d4f60960da2c97",
        "gateway/session_ingress.py": "060e9b3a65c876f1a3d4a10c3d2e4fea9251348611530bf60eebec1174ffa732",
        "gateway/session_authority.py": "0c63dfc2b7bde9a8392373cc91a67518d8e91956be96f9b01c31f3bc514678c4",
        "gateway/session_persistence.py": "e2430093c68fa427f59f592ac43dd98f3a5f2da3e87cf20b88f4bc5b39da9eda",
        "persistence_assembly.py": "c8e2502953355e25f0aa8b51da02d609c60f436e8aefd204f2b343849fc7d1c1",
    },
}


def _pinned_root(env: str, commit: str, digest_key: str) -> Path:
    configured = os.environ.get(env)
    if not configured:
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail(f"required {digest_key} pinned source missing")
        pytest.skip(f"{digest_key} pinned source not supplied")
    root = Path(configured)
    found = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    assert found == commit, (found, commit)
    assert subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True) == ""
    for name, digest in FILE_HASHES[digest_key].items():
        path = root / name
        assert path.is_file(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    return root


def _copy_detect_tree(root: Path, dest: Path) -> None:
    from hermes_feishu_card.admission_topology_0226 import SOURCE_HASHES_0233

    names = {
        *DECOMPOSED_SOURCE_TARGETS,
        *DECOMPOSED_PEER_SOURCES,
        "VERSION",
        "hermes_cli/__init__.py",
        *(name.replace(".", "/") + ".py" for name in SOURCE_HASHES_0233),
    }
    for name in names:
        src = root / name
        if src.is_file():
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)


def test_v0232_tag_stays_full_after_v0233_contract():
    root = _pinned_root(ENV_0232, COMMIT_0232, "v0.23.2")
    detection = detect_hermes(root)
    assert detection.supported is True
    assert detection.compatibility == "full", detection.reason
    assert detection.capabilities["admission_completion"] is True


def test_v0233_tag_finds_documented_anchors_and_is_full():
    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    detection = detect_hermes(root)
    for name in ANCHORS:
        assert detection.capabilities[name] is True, name
    assert detection.supported is True
    assert detection.compatibility == "full", detection.reason
    assert detection.reason == "supported"
    assert detection.capabilities["admission_completion"] is True
    assert detection.capabilities["exact_base_delivery"] is True


def test_v0233_legacy_observer_preserves_auxiliary_request_boundary(tmp_path):
    from hermes_feishu_card.install import legacy_observer

    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    assert legacy_observer.available(root)
    name = "agent/turn_response_intake.py"
    target = tmp_path / name
    target.parent.mkdir()
    source = (root / name).read_bytes()
    needle = b'getattr(agent, "_loop_aux_task", None)'
    assert needle in source
    target.write_bytes(source.replace(needle, b"False", 1))
    assert not legacy_observer.available(tmp_path)


def test_v0233_runner_and_handler_apply_with_peer_contracts():
    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    peers = read_peer_contract_sources(root)
    sources = {
        "gateway/run_turn.py": (root / "gateway/run_turn.py").read_text(encoding="utf-8"),
        "gateway/run_turn_runner.py": (root / "gateway/run_turn_runner.py").read_text(encoding="utf-8"),
    }
    patched_turn = apply_gateway_fragment(
        sources["gateway/run_turn.py"], "gateway/run_turn.py",
        completion_sources=sources, peer_sources=peers,
    )
    patched_runner = apply_gateway_fragment(
        sources["gateway/run_turn_runner.py"], "gateway/run_turn_runner.py",
        completion_sources=sources, peer_sources=peers,
    )
    assert "HERMES_FEISHU_CARD_" in patched_turn
    assert "HERMES_FEISHU_CARD_APPROVAL_PATCH_BEGIN" in patched_runner
    assert _approval_callback_name(sources["gateway/run_turn_runner.py"], peer_sources=peers) == "_render_approval_sync"


def test_v0233_admission_rejects_per_file_hash_drift(tmp_path):
    from hermes_feishu_card import admission_topology_0226 as topology

    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    copy = tmp_path / "clean"
    _copy_detect_tree(root, copy)
    assert detect_hermes(copy).compatibility == "full"
    assert topology.source_bytes_0233(copy) is not None

    for mod_name in sorted(topology.SOURCE_HASHES_0233):
        rel = mod_name.replace(".", "/") + ".py"
        target = copy / rel
        original = target.read_bytes()
        target.write_bytes(original + b"\n# unowned drift\n")
        try:
            assert topology.source_bytes_0233(copy) is None, rel
            if mod_name in (
                "gateway.session_ingress",
                "gateway.session_authority",
                "gateway.session_persistence",
                "persistence_assembly",
                "persistence.core.context",
                "persistence.authority.admissions",
                "persistence.sqlite._backend",
                "persistence.sqlite.authority.admissions",
                "persistence.sqlite.authority.runtime",
                "persistence.sqlite.authority._admission_store",
                "persistence.sqlite.authority._runtime",
                "persistence.sqlite.authority._runtime_ends",
            ):
                detection = detect_hermes(copy)
                assert detection.capabilities["admission_completion"] is False, rel
                assert detection.compatibility != "full", rel
        finally:
            target.write_bytes(original)
    assert detect_hermes(copy).compatibility == "full"


def test_v0233_cohort_and_store_signature_contracts():
    import inspect
    from hermes_feishu_card import admission_completion as ac, admission_topology_0226 as topology

    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    assert ac.SOURCE_COHORTS[-1] is ac._V0233_CORE_HASHES
    assert ac.SOURCE_COHORTS[-1] == topology.CORE_HASHES_0233
    assert "hermes_state_runtime" not in topology.SOURCE_HASHES_0233
    assert "hermes_state_runtime" not in topology.FUNCTIONS_0233
    assert "hermes_state_runtime" not in topology.OPTIONAL_MODULES_0233
    assert "close_discarded_turn" not in topology.FUNCTIONS_0233["gateway.session_results"]

    with _isolated_hermes_modules(root):
        import importlib
        store_mod = importlib.import_module("persistence.sqlite.authority._admission_store")
        sig = inspect.signature(store_mod.get_session_admission)
        assert list(sig.parameters) == ["holder", "admission_id"]
        assert sig.parameters["admission_id"].kind is inspect.Parameter.KEYWORD_ONLY


def test_v0233_loaded_admission_verifies_persistence_bindings(tmp_path, monkeypatch):
    from hermes_feishu_card import admission_completion, admission_topology_0226 as topology
    from hermes_feishu_card.install import decomposed_ownership
    from tests.integration.test_v0230_admission_sources import _build_modules

    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    copy = tmp_path / "installed"
    _copy_detect_tree(root, copy)
    originals = {name: (copy / name).read_bytes() for name in DECOMPOSED_SOURCE_TARGETS}
    for name, rendered in decomposed_ownership.render(
        originals, peer_sources=read_peer_contract_sources(copy)
    ).items():
        (copy / name).write_bytes(rendered)
    pair = topology.source_bytes_0233(copy)
    assert pair is not None
    modules = _build_modules(
        copy, pair[1], functions=topology.FUNCTIONS_0233,
        decorated=topology.DECORATED_0233,
        sync_decorated=topology.SYNC_DECORATED_0233,
    )
    modules["gateway.session_ingress"].authority_persistence = modules["gateway.session_persistence"].authority_persistence
    modules["gateway.session_authority"].authority_persistence = modules["gateway.session_persistence"].authority_persistence
    modules["gateway.session_settlement_recovery"].authority_persistence = modules["gateway.session_persistence"].authority_persistence
    modules["persistence.sqlite.authority.admissions"].store = modules["persistence.sqlite.authority._admission_store"]
    modules["persistence.sqlite.authority.admissions"].refused = modules["persistence.sqlite.authority._runtime"].refused
    modules["persistence.sqlite.authority.runtime"].refused = modules["persistence.sqlite.authority._runtime"].refused
    modules["persistence.sqlite.authority.runtime"]._runtime_ends = modules["persistence.sqlite.authority._runtime_ends"]
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    verified = admission_completion._verified_with_mode()
    assert verified is not None and verified[2] is True

    # Swapping authority_persistence on ingress or authority must fail closed.
    modules["gateway.session_authority"].authority_persistence = lambda a: a.persistence
    assert admission_completion._verified_with_mode() is None
    modules["gateway.session_authority"].authority_persistence = modules["gateway.session_persistence"].authority_persistence
    assert admission_completion._verified_with_mode() is not None

    # Swapping _admission_store on SQLiteAdmissionRepository module must fail closed.
    modules["persistence.sqlite.authority.admissions"].store = ModuleType("fake_store")
    assert admission_completion._verified_with_mode() is None
    modules["persistence.sqlite.authority.admissions"].store = modules["persistence.sqlite.authority._admission_store"]
    assert admission_completion._verified_with_mode() is not None

    # Swapping _runtime_ends on SQLiteSessionRuntimeRepository module must fail closed.
    modules["persistence.sqlite.authority.runtime"]._runtime_ends = ModuleType("fake_ends")
    assert admission_completion._verified_with_mode() is None


def test_v0233_base_patch_roundtrip_requires_peers_and_restores(tmp_path):
    from hermes_feishu_card.install import decomposed_ownership, patcher, recovery

    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    copy = tmp_path / "roundtrip"
    _copy_detect_tree(root, copy)
    peers = read_peer_contract_sources(copy)
    original = (copy / "gateway/platforms/base.py").read_text(encoding="utf-8")
    patched = patcher.apply_base_patch(original, peer_sources=peers)
    assert patcher.remove_base_patch(patched, peer_sources=peers) == original
    originals = {
        name: (copy / name).read_bytes()
        for name in DECOMPOSED_SOURCE_TARGETS
        if (copy / name).is_file()
    }
    rendered = decomposed_ownership.render(originals, peer_sources=peers)
    assert rendered["gateway/platforms/base.py"].decode("utf-8") == patched
    assert detect_hermes(copy).supported is True
    assert recovery._validate_base_reapplication(original, peer_sources=peers) == ""


@contextmanager
def _isolated_hermes_modules(root: Path):
    """Import real v0.23.3 persistence/gateway modules in isolation and restore sys.modules."""
    prefixes = (
        "persistence", "persistence_assembly", "hermes_constants", "hermes_cli",
        "hermes_dir_acl", "hermes_yaml", "hermes_scope", "utils", "agent",
        "gateway", "cron", "session_rules", "storage_errors", "storage_user_copy",
    )
    saved_modules = {
        name: mod for name, mod in sys.modules.items()
        if name == prefixes[0] or any(name == p or name.startswith(p + ".") for p in prefixes)
    }
    for name in list(saved_modules):
        sys.modules.pop(name, None)
    sys.path.insert(0, str(root))
    try:
        yield
    finally:
        if sys.path and sys.path[0] == str(root):
            sys.path.pop(0)
        for name in list(sys.modules):
            if any(name == p or name.startswith(p + ".") for p in prefixes):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)


class _Box:
    def __init__(self, **values):
        self.__dict__.update(values)


def _pinned_notice_functions(root, module, owner, functions):
    from tests.integration.test_v0230_admission_sources import _compiled_code

    path = root / (module.__name__.replace(".", "/") + ".py")
    compiled = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
    module.__dict__.setdefault("__builtins__", vars(builtins))
    return {
        name: FunctionType(_compiled_code(compiled, name, owner), vars(module), name, defaults)
        for name, defaults in functions.items()
    }


@pytest.mark.parametrize("entry", ["base_send", "edit"])
@pytest.mark.parametrize("template", [
    '✅ Background task complete\nPrompt: "Synthetic task"\n\nBody.',
    '🔄 Background task started: "Synthetic task"\nTask ID: bg_123456_abcdef\n'
    'You can keep chatting — results will appear when done.',
    "❌ Background task bg_123456_abcdef failed: synthetic failure",
])
async def test_pinned_v0233_ordinary_background_template_is_not_a_notice(
    notice_path, monkeypatch, entry, template,
):
    from hermes_feishu_card import hook_runtime

    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    source = SimpleNamespace(platform="feishu", chat_id="oc_group_1", chat_type="group", thread_id="omt_topic")
    adapter = notice_path.adapter
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(
        runner, event=SimpleNamespace(source=source, message_id="om_current"),
    )
    with _isolated_hermes_modules(root):
        from gateway.platforms.base import BasePlatformAdapter
        from hermes_cli.observability import shared_metrics_gateway

        monkeypatch.setattr(shared_metrics_gateway, "_submit", lambda *args, **kwargs: None)
        if entry == "base_send":
            metadata = {"thread_id": source.thread_id}
            result = await BasePlatformAdapter._send_with_retry(
                adapter, source.chat_id, template, reply_to="om_current", metadata=metadata,
            )
            assert adapter.sent == [(source.chat_id, template, "om_current", metadata)]
            assert adapter.edited == []
        else:
            result = await adapter.edit_message(source.chat_id, "om_existing", template, finalize=True)
            assert adapter.sent == []
            assert adapter.edited == [(source.chat_id, "om_existing", template, True)]
    assert result.success is True
    assert notice_path.notices == []
    assert notice_path.feishu.sent == notice_path.feishu.updated == []


@pytest.mark.parametrize("suppressed", [True, False])
async def test_pinned_v0233_background_start_uses_the_real_base_reply(notice_path, monkeypatch, suppressed):
    from hermes_feishu_card import hook_runtime

    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    chat_id = "oc_group_1" if suppressed else "oc_group_allow"
    source = SimpleNamespace(platform="feishu", chat_id=chat_id, chat_type="group", thread_id="omt_bg")
    event = SimpleNamespace(
        source=source, message_id="om_bg_request", text="/bg Check the release",
        get_command=lambda: "bg", get_command_args=lambda: "Check the release", media_urls=[], media_types=[],
    )
    adapter = notice_path.adapter
    with _isolated_hermes_modules(root):
        from agent.i18n import t
        from gateway.platforms import base
        from hermes_cli.observability import shared_metrics_gateway

        monkeypatch.setattr(shared_metrics_gateway, "_submit", lambda *args, **kwargs: None)
        commands = ModuleType("gateway.slash_commands")
        commands.__dict__.update(datetime=datetime, os=os, t=partial(t, lang="en"))
        commands.__dict__.update(_pinned_notice_functions(root, commands, None, {"_preview": (60,)}))
        monkeypatch.setitem(sys.modules, commands.__name__, commands)
        runner_type = type("Gateway", (), _pinned_notice_functions(
            root, commands, "GatewaySlashCommandsMixin", {"_handle_background_command": None},
        ))
        runner = runner_type()
        runner.adapters = {"feishu": adapter}
        runner._reply_anchor_for_event = base._reply_anchor_for_event
        launches = []
        runner._run_background_task = lambda *args, **kwargs: launches.append((args, kwargs))
        runner._track_background_task = lambda _coro: None

        adapter_type = type(adapter)
        adapter_type._dispatch_inline_reply = base.BasePlatformAdapter._dispatch_inline_reply
        adapter_type._send_with_retry = base.BasePlatformAdapter._send_with_retry
        adapter._unwrap_ephemeral = lambda response: (response, 0)
        assert hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)
        adapter._message_handler = runner._handle_background_command
        await adapter._dispatch_inline_reply(event)

        # Calling the real producer outside Base must give no delivery proof to
        # a later reply, even if the later reply is byte-for-byte identical.
        template = await runner._handle_background_command(event)
        adapter._message_handler = lambda _event: asyncio.sleep(0, result=template)
        await adapter._dispatch_inline_reply(event)

    assert len(launches) == 2
    [notice] = notice_path.notices
    assert notice["message_id"] == "om_bg_request"
    assert notice["thread_id"] == "omt_bg"
    assert notice["data"]["notice_kind"] == "background-task"
    assert notice["data"]["notice_terminal"] is False
    assert notice["data"]["notice_id"] == "background-task:" + launches[0][0][2]
    assert len(notice_path.feishu.sent) == int(not suppressed)
    [(native_chat, native_content, _, _)] = adapter.sent
    assert (native_chat, native_content) == (chat_id, template)


async def test_pinned_v0233_traditional_chinese_review_uses_real_callback(notice_path, monkeypatch):
    from hermes_feishu_card import hook_runtime

    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    adapter = notice_path.adapter
    source = SimpleNamespace(platform="feishu", chat_id="oc_group_1", chat_type="group", thread_id="omt_review")
    with _isolated_hermes_modules(root):
        from agent import async_utils
        from agent.i18n import t
        from gateway.platforms.base import Platform

        gateway = ModuleType("gateway.run")
        gateway.GatewayRunner = type("GatewayRunner", (), {})
        gateway.safe_schedule_threadsafe = async_utils.safe_schedule_threadsafe
        gateway._interim_metadata = lambda metadata: {**metadata, "_interim_send": True}
        gateway._non_conversational_metadata = lambda metadata, **kwargs: metadata
        monkeypatch.setitem(sys.modules, gateway.__name__, gateway)
        runner = gateway.GatewayRunner()
        runner.adapters = {"feishu": adapter}
        assert hook_runtime.install_feishu_command_card_adapter_methods(runner)

        progress = ModuleType("gateway.run_turn_progress")
        progress.logger = logging.getLogger("test.review")
        monkeypatch.setitem(sys.modules, progress.__name__, progress)
        progress_methods = _pinned_notice_functions(root, progress, "GatewayTurnProgressMixin", {
            "_schedule": (None,), "_send_status_text": None, "_status_live": None,
        })
        turn = ModuleType("gateway.run_turn_runner")
        turn.__dict__.update(threading=threading, Platform=Platform)
        monkeypatch.setitem(sys.modules, turn.__name__, turn)
        turn_methods = _pinned_notice_functions(root, turn, "TurnRunner", {"_make_bg_review_callbacks": None})
        turn_runner = type("TestTurn", (), {**progress_methods, **turn_methods})()
        turn_runner._approval_owner = None
        turn_runner._ctx = SimpleNamespace(
            source=source, _status_adapter=adapter, _status_chat_id=source.chat_id,
            _status_thread_metadata={"thread_id": source.thread_id},
            _loop_for_step=asyncio.get_running_loop(), _run_still_current=lambda: True,
        )
        review = ModuleType("agent.background_review")
        review.__dict__.update(t=partial(t, lang="zh-hant"), suppress=suppress)
        publish = _pinned_notice_functions(root, review, None, {"_publish_review_summary": None})
        futures = []
        schedule = asyncio.run_coroutine_threadsafe

        def record_schedule(coro, loop):
            future = schedule(coro, loop)
            futures.append(future)
            return future

        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", record_schedule)
        send, release = turn_runner._make_bg_review_callbacks()
        agent = SimpleNamespace(background_review_callback=send, _safe_print=lambda _text: None)
        await asyncio.to_thread(publish["_publish_review_summary"], agent, ["已儲存記憶"])
        assert futures == []
        release()
        [future] = futures
        assert (await asyncio.wrap_future(future)).success is True

    [notice] = notice_path.notices
    assert notice["data"]["notice_kind"] == "self-improvement"
    assert notice["data"]["content"] == "💾 自我改進檢閱：已儲存記憶"
    assert notice["thread_id"] == "omt_review"
    assert adapter.sent == notice_path.feishu.sent == []


@pytest.mark.parametrize("suppressed", [True, False])
async def test_pinned_v0233_background_worker_delivers_its_own_result(notice_path, monkeypatch, suppressed):
    from hermes_feishu_card import hook_runtime

    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    chat_id = "oc_group_1" if suppressed else "oc_group_allow"
    source = SimpleNamespace(platform="feishu", chat_id=chat_id, chat_type="group", thread_id="omt_bg")
    adapter = notice_path.adapter
    with _isolated_hermes_modules(root):
        from agent.i18n import t
        from gateway.run_background import GatewayBackgroundMixin

        class Gateway(GatewayBackgroundMixin):
            adapters = {"feishu": adapter}
            _provider_routing = {}

            def _delivery_adapter_for(self, target):
                assert target is source
                return adapter

            def _thread_metadata_for_source(self, target, message_id):
                return {"thread_id": target.thread_id, "reply_to_message_id": message_id}

            def _resolve_session_agent_runtime(self, **kwargs):
                return "fixture", {"api_key": "synthetic"}

            def _resolve_turn_toolsets(self, *args):
                return [], []

            def _resolve_session_reasoning_config(self, **kwargs):
                return None

            def _resolve_session_service_tier(self, source):
                return None

            def _resolve_turn_agent_config(self, *args, **kwargs):
                return {}

            async def _run_in_executor_with_context(self, _agent_run):
                return {"final_response": ""}

        gateway_module = ModuleType("gateway.run")
        gateway_module._load_gateway_config = dict
        gateway_module._checkpoint_agent_kwargs = lambda _cfg: {}
        gateway_module._current_max_iterations = lambda: 1
        gateway_module._platform_config_key = lambda platform: platform
        monkeypatch.setitem(sys.modules, gateway_module.__name__, gateway_module)
        agent_module = ModuleType("run_agent")
        agent_module.AIAgent = object
        monkeypatch.setitem(sys.modules, agent_module.__name__, agent_module)
        monkeypatch.setattr(sys.modules["gateway.run_background"], "t", partial(t, lang="en"))
        runner = Gateway()
        assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
        await runner._run_background_task_inner("Check the release", source, "bg_123456_abcdef", "om_bg")

    [notice] = notice_path.notices
    assert notice["data"]["content"] == '✅ Background task complete\nPrompt: "Check the release"\n\n(No response generated)'
    assert notice["data"]["notice_kind"] == "background-task"
    assert notice["data"]["notice_terminal"] is True
    assert notice["data"]["reply_to_message_id"] == "om_bg"
    assert notice["thread_id"] == "omt_bg"
    assert len(notice_path.feishu.sent) == int(not suppressed)
    assert adapter.sent == []


async def test_pinned_v0233_process_watcher_keeps_native_text_and_success_recall(notice_path, monkeypatch):
    from hermes_feishu_card import hook_runtime

    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    adapter = notice_path.adapter
    adapter._app_id = "synthetic-app"
    source = SimpleNamespace(platform="feishu", chat_id="oc_group_1", chat_type="group", thread_id="omt_process")
    recalled = []

    async def record_recall(message_id, **kwargs):
        recalled.append((message_id, kwargs))
        return True

    monkeypatch.setattr(hook_runtime, "schedule_message_recall_async", record_recall)
    with _isolated_hermes_modules(root):
        from agent.i18n import t
        from gateway.run_notifications import GatewayNotificationsMixin

        class Gateway(GatewayNotificationsMixin):
            adapters = {"feishu": adapter}

            def _build_process_event_source(self, watcher):
                return source

            def _resolve_injection_adapter(self, platform_name, target):
                return adapter

        gateway_module = ModuleType("gateway.run")
        gateway_module.t = partial(t, lang="en")
        gateway_module._non_conversational_metadata = lambda metadata, **kwargs: metadata
        gateway_module.__dict__.update(_pinned_notice_functions(root, gateway_module, None, {
            "_format_concise_process_notification": (None,), "_shorten_command_for_display": (80,),
        }))
        monkeypatch.setitem(sys.modules, gateway_module.__name__, gateway_module)
        gateway = Gateway()
        assert hook_runtime.install_feishu_command_card_adapter_methods(gateway)
        success = gateway_module._format_concise_process_notification("proc_111111111111", "test", 0, "", 6)
        full = t("gateway.background.final_output", lang="en", header=success, output="recorded output")
        for text in (success, full):
            await gateway._send_watcher_message("feishu", source.chat_id, source.thread_id, text, {})

    assert [send[1] for send in adapter.sent] == [success, full]
    [(message_id, recall)] = recalled
    assert message_id == "om_native_send"
    assert recall["route"]["conversation_id"] == source.thread_id
    assert notice_path.notices == notice_path.feishu.sent == []


@pytest.mark.parametrize("outcome", ["not_sent", "unknown", "delivered"])
async def test_pinned_v0233_platform_notice_uses_native_delivery_route(notice_path, monkeypatch, outcome):
    from hermes_feishu_card import hook_runtime

    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    notice_path.feishu.outcome = outcome
    adapter = notice_path.adapter
    source = SimpleNamespace(platform="feishu", chat_id="oc_group_allow", chat_type="group", thread_id="omt_notice")
    content = "📬 No home channel is set for Feishu. Type /sethome to make this chat your home channel."
    with _isolated_hermes_modules(root):
        from gateway.run_notifications import GatewayNotificationsMixin
        gateway_module = ModuleType("gateway.run")
        gateway_module._is_slack_ignored_channel = lambda *args: False
        monkeypatch.setitem(sys.modules, gateway_module.__name__, gateway_module)

        class Gateway(GatewayNotificationsMixin):
            adapters = {"feishu": adapter}

            def _delivery_adapter_for(self, target):
                assert target is source
                return adapter

            def _thread_metadata_for_source(self, target):
                return {"thread_id": target.thread_id, "native_metadata": True}

        gateway = Gateway()
        assert hook_runtime.install_feishu_command_card_adapter_methods(gateway)
        result = await gateway._deliver_platform_notice(source, content)

    [notice] = notice_path.notices
    assert notice["data"]["notice_scope"] == "independent"
    assert len(notice_path.feishu.sent) == 1
    if outcome == "delivered":
        assert result.success is True
        assert adapter.sent == []
    else:
        assert result is None  # Hermes' own method delivered the fallback.
        native_content = content if outcome == "not_sent" else hook_runtime._NOTICE_UNCERTAIN_WARNING
        assert adapter.sent == [(source.chat_id, native_content, None, {
            "thread_id": source.thread_id, "native_metadata": True,
        })]


@pytest.mark.asyncio
async def test_v0233_real_persistence_store_admission_completion_and_negative_controls(
    tmp_path, monkeypatch,
):
    from hermes_feishu_card import admission_completion as ac
    from hermes_feishu_card.install import decomposed_ownership
    from hermes_feishu_card.install.patcher import apply_base_patch
    from tests.integration.test_v0230_admission_sources import _build_modules

    root = _pinned_root(ENV_0233, COMMIT_0233, "v0.23.3")
    copy = tmp_path / "hermes"
    shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    peers = read_peer_contract_sources(copy)
    originals = {name: (copy / name).read_bytes() for name in DECOMPOSED_SOURCE_TARGETS}
    for name, rendered in decomposed_ownership.render(originals, peer_sources=peers).items():
        (copy / name).write_bytes(rendered)

    home = tmp_path / ".hermes"
    other_home = tmp_path / "other-home"
    home.mkdir(mode=0o700)
    other_home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_STATE_DB_GUARD_BYPASS", "1")

    with _isolated_hermes_modules(copy):
        import persistence_assembly
        from persistence.conversation.sessions import NewSession
        from hermes_feishu_card import admission_topology_0226 as topology

        scope = persistence_assembly.profile_scope(home=home)
        other_scope = persistence_assembly.profile_scope(home=other_home)
        store = persistence_assembly.open_persistence(scope, eager=True)
        other_store = persistence_assembly.open_persistence(other_scope, eager=True)
        try:
            epoch = store.runtime.begin_epoch(instance_id="inst-0233")
            other_store.runtime.begin_epoch(instance_id="inst-other")
            store.sessions.create(NewSession(session_id="sess-1", source="feishu"))
            payload = {
                "text": "hello",
                "native_text_v1": {
                    "route": "agent:main:feishu:dm:oc_1",
                    "event": {"message_id": "om_1"},
                    "source": {
                        "platform": "feishu",
                        "chat_id": "oc_1",
                        "thread_id": "",
                        "user_id": "ou_1",
                        "profile_id": "default",
                    },
                },
            }
            admitted, created = store.admissions.admit(
                epoch=epoch,
                principal_id="messaging:ou_1",
                session_id="sess-1",
                request_id="om_1",
                payload=payload,
            )
            assert created is True
            claimed = store.admissions.claim(epoch=epoch, session_id="sess-1")
            assert claimed is not None and claimed is not False

            pair = topology.source_bytes_0233(copy)
            assert pair is not None
            modules = _build_modules(
                copy, pair[1], functions=topology.FUNCTIONS_0233,
                decorated=topology.DECORATED_0233,
                sync_decorated=topology.SYNC_DECORATED_0233,
            )
            # Bind real persistence modules alongside the verified gateway modules.
            for real_name in (
                "persistence_assembly",
                "persistence.core.context",
                "persistence.authority.admissions",
                "persistence.authority.runtime",
                "persistence.sqlite._backend",
                "persistence.sqlite.authority.admissions",
                "persistence.sqlite.authority.runtime",
                "persistence.sqlite.authority._admission_store",
                "persistence.sqlite.authority._runtime",
                "persistence.sqlite.authority._runtime_ends",
            ):
                modules[real_name] = sys.modules[real_name]
            modules["gateway.session_ingress"].authority_persistence = modules["gateway.session_persistence"].authority_persistence
            modules["gateway.session_authority"].authority_persistence = modules["gateway.session_persistence"].authority_persistence
            modules["gateway.session_authority"].asdict = dataclasses.asdict
            modules["gateway.session_settlement_recovery"].authority_persistence = modules["gateway.session_persistence"].authority_persistence
            for name, module in modules.items():
                monkeypatch.setitem(sys.modules, name, module)

            # Build a real BasePlatformAdapter._extract_response_content code object.
            base_mod = modules["gateway.platforms.base"]
            base_compiled = compile(
                apply_base_patch((copy / "gateway/platforms/base.py").read_text(), peer_sources=peers),
                str(copy / "gateway/platforms/base.py"),
                "exec",
                dont_inherit=True,
            )
            extract_code = ac._compiled_code(base_compiled, "_extract_response_content", "BasePlatformAdapter")
            from types import FunctionType
            class FakeAdapter:
                _app_id = "cli_app"
            FakeAdapter._extract_response_content = FunctionType(extract_code, vars(base_mod), "_extract_response_content")
            adapter = FakeAdapter()

            source = _Box(
                platform="feishu", chat_id="oc_1", thread_id="", user_id="ou_1", profile_id="default",
            )
            event = _Box(
                message_id="om_1", source=source, internal=False, _claimed_owner=("sess-1", claimed.generation),
            )
            live = _Box(
                source=source,
                route="agent:main:feishu:dm:oc_1",
                superseded=False,
                task=asyncio.current_task(),
                event_stream=_Box(
                    execution={
                        "authority_epoch": epoch,
                        "execution_generation": claimed.generation,
                        "admission_id": claimed.admission_id,
                    }
                ),
            )
            runner = _Box(
                _adapter_for_source=lambda s: adapter,
                _is_session_run_current=lambda key, gen: True,
            )
            authority_cls = modules["gateway.session_authority"].SessionAuthority
            authority = authority_cls.__new__(authority_cls)
            authority.runner = runner
            authority.profile_id = str(home)
            authority.instance_id = "inst-0233"
            authority.epoch = epoch
            authority.persistence = store
            authority.retiring = False
            authority.sessions = {"sess-1": live}
            authority.executing = {"sess-1": claimed.generation}

            row_dict = modules["gateway.session_authority"].admission_row(claimed)
            ref = _Box(session_id="sess-1")
            ingress_mod = modules["gateway.session_ingress"]
            ingress_mod.executing_admission = ac.ContextVar("executing_admission", default=False)
            token = ingress_mod.executing_admission.set(True)
            try:
                def run_in_claim_frame():
                    values = {
                        "self": runner,
                        "event": event,
                        "source": source,
                        "run_generation": 1,
                        "session_key": "agent:main:feishu:dm:oc_1",
                        "session_entry": _Box(session_id="sess-1"),
                        "response": "final answer",
                    }
                    row = row_dict
                    # Patch _claim_frame to return this frame's locals cleanly.
                    monkeypatch.setattr(
                        ac, "_claim_frame",
                        lambda _ing: {"authority": authority, "ref": ref, "row": row, "event": event},
                    )
                    guard = ac.first_card_guard(values, "default")
                    assert guard is not None and guard() is True
                    stage = {
                        "payload": {
                            "turn_id": "om_1",
                            "chat_id": "oc_1",
                            "thread_id": "",
                            "data": {
                                "profile_id": "default",
                                "profile_source": "hermes_home",
                                "admission_user_id": "ou_1",
                            },
                        }
                    }
                    async def finish(_ev, _ok):
                        return None
                    offer = ac.prepare(values, stage, finish)
                    assert offer is not None
                    assert offer.persistence_mode is True
                    assert ac._authority_current(offer, started=True) is True

                    # Negative control 1: wrong store object identity.
                    authority.persistence = other_store
                    assert ac._authority_current(offer, started=True) is False
                    assert ac._prepare_authority_reason(offer) == "authority-epoch-stale"
                    authority.persistence = store
                    assert ac._authority_current(offer, started=True) is True

                    # Negative control 2: wrong store.scope.profile_dir.
                    assert ac._authority_profile_matches(
                        authority, "default", persistence_mode=True, store=other_store
                    ) is False
                    saved_scope = store.scope
                    store.scope = other_scope
                    assert ac._authority_current(offer, started=True) is False
                    store.scope = saved_scope
                    assert ac._authority_current(offer, started=True) is True

                    # Negative control 3: repository rebinding on bound SQLiteStore.
                    saved_admissions = store._store.admissions
                    store._store.admissions = other_store._store.admissions
                    assert ac._authority_current(offer, started=True) is False
                    store._store.admissions = saved_admissions
                    saved_runtime = store._store.runtime
                    store._store.runtime = other_store._store.runtime
                    assert ac._authority_current(offer, started=True) is False
                    store._store.runtime = saved_runtime
                    assert ac._authority_current(offer, started=True) is True

                    # Negative control 4: read-only Persistence handle refused.
                    with persistence_assembly.open_persistence_reader(home) as ro_store:
                        authority.persistence = ro_store
                        assert ac._authority_current(offer, started=True) is False
                    authority.persistence = store
                    assert ac._authority_current(offer, started=True) is True

                    # Negative control 5: wrong authority instance_id.
                    authority.instance_id = "inst-other"
                    assert ac._authority_current(offer, started=True) is False
                    authority.instance_id = "inst-0233"
                    assert ac._authority_current(offer, started=True) is True

                    # Negative control 6: replaced state.db file on disk (even with matching epoch=1).
                    db_path = home / "state.db"
                    backup_db = tmp_path / "state.db.orig"
                    shutil.copyfile(db_path, backup_db)
                    os.replace(other_home / "state.db", db_path)
                    assert ac._authority_current(offer, started=True) is False
                    assert ac._prepare_authority_reason(offer) == "authority-epoch-stale"
                    os.replace(backup_db, db_path)

                run_in_claim_frame()

                # Fresh store check for stale epoch and closed handle.
                store.close()
                store = persistence_assembly.open_persistence(scope, eager=True)
                authority.persistence = store
                def run_epoch_and_close_controls():
                    values = {
                        "self": runner,
                        "event": event,
                        "source": source,
                        "run_generation": 1,
                        "session_key": "agent:main:feishu:dm:oc_1",
                        "session_entry": _Box(session_id="sess-1"),
                        "response": "final answer",
                    }
                    stage = {
                        "payload": {
                            "turn_id": "om_1",
                            "chat_id": "oc_1",
                            "thread_id": "",
                            "data": {
                                "profile_id": "default",
                                "profile_source": "hermes_home",
                                "admission_user_id": "ou_1",
                            },
                        }
                    }
                    async def finish(_ev, _ok):
                        return None
                    offer = ac.prepare(values, stage, finish)
                    assert offer is not None
                    assert ac._authority_current(offer, started=True) is True

                    # Negative control 7: stale epoch in state.db.
                    store.runtime.begin_epoch(instance_id="inst-0233-new")
                    assert ac._authority_current(offer, started=True) is False
                    assert ac._prepare_authority_reason(offer) == "authority-epoch-stale"

                    # Negative control 8: closed Persistence handle refused.
                    store.close()
                    assert ac._authority_current(offer, started=True) is False

                run_epoch_and_close_controls()
            finally:
                ingress_mod.executing_admission.reset(token)
        finally:
            store.close()
            other_store.close()
