"""Verified Gateway source contracts, separate from patch rendering/writes.

Unknown source layouts remain rejected by the original structural predicates.
A returned location permits the caller's existing owned-block renderer only;
it does not establish a running task's identity or interrupted status.
"""
from __future__ import annotations

import ast
from collections.abc import Mapping

from .ast_contracts import _line_indent, _same_expression

SETUP_GUARD_PEER = "gateway/run_turn_setup_guard.py"
_PINNED_INPUTS = (
    "session_key, event.channel_prompt, source, "
    'internal=event.internal or getattr(event, "inherit_channel_pin", False)'
)
_SETUP_GUARD_PIN = f"channel = runner._pinned_channel_inputs({_PINNED_INPUTS})"
_RESOLVE_UNPACK = "source, session_entry, session_key = resolved"
_SUCCESS_RETURN = (
    "return (source, session_entry, session_key, prepared, env_tokens, hook_ctx, channel), None"
)
_HEARTBEAT_RETURN = (
    "return (source, session_entry, session_key, prepared, env_tokens, None, None), None"
)
_HANDLER_SETUP_CALL = (
    "setup, reply = await run_preagent_setup(self, event, source, _quick_key, run_generation)"
)
_HANDLER_SETUP_UNPACK = (
    "source, session_entry, session_key, prepared, _session_env_tokens, hook_ctx, channel = setup"
)
_HANDLER_CHANNEL_UNPACK = "_turn_channel_prompt, _turn_source = channel"


