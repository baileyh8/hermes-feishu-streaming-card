from __future__ import annotations

import ast
import copy
import json
import logging
import os
from functools import lru_cache
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hermes_plugin_runtime as runtime_module
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.session import CardSession
from hermes_feishu_card.render import render_card

FIXED_SHA = "3c27eb6234bf91b8ceee9e9071591b31e9b148cb"


@lru_cache(maxsize=4)
def official_call(name):
    root = Path(os.environ["HFC_FIXED_TAG_SOURCE_ROOT"])
    assert (
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        == FIXED_SHA
    )
    path = (
        "agent/turn_context.py"
        if name == "pre_llm_call"
        else "agent/conversation_loop.py"
        if name == "post_api_request"
        else "agent/turn_finalizer.py"
    )
    tree = ast.parse(
        subprocess.check_output(["git", "show", f"{FIXED_SHA}:{path}"], cwd=root)
    )
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_invoke_hook"
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == name
    ]
    assert len(calls) == 1
    return copy.deepcopy(calls[0])


def active_runtime():
    posted = []
    runtime = runtime_module.PluginRuntime(
        post=lambda p, t: (
            posted.append(copy.deepcopy(p)) or {"ok": True, "applied": True}
        )
    )
    assert runtime.bind_ingress_from_values(
        "default",
        "fallback_default",
        "synthetic-session",
        "synthetic-gateway",
        "generation",
        "synthetic-chat",
        "synthetic-input",
        "synthetic-input",
        "",
    )
    return runtime, posted


def invoke_original(runtime, name, agent, **overrides):
    values = dict(
        agent=agent,
        effective_task_id="synthetic-task",
        turn_id="synthetic-turn",
        original_user_message="SYNTHETIC_INPUT",
        messages=[],
        conversation_history=[],
        final_response="SYNTHETIC_ANSWER",
        completed=True,
        failed=False,
        interrupted=False,
        _turn_exit_reason="completed",
        api_request_id="synthetic-api-1",
        api_call_count=1,
        api_duration=1.0,
        api_start_time=100.0,
        _api_ended_at=101.0,
        finish_reason="stop",
        api_messages=[],
        response=SimpleNamespace(model="synthetic-response-model"),
        assistant_message=SimpleNamespace(content="SYNTHETIC_ANSWER", tool_calls=[]),
        _assistant_text="SYNTHETIC_ANSWER",
        _assistant_tool_calls=[],
        logger=logging.getLogger(__name__),
    )
    values.update(overrides)
    values["_invoke_hook"] = lambda hook_name, **kwargs: getattr(
        runtime, "handle_" + hook_name
    )(**kwargs)
    return eval(
        compile(
            ast.Expression(official_call(name)), "<actual-official-hook-call>", "eval"
        ),
        values,
    )


def synthetic_agent():
    return SimpleNamespace(
        session_id="synthetic-session",
        model="synthetic-configured-model",
        provider="synthetic-provider",
        platform="feishu",
        base_url="https://SYNTHETIC_PRIVATE_ENDPOINT.invalid",
        api_mode="chat_completions",
        _api_response_payload_for_hook=lambda *a, **k: {
            "SYNTHETIC_RAW_SECRET": "MUST_NOT_PUBLISH"
        },
        _usage_summary_for_api_request_hook=lambda response: None,
    )


def test_official_model_hook_reaches_terminal_session_and_footer():
    runtime, posted = active_runtime()
    agent = synthetic_agent()
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        invoke_original(runtime, "post_llm_call", agent)
        invoke_original(runtime, "on_session_end", agent)
        payload = next(p for p in reversed(posted) if p["event"] == "message.completed")
        assert payload["data"]["model"] == "synthetic-configured-model"
        event = SidecarEvent.from_dict(payload)
        session = CardSession(
            conversation_id=event.conversation_id,
            message_id=event.message_id,
            chat_id=event.chat_id,
        )
        assert session.apply(event)
        card = render_card(session, presentation="task")
        assert "synthetic-configured-model" in json.dumps(card, ensure_ascii=False)
        assert "SYNTHETIC_PRIVATE_ENDPOINT" not in json.dumps(card)
    finally:
        runtime.close()


def test_official_api_metrics_hook_has_registered_runtime_handler():
    runtime, posted = active_runtime()
    try:
        assert "post_api_request" in runtime_module.OFFICIAL_HOOKS
        assert callable(getattr(runtime, "handle_post_api_request", None))
    finally:
        runtime.close()


