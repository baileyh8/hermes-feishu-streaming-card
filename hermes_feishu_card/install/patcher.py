import ast


PATCH_BEGIN = "# HERMES_FEISHU_CARD_PATCH_BEGIN"
PATCH_END = "# HERMES_FEISHU_CARD_PATCH_END"
COMPLETE_PATCH_BEGIN = "# HERMES_FEISHU_CARD_COMPLETE_PATCH_BEGIN"
COMPLETE_PATCH_END = "# HERMES_FEISHU_CARD_COMPLETE_PATCH_END"
TOOL_PATCH_BEGIN = "# HERMES_FEISHU_CARD_TOOL_PATCH_BEGIN"
TOOL_PATCH_END = "# HERMES_FEISHU_CARD_TOOL_PATCH_END"
ANSWER_DELTA_PATCH_BEGIN = "# HERMES_FEISHU_CARD_ANSWER_DELTA_PATCH_BEGIN"
ANSWER_DELTA_PATCH_END = "# HERMES_FEISHU_CARD_ANSWER_DELTA_PATCH_END"
THINKING_DELTA_PATCH_BEGIN = "# HERMES_FEISHU_CARD_THINKING_DELTA_PATCH_BEGIN"
THINKING_DELTA_PATCH_END = "# HERMES_FEISHU_CARD_THINKING_DELTA_PATCH_END"

_HANDLER_NAME = "_handle_message_with_agent"
_NO_FINAL_NEWLINE = "# HERMES_FEISHU_CARD_NO_FINAL_NEWLINE"


def apply_patch(content: str) -> str:
    """Insert the Feishu card hook block into a safe Hermes message handler."""
    content = _apply_start_patch(content)
    content = _apply_complete_patch(content)
    content = _apply_callback_patch(
        content,
        callback_name="progress_callback",
        begin_marker=TOOL_PATCH_BEGIN,
        end_marker=TOOL_PATCH_END,
        renderer=_render_tool_hook_block,
        required_outer_names=(
            "source",
            "event_message_id",
            "_loop_for_step",
            "_run_still_current",
        ),
        required_callback_args=("event_type", "tool_name", "preview"),
    )
    content = _apply_callback_patch(
        content,
        callback_name="_stream_delta_cb",
        begin_marker=ANSWER_DELTA_PATCH_BEGIN,
        end_marker=ANSWER_DELTA_PATCH_END,
        renderer=_render_answer_delta_hook_block,
        required_outer_names=(
            "source",
            "event_message_id",
            "_loop_for_step",
            "_run_still_current",
        ),
        required_callback_args=("text",),
    )
    return _apply_callback_patch(
        content,
        callback_name="_interim_assistant_cb",
        begin_marker=THINKING_DELTA_PATCH_BEGIN,
        end_marker=THINKING_DELTA_PATCH_END,
        renderer=_render_thinking_delta_hook_block,
        required_outer_names=(
            "source",
            "event_message_id",
            "_loop_for_step",
            "_run_still_current",
        ),
        required_callback_args=("text", "already_streamed"),
    )


def _apply_start_patch(content: str) -> str:
    owned_block = _find_owned_block(content)
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = _render_hook_block(indent, newline)
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    tree = _parse_content(content)
    lines = content.splitlines(keepends=True)
    handler_body = _find_handler_body_location(tree, lines)
    if handler_body is None:
        raise ValueError("could not find safe handler")

    newline = _detect_newline(content)
    insert_at, body_indent = handler_body
    hook = _render_hook_block(body_indent, newline)
    if _needs_leading_newline(lines, insert_at):
        hook = [newline, f"{body_indent}{_NO_FINAL_NEWLINE}{newline}"] + hook

    return "".join(lines[:insert_at] + hook + lines[insert_at:])


def _apply_complete_patch(content: str) -> str:
    owned_block = _find_owned_complete_block(content)
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = _render_complete_hook_block(indent, newline)
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    tree = _parse_content(content)
    lines = content.splitlines(keepends=True)
    completion_location = _find_completion_return_location(tree, lines)
    if completion_location is None:
        return content

    newline = _detect_newline(content)
    insert_at, body_indent = completion_location
    hook = _render_complete_hook_block(body_indent, newline)
    return "".join(lines[:insert_at] + hook + lines[insert_at:])


