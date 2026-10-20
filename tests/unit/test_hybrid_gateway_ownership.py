from __future__ import annotations

import ast
import asyncio
import copy
from functools import lru_cache
import inspect
import logging
import os
from pathlib import Path
import queue
import subprocess
import time
from types import ModuleType, SimpleNamespace
import sys

import pytest

from hermes_feishu_card import hermes_plugin_runtime, hook_runtime
from hermes_feishu_card.install import patcher
from hermes_feishu_card.install import hybrid_renderers

FIXED_SHA = "3c27eb6234bf91b8ceee9e9071591b31e9b148cb"


@pytest.fixture(scope="module")
def official_gateway_source():
    root = Path(os.environ["HFC_FIXED_TAG_SOURCE_ROOT"])
    # Worktree may be officially installed; immutable Git blobs remain exact.
    assert (
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        == FIXED_SHA
    )
    return subprocess.check_output(
        ["git", "show", f"{FIXED_SHA}:gateway/run.py"], cwd=root
    )


@lru_cache(maxsize=2)
def _render(source):
    descriptor = next(
        d for d in patcher.HYBRID_PATCH_DESCRIPTORS if d.group == "terminal_disposition"
    )
    return hybrid_renderers._render_terminal_gateway(source, descriptor)


@lru_cache(maxsize=8)
def _method(source, name):
    tree = ast.parse(source)
    owner = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TurnRunner"
    )
    return next(
        n
        for n in owner.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )


def _terminal_record(outcome):
    return hook_runtime.HybridTerminalRecord(
        terminal_kind="failed" if outcome == "failed" else "completed",
        payload={
            "event": "message.failed" if outcome == "failed" else "message.completed",
            "turn_id": "synthetic-turn",
        },
        response={"ok": True, "applied": True}
        if outcome in {"card", "failed"}
        else None,
    )


def _outer_pipeline(source, result, *, footer=""):
    """Execute actual response/normalization/footer/consumer/return AST in source order.

    Unrelated Gateway scheduling and persistence are excluded. Sanitizer's
    dependencies use benign synthetic strings; its actual branching is executed.
    """
    tree = ast.parse(source)
    gateway = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "GatewayRunner"
    )
    method = next(
        n
        for n in gateway.body
        if isinstance(n, ast.AsyncFunctionDef)
        and n.name == "_handle_message_with_agent"
    )
    assignment = next(
        n
        for n in ast.walk(method)
        if isinstance(n, ast.Assign)
        and "agent_result.get('final_response')" in ast.unparse(n)
        and any(isinstance(t, ast.Name) and t.id == "response" for t in n.targets)
    )
    normalization = next(
        n
        for n in ast.walk(method)
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.UnaryOp)
        and isinstance(n.test.operand, ast.Name)
        and n.test.operand.id == "_intentional_silence"
        and any(
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == "_normalize_empty_agent_response"
            for c in ast.walk(n)
        )
    )
    consumer = next(
        n
        for n in ast.walk(method)
        if isinstance(n, ast.Try)
        and any(
            isinstance(c, ast.ImportFrom)
            and any(a.name == "apply_hybrid_terminal_record" for a in c.names)
            for c in n.body
        )
    )
    footer_if = next(
        n
        for n in ast.walk(method)
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.BoolOp)
        and isinstance(n.test.values[0], ast.Name)
        and n.test.values[0].id == "_footer_line"
    )
    final_return = next(
        n
        for n in ast.walk(method)
        if isinstance(n, ast.Return)
        and n.col_offset == 12
        and isinstance(n.value, ast.Name)
        and n.value.id == "response"
    )
    functions = [
        copy.deepcopy(n)
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name
        in {
            "_normalize_empty_agent_response",
            "_is_gateway_hidden_reasoning_incomplete_turn",
            "_sanitize_gateway_final_response",
        }
    ]
    for function in functions:
        function.returns = None
        for argument in (*function.args.args, *function.args.kwonlyargs):
            argument.annotation = None
    env = {
        "_gateway_surface_passes_raw_text": lambda platform: False,
        "INTERRUPT_WAITING_FOR_MODEL_PREFIX": "SYNTHETIC_INTERRUPT_SENTINEL",
        "_redact_gateway_user_facing_secrets": lambda text: text,
        "_looks_like_gateway_provider_error": lambda text: False,
        "agent_result": result,
        "_intentional_silence": False,
        "history": [],
        "source": SimpleNamespace(platform="feishu"),
        "_footer_line": footer,
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[])),
            "<official-normalization>",
            "exec",
        ),
        env,
    )
    nodes = sorted(
        [assignment, normalization, footer_if, consumer, final_return],
        key=lambda n: n.lineno,
    )
    wrapper = ast.FunctionDef(
        name="outer",
        args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
        ),
        body=copy.deepcopy(nodes),
        decorator_list=[],
    )
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
            "<actual-outer-response-pipeline>",
            "exec",
        ),
        env,
    )
    raw = result.get("final_response") or ""
    baseline = env["_normalize_empty_agent_response"](result, raw, history_len=0)
    if baseline and footer:
        baseline += "\n\n" + footer
    return env["outer"](), baseline