def test_official_lifecycle_contract_does_not_invent_usage_or_context():
    for name in ("pre_llm_call", "post_llm_call", "on_session_end"):
        keywords = {k.arg for k in official_call(name).keywords}
        assert "model" in keywords
        assert not keywords & {"tokens", "usage", "context", "duration", "duration_ms"}
    api = {k.arg for k in official_call("post_api_request").keywords}
    assert {
        "turn_id",
        "session_id",
        "api_request_id",
        "model",
        "response_model",
        "usage",
    } <= api
    assert not api & {"context", "context_length", "context_window"}


def finish(runtime, agent, **overrides):
    invoke_original(runtime, "post_llm_call", agent, **overrides)
    invoke_original(runtime, "on_session_end", agent, **overrides)


def terminal_session(posted):
    payload = next(p for p in reversed(posted) if p["event"] == "message.completed")
    event = SidecarEvent.from_dict(payload)
    session = CardSession(
        conversation_id=event.conversation_id,
        message_id=event.message_id,
        chat_id=event.chat_id,
    )
    assert session.apply(event)
    return payload, session


def normalized_usage(
    input_tokens=7, output_tokens=3, cache_read_tokens=2, cache_write_tokens=1
):
    prompt = input_tokens + cache_read_tokens + cache_write_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "reasoning_tokens": 2,
        "request_count": 1,
        "prompt_tokens": prompt,
        "total_tokens": prompt + output_tokens,
    }


def test_official_api_usage_aggregates_one_turn_and_keeps_actual_response_model():
    runtime, posted = active_runtime()
    agent = synthetic_agent()
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage()
        invoke_original(
            runtime,
            "post_api_request",
            agent,
            response=SimpleNamespace(model="synthetic-actual-first"),
        )
        # Same request is reported twice: never double count or replace its model.
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage(
            999, 999, 999, 999
        )
        invoke_original(
            runtime,
            "post_api_request",
            agent,
            response=SimpleNamespace(model="synthetic-duplicate-must-not-win"),
        )
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage(
            11, 5, 4, 0
        )
        invoke_original(
            runtime,
            "post_api_request",
            agent,
            api_request_id="synthetic-api-2",
            api_call_count=2,
            response=SimpleNamespace(model="synthetic-actual-last"),
        )
        finish(runtime, agent)
        payload, session = terminal_session(posted)
        assert payload["data"]["tokens"] == {"input_tokens": 25, "output_tokens": 8}
        assert payload["data"]["context"].get("used_tokens") == 15
        assert "max_tokens" not in payload["data"]["context"]
        assert "synthetic-actual-last" in payload["data"]["model"]
        for presentation in ("classic", "task"):
            raw = json.dumps(
                render_card(session, presentation=presentation), ensure_ascii=False
            )
            assert "synthetic-actual-last" in raw and "↑25" in raw and "↓8" in raw
            assert "ctx 0/0" not in raw
            assert (
                "SYNTHETIC_PRIVATE_ENDPOINT" not in raw
                and "SYNTHETIC_RAW_SECRET" not in raw
            )
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        "free text",
        [],
        {"input_tokens": True, "output_tokens": 3},
        {"input_tokens": -1, "output_tokens": 3},
        {"input_tokens": float("nan"), "output_tokens": 3},
    ],
)
def test_absent_or_malformed_usage_is_not_estimated_from_answer(usage):
    runtime, posted = active_runtime()
    agent = synthetic_agent()
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        agent._usage_summary_for_api_request_hook = lambda response: usage
        invoke_original(runtime, "post_api_request", agent)
        finish(runtime, agent, final_response="LONG_SYNTHETIC_ANSWER " * 100)
        payload, session = terminal_session(posted)
        assert not payload["data"].get("tokens")
        for presentation in ("classic", "task"):
            raw = json.dumps(
                render_card(session, presentation=presentation), ensure_ascii=False
            )
            assert "↑0" not in raw and "↓0" not in raw and "ctx 0/0" not in raw
    finally:
        runtime.close()