def remove_patch(content: str) -> str:
    """Remove the owned Feishu card hook block from patched Hermes content."""
    content = _remove_simple_owned_patch(
        content,
        TOOL_PATCH_BEGIN,
        TOOL_PATCH_END,
        _render_tool_hook_block,
        "tool patch markers",
    )
    content = _remove_simple_owned_patch(
        content,
        ANSWER_DELTA_PATCH_BEGIN,
        ANSWER_DELTA_PATCH_END,
        _render_answer_delta_hook_block,
        "answer delta patch markers",
    )
    content = _remove_simple_owned_patch(
        content,
        THINKING_DELTA_PATCH_BEGIN,
        THINKING_DELTA_PATCH_END,
        _render_thinking_delta_hook_block,
        "thinking delta patch markers",
    )
    content = _remove_complete_patch(content)
    owned_block = _find_owned_block(content)
    if owned_block is None:
        return content

    lines = content.splitlines(keepends=True)
    begin_index, end_index = owned_block
    if _has_no_final_newline_sentinel(lines, begin_index):
        return "".join(
            lines[: begin_index - 2]
            + [_strip_line_ending(lines[begin_index - 2])]
            + lines[end_index + 1 :]
        )
    return "".join(lines[:begin_index] + lines[end_index + 1 :])


def remove_patch_lenient(content: str) -> str:
    """Remove owned patch markers, accepting older generated block bodies."""
    owned_complete_block = _find_simple_marker_block(
        content,
        COMPLETE_PATCH_BEGIN,
        COMPLETE_PATCH_END,
        "completion patch markers",
    )
    if owned_complete_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_complete_block
        content = "".join(lines[:begin_index] + lines[end_index + 1 :])

    for begin_marker, end_marker in (
        (TOOL_PATCH_BEGIN, TOOL_PATCH_END),
        (ANSWER_DELTA_PATCH_BEGIN, ANSWER_DELTA_PATCH_END),
        (THINKING_DELTA_PATCH_BEGIN, THINKING_DELTA_PATCH_END),
    ):
        owned_block = _find_simple_marker_block(
            content,
            begin_marker,
            end_marker,
            "callback patch markers",
        )
        if owned_block is not None:
            lines = content.splitlines(keepends=True)
            begin_index, end_index = owned_block
            content = "".join(lines[:begin_index] + lines[end_index + 1 :])
    return remove_patch(content)


def _remove_complete_patch(content: str) -> str:
    owned_block = _find_owned_complete_block(content)
    if owned_block is None:
        return content
    lines = content.splitlines(keepends=True)
    begin_index, end_index = owned_block
    return "".join(lines[:begin_index] + lines[end_index + 1 :])


def _apply_callback_patch(
    content: str,
    *,
    callback_name: str,
    begin_marker: str,
    end_marker: str,
    renderer,
    required_outer_names=(),
    required_callback_args=(),
) -> str:
    owned_block = _find_simple_marker_block(
        content,
        begin_marker,
        end_marker,
        "callback patch markers",
    )
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = renderer(indent, newline)
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    tree = _parse_content(content)
    lines = content.splitlines(keepends=True)
    callback_body = _find_callback_body_location(
        tree,
        lines,
        callback_name,
        required_outer_names=required_outer_names,
        required_callback_args=required_callback_args,
    )
    if callback_body is None:
        return content

    newline = _detect_newline(content)
    insert_at, body_indent = callback_body
    hook = renderer(body_indent, newline)
    return "".join(lines[:insert_at] + hook + lines[insert_at:])


def _remove_simple_owned_patch(
    content: str,
    begin_marker: str,
    end_marker: str,
    renderer,
    error_label: str,
) -> str:
    owned_block = _find_simple_owned_patch(
        content, begin_marker, end_marker, renderer, error_label
    )
    if owned_block is None:
        return content
    lines = content.splitlines(keepends=True)
    begin_index, end_index = owned_block
    return "".join(lines[:begin_index] + lines[end_index + 1 :])


def _parse_content(content: str):
    try:
        return ast.parse(content)
    except SyntaxError as exc:
        raise ValueError("could not find safe handler") from exc


def _find_handler_body_location(tree, lines):
    for node in tree.body:
        if _is_handler(node):
            return _body_location(node, lines)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if _is_handler(child):
                    return _body_location(child, lines)

    return None