@pytest.mark.parametrize("branch", ["normal", "empty"])
@pytest.mark.parametrize(
    "outcome",
    [
        "card",
        "failed",
        "unknown",
        "changed-media",
        "changed-warning",
        "original-media",
        "outer-footer",
    ],
)
def test_actual_turnrunner_return_keeps_terminal_record_for_outer_consumer(
    official_gateway_source, branch, outcome
):
    source = _render(official_gateway_source)
    method = _method(source, "run_sync")
    returns = [
        n
        for n in ast.walk(method)
        if isinstance(n, ast.Return)
        and isinstance(n.value, ast.Dict)
        and "history_offset"
        in {k.value for k in n.value.keys if isinstance(k, ast.Constant)}
        and any(
            isinstance(k, ast.Constant) and k.value == "final_response"
            for k in n.value.keys
        )
    ]
    returns.sort(key=lambda n: n.lineno)
    assert len(returns) == 2
    projection = returns[0 if branch == "empty" else 1]
    record = _terminal_record(
        "card"
        if outcome
        in {"changed-media", "changed-warning", "original-media", "outer-footer"}
        else outcome
    )
    original_text = "NATIVE_FINAL"
    if outcome == "original-media":
        original_text += " MEDIA:/synthetic/file"
    projected_text = original_text
    if outcome == "changed-media":
        projected_text += " MEDIA:/synthetic/file"
    elif outcome == "changed-warning":
        projected_text += " Synthetic framework warning"
    original = {
        "final_response": original_text,
        "messages": [],
        "completed": outcome == "card",
        "failed": outcome == "failed",
        "_hfc_terminal_record": record,
    }
    values = {
        "ctx": SimpleNamespace(result_holder=[original], tools_holder=[[]]),
        "result": original,
        "final_response": projected_text,
        "_effective_history_offset": 0,
        "_compacted_in_place": False,
        "effective_session_id": "synthetic-session",
        "_last_prompt_toks": 0,
        "_input_toks": 0,
        "_output_toks": 0,
        "_resolved_model": "synthetic",
        "_context_length": 0,
    }
    wrapper = ast.FunctionDef(
        name="project",
        args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
        ),
        body=[copy.deepcopy(projection)],
        decorator_list=[],
    )
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
            "<actual-TurnRunner-return>",
            "exec",
        ),
        values,
    )
    returned = values["project"]()
    baseline_returns = [
        n
        for n in ast.walk(_method(official_gateway_source, "run_sync"))
        if isinstance(n, ast.Return)
        and isinstance(n.value, ast.Dict)
        and "history_offset"
        in {k.value for k in n.value.keys if isinstance(k, ast.Constant)}
        and any(
            isinstance(k, ast.Constant) and k.value == "final_response"
            for k in n.value.keys
        )
    ]
    baseline_returns.sort(key=lambda n: n.lineno)
    original_wrapper = copy.deepcopy(wrapper)
    original_wrapper.body = [
        copy.deepcopy(baseline_returns[0 if branch == "empty" else 1])
    ]
    original_values = dict(values)
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[original_wrapper], type_ignores=[])
            ),
            "<actual-baseline-return>",
            "exec",
        ),
        original_values,
    )
    assert {
        k: v for k, v in returned.items() if k != "_hfc_terminal_record"
    } == original_values["project"]()
    assert returned.get("_hfc_terminal_record") is (
        record if projected_text == original_text else None
    )
    output, baseline = _outer_pipeline(
        source, returned, footer="SYNTHETIC_FOOTER" if outcome == "outer-footer" else ""
    )
    assert output == ("" if outcome == "card" else baseline)
    assert "_hfc_terminal_record" not in returned
    assert original["_hfc_terminal_record"] is record


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        "CARD_ACTIVE",
        "PENDING_START",
        "NATIVE_BYPASS",
        "missing",
        "transition",
        "wrong-chat",
        "wrong-session",
        "wrong-generation",
        "wrong-thread",
        "wrong-profile",
        "wrong-turn",
        "terminal-cancel",
    ],
)
@pytest.mark.parametrize("stage", ["callback", "sender"])
@pytest.mark.parametrize("platform", ["feishu", "telegram"])
async def test_actual_native_progress_suppresses_only_card_active(
    official_gateway_source, monkeypatch, state, stage, platform
):
    source = _render(official_gateway_source)
    node = copy.deepcopy(
        _method(
            source,
            "progress_callback" if stage == "callback" else "send_progress_messages",
        )
    )
    namespace = {
        "asyncio": asyncio,
        "queue": queue,
        "inspect": inspect,
        "time": time,
        "logger": logging.getLogger(__name__),
    }

    class BasePlatformAdapter:
        async def edit_message(self, **kwargs):
            pass

    namespace["BasePlatformAdapter"] = BasePlatformAdapter
    node.returns = None
    for arg in node.args.args:
        arg.annotation = None
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
            "<actual-progress-method>",
            "exec",
        ),
        namespace,
    )
    runtime = hermes_plugin_runtime.PluginRuntime(
        post=lambda *args: {"ok": True, "applied": True}
    )
    assert runtime.bind_ingress_from_values(
        "default",
        "fallback_default",
        "synthetic-session",
        "gateway-session",
        "generation",
        "synthetic-chat",
        "synthetic-input",
        "synthetic-input",
        "",
    )
    runtime.handle_pre_llm_call(
        session_id="synthetic-session", turn_id="synthetic-turn", platform="feishu"
    )
    if state != "missing":
        runtime._turns["synthetic-turn"]._state = getattr(
            hermes_plugin_runtime.TurnState,
            "PENDING_START"
            if state == "transition"
            else "CARD_ACTIVE"
            if state.startswith("wrong-") or state == "terminal-cancel"
            else state,
        )
    monkeypatch.setattr(
        hook_runtime, "_plugin_runtime", lambda: None if state == "missing" else runtime
    )
    display = ModuleType("agent.display")
    display.get_tool_emoji = lambda *a, **kw: "tool"
    agent_module = ModuleType("agent")
    agent_module.__path__ = []
    monkeypatch.setitem(sys.modules, "agent", agent_module)
    monkeypatch.setitem(sys.modules, "agent.display", display)
    sent = []
    delivered = asyncio.Event()

    class Adapter:
        async def edit_message(self, **kwargs):
            return SimpleNamespace(success=True, message_id="native-progress")

        async def send(self, **kwargs):
            sent.append(kwargs)
            delivered.set()
            return SimpleNamespace(success=True, message_id="native-progress")

        async def send_typing(self, *args, **kwargs):
            pass

    adapter = Adapter()
    context = SimpleNamespace(
        source=SimpleNamespace(
            platform=SimpleNamespace(value=platform),
            chat_id="synthetic-chat",
            thread_id="",
            profile="default",
            profile_id="default",
        ),
        agent_holder=[
            SimpleNamespace(_current_turn_id="synthetic-turn", is_interrupted=False)
        ],
        _live_status_adapter=None,
        _live_status_mode="off",
        log_queue=None,
        progress_queue=queue.Queue(),
        _run_still_current=lambda: True,
        tool_progress_enabled=True,
        progress_mode="all",
        last_tool=[None],
        last_was_terminal_block=[False],
        last_progress_msg=[None],
        repeat_count=[0],
        progress_grouping="separate",
        _progress_metadata={},
        _progress_reply_to=None,
        _cleanup_progress=False,
        _cleanup_msg_ids=[],
        interrupted=False,
        session_key="gateway-session",
        run_generation="generation",
    )
    if state == "wrong-chat":
        context.source.chat_id = "another-chat"
    elif state == "wrong-session":
        context.session_key = "another-session"
    elif state == "wrong-generation":
        context.run_generation = "another-generation"
    elif state == "wrong-thread":
        context.source.thread_id = "another-thread"
    elif state == "wrong-profile":
        context.source.profile = context.source.profile_id = "another-profile"
    elif state == "wrong-turn":
        context.agent_holder[0]._current_turn_id = "another-turn"
    runner = SimpleNamespace(
        _ctx=context,
        _runner=SimpleNamespace(_adapter_for_source=lambda source: adapter),
    )
    task = None
    try:
        if stage == "callback":
            namespace["progress_callback"](runner, "tool.started", "synthetic-tool")
            assert context.progress_queue.empty() is (
                state in {"CARD_ACTIVE", "terminal-cancel"} and platform == "feishu"
            )
            if state == "transition":
                assert not context.progress_queue.empty()
                context.progress_queue.get_nowait()
                runtime._turns[
                    "synthetic-turn"
                ]._state = hermes_plugin_runtime.TurnState.CARD_ACTIVE
                namespace["progress_callback"](
                    runner, "tool.started", "synthetic-tool-next"
                )
                assert context.progress_queue.empty() is (platform == "feishu")
        else:
            context.progress_queue.put("PREQUEUED_NATIVE_PROGRESS")
            task = asyncio.create_task(namespace["send_progress_messages"](runner))
            await asyncio.sleep(0.05)
            assert bool(sent) is not (
                state in {"CARD_ACTIVE", "terminal-cancel"} and platform == "feishu"
            )
            if state == "transition":
                assert len(sent) == 1
                runtime._turns[
                    "synthetic-turn"
                ]._state = hermes_plugin_runtime.TurnState.CARD_ACTIVE
                await asyncio.sleep(1.6)  # Respect original native sender throttle.
                delivered.clear()
                context.progress_queue.put("NATIVE_AFTER_CARD_ACTIVE")
                if platform == "telegram":
                    await asyncio.wait_for(delivered.wait(), 2)
                else:
                    deadline = asyncio.get_running_loop().time() + 2
                    while (
                        not context.progress_queue.empty()
                        and asyncio.get_running_loop().time() < deadline
                    ):
                        await asyncio.sleep(0.01)
                    assert context.progress_queue.empty()
                    await asyncio.sleep(0)
                    assert not delivered.is_set()
                assert len(sent) == (1 if platform == "feishu" else 2)
            if state == "terminal-cancel":
                runtime._turns["synthetic-turn"].finish()
                context.progress_queue.put("QUEUED_AFTER_TERMINAL")
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                assert bool(sent) is (platform != "feishu")
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        runtime.close()


