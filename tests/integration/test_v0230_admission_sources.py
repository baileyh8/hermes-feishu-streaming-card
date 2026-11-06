"""Pinned Hermes v0.23.0 completion/admission contracts (#497).

The exact upstream checkout is supplied by CI. A local run can opt in with
'HFC_UPSTREAM_0230_ROOT'; every negative control uses a private copy.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import gzip
import hashlib
import itertools
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import weakref
from contextlib import suppress
from types import FunctionType, ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_feishu_card import admission_completion as ac
from hermes_feishu_card import admission_topology_0226 as topology
from hermes_feishu_card import hook_runtime as h
from hermes_feishu_card import __version__ as HFC_VERSION
from hermes_feishu_card.admission_completion import _compiled_code
from hermes_feishu_card.admission_topology_0226 import (
    SOURCE_HASHES_0230,
    source_bytes,
    source_bytes_0230,
)
from hermes_feishu_card.install import completion_contracts, decomposed_ownership
from hermes_feishu_card.install.detect import (
    _detect_admission_completion,
    detect_hermes,
)
from hermes_feishu_card.install.source_layout import DECOMPOSED_SOURCE_TARGETS

ENV = "HFC_UPSTREAM_0230_ROOT"
REQUIRE_ENV = "HFC_REQUIRE_0230_PIN"
MANIFEST = json.loads(
    (Path(__file__).parents[1] / "fixtures/hermes_v0230_sources.json").read_text()
)


def _pinned_root() -> Path:
    configured = os.environ.get(ENV)
    if not configured:
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail("required v0.23.0 pinned source missing")
        pytest.skip("v0.23.0 pinned source not supplied")
    root = Path(configured)
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    assert commit == MANIFEST["commit"], (commit, MANIFEST["commit"])
    assert subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain"], text=True
    ) == ""
    for name, digest in MANIFEST["files"].items():
        path = root / name
        assert path.is_file(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    return root


def _copy_sources(root: Path, target: Path, names=None) -> None:
    for name in names or MANIFEST["files"]:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, destination)


def _replace_once(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new, 1)


def _wrapper_node(source: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(source)
    wrappers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_deliver_turn_response_then_limit_offer"
    ]
    assert len(wrappers) == 1
    return wrappers[0]


def _rendered_gateway_sources(root: Path) -> dict[str, bytes]:
    sources = {
        name: (root / name).read_bytes()
        for name in DECOMPOSED_SOURCE_TARGETS
        if (root / name).is_file()
    }
    rendered = decomposed_ownership.render(sources)
    assert all(
        decomposed_ownership._remove_target(name, raw) == sources[name]
        for name, raw in rendered.items()
    )
    return rendered


def _patched_wrapper_namespace(rendered: dict[str, bytes]) -> dict:
    source = rendered["gateway/run_turn_persistence.py"].decode("utf-8")
    methods = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AsyncFunctionDef)
    }
    namespace = {
        "__builtins__": __builtins__,
        "diagnostic_wake_muted": lambda event: False,
        "suppress": suppress,
        "logger": logging.getLogger(__name__),
    }
    module = ast.Module(
        body=[
            methods["_hmwa_deliver_turn_response"],
            methods["_deliver_turn_response_then_limit_offer"],
        ],
        type_ignores=[],
    )
    exec(
        compile(
            ast.fix_missing_locations(module),
            "v0.23.0-rendered-run_turn_persistence.py",
            "exec",
        ),
        namespace,
    )
    return namespace


class BotRunner:
    def __init__(self, trace, note_source):
        self.trace = trace
        self.note_source = note_source
        self._app_id = "bot-app"

    def _delivery_adapter_for(self, source):
        return None

    def _should_send_voice_reply(self, *args, **kwargs):
        return False

    def _reply_anchor_for_event(self, event):
        return "fixture"

    def _schedule_usage_limit_offer(self, *args, **kwargs):
        return None

    def _note_bot_reply(self, source):
        if getattr(source, "is_bot", False):
            self.trace.append(("bot-note", self.note_source, source))


def _bot_locals(runner, *, message_id="fixture", user_id="bot-a", is_bot=True,
                already_sent=False, silence=False, **extra):
    source = SimpleNamespace(
        platform="feishu",
        chat_id="fixture",
        thread_id="",
        user_id=user_id,
        profile_id="default",
        is_bot=is_bot,
    )
    values = {
        "self": runner,
        "source": source,
        "event": SimpleNamespace(message_id=message_id),
        "message_id": message_id,
        "agent_result": {"already_sent": already_sent},
        "_intentional_silence": silence,
    }
    values.update(extra)
    return values


def _instrumentation_snapshot() -> dict:
    monitoring = {}
    monitor = getattr(sys, "monitoring", None)
    if monitor is not None:
        for tool_id in range(6):
            try:
                tool = monitor.get_tool(tool_id)
                events = int(monitor.get_events(tool_id)) if tool else 0
            except (AttributeError, TypeError, ValueError, OSError):
                tool, events = None, 0
            monitoring[str(tool_id)] = {"tool": tool, "events": events}
    return {
        "profile": sys.getprofile() is not None,
        "trace": sys.gettrace() is not None,
        "monitoring": monitoring,
    }


def test_v0230_pinned_checkout_is_supported():
    root = _pinned_root()
    detection = detect_hermes(root)
    assert detection.supported is True
    assert detection.compatibility == "full", detection.reason
    assert detection.reason == "supported"
    assert detection.capabilities["message_handler"] is True
    assert detection.capabilities["completion_return"] is True
    assert detection.capabilities["admission_completion"] is True
    assert ac.CORE_HASHES_0230 in ac.SOURCE_COHORTS
    assert source_bytes(root, SOURCE_HASHES_0230) is not None


def test_v0230_cohort_requires_every_pinned_source(tmp_path):
    root = _pinned_root()
    base = (root / "gateway/platforms/base.py").read_text(encoding="utf-8")
    candidate = tmp_path / "exact"
    _copy_sources(root, candidate)
    assert _detect_admission_completion(candidate, base) == (
        True,
        "verified-source-cohort",
    )
    for name in MANIFEST["files"]:
        changed = tmp_path / ("changed-" + name.replace("/", "_"))
        _copy_sources(root, changed)
        with (changed / name).open("ab") as stream:
            stream.write(b"\n# unowned drift\n")
        assert _detect_admission_completion(changed, base) == (
            False,
            "unsupported-source",
        ), name


def test_v0230_mixed_known_source_files_are_rejected(tmp_path):
    root = _pinned_root()
    manifest_path = Path(__file__).parents[1] / "fixtures/session_authority_0225.json"
    old = json.loads(manifest_path.read_text())
    archive = manifest_path.with_suffix(".json.gz").read_bytes()
    assert hashlib.sha256(archive).hexdigest() == old["archive_sha256"]
    sources = json.loads(gzip.decompress(archive))["files"]
    assert "gateway/session_authority.py" in sources
    candidate = tmp_path / "mixed"
    _copy_sources(root, candidate)
    (candidate / "gateway/session_authority.py").write_text(
        sources["gateway/session_authority.py"]
    )
    base = (root / "gateway/platforms/base.py").read_text(encoding="utf-8")
    assert _detect_admission_completion(candidate, base) == (
        False,
        "unsupported-source",
    )


def test_v0230_wrapper_exact_shape_and_negative_variants():
    root = _pinned_root()
    source = (root / "gateway/run_turn_persistence.py").read_text(encoding="utf-8")
    assert completion_contracts.wrapper_is_exact(_wrapper_node(source))
    assert completion_contracts.source_has_v0230_wrapper(source)
    negative = [
        (" and not intentional_silence:", ":"),
        ("(delivered or agent_result.get(\"already_sent\"))",
         "(response or agent_result.get(\"already_sent\"))"),
        ("            self._note_bot_reply(source)",
         "            self._note_bot_reply(event)"),
        ("            self._note_bot_reply(source)",
         "            await self._note_bot_reply(source)"),
        ("        if (delivered or agent_result.get(\"already_sent\")) and not intentional_silence:",
         "        if True:"),
        ("        if isinstance(agent_result.get(\"usage_limit\"), dict):",
         "        _hfc_extra = True\n        if isinstance(agent_result.get(\"usage_limit\"), dict):"),
        ("        return delivered\n",
         "        return delivered\n        if (delivered or agent_result.get(\"already_sent\")) and not intentional_silence:\n            self._note_bot_reply(source)\n"),
    ]
    for old, new in negative:
        changed = _replace_once(source, old, new)
        assert not completion_contracts.wrapper_is_exact(
            _wrapper_node(changed)
        ), (old, new)
        assert not completion_contracts.source_has_v0230_wrapper(changed)
    old_wrapper = """async def _deliver_turn_response_then_limit_offer(
        self, event, source, session_entry, session_key, run_generation,
        agent_result, agent_messages, response, footer_line, intentional_silence,
    ):
        delivered = await self._hmwa_deliver_turn_response(
            event, source, session_entry, session_key, run_generation,
            agent_result, agent_messages, response, footer_line, intentional_silence,
        )
        if isinstance(agent_result.get("usage_limit"), dict):
            self._schedule_usage_limit_offer(event, agent_result["usage_limit"], session_key=session_key,
                                             generation=run_generation)
        return delivered