@pytest.mark.parametrize("mismatch", ["session", "turn", "platform", "request_id"])
def test_api_metrics_require_exact_bound_turn_session_platform_and_request(mismatch):
    runtime, posted = active_runtime()
    agent = synthetic_agent()
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage()
        changes = {}
        if mismatch == "session":
            agent.session_id = "different-session"
        elif mismatch == "platform":
            agent.platform = "telegram"
        elif mismatch == "turn":
            changes["turn_id"] = "different-turn"
        else:
            changes["api_request_id"] = ""
        invoke_original(runtime, "post_api_request", agent, **changes)
        agent.session_id = "synthetic-session"
        agent.platform = "feishu"
        finish(runtime, agent)
        payload, _session = terminal_session(posted)
        assert not payload["data"].get("tokens")
        assert "synthetic-response-model" not in payload["data"].get("model", "")
    finally:
        runtime.close()


def test_no_api_reports_only_known_model_and_duration_not_fake_zero_metrics():
    now = [100.0]
    runtime, posted = active_runtime()
    runtime._now = lambda: now[0]
    agent = synthetic_agent()
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        now[0] = 102.5
        finish(runtime, agent)
        payload, session = terminal_session(posted)
        assert payload["data"].get("duration") == pytest.approx(2.5)
        assert not payload["data"].get("tokens") and not payload["data"].get("context")
        for presentation in ("classic", "task"):
            raw = json.dumps(
                render_card(session, presentation=presentation), ensure_ascii=False
            )
            assert "2s" in raw and "synthetic-configured-model" in raw
            assert "↑0" not in raw and "↓0" not in raw and "ctx 0/0" not in raw
    finally:
        runtime.close()


def test_api_ids_and_usage_do_not_carry_to_new_turn():
    runtime, posted = active_runtime()
    agent = synthetic_agent()
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage()
        invoke_original(runtime, "post_api_request", agent)
        finish(runtime, agent)
        before = len(posted)
        # Late old API callbacks must not reopen a terminal owner.
        invoke_original(runtime, "post_api_request", agent)
        assert len(posted) == before
        assert runtime.bind_ingress_from_values(
            "default",
            "fallback_default",
            "synthetic-session",
            "synthetic-gateway",
            "new-generation",
            "synthetic-chat",
            "new-input",
            "new-input",
            "",
        )
        invoke_original(runtime, "pre_llm_call", agent, turn_id="synthetic-new-turn")
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage(
            1, 2, 0, 0
        )
        invoke_original(
            runtime, "post_api_request", agent, turn_id="synthetic-new-turn"
        )
        finish(runtime, agent, turn_id="synthetic-new-turn")
        payload, _session = terminal_session(posted)
        assert payload["data"]["tokens"] == {"input_tokens": 1, "output_tokens": 2}
    finally:
        runtime.close()


@pytest.mark.parametrize("missing_position", [0, 1])
def test_one_missing_api_usage_hides_incomplete_turn_totals(missing_position):
    runtime, posted = active_runtime()
    agent = synthetic_agent()
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        for i in range(2):
            agent._usage_summary_for_api_request_hook = lambda response, i=i: (
                None if i == missing_position else normalized_usage()
            )
            invoke_original(
                runtime, "post_api_request", agent, api_request_id=f"api-{i}"
            )
        finish(runtime, agent)
        payload, _session = terminal_session(posted)
        assert not payload["data"].get("tokens")
    finally:
        runtime.close()