def _find_completion_return_location(tree, lines):
    handler = _find_handler_node(tree)
    if handler is None:
        return None

    returns = [
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.Return)
        and isinstance(getattr(node, "value", None), ast.Name)
        and node.value.id == "response"
        and node.lineno is not None
    ]
    if not returns:
        return None

    target = max(returns, key=lambda node: node.lineno)
    insert_at = target.lineno - 1
    return insert_at, _line_indent(lines, insert_at)


def _find_callback_body_location(
    tree,
    lines,
    callback_name: str,
    *,
    required_outer_names=(),
    required_callback_args=(),
):
    run_agent = _find_run_agent_node(tree)
    if run_agent is None:
        return None
    for node in ast.walk(run_agent):
        if isinstance(node, ast.FunctionDef) and node.name == callback_name:
            if not _has_required_callback_scope(
                run_agent,
                node,
                required_outer_names,
                required_callback_args,
            ):
                return None
            return _body_location(node, lines)
    return None


def _has_required_callback_scope(
    run_agent,
    callback,
    required_outer_names,
    required_callback_args,
) -> bool:
    outer_names = _function_scope_names(run_agent)
    callback_args = _function_argument_names(callback)
    return set(required_outer_names).issubset(outer_names) and set(
        required_callback_args
    ).issubset(callback_args)


def _function_scope_names(node) -> set[str]:
    names = set(_function_argument_names(node))
    for child in ast.walk(node):
        if child is node:
            continue
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(child.name)
        elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            names.add(child.id)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            names.add(child.name)
        elif isinstance(child, ast.arg):
            continue
    return names


def _function_argument_names(node) -> set[str]:
    args = []
    args.extend(getattr(node.args, "posonlyargs", []))
    args.extend(node.args.args)
    args.extend(node.args.kwonlyargs)
    if node.args.vararg is not None:
        args.append(node.args.vararg)
    if node.args.kwarg is not None:
        args.append(node.args.kwarg)
    return {arg.arg for arg in args}


def _body_location(node, lines):
    if not node.body:
        return None

    if _is_docstring_expr(node.body[0]):
        return _body_location_after_docstring(node, lines)

    insert_before = node.body[0]
    if _is_unsafe_one_line_body(node, insert_before):
        return None
    insert_at = insert_before.lineno - 1
    return insert_at, _line_indent(lines, insert_at)


def _body_location_after_docstring(node, lines):
    if len(node.body) > 1:
        insert_before = node.body[1]
        if _is_unsafe_one_line_body(node, insert_before):
            return None
        insert_at = insert_before.lineno - 1
        return insert_at, _line_indent(lines, insert_at)

    docstring = node.body[0]
    end_lineno = getattr(docstring, "end_lineno", docstring.lineno)
    if end_lineno is None or docstring.lineno is None or docstring.lineno == node.lineno:
        return None
    insert_at = end_lineno
    return insert_at, _line_indent(lines, docstring.lineno - 1)


def _is_unsafe_one_line_body(handler, body_node) -> bool:
    return body_node.lineno is None or body_node.lineno == handler.lineno