@pytest.mark.parametrize("historical_revision", ["504", "cd86", "738"])
def test_exact_historical_504_install_remains_inspectable_and_restorable(
    official_gateway_source, tmp_path, monkeypatch, historical_revision
):
    from dataclasses import replace
    import hashlib
    import json
    from hermes_feishu_card.install import v3
    from hermes_feishu_card.install.patch_descriptors import PatchDescriptorRegistry
    from tests.unit import test_install_v3_transaction as fixtures

    checkout = Path(os.environ["HFC_FIXED_TAG_SOURCE_ROOT"])
    immutable = tmp_path / "immutable-fixed-targets"
    for target in v3.HYBRID_PATCH_TARGET_ORDER:
        path = immutable / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            subprocess.check_output(
                ["git", "show", f"{FIXED_SHA}:{target}"], cwd=checkout
            )
        )
    assert (immutable / "gateway/run.py").read_bytes() == official_gateway_source
    monkeypatch.setattr(fixtures, "FIXED_SOURCE_ROOT", immutable)
    root, binding, entrypoint, decision = fixtures._fixture(tmp_path)
    originals = {
        target: (root / target).read_bytes() for target in v3.HYBRID_PATCH_TARGET_ORDER
    }
    original_config = binding.config_path.read_bytes()
    # Execute the immutable historical renderer, not a permissive new remover.
    fixture_dir = Path(__file__).resolve().parents[1] / "fixtures"
    old_source = (fixture_dir / f"hybrid_renderers_{historical_revision}.py.txt").read_bytes()
    evidence = json.loads(
        (fixture_dir / f"hybrid_renderers_{historical_revision}.provenance.json").read_text()
    )
    assert hashlib.sha256(old_source).hexdigest() == evidence["sha256"]
    assert evidence["commit"] == {"504":"50499df9dbf164d00261a05e19359ec72d986153","cd86":"cd86b7fdc081f4471bb9538ae5c6554dd85147c4", "738":"7380823467d73dadd09e8405bfbdbd56fc66d309"}[historical_revision]
    old_module = ModuleType(f"hermes_feishu_card.install._historical_{historical_revision}")
    old_module.__package__ = "hermes_feishu_card.install"
    exec(compile(old_source, f"<exact-{historical_revision}-renderer>", "exec"), old_module.__dict__)
    current = next(
        d for d in patcher.HYBRID_PATCH_DESCRIPTORS if d.group == "terminal_disposition"
    )
    old_base = tuple(
        replace(d, fragments=d.fragments[:1]) if d.group == current.group and historical_revision == "504" else d
        for d in patcher.HYBRID_PATCH_DESCRIPTORS
    )
    old_descriptors = old_module.reviewed_descriptors(old_base)
    historical = next(d for d in old_descriptors if d.group == current.group)
    old_registry = PatchDescriptorRegistry(
        descriptors=old_descriptors,
        required_groups=patcher.HYBRID_PATCH_REGISTRY.required_groups,
    )
    monkeypatch.setattr(
        fixtures.plugin, "_run_official_enable", fixtures._official_enable
    )
    from types import SimpleNamespace
    import yaml
    monkeypatch.setattr(fixtures.plugin, "_plan_enabled_config_bytes",
        lambda b, p, raw, desired, enabled: SimpleNamespace(
            postimage=yaml.safe_dump(desired).encode(), verify_source=lambda: None))
    with monkeypatch.context() as initial:
        initial.setattr(v3, "HYBRID_PATCH_REGISTRY", old_registry)
        initial.setattr(patcher, "HYBRID_PATCH_REGISTRY", old_registry)
        result = v3.execute_fixed_tag_hybrid_install(
            binding=binding,
            entrypoint=entrypoint,
            decision=decision,
            source_commit=v3.FIXED_TAG_COMMIT,
            plugin_evidence_sha256="sha256:" + "a" * 64,
            package_version="4.3.0",
        )
        assert result.status == "installed"
    manifest = json.loads((root / ".hermes_feishu_card_manifest").read_bytes())
    installed = {target: (root / target).read_bytes() for target in originals}
    for target, content in installed.items():
        assert (
            hashlib.sha256(content).hexdigest()
            == manifest["targets"][target]["patched_sha256"]
        )
        assert (root / (target + ".hermes_feishu_card.bak")).read_bytes() == originals[
            target
        ]
    assert len(historical.fragments) == {"504": 1, "cd86": 7, "738": 7}[historical_revision]
    with pytest.raises(
        v3.FixedTagInstallRefused,
        match="verified historical Hybrid revision requires official restore",
    ):
        v3.inspect_fixed_tag_hybrid_install(
            binding=binding, entrypoint=entrypoint, package_version="4.3.0"
        )
    assert all(
        (root / target).read_bytes() == content
        for target, content in installed.items()
    )
    assert v3.restore_fixed_tag_hybrid_install(binding=binding).status == "restored"
    assert all(
        (root / target).read_bytes() == content for target, content in originals.items()
    )
    assert binding.config_path.read_bytes() == original_config
    assert not (root / ".hermes_feishu_card_manifest").exists()
    fresh = v3.execute_fixed_tag_hybrid_install(
        binding=binding,
        entrypoint=entrypoint,
        decision=decision,
        source_commit=v3.FIXED_TAG_COMMIT,
        plugin_evidence_sha256="sha256:" + "a" * 64,
        package_version="4.3.0",
    )
    assert fresh.status == "installed"
    for fragment in current.fragments:
        assert (root / "gateway/run.py").read_bytes().count(fragment.begin_marker) == 1
    assert len(current.fragments) == 7
    assert (
        v3.inspect_fixed_tag_hybrid_install(
            binding=binding, entrypoint=entrypoint, package_version="4.3.0"
        ).status
        == "installed"
    )