def test_api_request_identity_storage_is_bounded_and_overflow_not_complete_usage():
    runtime, posted = active_runtime()
    agent = synthetic_agent()
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage(
            1, 1, 0, 0
        )
        for i in range(257):
            invoke_original(
                runtime, "post_api_request", agent, api_request_id=f"synthetic-api-{i}"
            )
        metrics = runtime._turns["synthetic-turn"].metrics
        assert len(metrics.request_ids) == 256
        finish(runtime, agent)
        payload, _session = terminal_session(posted)
        assert not payload["data"].get("tokens")
        assert not runtime._turns
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "label",
    [
        "https://SYNTHETIC_PRIVATE_ENDPOINT.invalid/model",
        "model?api_key=SYNTHETIC_SECRET",
        "bad model with spaces",
        "x" * 193,
        True,
        None,
    ],
)
def test_response_model_labels_cannot_publish_url_or_arbitrary_secret_text(label):
    runtime, posted = active_runtime()
    agent = synthetic_agent()
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage()
        invoke_original(
            runtime, "post_api_request", agent, response=SimpleNamespace(model=label)
        )
        finish(runtime, agent)
        payload, _session = terminal_session(posted)
        assert payload["data"]["model"] == "synthetic-configured-model"
        raw = json.dumps(payload)
        assert (
            "SYNTHETIC_PRIVATE_ENDPOINT" not in raw
            and "SYNTHETIC_SECRET" not in raw
            and "SYNTHETIC_RAW_SECRET" not in raw
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_real_signed_http_terminal_delivers_measured_footer_to_actual_owner(
    tmp_path,
):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    from hermes_feishu_card.server import create_app, SESSIONS_KEY

    cards = []

    class Transport:
        async def send_card(self, chat_id, card, **kwargs):
            return "synthetic-card"

        async def update_card_message(self, message_id, card):
            cards.append(copy.deepcopy(card))

    client = TestClient(
        TestServer(
            create_app(
                Transport(),
                card_config={"presentation": "task", "flush_interval_ms": 1},
                session_store_directory=tmp_path / "state",
            )
        )
    )
    await client.start_server()
    signed = runtime_module.SignedEventTransport(
        event_url=str(client.make_url("/events")),
        timeout_seconds=0.8,
        secret_reader=lambda: b"s" * 32,
    )
    runtime = runtime_module.PluginRuntime(post=signed)
    agent = synthetic_agent()
    assert runtime.bind_ingress_from_values(
        "default",
        "fallback_default",
        "synthetic-session",
        "synthetic-gateway",
        "generation",
        "synthetic-chat",
        "synthetic-input",
        "synthetic-input",
        "",
    )
    try:
        await asyncio.to_thread(invoke_original, runtime, "pre_llm_call", agent)
        assert (
            runtime.turn_state("synthetic-turn") is runtime_module.TurnState.CARD_ACTIVE
        )
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage()
        await asyncio.to_thread(
            invoke_original,
            runtime,
            "post_api_request",
            agent,
            response=SimpleNamespace(model="synthetic-real-response"),
        )
        await asyncio.to_thread(finish, runtime, agent)
        sessions = list(client.app[SESSIONS_KEY].values())
        assert len(sessions) == 1
        session = sessions[0]
        assert session.tokens == {"input_tokens": 10, "output_tokens": 3}
        assert "synthetic-real-response" in session.model
        assert session.status == "completed"
        assert any(
            "synthetic-real-response" in json.dumps(card)
            and "↑10" in json.dumps(card, ensure_ascii=False)
            and "↓3" in json.dumps(card, ensure_ascii=False)
            for card in cards
        )
        assert runtime.take_terminal_record("synthetic-turn")["response"] == {
            "ok": True,
            "applied": True,
        }
    finally:
        await asyncio.to_thread(runtime.close)
        await client.close()


def rendered_publish(agent, turn_id="synthetic-turn"):
    from hermes_feishu_card.install import patcher, hybrid_renderers

    root = Path(os.environ["HFC_FIXED_TAG_SOURCE_ROOT"])
    original = subprocess.check_output(
        ["git", "show", f"{FIXED_SHA}:agent/turn_context.py"], cwd=root
    )
    descriptor = next(
        d
        for d in patcher.HYBRID_PATCH_DESCRIPTORS
        if (d.group,d.target) == ("ingress_binding","agent/turn_context.py")
    )
    rendered = hybrid_renderers._render_turn_publish(original, descriptor)
    tree = ast.parse(rendered)
    assignment = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Attribute) and t.attr == "_current_turn_id"
            for t in n.targets
        )
    )
    block = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Try)
        and any(
            isinstance(x, ast.ImportFrom)
            and any(a.name == "publish_canonical_turn_id" for a in x.names)
            for x in n.body
        )
    )
    values = {"agent": agent, "turn_id": turn_id}
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[copy.deepcopy(assignment), copy.deepcopy(block)],
                    type_ignores=[],
                )
            ),
            "<actual-rendered-canonical-publish>",
            "exec",
        ),
        values,
    )
    return agent._hfc_canonical_turn_token


def weak_agent():
    class Agent:
        pass

    agent = Agent()
    agent.__dict__.update(vars(synthetic_agent()))
    agent.context_compressor = SimpleNamespace(context_length=200000)
    return agent