def find_stale_interrupted_location(
    handler: ast.AST | None,
    lines: list[str],
    peer_sources: Mapping[str, str] | None = None,
) -> tuple[int, str] | None:
    """Locate the exact discarded-result hook, preserving source provenance."""
    if handler is None:
        return None
    discard_calls = [node for node in ast.walk(handler)
                     if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute)
                     and node.func.attr == "_hmwa_discard_stale_result"]
    # Earlier verified layouts have no discarded-result branch.
    if not discard_calls:
        return None
    expected = ast.parse(
        "if not self._is_session_run_current(_quick_key, run_generation):\n"
        "    self._hmwa_discard_stale_result(source, _quick_key, run_generation)\n"
        "    return None\n"
    ).body[0]
    guards = [node for node in ast.walk(handler)
              if isinstance(node, ast.If) and ast.dump(node) == ast.dump(expected)]
    if len(discard_calls) != 1 or len(guards) != 1:
        raise ValueError("stale result discard contract changed")
    guard = guards[0]
    blocks = [value for node in ast.walk(handler) for _, value in ast.iter_fields(node)
              if isinstance(value, list) and guard in value]
    if len(blocks) != 1:
        raise ValueError("stale result control flow changed")
    preceding = blocks[0][:blocks[0].index(guard)]
    writes = [node for statement in preceding for node in ast.walk(statement)
              if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
              and any(isinstance(target, ast.Name) and target.id == "agent_result"
                      for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
    if len(writes) != 1 or writes[0] not in preceding:
        raise ValueError("stale result assignment changed")
    assignment = writes[0]
    awaited = assignment.value
    call = awaited.value if isinstance(awaited, ast.Await) else None
    required = {
        "source": "source", "session_key": "session_key",
        "run_generation": "run_generation",
        "event_message_id": "self._reply_anchor_for_event(event)",
    }
    keywords = {item.arg: item.value for item in call.keywords} if isinstance(call, ast.Call) else {}
    if "source" in keywords and _same_expression(keywords["source"], "_turn_source"):
        # Verified main c8301ea6 pins channel inputs before execution. Accept
        # only that exact tuple binding from this event/session/source; an
        # arbitrary alias or reassignment must not broaden turn ownership.
        pin_text = (
            "_turn_channel_prompt, _turn_source = self._pinned_channel_inputs("
            "session_key, event.channel_prompt, source, internal=event.internal)"
        )
        # 994df5e6de also inherits pins for synthetic continuation events.
        # Keep both verified expressions exact; arbitrary truthy flags remain unsafe.
        pin_variants = [ast.parse(text).body[0] for text in (
            pin_text,
            pin_text.replace("internal=event.internal)",
                             'internal=event.internal or getattr(event, "inherit_channel_pin", False))'),
        )]
        before_run = preceding[:preceding.index(assignment)]
        pins = [node for node in before_run
                if any(ast.dump(node) == ast.dump(pin) for pin in pin_variants)]
        pin_stores = [node for statement in before_run for node in ast.walk(statement)
                      if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                      and node.id in {"_turn_channel_prompt", "_turn_source"}]
        if not (len(pins) == 1 and len(pin_stores) == 2
                or _accepts_setup_guard_channel_pin(handler, before_run, peer_sources)):
            raise ValueError("stale result pinned source binding changed")
        required.update(source="_turn_source", channel_prompt="_turn_channel_prompt")
    if (not isinstance(assignment, ast.Assign) or len(assignment.targets) != 1
            or not isinstance(call, ast.Call) or call.args or None in keywords
            or not _same_expression(call.func, "self._run_agent")
            or any(key not in keywords or not _same_expression(keywords[key], value)
                   for key, value in required.items())):
        raise ValueError("stale result runner binding changed")
    index = guard.body[0].lineno - 1
    return index, _line_indent(lines, index)


def _parse_stmt(text: str) -> ast.AST:
    return ast.parse(text).body[0]


def _dump(node: ast.AST) -> str:
    return ast.dump(node)


def _has_import(node: ast.AST, module: str, name: str) -> bool:
    return any(
        isinstance(item, ast.ImportFrom)
        and item.module == module
        and item.level == 0
        and any(alias.name == name and alias.asname is None for alias in item.names)
        for item in ast.walk(node)
    )


def _statement_matches(node: ast.AST, text: str) -> bool:
    return _dump(node) == _dump(_parse_stmt(text))


def _contains_return_none(node: ast.AST) -> bool:
    return any(
        isinstance(item, ast.Return) and item.value is not None and _same_expression(item.value, "None")
        or isinstance(item, ast.Return) and item.value is None
        for item in ast.walk(node)
    )


def _accepts_setup_guard_channel_pin(
    handler: ast.AST,
    before_run: list[ast.stmt],
    peer_sources: Mapping[str, str] | None,
) -> bool:
    """Accept only the verified v0.23.2 setup-guard pin plus this-turn unpack."""
    if not _has_import(handler, "gateway.run_turn_setup_guard", "run_preagent_setup"):
        return False
    setup_call = unpack = channel_unpack = none_setup = none_hook = None
    for node in ast.walk(handler):
        if setup_call is None and _statement_matches(node, _HANDLER_SETUP_CALL):
            setup_call = node
        if unpack is None and _statement_matches(node, _HANDLER_SETUP_UNPACK):
            unpack = node
        if channel_unpack is None and _statement_matches(node, _HANDLER_CHANNEL_UNPACK):
            channel_unpack = node
        if (
            isinstance(node, ast.If)
            and _same_expression(node.test, "setup is None")
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Return)
            and _same_expression(node.body[0].value, "reply")
            and not node.orelse
        ):
            none_setup = node
        if (
            isinstance(node, ast.If)
            and _same_expression(node.test, "hook_ctx is None")
            and _contains_return_none(node)
            and not node.orelse
        ):
            none_hook = node
    if (
        not isinstance(setup_call, ast.stmt)
        or not isinstance(unpack, ast.stmt)
        or not isinstance(channel_unpack, ast.Assign)
        or not isinstance(none_setup, ast.If)
        or not isinstance(none_hook, ast.If)
    ):
        return False
    if channel_unpack not in before_run or unpack.lineno >= channel_unpack.lineno:
        return False
    if not (setup_call.lineno < none_setup.lineno < unpack.lineno
            < none_hook.lineno < channel_unpack.lineno):
        return False
    pin_stores = [node for statement in before_run for node in ast.walk(statement)
                  if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                  and node.id in {"_turn_channel_prompt", "_turn_source"}]
    target = channel_unpack.targets[0]
    if (len(pin_stores) != 2
            or len(channel_unpack.targets) != 1
            or not isinstance(target, ast.Tuple)
            or not all(isinstance(elt, ast.Name) for elt in target.elts)
            or [elt.id for elt in target.elts if isinstance(elt, ast.Name)]
            != ["_turn_channel_prompt", "_turn_source"]):
        return False
    return _setup_guard_binds_pinned_channel(peer_sources)


def _setup_guard_binds_pinned_channel(peer_sources: Mapping[str, str] | None) -> bool:
    if not peer_sources or SETUP_GUARD_PEER not in peer_sources:
        return False
    try:
        tree = ast.parse(peer_sources[SETUP_GUARD_PEER])
    except SyntaxError:
        return False
    functions = [node for node in tree.body
                 if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_preagent_setup"]
    if len(functions) != 1:
        return False
    function = functions[0]
    args = function.args
    if (args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg
            or [arg.arg for arg in args.args] != [
                "runner", "event", "source", "quick_key", "run_generation"]):
        return False
    resolve = pin = success = heartbeat = None
    for node in ast.walk(function):
        if resolve is None and _statement_matches(node, _RESOLVE_UNPACK):
            resolve = node
        if pin is None and _statement_matches(node, _SETUP_GUARD_PIN):
            pin = node
        if success is None and _statement_matches(node, _SUCCESS_RETURN):
            success = node
        if heartbeat is None and _statement_matches(node, _HEARTBEAT_RETURN):
            heartbeat = node
    if (
        not isinstance(resolve, ast.stmt)
        or not isinstance(pin, ast.stmt)
        or not isinstance(success, ast.stmt)
        or not isinstance(heartbeat, ast.stmt)
    ):
        return False
    if not (resolve.lineno < pin.lineno < success.lineno and heartbeat.lineno < pin.lineno):
        return False
    stores = [node for node in ast.walk(function)
              if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == "channel"]
    if len(stores) != 1 or stores[0].lineno != pin.lineno:
        return False
    return True