@pytest.mark.parametrize("text", ["NATIVE_FINAL", "NATIVE_FINAL MEDIA:/synthetic/file"])
def test_actual_normalizer_then_consumer_never_loses_text_or_adds_warning(
    official_gateway_source, text
):
    result = {
        "final_response": text,
        "api_calls": 2,
        "completed": True,
        "_hfc_terminal_record": _terminal_record("card"),
    }
    output, _baseline = _outer_pipeline(_render(official_gateway_source), result)
    assert output == (text if "MEDIA:" in text else "")
    assert "⚠" not in output


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "chat_id",
        "gateway_session_key",
        "generation",
        "thread_id",
        "profile_id",
        "turn_id",
        "owner",
        "runtime",
        "closed",
        "forged",
    ],
)
def test_progress_receipt_cannot_cross_route_owner_runtime_or_closed_lifetime(
    monkeypatch, change
):
    runtime = hermes_plugin_runtime.PluginRuntime(
        post=lambda *args: {"ok": True, "applied": True}
    )
    assert runtime.bind_ingress_from_values(
        "default",
        "fallback_default",
        "synthetic-session",
        "gateway-session",
        "generation",
        "synthetic-chat",
        "synthetic-input",
        "synthetic-input",
        "",
    )
    runtime.handle_pre_llm_call(
        session_id="synthetic-session", turn_id="synthetic-turn", platform="feishu"
    )
    route = {
        "_hfc_authorized": True,
        "platform": "feishu",
        "turn_id": "synthetic-turn",
        "chat_id": "synthetic-chat",
        "gateway_session_key": "gateway-session",
        "generation": "generation",
        "thread_id": "",
        "profile_id": "default",
    }
    owner = SimpleNamespace()
    monkeypatch.setattr(hook_runtime, "_plugin_runtime", lambda: runtime)
    replacement = None
    try:
        assert hook_runtime.suppress_hybrid_native_progress(route, owner=owner) is True
        runtime._turns["synthetic-turn"].finish()
        if change in route:
            route[change] = "another-value"
        elif change == "owner":
            owner = SimpleNamespace(
                _hfc_progress_ownership=owner._hfc_progress_ownership
            )
        elif change == "runtime":
            replacement = hermes_plugin_runtime.PluginRuntime(
                post=lambda *args: {"ok": True, "applied": True}
            )
            monkeypatch.setattr(hook_runtime, "_plugin_runtime", lambda: replacement)
        elif change == "closed":
            runtime.close()
        elif change == "forged":
            owner._hfc_progress_ownership = {
                "owner": owner,
                "runtime": runtime,
                "route": route,
            }
        assert hook_runtime.suppress_hybrid_native_progress(route, owner=owner) is (
            change == "none"
        )
    finally:
        runtime.close()
        if replacement is not None:
            replacement.close()