@pytest.mark.parametrize("limit", [65536, None, True, 0, -1, 3.5, "65536", 2**63])
def test_rendered_publish_reads_live_actual_context_limit_in_terminal_footer(limit):
    from hermes_feishu_card import hook_runtime

    runtime, posted = active_runtime()
    agent = weak_agent()
    token = rendered_publish(agent)
    assert token is not None
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage()
        invoke_original(runtime, "post_api_request", agent)
        # Canonical publication preceded preflight; final live limit may differ.
        agent.context_compressor.context_length = limit
        finish(runtime, agent)
        payload, session = terminal_session(posted)
        if type(limit) is int and 0 < limit < 2**63:
            assert payload["data"]["context"]["max_tokens"] == limit
            for presentation in ("classic", "task"):
                raw = json.dumps(
                    render_card(session, presentation=presentation), ensure_ascii=False
                )
                assert "ctx 10/" in raw
        else:
            assert "max_tokens" not in payload["data"].get("context", {})
            for presentation in ("classic", "task"):
                assert "ctx " not in json.dumps(
                    render_card(session, presentation=presentation), ensure_ascii=False
                )
    finally:
        runtime.close()
        assert hook_runtime.clear_canonical_turn_id(token)


def test_context_agent_reference_is_weak_and_clear_does_not_retain_it():
    import gc
    import weakref
    from hermes_feishu_card import hook_runtime

    agent = weak_agent()
    ref = weakref.ref(agent)
    token = rendered_publish(agent)
    assert hook_runtime.canonical_turn_context_limit("synthetic-turn") == 200000
    del agent
    gc.collect()
    try:
        assert ref() is None
        assert hook_runtime.canonical_turn_context_limit("synthetic-turn") is None
    finally:
        assert hook_runtime.clear_canonical_turn_id(token)


def test_terminal_context_metrics_do_not_import_hook_runtime(monkeypatch):
    """The plugin reads the shared carrier without re-entering its adapter."""
    import builtins
    from hermes_feishu_card import hook_runtime

    runtime, posted = active_runtime()
    agent = weak_agent()
    token = rendered_publish(agent)
    original_import = builtins.__import__
    adapter_imports = []

    def guarded_import(name, *args, **kwargs):
        if name == "hook_runtime" or name.endswith(".hook_runtime"):
            adapter_imports.append(name)
            raise ImportError("adapter must not be a metrics dependency")
        return original_import(name, *args, **kwargs)

    try:
        invoke_original(runtime, "pre_llm_call", agent)
        with monkeypatch.context() as patch:
            patch.setattr(builtins, "__import__", guarded_import)
            finish(runtime, agent)
        payload, _session = terminal_session(posted)
        assert not adapter_imports
        assert payload["data"]["context"]["max_tokens"] == 200000
    finally:
        runtime.close()
        assert hook_runtime.clear_canonical_turn_id(token)


def test_canonical_carrier_is_standalone_and_hook_exports_share_one_registry():
    import sys
    from hermes_feishu_card import canonical_turn, hook_runtime

    for name in (
        "publish_canonical_turn_id", "clear_canonical_turn_id",
        "consume_canonical_turn_id", "canonical_turn_context_limit",
        "_canonical_turn_registry_size",
    ):
        assert getattr(hook_runtime, name) is getattr(canonical_turn, name)
    child = subprocess.run(
        [sys.executable, "-I", "-c", """
import sys
sys.path.insert(0, sys.argv[1])
from hermes_feishu_card import canonical_turn as carrier
token = carrier.publish_canonical_turn_id('standalone-synthetic-turn')
assert carrier.consume_canonical_turn_id() == 'standalone-synthetic-turn'
assert carrier.clear_canonical_turn_id(token)
assert carrier._canonical_turn_registry_size() == 0
assert 'hermes_feishu_card.hook_runtime' not in sys.modules
assert 'hermes_feishu_card.hermes_plugin_runtime' not in sys.modules
""", str(Path(canonical_turn.__file__).resolve().parent.parent)],
        capture_output=True, text=True, timeout=10,
    )
    assert child.returncode == 0, child.stderr


