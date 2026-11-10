from __future__ import annotations

import ast
from dataclasses import replace
from collections.abc import Callable

from .patch_descriptors import PatchDescriptorRegistry, PatchGroupDescriptor
from .ast_contracts import find_slash_confirm_register


_REVISION = "fixed-tag-v2026.8.3-r1"


def _decode(content: bytes) -> tuple[str, str]:
    if type(content) is not bytes:
        raise TypeError("hybrid source must be ordinary bytes")
    text = content.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, newline


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _marker_block(fragment, indent: str, statements: tuple[str, ...], newline: str) -> str:
    lines = [
        indent + fragment.begin_marker.decode("ascii"),
        *(indent + statement if statement else "" for statement in statements),
        indent + fragment.end_marker.decode("ascii"),
    ]
    return newline.join(lines) + newline


def _insert_at_line(
    content: bytes,
    *,
    anchor: str,
    block: str,
    before: bool,
) -> bytes:
    text, _newline = _decode(content)
    lines = text.splitlines(keepends=True)
    matches = [index for index, line in enumerate(lines) if line.strip() == anchor]
    if len(matches) != 1:
        raise ValueError(f"fixed-tag anchor mismatch: {anchor}")
    index = matches[0] if before else matches[0] + 1
    lines.insert(index, block)
    rendered = "".join(lines)
    compile(rendered, "<hybrid-fixed-tag>", "exec")
    return rendered.encode("utf-8")


def _insert_in_function(
    content: bytes,
    *,
    function_name: str,
    anchor: str,
    block: str,
    before: bool,
    anchor_indent: str | None = None,
) -> bytes:
    text, _newline = _decode(content)
    tree = ast.parse(text)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    if len(functions) != 1:
        raise ValueError(f"fixed-tag function mismatch: {function_name}")
    function = functions[0]
    lines = text.splitlines(keepends=True)
    matches = [
        index
        for index in range(function.lineno - 1, function.end_lineno or function.lineno)
        if lines[index].strip() == anchor
        and (anchor_indent is None or _indent(lines[index]) == anchor_indent)
    ]
    if len(matches) != 1:
        raise ValueError(f"fixed-tag function anchor mismatch: {function_name}:{anchor}")
    index = matches[0] if before else matches[0] + 1
    lines.insert(index, block)
    rendered = "".join(lines)
    compile(rendered, "<hybrid-fixed-tag>", "exec")
    return rendered.encode("utf-8")


def _insert_after_sequence(content: bytes, sequence: str, block: str) -> bytes:
    text, _newline = _decode(content)
    if text.count(sequence) != 1:
        raise ValueError("fixed-tag sequence anchor mismatch")
    rendered = text.replace(sequence, sequence + block, 1)
    compile(rendered, "<hybrid-fixed-tag>", "exec")
    return rendered.encode("utf-8")