def _is_docstring_expr(node) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(getattr(node, "value", None), ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_handler(node) -> bool:
    return isinstance(node, ast.AsyncFunctionDef) and node.name == _HANDLER_NAME


def _find_owned_block(content: str):
    begin_count = content.count(PATCH_BEGIN)
    end_count = content.count(PATCH_END)
    lines = content.splitlines(keepends=True)
    sentinel_indexes = _sentinel_line_indexes(lines)
    if begin_count == 0 and end_count == 0:
        if sentinel_indexes:
            raise ValueError("corrupt patch markers")
        return None
    if begin_count != 1 or end_count != 1:
        raise ValueError("corrupt patch markers")

    begin_index = _exact_marker_line_index(lines, PATCH_BEGIN)
    end_index = _exact_marker_line_index(lines, PATCH_END)
    if begin_index is None or end_index is None or begin_index >= end_index:
        raise ValueError("corrupt patch markers")

    _validate_sentinel_marker_adjacency(sentinel_indexes, begin_index)

    indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
    newline = _line_ending(lines[begin_index]) or _detect_newline(content)
    expected = _render_hook_block(indent, newline)
    placeholder = _render_placeholder_hook_block(indent, newline)
    actual = lines[begin_index : end_index + 1]

    if actual not in (expected, placeholder):
        raise ValueError("corrupt patch markers")

    tree = _parse_content_with_markers(content)
    if _has_no_final_newline_sentinel(lines, begin_index):
        _validate_no_final_newline_sentinel(lines, begin_index, end_index, tree)

    handler_body = _find_handler_body_location(tree, lines)
    if handler_body is None:
        raise ValueError("corrupt patch markers")

    first_body_index, _body_indent = handler_body
    if begin_index != first_body_index - 1:
        raise ValueError("corrupt patch markers")
    return begin_index, end_index


def _find_owned_complete_block(content: str):
    begin_count = content.count(COMPLETE_PATCH_BEGIN)
    end_count = content.count(COMPLETE_PATCH_END)
    if begin_count == 0 and end_count == 0:
        return None
    if begin_count != 1 or end_count != 1:
        raise ValueError("corrupt completion patch markers")

    lines = content.splitlines(keepends=True)
    begin_index = _exact_marker_line_index(lines, COMPLETE_PATCH_BEGIN)
    end_index = _exact_marker_line_index(lines, COMPLETE_PATCH_END)
    if begin_index is None or end_index is None or begin_index >= end_index:
        raise ValueError("corrupt completion patch markers")

    indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
    newline = _line_ending(lines[begin_index]) or _detect_newline(content)
    expected = _render_complete_hook_block(indent, newline)
    legacy = _render_legacy_complete_hook_block(indent, newline)
    previous_async = _render_previous_async_complete_hook_block(indent, newline)
    actual = lines[begin_index : end_index + 1]
    if actual not in (expected, legacy, previous_async):
        raise ValueError("corrupt completion patch markers")
    return begin_index, end_index


def _parse_content_with_markers(content: str):
    try:
        return ast.parse(content)
    except SyntaxError as exc:
        raise ValueError("corrupt patch markers") from exc


def _sentinel_line_indexes(lines):
    return [
        index
        for index, line in enumerate(lines)
        if _strip_line_ending(line)
        == _leading_whitespace(_strip_line_ending(line)) + _NO_FINAL_NEWLINE
    ]


def _validate_sentinel_marker_adjacency(sentinel_indexes, begin_index: int) -> None:
    if not sentinel_indexes:
        return
    if len(sentinel_indexes) != 1 or sentinel_indexes[0] != begin_index - 1:
        raise ValueError("corrupt patch markers")


def _validate_no_final_newline_sentinel(lines, begin_index: int, end_index: int, tree) -> None:
    if end_index != len(lines) - 1:
        raise ValueError("corrupt patch markers")

    handler = _find_handler_node(tree)
    if (
        handler is None
        or len(handler.body) != 2
        or not _is_docstring_expr(handler.body[0])
        or not isinstance(handler.body[1], ast.Try)
    ):
        raise ValueError("corrupt patch markers")

    docstring_end_lineno = getattr(handler.body[0], "end_lineno", handler.body[0].lineno)
    if docstring_end_lineno is None:
        raise ValueError("corrupt patch markers")

    sentinel_index = begin_index - 1
    if sentinel_index != docstring_end_lineno or begin_index != sentinel_index + 1:
        raise ValueError("corrupt patch markers")


def _find_handler_node(tree):
    for node in tree.body:
        if _is_handler(node):
            return node

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if _is_handler(child):
                    return child

    return None


def _find_run_agent_node(tree):
    for node in tree.body:
        if _is_run_agent(node):
            return node

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if _is_run_agent(child):
                    return child

    return None


def _is_run_agent(node) -> bool:
    return isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_agent"


def _find_simple_owned_patch(
    content: str,
    begin_marker: str,
    end_marker: str,
    renderer,
    error_label: str,
):
    marker_block = _find_simple_marker_block(
        content,
        begin_marker,
        end_marker,
        error_label,
    )
    if marker_block is None:
        return None
    lines = content.splitlines(keepends=True)
    begin_index, end_index = marker_block
    indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
    newline = _line_ending(lines[begin_index]) or _detect_newline(content)
    expected = renderer(indent, newline)
    actual = lines[begin_index : end_index + 1]
    if actual != expected:
        raise ValueError(f"corrupt {error_label}")
    return begin_index, end_index


def _find_simple_marker_block(
    content: str,
    begin_marker: str,
    end_marker: str,
    error_label: str,
):
    begin_count = content.count(begin_marker)
    end_count = content.count(end_marker)
    if begin_count == 0 and end_count == 0:
        return None
    if begin_count != 1 or end_count != 1:
        raise ValueError(f"corrupt {error_label}")

    lines = content.splitlines(keepends=True)
    begin_index = _exact_marker_line_index(lines, begin_marker)
    end_index = _exact_marker_line_index(lines, end_marker)
    if begin_index is None or end_index is None or begin_index >= end_index:
        raise ValueError(f"corrupt {error_label}")
    return begin_index, end_index


def _exact_marker_line_index(lines, marker: str):
    for index, line in enumerate(lines):
        body = _strip_line_ending(line)
        if body == _leading_whitespace(body) + marker:
            return index
    return None


def _render_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    return [
        f"{indent}{PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import emit_from_hermes_locals as _hfc_emit{newline}"
        ),
        f"{inner_indent}_hfc_emit(locals()){newline}",
        f"{indent}except Exception:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}{PATCH_END}{newline}",
    ]


