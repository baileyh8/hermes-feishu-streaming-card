"""Verified Gateway source contracts, separate from patch rendering/writes.

Unknown source layouts remain rejected by the original structural predicates.
A returned location permits the caller's existing owned-block renderer only;
it does not establish a running task's identity or interrupted status.
"""
from __future__ import annotations

import ast

from .ast_contracts import _line_indent, _same_expression


def find_stale_interrupted_location(
    handler: ast.AST | None,
    lines: list[str],
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
        if len(pins) != 1 or len(pin_stores) != 2:
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