def _remove_fragments(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, _newline = _decode(content)
    lines = text.splitlines(keepends=True)
    for fragment in reversed(descriptor.fragments):
        begin = fragment.begin_marker.decode("ascii")
        end = fragment.end_marker.decode("ascii")
        begins = [index for index, line in enumerate(lines) if line.strip() == begin]
        ends = [index for index, line in enumerate(lines) if line.strip() == end]
        if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
            raise ValueError("hybrid fragment is missing or malformed")
        del lines[begins[0] : ends[0] + 1]
    restored = "".join(lines)
    compile(restored, "<hybrid-fixed-tag>", "exec")
    return restored.encode("utf-8")


def _render_ingress_run(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "_run_start_session_id = session_entry.session_id"
    line = next((item for item in text.splitlines() if item.strip() == anchor), None)
    if line is None:
        raise ValueError("fixed-tag ingress anchor missing")
    indent = _indent(line)
    first, second = descriptor.fragments
    first_block = _marker_block(
        first,
        indent,
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import bind_ingress_from_hermes_locals as _hfc_bind_ingress",
            "    _hfc_platform = getattr(getattr(source, \"platform\", None), \"value\", getattr(source, \"platform\", None))",
            "    _hfc_profile_value = getattr(source, \"profile\", None)",
            "    _hfc_profile_id = _hfc_profile_value if isinstance(_hfc_profile_value, str) and _hfc_profile_value else \"default\"",
            "    _hfc_profile_source = \"locals\" if _hfc_profile_value else \"fallback_default\"",
            "    _hfc_incoming_id = str(getattr(event, \"message_id\", \"\") or self._reply_anchor_for_event(event) or \"\")",
            "    _hfc_reply_id = str(self._reply_anchor_for_event(event) or _hfc_incoming_id)",
            "    _hfc_bind_ingress({",
            "        \"_hfc_authorized\": True,",
            "        \"platform\": _hfc_platform,",
            "        \"profile_id\": _hfc_profile_id,",
            "        \"profile_source\": _hfc_profile_source,",
            "        \"session_id\": _run_start_session_id,",
            "        \"gateway_session_key\": session_key,",
            "        \"generation\": str(run_generation),",
            "        \"chat_id\": str(getattr(source, \"chat_id\", \"\") or \"\"),",
            "        \"incoming_message_id\": _hfc_incoming_id,",
            "        \"reply_to_message_id\": _hfc_reply_id,",
            "        \"thread_id\": str(getattr(source, \"thread_id\", \"\") or \"\"),",
            "    })",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    second_block = _marker_block(
        second,
        indent,
        (
            "# The official pre_llm_call publishes the canonical turn next;",
            "# this marker proves ingress was bound before that lifecycle hook.",
        ),
        newline,
    )
    return _insert_at_line(
        content,
        anchor=anchor,
        block=first_block + second_block,
        before=False,
    )


def _render_turn_publish(content: bytes, descriptor: PatchGroupDescriptor, *, metrics: bool = True) -> bytes:
    text, newline = _decode(content)
    anchor = "agent._current_turn_id = turn_id"
    line = next((item for item in text.splitlines() if item.strip() == anchor), None)
    if line is None:
        raise ValueError("fixed-tag canonical publish anchor missing")
    indent = _indent(line)
    publish_args = "turn_id, metrics_agent=agent" if metrics else "turn_id"
    block = _marker_block(
        descriptor.fragments[0],
        indent,
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import publish_canonical_turn_id as _hfc_publish_turn",
            "    _hfc_platform = getattr(getattr(agent, \"platform\", None), \"value\", getattr(agent, \"platform\", None))",
            f'    agent._hfc_canonical_turn_token = _hfc_publish_turn({publish_args}) if _hfc_platform == "feishu" else None',
            "except Exception:",
            "    agent._hfc_canonical_turn_token = None",
        ),
        newline,
    )
    return _insert_at_line(content, anchor=anchor, block=block, before=False)


def _render_turn_clear(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "agent._turn_received_provider_response = False"
    line = next((item for item in text.splitlines() if item.strip() == anchor), None)
    if line is None:
        raise ValueError("fixed-tag canonical clear anchor missing")
    indent = _indent(line)
    block = _marker_block(
        descriptor.fragments[0],
        indent,
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import consume_terminal_record_from_hermes_locals as _hfc_take_terminal",
            "    from hermes_feishu_card.hook_runtime import clear_canonical_turn_id as _hfc_clear_turn",
            "    _hfc_platform = getattr(getattr(agent, \"platform\", None), \"value\", getattr(agent, \"platform\", None))",
            "    if _hfc_platform == \"feishu\":",
            "        _hfc_terminal_record = _hfc_take_terminal({\"_hfc_authorized\": True, \"platform\": \"feishu\", \"turn_id\": turn_id})",
            "        if _hfc_terminal_record is not None:",
            "            result[\"_hfc_terminal_record\"] = _hfc_terminal_record",
            "    _hfc_turn_token = getattr(agent, \"_hfc_canonical_turn_token\", None)",
            "    if _hfc_turn_token is not None:",
            "        _hfc_clear_turn(_hfc_turn_token)",
            "    agent._hfc_canonical_turn_token = None",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    return _insert_at_line(content, anchor=anchor, block=block, before=False)


def _render_terminal_gateway_v1(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = 'response = agent_result.get("final_response") or ""'
    line = next((item for item in text.splitlines() if item.strip() == anchor), None)
    if line is None:
        raise ValueError("fixed-tag terminal delivery anchor missing")
    indent = _indent(line)
    block = _marker_block(
        descriptor.fragments[0],
        indent,
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import apply_hybrid_terminal_record as _hfc_apply_terminal",
            "    _hfc_delivery_decision = _hfc_apply_terminal(agent_result.pop(\"_hfc_terminal_record\", None))",
            "    if _hfc_delivery_decision == \"card\":",
            "        response = \"\"",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    return _insert_at_line(content, anchor=anchor, block=block, before=False)


def _render_terminal_gateway(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    block = _marker_block(descriptor.fragments[0], "            ", (
        "try:",
        "    from hermes_feishu_card.hook_runtime import apply_hybrid_terminal_record as _hfc_apply_terminal",
        '    _hfc_record = agent_result.pop("_hfc_terminal_record", None)',
        '    if response == agent_result.get("final_response") and "MEDIA:" not in response:',
        '        if _hfc_apply_terminal(_hfc_record) == "card":',
        '            response = ""',
        "except Exception:",
        "    pass",
    ), newline)
    rendered = _insert_in_function(
        content, function_name="_handle_message_with_agent", anchor="return response",
        block=block, before=True, anchor_indent="            ",
    )
    text, newline = _decode(rendered)
    tree = ast.parse(text)
    runners = [node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "TurnRunner"]
    functions = [node for runner in runners for node in runner.body
                 if isinstance(node, ast.FunctionDef) and node.name == "run_sync"]
    if len(functions) != 1:
        raise ValueError("fixed-tag terminal result projection missing")
    returns = sorted(
        (node for node in ast.walk(functions[0])
         if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
         and {"final_response", "history_offset"}.issubset(
             {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
         )),
        key=lambda node: node.lineno,
    )
    if len(returns) != 2 or len(descriptor.fragments) != 7:
        raise ValueError("fixed-tag terminal result projection changed")
    lines = text.splitlines(keepends=True)
    for node, fragment in reversed(list(zip(returns, descriptor.fragments[1:3]))):
        if lines[node.lineno - 1].strip() != "return {":
            raise ValueError("fixed-tag terminal result return shape changed")
        lines.insert(node.lineno, _marker_block(
            fragment, _indent(lines[node.lineno - 1]) + "    ",
            ('"_hfc_terminal_record": (result.get("_hfc_terminal_record")',
             '    if final_response == result.get("final_response") else None),'),
            newline,
        ))
    rendered_text = "".join(lines)
    compile(rendered_text, "<hybrid-terminal-carrier>", "exec")
    rendered = rendered_text.encode("utf-8")
    for fragment, function, anchor, before, action, anchor_indent in (
        (descriptor.fragments[3], "progress_callback",
         "if not ctx.progress_queue or not ctx._run_still_current():", True, "return", "        "),
        (descriptor.fragments[4], "send_progress_messages",
         "raw = ctx.progress_queue.get_nowait()", False, "continue", "                "),
        (descriptor.fragments[5], "send_progress_messages",
         "except asyncio.CancelledError:", False, "return", "            "),
    ):
        current, newline = _decode(rendered)
        node = next(n for n in ast.walk(ast.parse(current))
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == function)
        source_lines = current.splitlines()
        anchors = [line for line in source_lines[node.lineno - 1:node.end_lineno]
                   if line.strip() == anchor and _indent(line) == anchor_indent]
        if len(anchors) != 1:
            raise ValueError("fixed-tag progress boundary changed")
        block_indent = anchor_indent + ("    " if anchor == "except asyncio.CancelledError:" else "")
        block = _marker_block(fragment, block_indent, (
            "try:",
            "    from hermes_feishu_card.hook_runtime import suppress_hybrid_native_progress as _hfc_suppress_progress",
            '    _hfc_progress_agent = ctx.agent_holder[0]',
            '    _hfc_progress_profile = getattr(ctx.source, "profile", None)',
            '    if ctx._run_still_current() and _hfc_suppress_progress({',
            '        "_hfc_authorized": True,',
            '        "platform": getattr(ctx.source.platform, "value", ctx.source.platform),',
            '        "turn_id": getattr(_hfc_progress_agent, "_current_turn_id", None),',
            '        "gateway_session_key": ctx.session_key,',
            '        "generation": str(ctx.run_generation),',
            '        "chat_id": ctx.source.chat_id,',
            '        "thread_id": getattr(ctx.source, "thread_id", "") or "",',
            '        "profile_id": _hfc_progress_profile or "default",',
            '    }, owner=ctx):',
            "        " + action,
            "except Exception:",
            "    pass",
        ), newline)
        rendered = _insert_in_function(rendered, function_name=function,
                                      anchor=anchor, block=block, before=before,
                                      anchor_indent=anchor_indent)
    queued_block = _marker_block(descriptor.fragments[6], "                    ", (
        "try:",
        "    from hermes_feishu_card.hook_runtime import apply_hybrid_terminal_record as _hfc_apply_terminal",
        '    _hfc_record = _delivery_result.pop("_hfc_terminal_record", None)',
        '    if first_response == _delivery_result.get("final_response") and "MEDIA:" not in first_response:',
        '        if _hfc_apply_terminal(_hfc_record) == "card":',
        '            _already_streamed = True',
        "except Exception:",
        "    pass",
    ), newline)
    return _insert_in_function(
        rendered, function_name="_run_agent_inner",
        anchor="# Apply the same predicate as the normal completed-turn path.",
        block=queued_block, before=True, anchor_indent="                    ",
    )


def _render_answer_delta(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:"
    line = next((item for item in text.splitlines() if item.strip() == anchor), None)
    if line is None:
        raise ValueError("fixed-tag answer callback anchor missing")
    indent = _indent(line)
    block = _marker_block(
        descriptor.fragments[0],
        indent,
        (
            "_hfc_native_stream_delta_cb = _stream_delta_cb",
            "def _stream_delta_cb(text: str) -> None:",
            "    try:",
            "        from hermes_feishu_card.hook_runtime import emit_delta_from_hermes_locals_threadsafe as _hfc_emit_delta",
            "        if _hfc_emit_delta({\"_hfc_authorized\": True, \"platform\": \"feishu\", \"turn_id\": str(getattr(agent, \"_current_turn_id\", \"\") or \"\"), \"text\": text}, \"answer.delta\"):",
            "            return",
            "    except Exception:",
            "        pass",
            "    if _hfc_native_stream_delta_cb is not None:",
            "        _hfc_native_stream_delta_cb(text)",
        ),
        newline,
    )
    return _insert_at_line(content, anchor=anchor, block=block, before=True)


def _render_thinking_delta(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:"
    line = next((item for item in text.splitlines() if item.strip() == anchor), None)
    if line is None:
        raise ValueError("fixed-tag thinking callback anchor missing")
    indent = _indent(line) + "    "
    block = _marker_block(
        descriptor.fragments[0],
        indent,
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import emit_delta_from_hermes_locals_threadsafe as _hfc_emit_delta",
            "    if not already_streamed and _hfc_emit_delta({\"_hfc_authorized\": True, \"platform\": \"feishu\", \"turn_id\": str(getattr(agent, \"_current_turn_id\", \"\") or \"\"), \"text\": text}, \"thinking.delta\"):",
            "        return",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    return _insert_at_line(content, anchor=anchor, block=block, before=False)


def _render_status_notice(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "ctx = self._ctx"
    function = next(
        node
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.FunctionDef) and node.name == "_status_callback_sync"
    )
    line = text.splitlines()[function.lineno]
    indent = _indent(line)
    block = _marker_block(
        descriptor.fragments[0],
        indent,
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import submit_status_notice_from_hermes_locals as _hfc_status_notice",
            "    if _hfc_status_notice({\"_hfc_authorized\": True, \"platform\": \"feishu\"}, event_type=event_type, message=message):",
            "        return",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    return _insert_in_function(
        content,
        function_name="_status_callback_sync",
        anchor=anchor,
        block=block,
        before=False,
    )


def _render_command_card_startup(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "await self._redeliver_pending_obligations()"
    line = next(item for item in text.splitlines() if item.strip() == anchor)
    block = _marker_block(
        descriptor.fragments[0],
        _indent(line),
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import install_feishu_command_card_adapter_methods as _hfc_install_command_cards",
            "    _hfc_install_command_cards(self)",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    return _insert_at_line(content, anchor=anchor, block=block, before=True)


def _render_command_card_adapter(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "source = event.source"
    function = next(
        node
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_handle_message"
    )
    line = text.splitlines()[
        next(
            index
            for index in range(function.lineno - 1, function.end_lineno or function.lineno)
            if text.splitlines()[index].strip() == anchor
        )
    ]
    block = _marker_block(
        descriptor.fragments[0],
        _indent(line),
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import install_feishu_command_card_adapter_methods as _hfc_install_command_cards",
            "    _hfc_install_command_cards(self, event=event)",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    return _insert_in_function(
        content,
        function_name="_handle_message",
        anchor=anchor,
        block=block,
        before=False,
        anchor_indent=_indent(line),
    )


def _render_native_redelivery(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "result = await adapter.send("
    function = next(
        node
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_redeliver_pending_obligations"
    )
    lines = text.splitlines()
    line = lines[
        next(
            index
            for index in range(function.lineno - 1, function.end_lineno or function.lineno)
            if lines[index].strip() == anchor
        )
    ]
    block = _marker_block(
        descriptor.fragments[0],
        _indent(line),
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import prepare_native_handoff_recovery as _hfc_prepare_native_handoff_recovery",
            "    await _hfc_prepare_native_handoff_recovery(",
            "        adapter=adapter, obligation_id=row.get(\"obligation_id\"),",
            "        chat_id=row.get(\"chat_id\"), content=content,",
            "        original_content=row.get(\"content\"), thread_id=row.get(\"thread_id\") or \"\",",
            "    )",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    return _insert_in_function(
        content,
        function_name="_redeliver_pending_obligations",
        anchor=anchor,
        block=block,
        before=True,
    )


def _render_platform_notice(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = '"""Deliver a setup/operational notice using platform-specific privacy rules."""'
    line = next(item for item in text.splitlines() if item.strip() == anchor)
    block = _marker_block(
        descriptor.fragments[0],
        _indent(line),
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import handle_platform_notice_from_hermes as _hfc_handle_platform_notice",
            "    if _hfc_handle_platform_notice(self, source, content):",
            "        return None",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    return _insert_at_line(content, anchor=anchor, block=block, before=False)


def _render_hfc_command(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "_quick_key = self._session_key_for_source(source)"
    function = next(
        node
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_handle_message"
    )
    lines = text.splitlines()
    line = lines[
        next(
            index
            for index in range(function.lineno - 1, function.end_lineno or function.lineno)
            if lines[index].strip() == anchor
        )
    ]
    block = _marker_block(
        descriptor.fragments[0],
        _indent(line),
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import maintenance_admission_from_hermes_locals as _hfc_maintenance_admission",
            "    if await _hfc_maintenance_admission(locals()):",
            "        return None",
            "    from hermes_feishu_card.hook_runtime import handle_hfc_command_from_hermes_locals as _hfc_handle_command",
            "    _hfc_message_id = self._reply_anchor_for_event(event) or getattr(event, \"message_id\", None)",
            "    if _hfc_handle_command({**locals(), \"message_id\": _hfc_message_id}):",
            "        return None",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    return _insert_in_function(
        content,
        function_name="_handle_message",
        anchor=anchor,
        block=block,
        before=False,
    )


def _render_cron_delivery(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "media_files = BasePlatformAdapter.filter_media_delivery_paths(media_files)"
    function = next(
        node
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.FunctionDef) and node.name == "_deliver_result"
    )
    lines = text.splitlines()
    line = lines[
        next(
            index
            for index in range(function.lineno - 1, function.end_lineno or function.lineno)
            if lines[index].strip() == anchor
        )
    ]
    block = _marker_block(
        descriptor.fragments[0],
        _indent(line),
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import emit_cron_delivery as _hfc_emit_cron",
            "    _hfc_cron_metadata = {\"delivery_kind\": \"cron\"}",
            "    if _hfc_emit_cron({**locals(), \"_hfc_resolved_targets\": locals().get(\"targets\", [])}):",
            "        if media_files:",
            "            cleaned_delivery_content = \"\"",
            "        else:",
            "            return None",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    return _insert_in_function(
        content,
        function_name="_deliver_result",
        anchor=anchor,
        block=block,
        before=False,
    )


def _render_exact_base_no_text(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "if text_content and not _tts_caption_delivered:"
    line = next(item for item in text.splitlines() if item.strip() == anchor)
    block = _marker_block(
        descriptor.fragments[0],
        _indent(line),
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import finalize_exact_base_no_text as _hfc_finalize_no_text",
            "    if not text_content or _tts_caption_delivered:",
            "        await _hfc_finalize_no_text({**locals(), \"source\": event.source})",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    return _insert_at_line(content, anchor=anchor, block=block, before=True)


def _render_exact_base_final_delivery(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "result = await delivery_adapter._send_with_retry("
    line = next(item for item in text.splitlines() if item.strip() == anchor)
    block = _marker_block(
        descriptor.fragments[0],
        _indent(line),
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import prepare_exact_base_final_delivery as _hfc_prepare_final_delivery",
            "    delivery_adapter, text_content, _reply_anchor, _final_thread_metadata = await _hfc_prepare_final_delivery({",
            "        **locals(), \"source\": event.source, \"delivery_adapter\": delivery_adapter,",
            "        \"content\": text_content, \"obligation_id\": _obligation_id,",
            "        \"reply_to\": _reply_anchor, \"metadata\": _final_thread_metadata,",
            "    })",
            "except Exception:",
            "    pass",
        ),
        newline,
    )
    return _insert_at_line(content, anchor=anchor, block=block, before=True)


def _render_clarify_round_trip(content: bytes, descriptor: PatchGroupDescriptor, *, preserve_owned: bool = True, observe_timeout: bool = True) -> bytes:
    _text, newline = _decode(content)
    indent = " " * 12
    register_fragment, resolve_fragment = descriptor.fragments
    register_block = _marker_block(
        register_fragment,
        indent,
        (
            "_hfc_clarify_entry = _clarify_mod.get_pending_for_session(ctx.session_key or \"\", include_choice_prompts=True)",
            "_hfc_clarify_owned = False",
            "try:",
            "    from hashlib import sha256 as _hfc_sha256",
            "    _hfc_clarify_fingerprint = _hfc_sha256((str(question) + \"\\0\" + repr(list(choices) if choices else []) + \"\\0\" + str(bool(multi_select))).encode(\"utf-8\")).hexdigest()",
            "    def _hfc_resolve_clarify(selected_value):",
            "        return _clarify_mod.resolve_gateway_clarify(clarify_id, selected_value)",
            "except Exception:",
            "    _hfc_clarify_entry = None",
        ),
        newline,
    )
    resolve_block = _marker_block(
        resolve_fragment,
        indent,
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import admit_pending_interaction_from_hermes_locals as _hfc_admit_interaction",
            "    if _hfc_clarify_entry is not None:",
            "        _hfc_clarify_timeout = _clarify_mod.get_clarify_timeout()",
            "        _hfc_clarify_owned = _hfc_admit_interaction(",
            "            {\"_hfc_authorized\": True, \"platform\": \"feishu\", \"turn_id\": str(getattr(agent, \"_current_turn_id\", \"\") or \"\")}, \"clarify\",",
            "            {\"session_identity\": ctx.session_key or ctx._status_chat_id, \"interaction_id\": clarify_id, \"fingerprint\": _hfc_clarify_fingerprint},",
            "            _hfc_clarify_entry, _hfc_resolve_clarify,",
            "            {\"prompt\": str(question), \"description\": \"\", \"allow_custom_input\": True,",
            "             \"multi_select\": bool(multi_select), \"timeout_seconds\": float(_hfc_clarify_timeout),",
            "             \"options\": [{\"label\": str(item), \"value\": str(item), \"style\": \"default\"} for item in (list(choices) if choices else [])]},",
            "        )",
            "    if _hfc_clarify_owned:",
            "        try:",
            "            ctx._status_adapter.pause_typing_for_chat(ctx._status_chat_id)",
            "        except Exception:",
            "            pass",
            *((
                "        from hermes_feishu_card.hook_runtime import wait_for_owned_clarify as _hfc_wait_clarify",
                "        _hfc_clarify_response = _hfc_wait_clarify(",
                "            {\"_hfc_authorized\": True, \"platform\": \"feishu\", \"turn_id\": str(getattr(agent, \"_current_turn_id\", \"\") or \"\")},",
                "            {\"session_identity\": ctx.session_key or ctx._status_chat_id, \"interaction_id\": clarify_id, \"fingerprint\": _hfc_clarify_fingerprint},",
                "            _hfc_clarify_entry, _clarify_mod.wait_for_response, _hfc_clarify_timeout,",
                "        )",
            ) if observe_timeout else (
                "        _hfc_clarify_response = _clarify_mod.wait_for_response(clarify_id, timeout=float(_hfc_clarify_timeout))",
            )),
            "        if _hfc_clarify_response is None or _hfc_clarify_response == \"\":",
            "            return f\"[user did not respond within {int(_hfc_clarify_timeout / 60)}m]\"",
            "        return _hfc_clarify_response",
            "except Exception:",
            *(("    if _hfc_clarify_owned:", "        raise") if preserve_owned else ()),
            "    _hfc_clarify_owned = False",
        ),
        newline,
    )
    sequence = (
        "                multi_select=bool(multi_select)," + newline
        + "            )" + newline
    )
    return _insert_after_sequence(
        content,
        sequence,
        register_block + resolve_block,
    )


def _render_approval_round_trip(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    anchor = "_gateway_queues.setdefault(session_key, []).append(entry)"
    function = next(
        node
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.FunctionDef) and node.name == "_await_gateway_decision"
    )
    lines = text.splitlines()
    line = lines[
        next(
            index
            for index in range(function.lineno - 1, function.end_lineno or function.lineno)
            if lines[index].strip() == anchor
        )
    ]
    indent = _indent(line)
    register_fragment, resolve_fragment = descriptor.fragments
    register_block = _marker_block(
        register_fragment,
        indent,
        (
            "_hfc_approval_owned = False",
            "try:",
            "    from hashlib import sha256 as _hfc_sha256",
            "    _hfc_approval_command = \" \".join(str(command).split())",
            "    _hfc_approval_fingerprint = _hfc_sha256(_hfc_approval_command.encode(\"utf-8\")).hexdigest()",
            "    _hfc_approval_tool_call_id = _approval_tool_call_id.get()",
            "    _hfc_approval_interaction_id = \"approval:\" + str(_approval_turn_id.get()) + \":\" + str(_hfc_approval_tool_call_id) + \":\" + _hfc_approval_fingerprint[:16]",
            "    def _hfc_resolve_approval(selected_value):",
            "        if selected_value not in {\"once\", \"session\", \"always\", \"deny\"}:",
            "            return False",
            "        with _lock:",
            "            _hfc_queue = _gateway_queues.get(session_key, [])",
            "            if entry not in _hfc_queue or entry.event.is_set():",
            "                return False",
            "            entry.result = selected_value",
            "            entry.event.set()",
            "            return True",
            "except Exception:",
            "    _hfc_approval_interaction_id = \"\"",
        ),
        newline,
    )
    resolve_block = _marker_block(
        resolve_fragment,
        indent,
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import admit_pending_interaction_from_hermes_locals as _hfc_admit_interaction",
            "    _hfc_approval_options = [{\"label\": \"允许一次\", \"value\": \"once\", \"style\": \"primary\"}]",
            "    if approval_data.get(\"allow_session\", True):",
            "        _hfc_approval_options.append({\"label\": \"本会话允许\", \"value\": \"session\", \"style\": \"default\"})",
            "    if approval_data.get(\"allow_permanent\", True):",
            "        _hfc_approval_options.append({\"label\": \"始终允许\", \"value\": \"always\", \"style\": \"default\"})",
            "    _hfc_approval_options.append({\"label\": \"拒绝\", \"value\": \"deny\", \"style\": \"danger\"})",
            "    if _hfc_approval_interaction_id:",
            "        _hfc_approval_owned = _hfc_admit_interaction(",
            "            {\"_hfc_authorized\": True, \"platform\": \"feishu\", \"turn_id\": str(_approval_turn_id.get() or \"\")}, \"approval\",",
            "            {\"session_identity\": session_key, \"interaction_id\": _hfc_approval_interaction_id, \"fingerprint\": _hfc_approval_fingerprint},",
            "            entry, _hfc_resolve_approval,",
            "            {\"prompt\": \"需要授权后继续执行\", \"description\": str(description),",
            "             \"allow_custom_input\": False, \"multi_select\": False,",
            "             \"timeout_seconds\": float(_get_approval_timeout()), \"options\": _hfc_approval_options},",
            "        )",
            "    if _hfc_approval_owned:",
            "        notify_cb = lambda _approval_data: None",
            "except Exception:",
            "    _hfc_approval_owned = False",
        ),
        newline,
    )
    return _insert_in_function(
        content,
        function_name="_await_gateway_decision",
        anchor=anchor,
        block=register_block + resolve_block,
        before=False,
    )


def _render_slash_confirm(content: bytes, descriptor: PatchGroupDescriptor) -> bytes:
    text, newline = _decode(content)
    statement = find_slash_confirm_register(text)
    if statement is None or statement.end_lineno is None:
        raise ValueError("slash confirm register contract changed")
    lines = text.splitlines(keepends=True)
    indent = _indent(lines[statement.lineno - 1])
    register_fragment, resolve_fragment = descriptor.fragments
    register_block = _marker_block(
        register_fragment,
        indent,
        (
            "_hfc_slash_owned = False",
            "try:",
            "    import asyncio as _hfc_asyncio",
            "    from hashlib import sha256 as _hfc_sha256",
            "    _hfc_slash_loop = _hfc_asyncio.get_running_loop()",
            "    with _slash_confirm_mod._lock:",
            "        _hfc_slash_pending = _slash_confirm_mod._pending.get(session_key)",
            "    _hfc_slash_fingerprint = _hfc_sha256((str(session_key) + \"\\0\" + str(confirm_id) + \"\\0\" + str(command)).encode(\"utf-8\")).hexdigest()",
            "    def _hfc_resolve_slash(selected_value):",
            "        if selected_value not in {\"once\", \"always\", \"cancel\"}:",
            "            return False",
            "        _hfc_asyncio.run_coroutine_threadsafe(_slash_confirm_mod.resolve(session_key, confirm_id, selected_value), _hfc_slash_loop)",
            "        return True",
            "except Exception:",
            "    _hfc_slash_pending = None",
        ),
        newline,
    )
    resolve_block = _marker_block(
        resolve_fragment,
        indent,
        (
            "try:",
            "    from hermes_feishu_card.hook_runtime import admit_pending_interaction_from_hermes_locals as _hfc_admit_interaction",
            "    if _hfc_slash_pending is not None:",
            "        _hfc_slash_owned = _hfc_admit_interaction(",
            "            {\"_hfc_authorized\": True, \"platform\": \"feishu\"}, \"slash\",",
            "            {\"session_identity\": session_key, \"interaction_id\": \"slash:\" + str(session_key) + \":\" + str(confirm_id), \"fingerprint\": _hfc_slash_fingerprint},",
            "            _hfc_slash_pending, _hfc_resolve_slash,",
            "            {\"prompt\": str(title), \"description\": str(message), \"allow_custom_input\": False,",
            "             \"multi_select\": False, \"timeout_seconds\": 300.0,",
            "             \"options\": [{\"label\": \"允许一次\", \"value\": \"once\", \"style\": \"primary\"},",
            "                         {\"label\": \"始终允许\", \"value\": \"always\", \"style\": \"default\"},",
            "                         {\"label\": \"取消\", \"value\": \"cancel\", \"style\": \"danger\"}]},",
            "        )",
            "    if _hfc_slash_owned:",
            "        return None",
            "except Exception:",
            "    _hfc_slash_owned = False",
        ),
        newline,
    )
    lines.insert(statement.end_lineno, register_block + resolve_block)
    rendered = "".join(lines)
    compile(rendered, "<hybrid-fixed-tag>", "exec")
    return rendered.encode("utf-8")


def _render_subagent_parent_identity(
    content: bytes,
    descriptor: PatchGroupDescriptor,
) -> bytes:
    text, newline = _decode(content)
    tree = ast.parse(text)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_finalize_child_results"
    )
    original = 'parent_turn_id=getattr(parent_agent, "_current_turn_id", "") or "",'
    lines = text.splitlines(keepends=True)
    matches = [
        index
        for index in range(function.lineno - 1, function.end_lineno or function.lineno)
        if lines[index].strip() == original
    ]
    if len(matches) != 1:
        raise ValueError("fixed-tag immutable parent anchor mismatch")
    index = matches[0]
    indent = _indent(lines[index])
    lines[index] = _marker_block(
        descriptor.fragments[0],
        indent,
        ('parent_turn_id=getattr(child, "_parent_turn_id", "") or "",',),
        newline,
    )
    rendered = "".join(lines)
    compile(rendered, "<hybrid-fixed-tag>", "exec")
    return rendered.encode("utf-8")


def _remove_subagent_parent_identity(
    content: bytes,
    descriptor: PatchGroupDescriptor,
) -> bytes:
    text, newline = _decode(content)
    lines = text.splitlines(keepends=True)
    fragment = descriptor.fragments[0]
    begin = fragment.begin_marker.decode("ascii")
    end = fragment.end_marker.decode("ascii")
    begins = [index for index, line in enumerate(lines) if line.strip() == begin]
    ends = [index for index, line in enumerate(lines) if line.strip() == end]
    if len(begins) != 1 or len(ends) != 1 or ends[0] != begins[0] + 2:
        raise ValueError("immutable parent patch is missing or malformed")
    indent = _indent(lines[begins[0]])
    expected = 'parent_turn_id=getattr(child, "_parent_turn_id", "") or "",'
    if lines[begins[0] + 1].strip() != expected:
        raise ValueError("immutable parent patch body changed")
    lines[begins[0] : ends[0] + 1] = [
        indent
        + 'parent_turn_id=getattr(parent_agent, "_current_turn_id", "") or "",'
        + newline
    ]
    restored = "".join(lines)
    compile(restored, "<hybrid-fixed-tag>", "exec")
    return restored.encode("utf-8")


_RENDERERS: dict[tuple[str, str], Callable[[bytes, PatchGroupDescriptor], bytes]] = {
    ("ingress_binding", "gateway/run.py"): _render_ingress_run,
    ("ingress_binding", "agent/turn_context.py"): _render_turn_publish,
    ("ingress_binding", "agent/turn_finalizer.py"): _render_turn_clear,
    ("terminal_disposition", "gateway/run.py"): _render_terminal_gateway,
    ("answer_delta", "gateway/run.py"): _render_answer_delta,
    ("thinking_delta", "gateway/run.py"): _render_thinking_delta,
    ("status_notice", "gateway/run.py"): _render_status_notice,
    ("command_card_startup", "gateway/run.py"): _render_command_card_startup,
    ("command_card_adapter", "gateway/run.py"): _render_command_card_adapter,
    ("native_redelivery", "gateway/run.py"): _render_native_redelivery,
    ("platform_notice", "gateway/run.py"): _render_platform_notice,
    ("hfc_command", "gateway/run.py"): _render_hfc_command,
    ("cron_delivery", "cron/scheduler.py"): _render_cron_delivery,
    ("exact_base_no_text", "gateway/platforms/base.py"): _render_exact_base_no_text,
    ("exact_base_final_delivery", "gateway/platforms/base.py"): _render_exact_base_final_delivery,
    ("clarify_round_trip", "gateway/run.py"): _render_clarify_round_trip,
    ("approval_round_trip", "tools/approval.py"): _render_approval_round_trip,
    ("slash_confirm", "gateway/run.py"): _render_slash_confirm,
    ("subagent_parent_identity", "tools/delegate_tool.py"): _render_subagent_parent_identity,
}

_REMOVERS: dict[
    tuple[str, str], Callable[[bytes, PatchGroupDescriptor], bytes]
] = {
    (
        "subagent_parent_identity",
        "tools/delegate_tool.py",
    ): _remove_subagent_parent_identity,
}


def reviewed_descriptors(
    descriptors: tuple[PatchGroupDescriptor, ...],
) -> tuple[PatchGroupDescriptor, ...]:
    reviewed: list[PatchGroupDescriptor] = []
    for descriptor in descriptors:
        implementation = _RENDERERS.get((descriptor.group, descriptor.target))
        if implementation is None:
            reviewed.append(descriptor)
            continue

        def render(content: bytes, *, _descriptor=descriptor, _implementation=implementation) -> bytes:
            return _implementation(content, _descriptor)

        remover = _REMOVERS.get((descriptor.group, descriptor.target))

        def remove(
            content: bytes,
            *,
            _descriptor=descriptor,
            _remover=remover,
        ) -> bytes:
            if _remover is not None:
                return _remover(content, _descriptor)
            return _remove_fragments(content, _descriptor)

        reviewed.append(
            PatchGroupDescriptor(
                group=descriptor.group,
                target=descriptor.target,
                fragments=descriptor.fragments,
                renderer=render,
                remover=remove,
                renderer_revision=("fixed-tag-v2026.8.3-terminal-r2"
                                   if descriptor.group == "terminal_disposition" else
                                   "fixed-tag-v2026.8.3-metrics-r2"
                                   if (descriptor.group, descriptor.target) == ("ingress_binding", "agent/turn_context.py")
                                   else "fixed-tag-v2026.8.3-clarify-r2"
                                   if descriptor.group == "clarify_round_trip"
                                   else _REVISION),
            )
        )
    return tuple(reviewed)


def historical_terminal_registry(registry: PatchDescriptorRegistry) -> PatchDescriptorRegistry:
    """Exact pre-carrier renderer, solely for verified installed-state recovery."""
    descriptors = []
    for descriptor in registry.descriptors:
        if (descriptor.group, descriptor.target) != ("terminal_disposition", "gateway/run.py"):
            descriptors.append(descriptor)
            continue
        historical = replace(descriptor, fragments=descriptor.fragments[:1])
        descriptors.append(replace(
            historical,
            renderer=lambda content, d=historical: _render_terminal_gateway_v1(content, d),
            remover=lambda content, d=historical: _remove_fragments(content, d),
            renderer_revision="fixed-tag-v2026.8.3-r1",
        ))
    return replace(registry, descriptors=tuple(descriptors))


def historical_publish_registry(registry: PatchDescriptorRegistry) -> PatchDescriptorRegistry:
    """Exact pre-metrics carrier, for verified official restore only."""
    descriptors = tuple(
        replace(d, renderer=lambda content, d=d: _render_turn_publish(content, d, metrics=False),
                renderer_revision=_REVISION)
        if (d.group, d.target) == ("ingress_binding", "agent/turn_context.py") else d
        for d in registry.descriptors
    )
    return replace(registry, descriptors=descriptors)


def historical_clarify_registry(registry: PatchDescriptorRegistry) -> PatchDescriptorRegistry:
    """Exact pre-owned-wait guard, for verified official restore only."""
    descriptors = tuple(
        replace(d, renderer=lambda content, d=d: _render_clarify_round_trip(content, d, preserve_owned=False, observe_timeout=False),
                renderer_revision=_REVISION)
        if d.group == "clarify_round_trip" else d
        for d in registry.descriptors
    )
    return replace(registry, descriptors=descriptors)


def historical_clarify_wait_registry(registry: PatchDescriptorRegistry) -> PatchDescriptorRegistry:
    """Exact 9e/738 owned wait before timeout observation, for restore only."""
    return replace(registry, descriptors=tuple(
        replace(d, renderer=lambda content, d=d: _render_clarify_round_trip(content, d, observe_timeout=False))
        if d.group == "clarify_round_trip" else d
        for d in registry.descriptors
    ))