def _render_complete_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{COMPLETE_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import emit_from_hermes_locals_async as _hfc_emit_async{newline}"
        ),
        f"{inner_indent}_hfc_card_delivered = await _hfc_emit_async({{{newline}",
        f"{deeper_indent}**locals(),{newline}",
        f"{deeper_indent}\"answer\": response,{newline}",
        f"{deeper_indent}\"duration\": _response_time,{newline}",
        f"{deeper_indent}\"model\": agent_result.get(\"model\", \"\"),{newline}",
        f"{deeper_indent}\"tokens\": {{{newline}",
        f"{deeper_indent}    \"input_tokens\": agent_result.get(\"input_tokens\", 0),{newline}",
        f"{deeper_indent}    \"output_tokens\": agent_result.get(\"output_tokens\", 0),{newline}",
        f"{deeper_indent}}},{newline}",
        f"{deeper_indent}\"context\": {{{newline}",
        f"{deeper_indent}    \"used_tokens\": agent_result.get(\"last_prompt_tokens\", 0),{newline}",
        f"{deeper_indent}    \"max_tokens\": agent_result.get(\"context_length\", 0),{newline}",
        f"{deeper_indent}}},{newline}",
        f"{inner_indent}}}, event_name=\"message.completed\"){newline}",
        f"{inner_indent}if _hfc_card_delivered:{newline}",
        f"{deeper_indent}return None{newline}",
        f"{indent}except Exception:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}{COMPLETE_PATCH_END}{newline}",
    ]


def _render_legacy_complete_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{COMPLETE_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import emit_from_hermes_locals as _hfc_emit{newline}"
        ),
        f"{inner_indent}_hfc_emit({{{newline}",
        f"{deeper_indent}**locals(),{newline}",
        f"{deeper_indent}\"answer\": response,{newline}",
        f"{deeper_indent}\"duration\": _response_time,{newline}",
        f"{deeper_indent}\"tokens\": {{{newline}",
        f"{deeper_indent}    \"input_tokens\": agent_result.get(\"input_tokens\", 0),{newline}",
        f"{deeper_indent}    \"output_tokens\": agent_result.get(\"output_tokens\", 0),{newline}",
        f"{deeper_indent}}},{newline}",
        f"{inner_indent}}}, event_name=\"message.completed\"){newline}",
        f"{indent}except Exception:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}{COMPLETE_PATCH_END}{newline}",
    ]


def _render_previous_async_complete_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{COMPLETE_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import emit_from_hermes_locals_async as _hfc_emit_async{newline}"
        ),
        f"{inner_indent}_hfc_card_delivered = await _hfc_emit_async({{{newline}",
        f"{deeper_indent}**locals(),{newline}",
        f"{deeper_indent}\"answer\": response,{newline}",
        f"{deeper_indent}\"duration\": _response_time,{newline}",
        f"{deeper_indent}\"tokens\": {{{newline}",
        f"{deeper_indent}    \"input_tokens\": agent_result.get(\"input_tokens\", 0),{newline}",
        f"{deeper_indent}    \"output_tokens\": agent_result.get(\"output_tokens\", 0),{newline}",
        f"{deeper_indent}}},{newline}",
        f"{inner_indent}}}, event_name=\"message.completed\"){newline}",
        f"{inner_indent}if _hfc_card_delivered:{newline}",
        f"{deeper_indent}return None{newline}",
        f"{indent}except Exception:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}{COMPLETE_PATCH_END}{newline}",
    ]