@pytest.mark.parametrize(
    "case",
    [
        "wrong-turn",
        "changed-agent-turn",
        "no-agent",
        "nonweak-agent",
        "cleared",
        "wrong-thread",
    ],
)
def test_context_bridge_unknown_or_unowned_agent_never_supplies_context(case):
    from hermes_feishu_card import hook_runtime
    from concurrent.futures import ThreadPoolExecutor
    import contextvars

    agent = weak_agent()
    token = (
        hook_runtime.publish_canonical_turn_id("synthetic-turn")
        if case == "no-agent"
        else rendered_publish(SimpleNamespace(**vars(agent)))
        if case == "nonweak-agent"
        else rendered_publish(agent)
    )
    try:
        name = "other-turn" if case == "wrong-turn" else "synthetic-turn"
        if case == "changed-agent-turn":
            agent._current_turn_id = "another-turn"
        if case == "cleared":
            assert hook_runtime.clear_canonical_turn_id(token)
            token = None
        if case == "wrong-thread":
            context = contextvars.copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert (
                    pool.submit(
                        context.run, hook_runtime.canonical_turn_context_limit, name
                    ).result(timeout=1)
                    is None
                )
        else:
            assert hook_runtime.canonical_turn_context_limit(name) is None
    finally:
        if token is not None:
            assert hook_runtime.clear_canonical_turn_id(token)


@pytest.mark.asyncio
async def test_context_snapshot_cannot_cross_async_task_owner():
    import asyncio
    from hermes_feishu_card import hook_runtime

    agent = weak_agent()
    token = rendered_publish(agent)
    try:

        async def child():
            assert hook_runtime.canonical_turn_context_limit("synthetic-turn") is None
            assert hook_runtime.clear_canonical_turn_id(token) is False

        await asyncio.create_task(child())
        assert hook_runtime.canonical_turn_context_limit("synthetic-turn") == 200000
    finally:
        assert hook_runtime.clear_canonical_turn_id(token)


@pytest.mark.parametrize("invalid_id", ["", " ", None, True, "x" * 257])
def test_invalid_api_request_after_valid_usage_marks_total_and_latest_context_unknown(
    invalid_id,
):
    runtime, posted = active_runtime()
    agent = synthetic_agent()
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage()
        invoke_original(runtime, "post_api_request", agent)
        invoke_original(runtime, "post_api_request", agent, api_request_id=invalid_id)
        finish(runtime, agent)
        payload, _session = terminal_session(posted)
        assert not payload["data"].get("tokens")
        assert "used_tokens" not in payload["data"].get("context", {})
    finally:
        runtime.close()


def test_latest_missing_usage_does_not_leave_previous_context_usage():
    runtime, posted = active_runtime()
    agent = synthetic_agent()
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage()
        invoke_original(runtime, "post_api_request", agent)
        agent._usage_summary_for_api_request_hook = lambda response: None
        invoke_original(runtime, "post_api_request", agent, api_request_id="latest-api")
        finish(runtime, agent)
        payload, _session = terminal_session(posted)
        assert "used_tokens" not in payload["data"].get("context", {})
    finally:
        runtime.close()


def test_known_context_limit_without_reported_prompt_is_not_zero_percent():
    from hermes_feishu_card import hook_runtime
    runtime, posted = active_runtime()
    agent = weak_agent()
    token = rendered_publish(agent)
    try:
        invoke_original(runtime,'pre_llm_call',agent)
        agent._usage_summary_for_api_request_hook=lambda response:None
        invoke_original(runtime,'post_api_request',agent)
        finish(runtime,agent)
        payload,session=terminal_session(posted)
        assert payload['data']['context']['max_tokens']==200000
        assert 'used_tokens' not in payload['data']['context']
        for presentation in ('classic','task'):
            assert 'ctx 0/' not in json.dumps(render_card(session,presentation=presentation),ensure_ascii=False)
    finally:
        runtime.close()
        assert hook_runtime.clear_canonical_turn_id(token)


# --- #392 phase 1: exact per-invocation output rate -------------------------

from hermes_feishu_card.turn_metrics import TurnMetrics  # noqa: E402


def api_values(request_id="rate-api-1", output=100, *, started=100.0, first=100.5,
               ended=102.5, duration=None, usage=True, **extra):
    values = {
        "api_request_id": request_id,
        "started_at": started,
        "ended_at": ended,
        "first_chunk_at": first,
        "api_duration": ended - started if duration is None else duration,
        "usage": normalized_usage(output_tokens=output) if usage else None,
    }
    values.update(extra)
    return values


def test_rate_single_streamed_call_uses_decode_window():
    metrics = TurnMetrics(0.0)
    metrics.observe_api(api_values(output=100))  # 2.0 s from first chunk to end
    assert metrics.payload(10.0)["rate"] == {"output_tps": 50.0, "basis": "decode"}


