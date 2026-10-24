"""Pinned contracts for the 0.22.5 completion wrapper and callback ordering.

The upstream source checkouts are supplied by CI. Local runs can opt in with
``HFC_UPSTREAM_ISSUE456_0225_ROOT`` and ``HFC_UPSTREAM_LATEST_STABLE_ROOT``.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.install import decomposed, decomposed_ownership, patcher
from hermes_feishu_card.install.detect import detect_hermes


ISSUE456_ENV = "HFC_UPSTREAM_ISSUE456_0225_ROOT"
LATEST_STABLE_ENV = "HFC_UPSTREAM_LATEST_STABLE_ROOT"
BASELINE_BY_ENV = {
    ISSUE456_ENV: "issue456_0225",
    LATEST_STABLE_ENV: "latest_stable",
}
UPSTREAM_SOURCES = json.loads(
    (Path(__file__).parents[1] / "fixtures/hermes_upstream_sources.json").read_text()
)


def _pinned_sources(env_name: str) -> dict[str, bytes]:
    configured = os.environ.get(env_name)
    if not configured:
        pytest.skip(f"exact upstream source checkout not supplied: {env_name}")
    root = Path(configured)
    baseline = BASELINE_BY_ENV[env_name]
    expected = UPSTREAM_SOURCES[baseline]
    assert root.is_dir(), f"configured fixture does not exist: {root}"
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        status = subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain"], text=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.fail(f"fixture is not a readable Git checkout: {exc}")
    assert commit == expected["commit"], (baseline, commit, expected["commit"])
    assert status == "", f"fixture checkout is dirty: {baseline}"
    for name, digest in expected["sha256"].items():
        path = root / name
        assert path.is_file(), f"missing pinned source: {baseline} {name}"
        assert sha256(path.read_bytes()).hexdigest() == digest, (
            f"source digest changed: {baseline} {name}"
        )
    return {name: (root / name).read_bytes() for name in decomposed.SOURCE_TARGETS}


def _replace_once(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new, 1)


def test_pinned_sources_fails_instead_of_skipping_a_configured_missing_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv(ISSUE456_ENV, str(tmp_path / "missing-fixture"))

    with pytest.raises(AssertionError, match="does not exist"):
        _pinned_sources(ISSUE456_ENV)


def test_issue456_0225_completion_wrapper_renders_and_restores():
    sources = _pinned_sources(ISSUE456_ENV)

    rendered = decomposed_ownership.render(sources)

    assert patcher.COMPLETE_PATCH_BEGIN.encode() in rendered["gateway/run_turn_persistence.py"]
    for name, original in sources.items():
        assert decomposed_ownership._remove_target(name, rendered[name]) == original


def test_issue456_0225_install_is_idempotent_and_restores_exact_bytes(tmp_path):
    sources = _pinned_sources(ISSUE456_ENV)
    root = tmp_path / "hermes"
    root.mkdir()
    for name, raw in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (root / "VERSION").write_text("0.22.5\n")

    detection = detect_hermes(root)
    assert detection.supported, detection.reason
    assert decomposed.install(detection)
    installed = {name: (root / name).read_bytes() for name in sources}
    assert installed != sources
    assert not decomposed.install(detect_hermes(root))
    assert installed == {name: (root / name).read_bytes() for name in sources}
    decomposed.restore(detect_hermes(root))
    assert sources == {name: (root / name).read_bytes() for name in sources}


@pytest.mark.parametrize(
    "old,new",
    [
        (
            "delivered = await self._hmwa_deliver_turn_response(",
            "delivered = await self._hmwa_deliver_turn_response_wrong(",
        ),
        (
            "            event, source, session_entry, session_key, run_generation,\n"
            "            agent_result, agent_messages, response, footer_line, intentional_silence,",
            "            event, source, session_entry, session_key, run_generation,\n"
            "            agent_result, agent_messages, response, intentional_silence, footer_line,",
        ),
        (
            "self._schedule_usage_limit_offer(event, agent_result[\"usage_limit\"], session_key=session_key,",
            "self._schedule_usage_limit_offer(event, agent_result[\"usage_limit\"], session_key=run_generation,",
        ),
        ("return delivered", "return response"),
        (
            "        agent_result, agent_messages, response, footer_line, intentional_silence,\n"
            "    ):\n",
            "        agent_result, agent_messages, response, footer_line, intentional_silence=None,\n"
            "    ):\n",
        ),
    ],
)
def test_issue456_0225_completion_wrapper_drift_is_rejected(old, new):
    sources = _pinned_sources(ISSUE456_ENV)
    persistence = sources["gateway/run_turn_persistence.py"].decode()
    sources["gateway/run_turn_persistence.py"] = _replace_once(persistence, old, new).encode()

    with pytest.raises(ValueError, match="inherited completion"):
        patcher._split_completion_node(
            {name: raw.decode() for name, raw in sources.items()},
            "gateway/run_turn.py",
        )


def test_issue456_0225_handler_return_drift_is_rejected():
    sources = _pinned_sources(ISSUE456_ENV)
    handler = sources["gateway/run_turn.py"].decode()
    sources["gateway/run_turn.py"] = _replace_once(
        handler,
        "return await self._deliver_turn_response_then_limit_offer(",
        "return await self._hmwa_deliver_turn_response(",
    ).encode()

    with pytest.raises(ValueError, match="inherited completion"):
        patcher._split_completion_node(
            {name: raw.decode() for name, raw in sources.items()},
            "gateway/run_turn.py",
        )


def test_issue456_0225_agent_end_drift_is_rejected():
    sources = _pinned_sources(ISSUE456_ENV)
    handler = sources["gateway/run_turn.py"].decode()
    sources["gateway/run_turn.py"] = _replace_once(
        handler,
        "            await self._hmwa_post_turn_hooks(hook_ctx, agent_result, response)\n",
        "",
    ).encode()

    with pytest.raises(ValueError, match="inherited completion"):
        patcher._split_completion_node(
            {name: raw.decode() for name, raw in sources.items()},
            "gateway/run_turn.py",
        )


def test_issue456_0225_owner_mro_drift_is_rejected():
    sources = _pinned_sources(ISSUE456_ENV)
    handler = sources["gateway/run_turn.py"].decode()
    sources["gateway/run_turn.py"] = _replace_once(
        handler,
        "class GatewayTurnMixin(GatewayTurnPrepareMixin, GatewayTurnHygieneMixin, "
        "GatewayTurnPersistenceMixin):",
        "class GatewayTurnMixin(GatewayTurnPrepareMixin, GatewayTurnHygieneMixin):",
    ).encode()

    with pytest.raises(ValueError, match="inherited completion"):
        patcher._split_completion_node(
            {name: raw.decode() for name, raw in sources.items()},
            "gateway/run_turn.py",
        )


def test_issue456_0225_wrapper_calls_original_delivery_once_before_limit_offer():
    sources = _pinned_sources(ISSUE456_ENV)
    tree = ast.parse(sources["gateway/run_turn_persistence.py"])
    peer = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                and node.name == "GatewayTurnPersistenceMixin")
    wrapper = next(node for node in peer.body if isinstance(node, ast.AsyncFunctionDef)
                   and node.name == "_deliver_turn_response_then_limit_offer")
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), wrapper],
        type_ignores=[],
    )
    namespace: dict[str, object] = {}
    exec(compile(ast.fix_missing_locations(module), "pinned-run-turn-persistence", "exec"), namespace)

    calls: list[object] = []
    async def deliver(*args):
        calls.append(("deliver", args))
        return "delivered"

    def schedule(*args, **kwargs):
        calls.append(("schedule", args, kwargs))

    runner = SimpleNamespace(
        _hmwa_deliver_turn_response=deliver,
        _schedule_usage_limit_offer=schedule,
    )
    event, source, entry = object(), object(), object()
    result = asyncio.run(namespace["_deliver_turn_response_then_limit_offer"](
        runner, event, source, entry, "session", 7,
        {"usage_limit": {"kind": "window"}}, [], "answer", "footer", False,
    ))

    assert result == "delivered"
    assert [call[0] for call in calls] == ["deliver", "schedule"]
    assert calls[0][1] == (event, source, entry, "session", 7, {"usage_limit": {"kind": "window"}},
                           [], "answer", "footer", False)
    assert calls[1][1] == (event, {"kind": "window"})
    assert calls[1][2] == {"session_key": "session", "generation": 7}


def _callback_fixture() -> str:
    return (
        "class TurnRunner:\n"
        "    def _wire_turn_agent_callbacks(self, agent, turn_route, reasoning_config,\n"
        "                                   stream_delta_cb, interim_assistant_cb, want_interim_messages):\n"
        "        ctx = self._ctx\n"
        "        agent.tool_progress_callback = ctx.progress_callback\n"
        "        agent.tool_start_callback = ctx.native_tool_start_callback\n"
        "        agent.tool_complete_callback = ctx.native_tool_complete_callback\n"
        "        if self._approval_owner is not None:\n"
        "            agent.tool_start_callback = self.combined_tool_start_callback\n"
        "            agent.tool_complete_callback = self.combined_tool_complete_callback\n"
        "        agent.reasoning_callback = None\n"
        "        if ctx.mute_notification_reply:\n"
        "            agent.tool_progress_callback = None\n"
        "            agent.tool_start_callback = None\n"
        "            agent.tool_complete_callback = None\n"
        "        return agent\n"
    )


@pytest.mark.asyncio
async def test_issue453_callbacks_bind_after_final_turn_configuration(monkeypatch):
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        hook_runtime,
        "emit_from_hermes_locals_threadsafe",
        lambda data, *, event_name: events.append((event_name, data)) or True,
    )
    namespace: dict[str, object] = {}
    exec(patcher._apply_stable_tool_lifecycle_patch(_callback_fixture()), namespace)

    original_start = []
    original_complete = []
    runner = namespace["TurnRunner"]()
    runner._approval_owner = object()
    runner.combined_tool_start_callback = lambda *args: original_start.append(args)
    runner.combined_tool_complete_callback = lambda *args: original_complete.append(args)
    ctx = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="chat_fixture"),
        event_message_id="message_fixture",
        _loop_for_step=asyncio.get_running_loop(),
        _run_still_current=lambda: True,
        progress_callback=None,
        native_tool_start_callback=None,
        native_tool_complete_callback=None,
        mute_notification_reply=False,
    )
    runner._ctx = ctx
    agent = SimpleNamespace(reasoning_callback=None)

    runner._wire_turn_agent_callbacks(agent, None, None, None, None, False)
    assert agent.tool_start_callback is not None
    assert agent.tool_complete_callback is not None
    assert agent.reasoning_callback is not None
    assert agent.reasoning_callback is not None
    agent.tool_start_callback("call-1", "terminal", {"command": "true"})
    agent.tool_complete_callback("call-1", "terminal", {}, {"exit_code": 0})
    agent.reasoning_callback("reasoning one")

    assert original_start == [("call-1", "terminal", {"command": "true"})]
    assert original_complete == [("call-1", "terminal", {}, {"exit_code": 0})]
    assert [name for name, _ in events] == ["tool.updated", "tool.updated", "thinking.delta"]

    # A cached agent receives a fresh callback set on the next turn; the old
    # reasoning sink must not survive the final `reasoning_callback = None`.
    new_starts = []
    runner._approval_owner = object()
    runner.combined_tool_start_callback = lambda *args: new_starts.append(args)
    runner.combined_tool_complete_callback = lambda *args: None
    ctx.event_message_id = "message_fixture_2"
    runner._wire_turn_agent_callbacks(agent, None, None, None, None, False)
    events.clear()
    agent.tool_start_callback("call-2", "terminal", {})
    agent.reasoning_callback("reasoning two")

    assert new_starts == [("call-2", "terminal", {})]
    assert original_start == [("call-1", "terminal", {"command": "true"})]
    assert [name for name, _ in events] == ["tool.updated", "thinking.delta"]


@pytest.mark.asyncio
async def test_issue453_without_approval_keeps_native_callbacks_exactly_once(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(
        hook_runtime,
        "emit_from_hermes_locals_threadsafe",
        lambda data, *, event_name: events.append(event_name) or True,
    )
    namespace: dict[str, object] = {}
    exec(patcher._apply_stable_tool_lifecycle_patch(_callback_fixture()), namespace)

    native_start = []
    native_complete = []
    runner = namespace["TurnRunner"]()
    runner._approval_owner = None
    runner._ctx = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="chat_fixture"),
        event_message_id="message_fixture",
        _loop_for_step=asyncio.get_running_loop(),
        _run_still_current=lambda: True,
        progress_callback=None,
        native_tool_start_callback=lambda *args: native_start.append(args),
        native_tool_complete_callback=lambda *args: native_complete.append(args),
        mute_notification_reply=False,
    )
    agent = SimpleNamespace(reasoning_callback=None)

    runner._wire_turn_agent_callbacks(agent, None, None, None, None, False)
    agent.tool_start_callback("call-1", "terminal", {})
    agent.tool_complete_callback("call-1", "terminal", {}, {})
    agent.reasoning_callback("reasoning")

    assert native_start == [("call-1", "terminal", {})]
    assert native_complete == [("call-1", "terminal", {}, {})]
    assert events == ["tool.updated", "tool.updated", "thinking.delta"]


@pytest.mark.parametrize("env_name", [LATEST_STABLE_ENV, ISSUE456_ENV])
def test_issue453_callback_anchor_follows_reasoning_reset_in_pinned_sources(env_name):
    sources = _pinned_sources(env_name)
    raw = sources["gateway/run_turn_runner.py"].decode()
    patched = patcher.apply_gateway_fragment(raw, "gateway/run_turn_runner.py")
    lines = patched.splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith("    def _wire_turn_agent_callbacks"))
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].startswith("    def ") or lines[i].startswith("    async def "))
    lines = lines[start:end]

    clear = next(i for i, line in enumerate(lines) if "agent.reasoning_callback = None" in line)
    approval = next(i for i, line in enumerate(lines) if "if self._approval_owner is not None:" in line)
    approval_start = next(i for i, line in enumerate(lines)
                          if "agent.tool_start_callback = self.combined_tool_start_callback" in line)
    approval_complete = next(i for i, line in enumerate(lines)
                             if "agent.tool_complete_callback = self.combined_tool_complete_callback" in line)
    begin = next(i for i, line in enumerate(lines) if patcher.STABLE_TOOL_PATCH_BEGIN in line)
    mute = next(i for i, line in enumerate(lines) if "if ctx.mute_notification_reply:" in line)

    assert approval < approval_start < approval_complete < clear < begin < mute
    assert patched.count(patcher.STABLE_TOOL_PATCH_BEGIN) == 1
    assert patched.count("_hfc_bind_reasoning(agent") == 1
    assert patcher.remove_patch(patched) == raw


@pytest.mark.parametrize("replacement", [
    "        if evil(mute_notification_reply):\n"
    "            agent.tool_start_callback = None\n",
    "        if ctx.mute_notification_reply:\n"
    "            agent.tool_start_callback = fallback_callback\n",
    "        if ctx.mute_notification_reply:\n"
    "            if nested_condition:\n"
    "                agent.tool_start_callback = None\n",
])
def test_issue453_unknown_later_callback_branch_fails_closed(replacement):
    content = _callback_fixture()
    original = (
        "        if ctx.mute_notification_reply:\n"
        "            agent.tool_progress_callback = None\n"
        "            agent.tool_start_callback = None\n"
        "            agent.tool_complete_callback = None\n"
    )
    assert original in content
    patched = patcher._apply_stable_tool_lifecycle_patch(content.replace(original, replacement))

    assert patcher.STABLE_TOOL_PATCH_BEGIN not in patched


@pytest.mark.asyncio
async def test_issue453_cli_progress_callback_survives_stable_wrapper(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(
        hook_runtime,
        "emit_from_hermes_locals_threadsafe",
        lambda data, *, event_name: events.append(event_name) or True,
    )
    namespace: dict[str, object] = {}
    exec(patcher._apply_stable_tool_lifecycle_patch(_callback_fixture()), namespace)

    progress: list[tuple[object, ...]] = []
    runner = namespace["TurnRunner"]()
    runner._approval_owner = None
    runner._ctx = SimpleNamespace(
        source=SimpleNamespace(platform="cli", chat_id="cli_fixture"),
        event_message_id="message_fixture",
        _loop_for_step=asyncio.get_running_loop(),
        _run_still_current=lambda: True,
        progress_callback=lambda *args, **kwargs: progress.append((args, kwargs)),
        native_tool_start_callback=None,
        native_tool_complete_callback=None,
        mute_notification_reply=False,
    )
    agent = SimpleNamespace(reasoning_callback=None)

    runner._wire_turn_agent_callbacks(agent, None, None, None, None, False)
    agent.tool_progress_callback("tool.started", "terminal", "preview", {"command": "true"})

    assert progress == [(
        ("tool.started", "terminal", "preview", {"command": "true"}), {}
    )]
    assert events == []


@pytest.mark.asyncio
async def test_issue453_feishu_progress_callback_remains_owned_by_stable_wrapper(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(
        hook_runtime,
        "emit_from_hermes_locals_threadsafe",
        lambda data, *, event_name: events.append(event_name) or True,
    )
    namespace: dict[str, object] = {}
    exec(patcher._apply_stable_tool_lifecycle_patch(_callback_fixture()), namespace)

    progress: list[object] = []
    runner = namespace["TurnRunner"]()
    runner._approval_owner = None
    runner._ctx = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="chat_fixture"),
        event_message_id="message_fixture",
        _loop_for_step=asyncio.get_running_loop(),
        _run_still_current=lambda: True,
        progress_callback=lambda *args, **kwargs: progress.append(args),
        native_tool_start_callback=None,
        native_tool_complete_callback=None,
        mute_notification_reply=False,
    )
    agent = SimpleNamespace(reasoning_callback=None)

    runner._wire_turn_agent_callbacks(agent, None, None, None, None, False)
    agent.tool_progress_callback("tool.started", "terminal", "preview", {"command": "true"})

    assert progress == []
    assert events == []


def test_issue453_v0225_gateway_agent_progress_contract(monkeypatch):
    _pinned_sources(ISSUE456_ENV)
    root = Path(os.environ[ISSUE456_ENV])
    source = (root / "hermes_cli/gateway_agent.py").read_text()
    tree = ast.parse(source)
    gateway_agent = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                         and node.name == "GatewayAgent")
    setter = next(node for node in gateway_agent.body if isinstance(node, ast.FunctionDef)
                  and node.name == "reasoning_callback"
                  and any(isinstance(decorator, ast.Attribute) and decorator.attr == "setter"
                          for decorator in node.decorator_list))
    setter.decorator_list = []
    tool_start = next(node for node in gateway_agent.body if isinstance(node, ast.FunctionDef)
                      and node.name == "_on_tool_start")
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
              setter, tool_start],
        type_ignores=[],
    )
    namespace = {"GatewayClientError": Exception, "time": __import__("time")}
    exec(compile(ast.fix_missing_locations(module), "pinned-gateway-agent", "exec"), namespace)

    class Connection:
        def __init__(self):
            self.calls = []
        def call(self, method, **kwargs):
            self.calls.append((method, kwargs))

    connection = Connection()
    progress: list[tuple[object, ...]] = []
    agent = SimpleNamespace(
        connection=connection,
        session_id="session_fixture",
        _reasoning_callback=None,
        _show_reasoning_sent=None,
        tool_progress_callback=lambda *args, **kwargs: progress.append((args, kwargs)),
        _usage_stale=False,
        _close_segment=lambda starts: None,
    )
    namespace["reasoning_callback"](agent, lambda text: None)
    assert connection.calls == [("config.set", {
        "session_id": "session_fixture", "key": "show_reasoning", "value": "1", "timeout": 10,
    })]

    display = SimpleNamespace(build_tool_preview=lambda name, args: "preview")
    monkeypatch.setitem(sys.modules, "agent.display", display)
    namespace["_on_tool_start"](agent, {"name": "terminal", "args": {"command": "true"},
                                       "tool_id": "call-1"}, None, {})
    assert progress == [(
        ("tool.started", "terminal", "preview", {"command": "true"}), {}
    )]

    run_turn = ast.parse((root / "gateway/run_turn.py").read_text())
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == "run_agent"
        and any(alias.name == "AIAgent" for alias in node.names)
        for node in ast.walk(run_turn)
    )
    assert "GatewayAgent" not in (root / "gateway/run_turn.py").read_text()
