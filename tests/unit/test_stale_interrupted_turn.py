"""The real Hermes /stop path discards an explicitly interrupted result."""
import asyncio
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from hermes_feishu_card import cli, hook_runtime
from hermes_feishu_card.install import patcher


FIXTURE = Path(__file__).parents[1] / "fixtures/hermes_decomposed/gateway/run_turn.py"


def stop_source(*, pinned_channel=False):
    source = FIXTURE.read_text()
    source = source.replace(
        "agent_result = await self._run_agent(event, source)",
        "agent_result = await self._run_agent(\n"
        "            source=source, session_key=session_key, run_generation=run_generation,\n"
        "            event_message_id=self._reply_anchor_for_event(event))\n"
        "        if not self._is_session_run_current(_quick_key, run_generation):\n"
        "            self._hmwa_discard_stale_result(source, _quick_key, run_generation)\n"
        "            return None",
    )
    if pinned_channel:
        source = source.replace(
            "agent_result = await self._run_agent(\n",
            "_turn_channel_prompt, _turn_source = self._pinned_channel_inputs(\n"
            "            session_key, event.channel_prompt, source, internal=event.internal)\n"
            "        agent_result = await self._run_agent(\n",
        ).replace("source=source, session_key=session_key", "source=_turn_source, session_key=session_key"
        ).replace("event_message_id=self._reply_anchor_for_event(event))",
                  "event_message_id=self._reply_anchor_for_event(event), channel_prompt=_turn_channel_prompt)")
    return source


@pytest.mark.asyncio
@pytest.mark.parametrize("pinned_channel", [False, True])
@pytest.mark.parametrize("result,expected", [
    ({"interrupted": True, "completed": False}, 1),
    ({"failed": True}, 0),
    ({"interrupted": "true"}, 0),
    ({"interrupted": False, "completed": True}, 0),
])
async def test_stale_guard_delivers_only_explicit_interrupted_result(monkeypatch, result, expected, pinned_channel):
    emitted = []
    async def emit(local_vars, *, event_name):
        emitted.append((event_name, local_vars))
        return True
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", emit)
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals", lambda *a, **kw: False)
    namespace = {}
    exec(patcher.apply_gateway_fragment(stop_source(pinned_channel=pinned_channel), "gateway/run_turn.py"), namespace)
    runner = namespace["GatewayTurnMixin"]()
    async def run(**kwargs):
        return result
    runner._run_agent = run
    # Latest main pins channel context separately; the original event still
    # owns this terminal card even when the tool runner receives a source copy.
    from copy import copy
    runner._pinned_channel_inputs = lambda key, prompt, source, **kwargs: (prompt, copy(source))
    runner._is_session_run_current = lambda *args: False
    discarded = []
    runner._hmwa_discard_stale_result = lambda *args: discarded.append(args)
    source = SimpleNamespace(platform="feishu", chat_id="fixture-chat", thread_id="topic-1")
    event = SimpleNamespace(source=source, message_id="original-turn", reply_to_message_id="reply-alias",
                            channel_prompt="fixture-channel", internal=False)
    assert await runner._handle_message_with_agent(event, source, "gateway-session", 7) is None
    assert len(discarded) == 1
    assert len(emitted) == expected
    if expected:
        name, values = emitted[0]
        assert name == "message.failed"
        assert values["turn_id"] == values["message_id"] == "original-turn"
        assert values["source"].thread_id == "topic-1"


@pytest.mark.parametrize("old,new", [
    ("_is_session_run_current(_quick_key, run_generation)", "_is_session_run_current(_quick_key, other_generation)"),
    ("_hmwa_discard_stale_result(source, _quick_key, run_generation)", "_hmwa_discard_stale_result(other_source, _quick_key, run_generation)"),
    ("source=source, session_key=session_key", "source=other_source, session_key=session_key"),
    ("event_message_id=self._reply_anchor_for_event(event)", "event_message_id=other_message"),
    ("            return None", "            return agent_result"),
])
def test_stale_interrupted_anchor_drift_is_refused(old, new):
    source = stop_source()
    assert old in source
    with pytest.raises(ValueError, match="stale result"):
        patcher.apply_gateway_fragment(source.replace(old, new), "gateway/run_turn.py")


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("pinned_channel", [False, True])
def test_stale_interrupted_patch_roundtrip(newline, pinned_channel):
    source = stop_source(pinned_channel=pinned_channel).replace("\n", newline)
    patched = patcher.apply_gateway_fragment(source, "gateway/run_turn.py")
    assert "HERMES_FEISHU_CARD_STALE_INTERRUPTED_PATCH_BEGIN" in patched
    assert patcher.apply_gateway_fragment(patched, "gateway/run_turn.py") == patched
    assert patcher.remove_patch(patched) == source
    with pytest.raises(ValueError, match="stale interrupted"):
        patcher.remove_patch(patched.replace("_hfc_emit_interrupted(locals())", "_hfc_emit_interrupted({})"))