def _render_tool_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{TOOL_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import emit_from_hermes_locals_threadsafe as _hfc_emit_threadsafe{newline}"
        ),
        f"{inner_indent}if event_type in (\"tool.started\", \"tool.completed\") and _run_still_current():{newline}",
        f"{deeper_indent}if _hfc_emit_threadsafe({{{newline}",
        f"{deeper_indent}    **locals(),{newline}",
        f"{deeper_indent}    \"source\": source,{newline}",
        f"{deeper_indent}    \"message_id\": event_message_id,{newline}",
        f"{deeper_indent}    \"_hfc_loop\": _loop_for_step,{newline}",
        f"{deeper_indent}    \"tool_id\": tool_name or \"tool\",{newline}",
        f"{deeper_indent}    \"name\": tool_name or \"tool\",{newline}",
        f"{deeper_indent}    \"status\": \"completed\" if event_type == \"tool.completed\" else \"running\",{newline}",
        f"{deeper_indent}    \"detail\": preview or \"\",{newline}",
        f"{deeper_indent}}}, event_name=\"tool.updated\"):{newline}",
        f"{deeper_indent}    return{newline}",
        f"{indent}except Exception:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}{TOOL_PATCH_END}{newline}",
    ]


def _render_answer_delta_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{ANSWER_DELTA_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import emit_from_hermes_locals_threadsafe as _hfc_emit_threadsafe{newline}"
        ),
        f"{inner_indent}if text and _run_still_current():{newline}",
        f"{deeper_indent}if _hfc_emit_threadsafe({{{newline}",
        f"{deeper_indent}    **locals(),{newline}",
        f"{deeper_indent}    \"source\": source,{newline}",
        f"{deeper_indent}    \"message_id\": event_message_id,{newline}",
        f"{deeper_indent}    \"_hfc_loop\": _loop_for_step,{newline}",
        f"{deeper_indent}    \"text\": text,{newline}",
        f"{deeper_indent}}}, event_name=\"answer.delta\"):{newline}",
        f"{deeper_indent}    return{newline}",
        f"{indent}except Exception:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}{ANSWER_DELTA_PATCH_END}{newline}",
    ]


def _render_thinking_delta_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{THINKING_DELTA_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import emit_from_hermes_locals_threadsafe as _hfc_emit_threadsafe{newline}"
        ),
        f"{inner_indent}if text and not already_streamed and _run_still_current():{newline}",
        f"{deeper_indent}if _hfc_emit_threadsafe({{{newline}",
        f"{deeper_indent}    **locals(),{newline}",
        f"{deeper_indent}    \"source\": source,{newline}",
        f"{deeper_indent}    \"message_id\": event_message_id,{newline}",
        f"{deeper_indent}    \"_hfc_loop\": _loop_for_step,{newline}",
        f"{deeper_indent}    \"text\": text,{newline}",
        f"{deeper_indent}}}, event_name=\"thinking.delta\"):{newline}",
        f"{deeper_indent}    return{newline}",
        f"{indent}except Exception:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}{THINKING_DELTA_PATCH_END}{newline}",
    ]


def _render_placeholder_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    return [
        f"{indent}{PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}except Exception:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}{PATCH_END}{newline}",
    ]


def _child_indent(indent: str) -> str:
    if indent.endswith("\t"):
        return indent + "\t"
    return indent + " " * 4


def _line_indent(lines, index: int) -> str:
    if index < 0 or index >= len(lines):
        return ""
    return _leading_whitespace(_strip_line_ending(lines[index]))


def _needs_leading_newline(lines, insert_at: int) -> bool:
    return insert_at == len(lines) and bool(lines) and _line_ending(lines[-1]) == ""


def _has_no_final_newline_sentinel(lines, begin_index: int) -> bool:
    if begin_index <= 1:
        return False
    sentinel_line = _strip_line_ending(lines[begin_index - 1])
    return sentinel_line == _leading_whitespace(sentinel_line) + _NO_FINAL_NEWLINE


def _detect_newline(content: str) -> str:
    crlf_index = content.find("\r\n")
    lf_index = content.find("\n")
    if crlf_index != -1 and crlf_index == lf_index - 1:
        return "\r\n"
    return "\n"


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _strip_line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n"):
        return line[:-1]
    return line


def _leading_whitespace(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]