def test_rate_multi_call_is_token_weighted_and_excludes_tool_gaps():
    metrics = TurnMetrics(0.0)
    metrics.observe_api(api_values("a", 30, started=100.0, first=100.2, ended=101.2))
    # A long tool gap (101.2 -> 160.0) between invocations never counts.
    metrics.observe_api(api_values("b", 90, started=160.0, first=161.0, ended=164.0))
    rate = metrics.payload(200.0)["rate"]
    assert rate == {"output_tps": 120 / 4.0, "basis": "decode"}


def test_rate_without_first_chunk_falls_back_to_request_duration():
    metrics = TurnMetrics(0.0)
    metrics.observe_api(api_values(output=60, first=None, ended=103.0))
    assert metrics.payload(10.0)["rate"] == {"output_tps": 20.0, "basis": "request"}


def test_rate_mixed_basis_reports_request_basis():
    metrics = TurnMetrics(0.0)
    metrics.observe_api(api_values("a", 50, first=101.0, ended=102.0))
    metrics.observe_api(api_values("b", 50, first=None, started=110.0, ended=111.0))
    assert metrics.payload(200.0)["rate"] == {"output_tps": 50.0, "basis": "request"}


@pytest.mark.parametrize("broken", ["usage", "output", "timing", "request_id", "moa"])
def test_rate_any_unknown_invocation_hides_rate(broken):
    metrics = TurnMetrics(0.0)
    metrics.observe_api(api_values("good", 100))
    if broken == "usage":
        metrics.observe_api(api_values("bad", usage=False))
    elif broken == "output":
        bad = api_values("bad")
        bad["usage"] = {"prompt_tokens": 5}
        metrics.observe_api(bad)
    elif broken == "timing":
        metrics.observe_api(api_values("bad", first=None, ended=None, duration="slow"))
    elif broken == "request_id":
        metrics.observe_api(api_values(None))
    else:
        metrics.observe_api(api_values("bad", moa_references=[{"model": "advisor"}]))
    assert "rate" not in metrics.payload(10.0)


def test_rate_duplicate_request_id_is_not_double_counted():
    metrics = TurnMetrics(0.0)
    metrics.observe_api(api_values("same", 100))
    metrics.observe_api(api_values("same", 900, first=100.0, ended=100.4))
    assert metrics.payload(10.0)["rate"] == {"output_tps": 50.0, "basis": "decode"}
    assert metrics.payload(10.0)["tokens"]["output_tokens"] == 100


@pytest.mark.parametrize(
    "timing",
    [
        {"first": float("nan")},
        {"ended": float("inf")},
        {"first": 103.0},  # first chunk after end
        {"first": 102.5},  # zero window
        {"first": None, "ended": None, "duration": 3600.0},
        {"first": None, "ended": None, "duration": -1.0},
        {"first": None, "ended": None, "duration": True},
        {"first": "100.5"},
    ],
)
def test_rate_nonfinite_or_out_of_range_timing_hides_rate(timing):
    metrics = TurnMetrics(0.0)
    metrics.observe_api(api_values(**timing))
    assert "rate" not in metrics.payload(10.0)
    assert metrics.payload(10.0)["tokens"]["output_tokens"] == 100


@pytest.mark.parametrize("output,first", [(19, 100.5), (100, 102.3)])
def test_rate_below_sample_threshold_is_hidden(output, first):
    metrics = TurnMetrics(0.0)
    metrics.observe_api(api_values(output=output, first=first))
    assert "rate" not in metrics.payload(10.0)


def rate_session(rate, output_tokens=100):
    session = CardSession(conversation_id="c", message_id="m", chat_id="synthetic-chat")
    data = {"answer": "SYNTHETIC_ANSWER", "model": "synthetic-model", "duration": 5.0,
            "tokens": {"input_tokens": 10, "output_tokens": output_tokens}}
    if rate is not None:
        data["rate"] = rate
    assert session.apply(SidecarEvent.from_dict({
        "schema_version": "1", "event": "message.completed", "conversation_id": "c",
        "message_id": "m", "chat_id": "synthetic-chat", "platform": "feishu",
        "sequence": 1, "created_at": 1.0, "data": data,
    }))
    return session


def test_default_footer_does_not_show_output_rate():
    session = rate_session({"output_tps": 42.4, "basis": "decode"})
    for presentation in ("classic", "task"):
        raw = json.dumps(render_card(session, presentation=presentation), ensure_ascii=False)
        assert "tok/s" not in raw and "↓100" in raw