"""
    assert completion_contracts.wrapper_is_exact(_wrapper_node(old_wrapper))
    assert not completion_contracts.source_has_v0230_wrapper(old_wrapper)


@pytest.mark.parametrize("delivered", [None, "answer"])
@pytest.mark.parametrize("already_sent", [False, True])
@pytest.mark.parametrize("silence", [False, True])
def test_v0230_wrapper_truth_table_preserves_result(delivered, already_sent, silence):
    root = _pinned_root()
    source = (root / "gateway/run_turn_persistence.py").read_text(encoding="utf-8")
    wrapper = _wrapper_node(source)
    namespace = {"__builtins__": __builtins__}
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
            "v0.23.0-wrapper.py",
            "exec",
        ),
        namespace,
    )
    notes = []

    class Runner:
        async def _hmwa_deliver_turn_response(self, *args):
            return delivered

        def _note_bot_reply(self, source):
            notes.append(source)

        def _schedule_usage_limit_offer(self, *args, **kwargs):
            return None

    agent_result = {"already_sent": already_sent}
    before = copy.deepcopy(agent_result)
    result = asyncio.run(
        namespace["_deliver_turn_response_then_limit_offer"](
            Runner(),
            SimpleNamespace(message_id="fixture"),
            SimpleNamespace(platform="feishu", is_bot=True),
            None,
            "route",
            1,
            agent_result,
            [],
            "answer",
            "",
            silence,
        )
    )
    assert result is delivered
    assert agent_result == before
    assert len(notes) == int(bool(delivered or already_sent) and not silence)


def test_v0230_bot_reply_claim_is_owner_scoped_duplicate_and_late_safe():
    h.reset_runtime_state()
    trace = []
    runner_a = BotRunner(trace, "runner-A")
    runner_b = BotRunner(trace, "runner-B")
    first = _bot_locals(runner_a)
    duplicate = _bot_locals(runner_a)
    other_owner = _bot_locals(runner_b)
    new_turn = _bot_locals(runner_a, message_id="next", user_id="bot-b")
    late = _bot_locals(runner_a)
    assert h.note_bot_reply_for_delivered_card(first, True, True) is True
    assert h.note_bot_reply_for_delivered_card(duplicate, True, True) is False
    assert h.note_bot_reply_for_delivered_card(other_owner, True, True) is True
    assert h.note_bot_reply_for_delivered_card(new_turn, True, True) is True
    assert h.note_bot_reply_for_delivered_card(late, True, True) is False
    assert [entry[1] for entry in trace] == ["runner-A", "runner-B", "runner-A"]
    assert trace[-1][2].user_id == "bot-b"
    h.reset_runtime_state()


def test_v0230_rendered_completion_helper_notes_bot_reply_once():
    root = _pinned_root()
    rendered = _rendered_gateway_sources(root)
    assert b"note_bot_reply_for_delivered_card" in rendered[
        "gateway/run_turn_persistence.py"
    ]
    namespace = _patched_wrapper_namespace(rendered)
    cases = []
    for staged, already_sent, silence, ack, bot in itertools.product(
        (False, True), repeat=5
    ):
        h.reset_runtime_state()
        trace = []
        runner = BotRunner(trace, "runner")
        runner._hmwa_deliver_turn_response = (
            lambda *args: namespace["_hmwa_deliver_turn_response"](runner, *args)
        )
        source = SimpleNamespace(
            platform="feishu",
            chat_id="fixture",
            thread_id="",
            user_id="bot-a",
            profile_id="default",
            is_bot=bot,
        )
        event = SimpleNamespace(message_id="fixture")
        agent_result = {"already_sent": already_sent}
        before = copy.deepcopy(agent_result)

        async def emit(*args, **kwargs):
            trace.append("card-ack" if ack else "no-card-ack")
            return ack

        async def stage(*args):
            trace.append("staged")
            return True

        with patch.object(
            h, "build_event", return_value={"data": {"native_delivery": "allowed"}}
        ), patch.object(
            h, "can_stage_exact_base_completion", return_value=staged
        ), patch.object(
            h, "stage_message_completed_from_hermes_locals_async", side_effect=stage
        ), patch.object(
            h, "admission_completion_mode", return_value="ordinary"
        ), patch.object(
            h, "emit_from_hermes_locals_async", side_effect=emit
        ):
            result = asyncio.run(
                namespace["_deliver_turn_response_then_limit_offer"](
                    runner,
                    event,
                    source,
                    SimpleNamespace(session_id="fixture"),
                    "fixture",
                    1,
                    agent_result,
                    [],
                    "actual answer",
                    "",
                    silence,
                )
            )
        assert agent_result == before
        assert sum(1 for item in trace if isinstance(item, tuple) and item[0] == "bot-note") == int(
            bot and not silence
        ), (staged, already_sent, silence, ack, bot, trace)
        cases.append((result, trace))
    assert len(cases) == 32
    h.reset_runtime_state()


def test_v0230_queued_final_receipt_matches_native_wrapper_note_trace():
    root = _pinned_root()
    native_source = (root / "gateway/run_turn_persistence.py").read_text(
        encoding="utf-8"
    )
    wrapper = _wrapper_node(native_source)
    native = {"__builtins__": __builtins__}
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
            "v0.23.0-native-wrapper.py",
            "exec",
        ),
        native,
    )
    outer = SimpleNamespace(
        platform="feishu", chat_id="outer", user_id="bot-a", is_bot=True
    )
    next_source = SimpleNamespace(
        platform="feishu", chat_id="next", user_id="bot-b", is_bot=True
    )
    native_trace = []

    class NativeRunner:
        async def _hmwa_deliver_turn_response(self, *args):
            return "actual answer"

        def _note_bot_reply(self, source):
            native_trace.append(source)

        def _schedule_usage_limit_offer(self, *args, **kwargs):
            return None

    queued = {
        "queued_terminal_source": next_source,
        "queued_terminal_inbound_id": "next-inbound",
    }
    asyncio.run(
        native["_deliver_turn_response_then_limit_offer"](
            NativeRunner(),
            SimpleNamespace(message_id="outer"),
            outer,
            SimpleNamespace(session_id="fixture"),
            "fixture",
            1,
            dict(queued),
            [],
            "actual answer",
            "",
            False,
        )
    )
    h.reset_runtime_state()
    rendered = _rendered_gateway_sources(root)
    namespace = _patched_wrapper_namespace(rendered)
    hfc_trace = []
    runner = BotRunner(hfc_trace, "runner")
    runner._hmwa_deliver_turn_response = (
        lambda *args: namespace["_hmwa_deliver_turn_response"](runner, *args)
    )
    agent_result = {**queued, "_hfc_queued_final_delivered": True}
    before = copy.deepcopy(agent_result)
    with patch.object(
        h, "build_event", return_value={"data": {"native_delivery": "allowed"}}
    ):
        result = asyncio.run(
            namespace["_deliver_turn_response_then_limit_offer"](
                runner,
                SimpleNamespace(message_id="outer"),
                outer,
                SimpleNamespace(session_id="fixture"),
                "fixture",
                1,
                agent_result,
                [],
                "actual answer",
                "",
                False,
            )
        )
    assert result is None
    assert agent_result == before
    assert native_trace == [outer]
    assert [entry[2] for entry in hfc_trace] == native_trace
    assert hfc_trace[0][2] is not next_source
    h.reset_runtime_state()


def test_v0230_queued_final_without_terminal_id_does_not_note():
    h.reset_runtime_state()
    trace = []
    runner = BotRunner(trace, "runner")
    values = _bot_locals(runner)
    values["agent_result"]["_hfc_queued_final_delivered"] = True
    assert h.note_bot_reply_for_delivered_card(values, True, True) is False
    assert trace == []
    h.reset_runtime_state()


def test_v0230_expected_defaults_match_exact_pinned_helpers():
    root = _pinned_root()
    sentinel = object()
    namespace = {"_ANY_HEAD": sentinel}
    checked = 0
    for module_name, qualified_names in topology.FUNCTIONS_0230.items():
        raw = (root / (module_name.replace(".", "/") + ".py")).read_bytes()
        for qualified in qualified_names:
            expected = topology._expected_function_defaults(
                raw, qualified, namespace, module_name + ".py"
            )
            assert expected is not None, (module_name, qualified)
            fake = SimpleNamespace(
                __defaults__=expected[0] or None,
                __kwdefaults__=expected[1] or None,
            )
            assert topology._defaults_match(fake, expected)
            if expected[1]:
                key = next(iter(expected[1]))
                changed = dict(expected[1])
                changed[key] = object()
                assert not topology._defaults_match(
                    SimpleNamespace(
                        __defaults__=expected[0] or None,
                        __kwdefaults__=changed,
                    ),
                    expected,
                )
            elif expected[0]:
                changed = (object(), *expected[0][1:])
                assert not topology._defaults_match(
                    SimpleNamespace(__defaults__=changed, __kwdefaults__=None),
                    expected,
                )
            checked += 1
    assert checked >= 14


def _build_modules(root: Path, sources: dict[str, bytes]) -> dict[str, ModuleType]:
    """Compile exact pinned functions into synthetic globals; no Hermes import."""
    modules = {}
    for name in topology.SOURCE_HASHES_0230:
        module = ModuleType(name)
        module.__file__ = str(root / (name.replace(".", "/") + ".py"))
        module._ANY_HEAD = object()
        modules[name] = module
    for name, names in topology.FUNCTIONS_0230.items():
        if name == "gateway.session_ingress":
            names = (
                *names,
                "_hand_over_turn_marker",
                "_move_marker",
                "deliver_response",
                "deliver_settled",
                "execute_admission",
                "dispatch_shared_busy",
            )
        elif name == "gateway.session_authority":
            names = (
                *names,
                "SessionAuthority._drain",
                "SessionAuthority._schedule",
                "SessionAuthority._resolve_settled_waiter",
                "SessionAuthority._execute_claimed",
                "SessionAuthority._stamp_claim",
            )
        module = modules[name]
        raw = sources[name]
        compiled = compile(raw, module.__file__, "exec", dont_inherit=True)
        for qualified in names:
            owner, _, function_name = qualified.rpartition(".")
            code = _compiled_code(compiled, function_name, owner or None)
            assert not code.co_freevars, (name, qualified)
            expected = topology._expected_function_defaults(
                raw, qualified, vars(module), module.__file__
            )
            assert expected is not None, (name, qualified)
            function = FunctionType(
                code, vars(module), function_name, expected[0] or None
            )
            function.__kwdefaults__ = expected[1] or None
            if (name, qualified) in topology.DECORATED_0230:
                function = contextlib.asynccontextmanager(function)
            if owner:
                if not hasattr(module, owner):
                    setattr(module, owner, type(owner, (), {}))
                setattr(getattr(module, owner), function_name, function)
            else:
                setattr(module, function_name, function)
    lifecycle = modules["gateway.session_producer_lifecycle"]
    ingress = modules["gateway.session_ingress"]
    authority = modules["gateway.session_authority"]
    base = modules["gateway.platforms.base"]
    event = modules["gateway.platforms.event"]
    contract = modules["gateway.session_contract"]
    event.ProcessingOutcome = type("ProcessingOutcome", (), {})
    ingress.ProcessingOutcome = lifecycle.ProcessingOutcome = event.ProcessingOutcome
    ingress.producer_turn = lifecycle.producer_turn
    ingress.close_producer_turn = lifecycle.close_producer_turn
    authority.mark_producer_admission = lifecycle.mark_producer_admission
    authority.drop_producer_turn = lifecycle.drop_producer_turn
    base.run_post_delivery_callback = modules[
        "gateway.platforms.base_post_delivery"
    ].run_post_delivery_callback
    base._POST_DELIVERY_CALLBACK_TIMEOUT_SECONDS = 30.0
    modules["gateway.run_turn_followup_ack"].BasePlatformAdapter = (
        base.BasePlatformAdapter
    )
    modules["gateway.session_settlement_recovery"].drop_producer_turn = (
        lifecycle.drop_producer_turn
    )
    contract.SessionActor = type("SessionActor", (), {})
    authority.SessionActor = contract.SessionActor
    return modules


def test_v0230_installed_rendered_sources_are_verified(tmp_path, monkeypatch):
    root = _pinned_root()
    clean = tmp_path / "clean"
    installed = tmp_path / "installed"
    names = list(MANIFEST["files"])
    _copy_sources(root, clean, names)
    _copy_sources(root, installed, names)
    assert source_bytes(clean, SOURCE_HASHES_0230) is not None
    originals = {
        name: (installed / name).read_bytes()
        for name in DECOMPOSED_SOURCE_TARGETS
    }
    rendered = decomposed_ownership.render(originals)
    for name, raw in rendered.items():
        (installed / name).write_bytes(raw)
    assert source_bytes(installed, SOURCE_HASHES_0230) is not None
    pair = source_bytes_0230(installed)
    assert pair is not None
    modules = _build_modules(installed, pair[1])
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def checked():
        return topology.verified(
            installed,
            modules["gateway.session_ingress"],
            modules["gateway.session_authority"],
            topology.SOURCE_HASHES_0230,
            functions=topology.FUNCTIONS_0230,
            decorated=topology.DECORATED_0230,
            optional_modules=topology.OPTIONAL_MODULES_0230,
        )

    topology._COMPILE_CACHE.clear()
    topology._FUNCTION_NODE_CACHE.clear()
    topology._AST_TREE_CACHE.clear()
    topology._OWNED_REMOVAL_CACHE.clear()
    cold_cpu_start = time.process_time()
    started = time.monotonic()
    assert checked()
    first_seconds = time.monotonic() - started
    cold_cpu_seconds = time.process_time() - cold_cpu_start
    warm_cpu_start = time.process_time()
    started = time.monotonic()
    for _ in range(4):
        assert ac._verified() is not None
    repeated_seconds = time.monotonic() - started
    warm_cpu_seconds = time.process_time() - warm_cpu_start
    print(
        f"v0230 installed topology: cold_wall={first_seconds:.3f}s "
        f"cold_cpu={cold_cpu_seconds:.3f}s warm4_wall={repeated_seconds:.3f}s "
        f"warm4_cpu={warm_cpu_seconds:.3f}s "
        f"instrumentation={json.dumps(_instrumentation_snapshot(), sort_keys=True)}"
    )
    key_bindings = {
        "settle_and_deliver",
        "settle_with_retry",
        "track_mutation",
        "_note_bot_reply",
        "_hmwa_deliver_turn_response",
        "_handle_message_with_agent",
        "mark_producer_admission",
        "producer_turn",
    }
    refusals = []
    for name, qualified_names in topology.FUNCTIONS_0230.items():
        for qualified in qualified_names:
            function_name = qualified.rpartition(".")[-1]
            if function_name not in key_bindings:
                continue
            owner, _, _ = qualified.rpartition(".")
            scope = getattr(modules[name], owner) if owner else modules[name]
            original = getattr(scope, function_name)
            setattr(scope, function_name, lambda: None)
            try:
                assert not checked(), (name, qualified)
                refusals.append(name + "." + qualified)
            finally:
                setattr(scope, function_name, original)
    assert len(refusals) == 8
    assert checked()
    assert first_seconds < 5.0
    assert repeated_seconds / 4 < first_seconds
    assert repeated_seconds < 2.0
    for name in names:
        candidate = tmp_path / ("drift-" + name.replace("/", "_"))
        shutil.copytree(installed, candidate)
        with (candidate / name).open("ab") as stream:
            stream.write(b"\n# unowned drift\n")
        assert source_bytes(candidate, SOURCE_HASHES_0230) is None, name


def test_v0230_rendered_digests_match_full_exact_patcher():
    root = _pinned_root()
    assert HFC_VERSION == MANIFEST["render_version"] == topology.RENDER_VERSION_0230
    originals = {
        name: (root / name).read_bytes()
        for name in DECOMPOSED_SOURCE_TARGETS
        if (root / name).is_file()
    }
    rendered = decomposed_ownership.render(originals)
    actual = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in rendered.items()
    }
    assert actual == MANIFEST["rendered_files"]
    assert topology.EXPECTED_RENDERED_HASHES_0230 == MANIFEST["rendered_files"]


def test_v0230_installed_verification_uses_pinned_rendered_sources(tmp_path, monkeypatch):
    root = _pinned_root()
    installed = tmp_path / "installed"
    _copy_sources(root, installed)
    originals = {
        name: (installed / name).read_bytes()
        for name in DECOMPOSED_SOURCE_TARGETS
        if (installed / name).is_file()
    }
    rendered = decomposed_ownership.render(originals)
    for name, raw in rendered.items():
        (installed / name).write_bytes(raw)

    def forbidden(*args, **kwargs):
        pytest.fail("runtime verification re-ran the full installer render")

    monkeypatch.setattr(decomposed_ownership, "render", forbidden)
    assert source_bytes(installed, SOURCE_HASHES_0230) is not None
    assert source_bytes_0230(installed) is not None

    mixed = tmp_path / "mixed"
    _copy_sources(root, mixed)
    for name, raw in rendered.items():
        (mixed / name).write_bytes(raw)
    (mixed / "gateway/run_turn.py").write_bytes(
        (root / "gateway/run_turn.py").read_bytes()
    )
    assert source_bytes(mixed, SOURCE_HASHES_0230) is None
    monkeypatch.setattr(topology, "_HFC_VERSION", "5.0.18")
    assert source_bytes(installed, SOURCE_HASHES_0230) is None


def test_v0230_owned_removal_memo_is_pure_and_drift_sensitive(tmp_path, monkeypatch):
    root = _pinned_root()
    installed = tmp_path / "installed"
    _copy_sources(root, installed)
    originals = {
        name: (installed / name).read_bytes()
        for name in DECOMPOSED_SOURCE_TARGETS
        if (installed / name).is_file()
    }
    rendered = decomposed_ownership.render(originals)
    for name, raw in rendered.items():
        (installed / name).write_bytes(raw)
    topology._OWNED_REMOVAL_CACHE.clear()
    calls = []
    strict_remove = decomposed_ownership._remove_target

    def counted(target, raw):
        calls.append((target, hashlib.sha256(raw).hexdigest()))
        return strict_remove(target, raw)

    monkeypatch.setattr(decomposed_ownership, "_remove_target", counted)
    assert source_bytes(installed, SOURCE_HASHES_0230) is not None
    first_calls = len(calls)
    assert first_calls == len(DECOMPOSED_SOURCE_TARGETS)
    assert len(topology._OWNED_REMOVAL_CACHE) <= 32
    assert source_bytes(installed, SOURCE_HASHES_0230) is not None
    assert len(calls) == first_calls

    target = installed / "gateway/run_turn.py"
    original = target.read_bytes()
    target.write_bytes(original + b"\n# unowned drift\n")
    assert source_bytes(installed, SOURCE_HASHES_0230) is None
    assert len(calls) > first_calls
    target.write_bytes(original)
    assert source_bytes(installed, SOURCE_HASHES_0230) is not None


def test_v0230_pinned_rendered_sources_reject_relocation_defaults_and_warm_drift(
    tmp_path, monkeypatch
):
    root = _pinned_root()
    installed = tmp_path / "installed"
    _copy_sources(root, installed)
    originals = {
        name: (installed / name).read_bytes()
        for name in DECOMPOSED_SOURCE_TARGETS
        if (installed / name).is_file()
    }
    rendered = decomposed_ownership.render(originals)
    for name, raw in rendered.items():
        (installed / name).write_bytes(raw)
    assert source_bytes_0230(installed) is not None
    with patch.object(topology, "_HFC_VERSION", "5.0.18"):
        assert source_bytes_0230(installed) is None

    target = "gateway/run_turn_persistence.py"
    lines = rendered[target].decode().splitlines(keepends=True)
    start = next(
        i for i, line in enumerate(lines)
        if line.strip() == "# HERMES_FEISHU_CARD_COMPLETE_PATCH_BEGIN"
    )
    end = next(
        i for i, line in enumerate(lines)
        if line.strip() == "# HERMES_FEISHU_CARD_COMPLETE_PATCH_END"
    ) + 1
    block = lines[start:end]
    rest = lines[:start] + lines[end:]
    helper = next(
        node for node in ast.walk(ast.parse("".join(rest)))
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_hmwa_deliver_turn_response"
    )
    moved = "".join(
        rest[: helper.end_lineno] + block + rest[helper.end_lineno :]
    ).encode()
    assert decomposed_ownership._remove_target(target, moved) == (
        decomposed_ownership._remove_target(target, rendered[target])
    )
    assert hashlib.sha256(moved).hexdigest() != MANIFEST["rendered_files"][target]
    (installed / target).write_bytes(moved)
    assert source_bytes_0230(installed) is None
    (installed / target).write_bytes(rendered[target])

    pair = source_bytes_0230(installed)
    assert pair is not None
    modules = _build_modules(installed, pair[1])
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def checked():
        return topology.verified(
            installed,
            modules["gateway.session_ingress"],
            modules["gateway.session_authority"],
            topology.SOURCE_HASHES_0230,
            functions=topology.FUNCTIONS_0230,
            decorated=topology.DECORATED_0230,
            optional_modules=topology.OPTIONAL_MODULES_0230,
        )

    assert checked()
    persist = modules["gateway.session_runtime_workers"].persist_work_count
    original_defaults = persist.__defaults__
    persist.__defaults__ = (object(),)
    assert not checked()
    persist.__defaults__ = original_defaults
    assert checked()
    claim = modules["hermes_state_runtime"].claim_session_input
    original_kwdefaults = claim.__kwdefaults__
    claim.__kwdefaults__ = {**original_kwdefaults, "head": object()}
    assert not checked()
    claim.__kwdefaults__ = original_kwdefaults
    assert checked()

    checked_defaults = 0
    for module_name, qualified_names in topology.FUNCTIONS_0230.items():
        raw = pair[1][module_name]
        tree = topology._parsed_ast(raw)
        before = ast.dump(tree, include_attributes=True)
        for qualified in qualified_names:
            assert topology._expected_function_defaults(
                raw,
                qualified,
                vars(modules[module_name]),
                str(installed / (module_name.replace(".", "/") + ".py")),
            ) is not None
            checked_defaults += 1
        assert ast.dump(tree, include_attributes=True) == before, module_name
    assert checked_defaults == 81

    ingress_path = installed / "gateway/session_ingress.py"
    original = ingress_path.read_bytes()
    ingress_path.write_bytes(original + b"\n# warm disk mutation\n")
    assert not checked()
    ingress_path.write_bytes(original)
    assert checked()


class Port:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class StoreError(Exception):
    pass


def _compiled_functions(module, root, path, names, owner=None):
    raw = (root / path).read_text(encoding="utf-8")
    code = compile(raw, str(root / path), "exec", dont_inherit=True)
    for name in names:
        native = _compiled_code(code, name, owner)
        qualified = (owner + "." if owner else "") + name
        expected = topology._expected_function_defaults(
            raw, qualified, vars(module), str(root / path)
        )
        assert expected is not None, (path, qualified)
        function = FunctionType(
            native, vars(module), name, expected[0] or None
        )
        function.__kwdefaults__ = expected[1] or None
        setattr(module, name, function)
    return module


async def _settlement_case(root: Path, mode: str, monkeypatch):
    """Exact v0.23.0 settlement Task with real HFC handoff and bounded ports.

    _verified is a metadata port, get_session_admission a DB read port and
    _base_extractor a platform extraction seam. The only real SQL is the
    runtime_epoch table; no Hermes import, Gateway, model or Feishu client.
    """
    raw = {}
    for name in (
        "gateway/session_authority.py",
        "gateway/session_ingress.py",
        "gateway/session_settlement_recovery.py",
        "gateway/session_runtime_workers.py",
        "gateway/session_producer_lifecycle.py",
    ):
        raw[name] = (root / name).read_text(encoding="utf-8")
        assert hashlib.sha256(raw[name].encode()).hexdigest() == MANIFEST["files"][name]

    trace = []

    def module(name):
        value = ModuleType(name)
        monkeypatch.setitem(sys.modules, name, value)
        return value

    for name in ("gateway", "gateway.platforms", "agent"):
        module(name).__path__ = []

    state = module("hermes_state_runtime")
    state.RuntimeStoreError = StoreError
    state.get_session_admission = lambda db, **kw: db.row
    automation = module("gateway.session_automation")
    automation.admission_display = lambda payload: {}
    secret = module("agent.secret_scope")
    secret._is_process_home = lambda home: False
    base = module("gateway.platforms.base")
    base._thread_metadata_for_event = lambda event: {}
    base._mark_notify_metadata = lambda value: value
    controls = module("gateway.session_queued_controls")
    controls.publish_transfers = lambda *args: trace.append("transfers")

    lifecycle = module("gateway.session_producer_lifecycle")
    lifecycle.asyncio = asyncio
    lifecycle.ProcessingOutcome = Port(SUCCESS="success", FAILURE="failure")
    _compiled_functions(
        lifecycle,
        root,
        "gateway/session_producer_lifecycle.py",
        ["_turns", "close_producer_turn", "drop_producer_turn"],
    )

    ingress = module("gateway.session_ingress")
    ingress.asyncio = asyncio
    ingress.logger = logging.getLogger(__name__)
    ingress.nullcontext = contextlib.nullcontext
    ingress._MARKER_FIELDS = (
        "_gateway_active_turn_session_key",
        "_gateway_active_turn_token",
    )
    ingress.ProcessingOutcome = lifecycle.ProcessingOutcome
    ingress.close_producer_turn = lifecycle.close_producer_turn
    _compiled_functions(
        ingress,
        root,
        "gateway/session_ingress.py",
        [
            "_marker_adopters",
            "_held_markers",
            "_registry",
            "waiter_replies",
            "_move_marker",
            "_release_turn_marker",
            "_hand_over_turn_marker",
            "_hand_back_post_delivery",
            "fire_post_delivery",
            "retain_unsettled_delivery",
            "_launch_body_if_launch_owner",
            "_execution_scope",
            "deliver_settled",
            "deliver_response",
            "dispatch_shared_busy",
        ],
    )

    auth = module("gateway.session_authority")
    auth.asyncio = asyncio
    auth.contextlib = contextlib
    names = [
        "_schedule",
        "_drain",
        "_resolve_settled_waiter",
        "_execute_claimed",
        "_stamp_claim",
        "_run_deferred_reset",
    ]
    _compiled_functions(
        auth, root, "gateway/session_authority.py", names,
        owner="SessionAuthority",
    )
    auth.SessionAuthority = type(
        "SessionAuthority",
        (),
        {
            name: (
                staticmethod(getattr(auth, name))
                if name == "_run_deferred_reset"
                else getattr(auth, name)
            )
            for name in names
        },
    )

    workers = module("gateway.session_runtime_workers")
    workers.asyncio = asyncio
    _compiled_functions(
        workers,
        root,
        "gateway/session_runtime_workers.py",
        ["persist_work_count", "track_mutation"],
    )

    settle = module("gateway.session_settlement_recovery")
    settle.asyncio = asyncio
    settle.logger = logging.getLogger(__name__)
    settle.RuntimeStoreError = StoreError
    settle.get_session_admission = state.get_session_admission
    settle.drop_producer_turn = lifecycle.drop_producer_turn
    _compiled_functions(
        settle,
        root,
        "gateway/session_settlement_recovery.py",
        ["settle_and_deliver"],
    )

    tree = ast.parse(raw["gateway/session_authority.py"])
    drain = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_drain"
    )
    tail = None
    for node in ast.walk(drain):
        for _, value in ast.iter_fields(node):
            if isinstance(value, list):
                for index, item in enumerate(value):
                    if (
                        isinstance(item, ast.ImportFrom)
                        and item.module == "gateway.session_runtime_workers"
                        and any(alias.name == "track_mutation" for alias in item.names)
                    ):
                        tail = value[index:]
    assert tail is not None and len(tail) == 5, len(tail or [])
    wrapped = ast.parse(
        "async def drain_tail(self, live, ref, row, response, outcome):\n pass"
    ).body[0]
    wrapped.body = tail
    namespace = {"asyncio": asyncio, "__builtins__": __builtins__}
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[wrapped], type_ignores=[])
            ),
            str(root / "gateway/session_authority.py"),
            "exec",
        ),
        namespace,
    )

    db = Port(row=None)
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE runtime_epoch(singleton INTEGER, epoch INTEGER, instance_id TEXT)"
    )
    connection.execute(
        "INSERT INTO runtime_epoch VALUES(1,9,'fixture-owner')"
    )
    db._read_ctx = lambda: contextlib.nullcontext(connection)
    runner = Port(
        config=Port(multiplex_profiles=False),
        _draining=False,
        _is_session_run_current=lambda key, generation: generation == 7,
        _persist_active_agents=lambda: trace.append("persist-work"),
    )
    source = Port(
        platform="feishu",
        chat_id="fixture-chat",
        thread_id="",
        user_id="fixture-sender",
        profile_id="default",
    )
    event = Port(source=source, message_id="fixture-message")
    ref = Port(session_id="fixture-session")
    row = {
        "admission_id": "fixture-admission",
        "generation": 7,
        "target_session_id": ref.session_id,
        "owner_epoch": 9,
        "status": "started",
        "payload": {
            "native_text_v1": {
                "source": {
                    "platform": "feishu",
                    "chat_id": source.chat_id,
                    "thread_id": "",
                    "user_id": source.user_id,
                    "profile": "default",
                },
                "event": {"message_id": event.message_id},
                "route": "fixture-route",
            }
        },
    }
    db.row = row
    stream = Port(
        lock=threading.RLock(),
        execution={},
        publish=lambda *args, **kwargs: trace.append("claim-published"),
    )
    live = Port(
        source=source,
        route="fixture-route",
        task=asyncio.current_task(),
        event_stream=stream,
        superseded=False,
    )
    authority = auth.SessionAuthority()
    authority.runner = runner
    authority.profile_id = "default"
    authority.epoch = 9
    authority.db = db
    authority.instance_id = "fixture-owner"
    authority.retiring = False
    authority.sessions = {ref.session_id: live}
    authority.executing = {}
    authority.pending_results = {
        row["admission_id"]: {"result": {"final_response": "answer"}, "usage": {}}
    }
    authority.pending_deliveries = {}
    authority.native_waiters = set()
    authority.waiters = {
        row["admission_id"]: asyncio.get_running_loop().create_future()
    }
    authority.pending_stops = {}
    authority.pending_stop_causes = {}
    authority.pending_steers = {}
    authority.adopted = {}
    authority.deferred_resets = {}
    authority._publish_pending = lambda ref: trace.append("pending")
    authority._pause = lambda ref, reason: trace.append("pause:" + reason)
    authority._stamp_claim(ref, live, row)
    waiter = authority.waiters[row["admission_id"]]

    staged = []
    native = []
    finishes = []
    producer = asyncio.current_task()
    settlement_tasks = []

    class Adapter:
        _app_id = "fixture-app"

        def _unwrap_ephemeral(self, response):
            return response, 0

        async def _extract_response_content(self, text, event, key, **kwargs):
            stage = ac.consume_extracted(
                {
                    "self": self,
                    "event": event,
                    "session_key": key,
                    "pre_extract": text,
                    "text_content": text,
                    "images": [],
                    "local_files": [],
                    "media_files": [],
                },
                "default",
            )
            if stage:
                staged.append(stage)
                trace.append("card-ack")
                return Port(
                    text_content="", images=[], local_files=[], media_files=[]
                )
            return Port(
                text_content=text, images=[], local_files=[], media_files=[]
            )

        async def _send_final_text(
            self, event, key, text, metadata, ephemeral, ttl, record
        ):
            native.append(text)
            record(Port(success=True))

        async def _deliver_attachments(self, *args, **kwargs):
            return None

    adapter = Adapter()
    authority.pending_deliveries[row["admission_id"]] = (
        adapter,
        event,
        live.route,
        "answer",
        None,
    )
    offer = ac.Completion(
        stage={"payload": {"data": {"answer": "answer"}}},
        route=(
            "default",
            source.chat_id,
            "",
            source.user_id,
            event.message_id,
        ),
        runner=weakref.ref(runner),
        adapter=weakref.ref(adapter),
        producer=weakref.ref(producer),
        source_event=weakref.ref(event),
        authority_owner=(ref.session_id, 7),
        runtime_generation=7,
        session_key=live.route,
        physical_session="fixture-physical",
        native_response="answer",
        expires=time.monotonic() + 60,
        admission_id=row["admission_id"],
        authority=weakref.ref(authority),
        epoch=9,
        live=weakref.ref(live),
        app_id=adapter._app_id,
        instance_id="fixture-owner",
        authority_profile="default",
        db=weakref.ref(db),
    )
    setattr(event, ac._ATTR, offer)

    async def finish(event, ok):
        finishes.append((event, ok))
        trace.append("hfc-finish")

    entered = asyncio.Event()
    release = asyncio.Event()

    async def commit(authority, live, ref, row, response, outcome, captured, settlement):
        settlement_tasks.append(asyncio.current_task())
        assert asyncio.current_task() is not producer
        trace.append("settlement-enter")
        entered.set()
        if mode == "cancel":
            await release.wait()
        if mode == "unknown":
            raise OSError("synthetic write refusal")
        row.update(status="terminal", outcome="completed")
        settlement.update(settled=dict(row), response=response)
        trace.append("commit")

    async def recover(authority, row):
        row["status"] = "unknown"
        trace.append("unknown")
        return "unknown"

    settle.settle_with_retry = commit
    settle.recover_failed_settlement = recover
    settle.release_media_after_delivery = lambda *args: trace.append("release-media")
    try:
        with patch.object(
            ac, "_verified", return_value=(ingress, auth)
        ), patch.object(
            ac,
            "_base_extractor",
            side_effect=lambda adapter: adapter._extract_response_content.__func__.__code__,
        ):
            ac._install(ingress, finish)
            await ingress._hand_over_turn_marker(authority, row["admission_id"], event)
            assert offer.state == "offered", offer.state
            authority.executing.pop(ref.session_id, None)
            if mode == "cancel":
                async def cancel_producer():
                    await entered.wait()
                    producer.cancel()
                    await asyncio.sleep(0)
                    assert not producer.done()
                    assert not settlement_tasks[0].cancelled()
                    release.set()

                controller = asyncio.create_task(cancel_producer())
                try:
                    await namespace["drain_tail"](
                        authority, live, ref, row, "answer", "completed"
                    )
                except asyncio.CancelledError:
                    trace.append("cancel-propagated")
                else:
                    raise AssertionError("native drain cancellation did not propagate")
                await controller
            else:
                await namespace["drain_tail"](
                    authority, live, ref, row, "answer", "completed"
                )
            if mode == "unknown":
                assert not staged and not native and not finishes
                assert row["status"] == "unknown"
                assert "pause:unknown_execution" in trace
                assert row["admission_id"] in authority.unsettled_deliveries
                assert not waiter.done()
                assert not ac._authority_current(offer, started=False)
            else:
                assert len(staged) == 1 and not native and len(finishes) == 1, (
                    trace,
                    native,
                    finishes,
                )
                assert waiter.result() == "answer"
                assert staged[0]["payload"]["data"]["answer"] == "answer"
                await ingress.deliver_settled(authority, row["admission_id"])
                assert len(staged) == 1 and len(finishes) == 1
                assert (
                    trace.index("commit")
                    < trace.index("card-ack")
                    < trace.index("release-media")
                )
            await asyncio.sleep(0)
            assert not getattr(authority, "_mutation_tasks", set())
            return {
                "mode": mode,
                "trace": trace,
                "cards": len(staged),
                "native_sends": len(native),
                "hfc_finish": len(finishes),
                "new_task": settlement_tasks[0] is not producer,
                "waiter_done": waiter.done(),
                "native_shield_tail": "exact AST",
            }
    finally:
        connection.close()


@pytest.mark.parametrize("mode", ["normal", "cancel", "unknown"])
def test_v0230_settlement_new_task_hfc_handoff(mode, monkeypatch):
    root = _pinned_root()
    h.reset_runtime_state()

    async def run_case():
        return await asyncio.create_task(_settlement_case(root, mode, monkeypatch))

    try:
        result = asyncio.run(run_case())
    finally:
        h.reset_runtime_state()
    assert result["new_task"] is True
    assert result["native_shield_tail"] == "exact AST"
    if mode == "unknown":
        assert result["cards"] == 0
        assert result["native_sends"] == 0
        assert result["hfc_finish"] == 0
        assert result["waiter_done"] is False
    else:
        assert result["cards"] == 1
        assert result["native_sends"] == 0
        assert result["hfc_finish"] == 1
        assert result["waiter_done"] is True