@pytest.mark.parametrize(
    "outcome", ["card", "failed", "unknown", "media", "dropped", "streamed"]
)
def test_actual_queued_final_send_uses_receipt_and_preserves_callback(
    official_gateway_source, monkeypatch, outcome
):
    # The clean queued branch is the exact pre-seventh-fragment baseline.
    source_bytes = (
        official_gateway_source
        if os.environ.get("HFC_QUEUED_RED")
        else _render(official_gateway_source)
    )
    tree = ast.parse(source_bytes)
    gateway = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "GatewayRunner"
    )
    method = next(
        n
        for n in gateway.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_agent_inner"
    )
    confirmed = next(
        n
        for n in ast.walk(method)
        if isinstance(n, ast.FunctionDef)
        and n.name == "_stream_confirmed_final_delivery"
    )
    confirmed = copy.deepcopy(confirmed)
    confirmed.returns = None
    for arg in confirmed.args.args:
        arg.annotation = None
    body = next(
        value
        for node in ast.walk(method)
        for value in vars(node).values()
        if isinstance(value, list)
        and any(
            isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "_delivery_result"
                for t in n.targets
            )
            for n in value
        )
    )
    start = next(
        i
        for i, n in enumerate(body)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "_delivery_result" for t in n.targets
        )
    )
    end = next(
        i
        for i, n in enumerate(body[start:], start)
        if isinstance(n, ast.If) and "pop_post_delivery_callback" in ast.unparse(n)
    )
    selected = copy.deepcopy(body[start : end + 1])
    wrapped = ast.AsyncFunctionDef(
        name="queued",
        args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
        ),
        body=selected,
        decorator_list=[],
    )
    calls = []
    callbacks = []

    class Adapter:
        async def send(self, chat, text, *, metadata):
            calls.append((chat, text, metadata))

        def pop_post_delivery_callback(self, key, *, generation):
            assert key == "synthetic-session" and generation == 7
            return (lambda: callbacks.append("called")) if not callbacks else None

    filters = ModuleType("gateway.response_filters")
    filters.is_intentional_silence_agent_result = lambda result, text: False
    monkeypatch.setitem(sys.modules, "gateway.response_filters", filters)
    text = "SYNTHETIC_FINAL" + (" MEDIA:/synthetic/file" if outcome == "media" else "")
    receipt = _terminal_record(outcome if outcome in {"failed", "unknown"} else "card")
    finalized = {"final_response": text, "_hfc_terminal_record": receipt}
    if outcome == "dropped":
        finalized.pop("_hfc_terminal_record")
    raw = {
        "final_response": "RAW_MUST_NOT_SEND",
        "_hfc_terminal_record": _terminal_record("card"),
    }
    before = finalized.copy()
    values = dict(
        response=finalized,
        result=raw,
        _sc=SimpleNamespace(final_response_sent=True)
        if outcome == "streamed"
        else None,
        logger=logging.getLogger(__name__),
        session_key="synthetic-session",
        run_generation=7,
        source=SimpleNamespace(chat_id="synthetic-chat"),
        adapter=Adapter(),
        _status_thread_metadata={"thread_id": "synthetic-thread"},
        inspect=inspect,
    )
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[confirmed, wrapped], type_ignores=[])
            ),
            "<actual-queued-Gateway-branch>",
            "exec",
        ),
        values,
    )
    asyncio.run(values["queued"]())
    assert calls == (
        []
        if outcome in {"card", "streamed"}
        else [("synthetic-chat", text, {"thread_id": "synthetic-thread"})]
    )
    assert callbacks == ["called"]
    assert {k: v for k, v in finalized.items() if k != "_hfc_terminal_record"} == {
        k: v for k, v in before.items() if k != "_hfc_terminal_record"
    }
    if not os.environ.get("HFC_QUEUED_RED"):
        assert "_hfc_terminal_record" not in finalized
    assert raw["final_response"] == "RAW_MUST_NOT_SEND"
    # Execute only the original post-delivery pop-and-call a second time.
    wrapped.body = [copy.deepcopy(body[end])]
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[wrapped], type_ignores=[])),
            "<actual-queued-callback>",
            "exec",
        ),
        values,
    )
    asyncio.run(values["queued"]())
    assert callbacks == ["called"]