@pytest.mark.parametrize("basis,text", [("decode", "42 tok/s"), ("request", "~42 tok/s (含TTFT)")])
def test_opt_in_footer_shows_output_rate(basis, text):
    session = rate_session({"output_tps": 42.4, "basis": basis})
    fields = ["duration", "output_tokens", "output_rate"]
    for presentation in ("classic", "task"):
        raw = json.dumps(
            render_card(session, presentation=presentation, footer_fields=fields), ensure_ascii=False
        )
        assert text in raw
        if basis == "decode":
            assert "含TTFT" not in raw


@pytest.mark.parametrize(
    "rate",
    [None, {}, {"output_tps": float("nan"), "basis": "decode"}, {"output_tps": -1, "basis": "decode"},
     {"output_tps": True, "basis": "decode"}, {"output_tps": 40, "basis": "estimate"}, "40 tok/s"],
)
def test_opt_in_footer_hides_missing_or_invalid_rate(rate):
    session = rate_session(rate)
    raw = json.dumps(render_card(session, footer_fields=["output_tokens", "output_rate"]), ensure_ascii=False)
    assert "tok/s" not in raw


def test_rate_without_output_tokens_is_not_adopted():
    session = rate_session({"output_tps": 42.0, "basis": "decode"}, output_tokens=0)
    assert "output_tps" not in session.tokens


HOOK_CONTRACT_SHAS = {
    "v0.22.2": "d923a5eb487a17c0a68801bba9ef0f56c81040a8",
    "main": "ecac27c0af86064c40eeb8097cc20377cf2eee12",
}


@pytest.mark.parametrize("ref", sorted(HOOK_CONTRACT_SHAS))
def test_upstream_post_api_request_kwargs_feed_exact_rate(ref):
    """Evaluate the real v0.22.2/main ``post_api_request`` call against the runtime.

    Opt-in: HFC_UPSTREAM_HOOK_CONTRACT_ROOT names a Hermes Git checkout holding both commits.
    """
    configured = os.environ.get("HFC_UPSTREAM_HOOK_CONTRACT_ROOT")
    if not configured:
        pytest.skip("Hermes Git checkout with v0.22.2/main hook sources not supplied")
    sha = HOOK_CONTRACT_SHAS[ref]
    source = subprocess.check_output(
        ["git", "show", f"{sha}:agent/turn_response_intake.py"], cwd=configured
    )
    calls = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "_invoke_hook" and node.args
        and isinstance(node.args[0], ast.Constant) and node.args[0].value == "post_api_request"
    ]
    assert len(calls) == 1
    keywords = {keyword.arg for keyword in calls[0].keywords}
    assert {"api_request_id", "api_duration", "ended_at", "first_chunk_at", "usage",
            "moa_references"} <= keywords
    runtime, posted = active_runtime()
    agent = synthetic_agent()
    try:
        invoke_original(runtime, "pre_llm_call", agent)
        agent._usage_summary_for_api_request_hook = lambda response: normalized_usage(output_tokens=80)
        agent._last_api_first_chunk_at = 100.4
        agent.client = SimpleNamespace(last_reference_metrics=lambda: None)
        values = dict(
            agent=agent, effective_task_id="synthetic-task", turn_id="synthetic-turn",
            api_request_id="synthetic-api-1", api_call_count=1, api_duration=2.4,
            api_start_time=100.0, finish_reason="stop", api_messages=[],
            response=SimpleNamespace(model="synthetic-response-model"),
            assistant_message=SimpleNamespace(content="SYNTHETIC_ANSWER", tool_calls=[]),
            _moa_reference_metrics_for_hook=lambda a: a.client.last_reference_metrics(),
            _invoke_hook=lambda hook_name, **kwargs: getattr(runtime, "handle_" + hook_name)(**kwargs),
        )
        eval(compile(ast.Expression(calls[0]), "<upstream-post-api-request>", "eval"), values)
        finish(runtime, agent)
        payload, session = terminal_session(posted)
        assert payload["data"]["rate"]["basis"] == "decode"
        assert payload["data"]["rate"]["output_tps"] == pytest.approx(40.0)
        raw = json.dumps(render_card(session, footer_fields=["output_rate"]), ensure_ascii=False)
        assert "40 tok/s" in raw
    finally:
        runtime.close()