@pytest.mark.parametrize("old,new", [
    ("session_key, event.channel_prompt, source, internal=event.internal",
     "other_session, event.channel_prompt, source, internal=event.internal"),
    ("session_key, event.channel_prompt, source, internal=event.internal",
     "session_key, event.channel_prompt, other_source, internal=event.internal"),
    ("internal=event.internal", "internal=True"),
    ("channel_prompt=_turn_channel_prompt", "channel_prompt=other_prompt"),
    ("self._pinned_channel_inputs(", "self.other_inputs("),
    ("        agent_result = await self._run_agent(",
     "        _turn_source = other_source\n        agent_result = await self._run_agent("),
])
def test_pinned_channel_stale_interrupt_binding_drift_is_refused(old, new):
    source = stop_source(pinned_channel=True)
    assert old in source
    with pytest.raises(ValueError, match="stale result"):
        patcher.apply_gateway_fragment(source.replace(old, new), "gateway/run_turn.py")


@pytest.mark.asyncio
async def test_interrupted_terminal_is_per_turn_and_retryable(monkeypatch):
    events = []
    outcomes = iter((False, True, True))
    async def emit(local_vars, *, event_name):
        events.append((event_name, hook_runtime.build_event(event_name, local_vars)))
        await asyncio.sleep(0)
        return next(outcomes)
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", emit)
    sources = [SimpleNamespace(platform="feishu", chat_id="shared-chat", thread_id="topic-1",
                               _hfc_turn_id=turn, _hfc_conversation_id="conversation")
               for turn in ("turn-alice", "turn-bob")]
    def values(source):
        return {"source": source, "event": SimpleNamespace(message_id=source._hfc_turn_id),
                "agent_result": {"interrupted": True}, "_turn_seconds": 12.5}
    assert await hook_runtime.emit_stale_interrupted_turn_async(values(sources[0])) is False
    assert await hook_runtime.emit_stale_interrupted_turn_async(values(sources[0])) is True
    assert await hook_runtime.emit_stale_interrupted_turn_async(values(sources[0])) is False
    assert await hook_runtime.emit_stale_interrupted_turn_async(values(sources[1])) is True
    assert [payload["turn_id"] for _, payload in events] == ["turn-alice", "turn-alice", "turn-bob"]
    assert all(payload["data"]["duration"] == 12.5 for _, payload in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unbound", "wrong_event", "native", "no_result", "queued_child"])
async def test_interrupted_terminal_cannot_guess_owner(monkeypatch, change):
    source = SimpleNamespace(platform="feishu", chat_id="shared-chat", _hfc_turn_id="original")
    values = {"source": source, "event": SimpleNamespace(message_id="original"),
              "agent_result": {"interrupted": True}}
    if change == "unbound":
        del source._hfc_turn_id
    elif change == "wrong_event":
        values["event"].message_id = "new-turn"
    elif change == "native":
        source.platform = "slack"
    elif change == "queued_child":
        values["agent_result"]["queued_terminal_inbound_id"] = "child-turn"
    else:
        values["agent_result"] = None
    async def unexpected(*a, **kw):
        pytest.fail("unverified interrupted owner emitted")
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", unexpected)
    assert await hook_runtime.emit_stale_interrupted_turn_async(values) is False


@pytest.mark.asyncio
async def test_parallel_duplicate_and_new_turn_copy_are_isolated(monkeypatch):
    from copy import copy
    calls = []
    async def emit(values, *, event_name):
        calls.append(values["turn_id"])
        await asyncio.sleep(0)
        return True
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", emit)
    source = SimpleNamespace(platform="feishu", chat_id="shared-chat", _hfc_turn_id="old-turn")
    def values(owner):
        return {"source": owner, "event": SimpleNamespace(message_id=owner._hfc_turn_id),
                "agent_result": {"interrupted": True}}
    outcomes = await asyncio.gather(
        hook_runtime.emit_stale_interrupted_turn_async(values(source)),
        hook_runtime.emit_stale_interrupted_turn_async(values(source)),
    )
    assert outcomes == [True, False]
    new_source = copy(source)
    new_source._hfc_turn_id = "new-turn"
    assert await hook_runtime.emit_stale_interrupted_turn_async(values(new_source)) is True
    assert calls == ["old-turn", "new-turn"]


def test_cli_upgrade_old_owned_hooks_and_uninstall_exactly(tmp_path, monkeypatch):
    from hermes_feishu_card.install import decomposed
    root = tmp_path / "hermes"
    shutil.copytree(FIXTURE.parents[1], root, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "VERSION").write_text("0.20.0\n")
    (root / "gateway/run_turn.py").write_text(stop_source())
    before = {name: (root / name).read_bytes() for name in decomposed.SOURCE_TARGETS
              if (root / name).exists()}
    monkeypatch.setattr(cli, "_ensure_hermes_runtime_package", lambda detection: None)
    monkeypatch.setattr(cli, "_ensure_hermes_feishu_sdk", lambda detection: None)
    args = ["install", "--hermes-dir", str(root), "--yes"]
    with monkeypatch.context() as old:
        old.setattr(patcher, "_apply_stale_interrupted_patch", lambda source: source)
        assert cli.main(args) == 0
    target = root / "gateway/run_turn.py"
    assert patcher.STALE_INTERRUPTED_PATCH_BEGIN not in target.read_text()
    assert cli.main(args) == 0
    upgraded = target.read_bytes()
    assert patcher.STALE_INTERRUPTED_PATCH_BEGIN.encode() in upgraded
    assert cli.main(args) == 0
    assert target.read_bytes() == upgraded
    assert cli.main(["uninstall", "--hermes-dir", str(root), "--yes"]) == 0
    assert {name: (root / name).read_bytes() for name in before} == before
    assert not list(root.rglob("*.hermes_feishu_card.bak"))
