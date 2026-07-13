import ast


PATCH_BEGIN = "# HERMES_FEISHU_CARD_PATCH_BEGIN"
PATCH_END = "# HERMES_FEISHU_CARD_PATCH_END"
COMPLETE_PATCH_BEGIN = "# HERMES_FEISHU_CARD_COMPLETE_PATCH_BEGIN"
COMPLETE_PATCH_END = "# HERMES_FEISHU_CARD_COMPLETE_PATCH_END"
QUEUED_COMPLETE_PATCH_BEGIN = "# HERMES_FEISHU_CARD_QUEUED_COMPLETE_PATCH_BEGIN"
QUEUED_COMPLETE_PATCH_END = "# HERMES_FEISHU_CARD_QUEUED_COMPLETE_PATCH_END"
TOOL_PATCH_BEGIN = "# HERMES_FEISHU_CARD_TOOL_PATCH_BEGIN"
TOOL_PATCH_END = "# HERMES_FEISHU_CARD_TOOL_PATCH_END"
ANSWER_DELTA_PATCH_BEGIN = "# HERMES_FEISHU_CARD_ANSWER_DELTA_PATCH_BEGIN"
ANSWER_DELTA_PATCH_END = "# HERMES_FEISHU_CARD_ANSWER_DELTA_PATCH_END"
THINKING_DELTA_PATCH_BEGIN = "# HERMES_FEISHU_CARD_THINKING_DELTA_PATCH_BEGIN"
THINKING_DELTA_PATCH_END = "# HERMES_FEISHU_CARD_THINKING_DELTA_PATCH_END"
CLARIFY_PATCH_BEGIN = "# HERMES_FEISHU_CARD_CLARIFY_PATCH_BEGIN"
CLARIFY_PATCH_END = "# HERMES_FEISHU_CARD_CLARIFY_PATCH_END"
APPROVAL_PATCH_BEGIN = "# HERMES_FEISHU_CARD_APPROVAL_PATCH_BEGIN"
APPROVAL_PATCH_END = "# HERMES_FEISHU_CARD_APPROVAL_PATCH_END"
CRON_PATCH_BEGIN = "# HERMES_FEISHU_CARD_CRON_PATCH_BEGIN"
CRON_PATCH_END = "# HERMES_FEISHU_CARD_CRON_PATCH_END"
SLASH_CONFIRM_PATCH_BEGIN = "# HERMES_FEISHU_CARD_SLASH_CONFIRM_PATCH_BEGIN"
SLASH_CONFIRM_PATCH_END = "# HERMES_FEISHU_CARD_SLASH_CONFIRM_PATCH_END"
COMMAND_CARD_PATCH_BEGIN = "# HERMES_FEISHU_CARD_COMMAND_CARD_PATCH_BEGIN"
COMMAND_CARD_PATCH_END = "# HERMES_FEISHU_CARD_COMMAND_CARD_PATCH_END"
COMMAND_CARD_STARTUP_PATCH_BEGIN = (
    "# HERMES_FEISHU_CARD_COMMAND_CARD_STARTUP_PATCH_BEGIN"
)
COMMAND_CARD_STARTUP_PATCH_END = "# HERMES_FEISHU_CARD_COMMAND_CARD_STARTUP_PATCH_END"
PLATFORM_NOTICE_PATCH_BEGIN = "# HERMES_FEISHU_CARD_PLATFORM_NOTICE_PATCH_BEGIN"
PLATFORM_NOTICE_PATCH_END = "# HERMES_FEISHU_CARD_PLATFORM_NOTICE_PATCH_END"
HFC_COMMAND_PATCH_BEGIN = "# HERMES_FEISHU_CARD_HFC_COMMAND_PATCH_BEGIN"
HFC_COMMAND_PATCH_END = "# HERMES_FEISHU_CARD_HFC_COMMAND_PATCH_END"

_HANDLER_NAME = "_handle_message_with_agent"
_CRON_DELIVER_NAME = "_deliver_result"
_NO_FINAL_NEWLINE = "# HERMES_FEISHU_CARD_NO_FINAL_NEWLINE"
_SUPPORTED_STRATEGIES = {"legacy_gateway_run", "gateway_run_013_plus"}


def apply_patch(content: str, strategy: str = "legacy_gateway_run") -> str:
    """Insert the Feishu card hook block into a safe Hermes message handler."""
    if strategy not in _SUPPORTED_STRATEGIES:
        raise ValueError(f"unsupported patch strategy: {strategy}")
    content = _apply_start_patch(content, strategy=strategy)
    content = _apply_complete_patch(content)
    content = _apply_queued_complete_patch(content)
    if strategy == "gateway_run_013_plus":
        content = _apply_cron_patch(content)
        content = _apply_command_card_startup_patch(content)
        content = _apply_command_card_adapter_patch(content)
        content = _apply_hfc_command_patch(content)
        content = _apply_platform_notice_patch(content)
        content = _apply_slash_confirm_patch(content)
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
    content = _apply_callback_patch(
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
    content = _apply_callback_patch(
        content,
        callback_name="_clarify_callback_sync",
        begin_marker=CLARIFY_PATCH_BEGIN,
        end_marker=CLARIFY_PATCH_END,
        renderer=_render_clarify_hook_block,
        required_outer_names=(
            "source",
            "event_message_id",
            "_status_chat_id",
            "session_key",
            "_run_still_current",
        ),
        required_callback_args=("question", "choices"),
    )
    return _apply_callback_patch(
        content,
        callback_name="_approval_notify_sync",
        begin_marker=APPROVAL_PATCH_BEGIN,
        end_marker=APPROVAL_PATCH_END,
        renderer=_render_approval_hook_block,
        required_outer_names=(
            "source",
            "event_message_id",
            "_status_chat_id",
            "_approval_session_key",
            "_run_still_current",
        ),
        required_callback_args=("approval_data",),
    )


def apply_cron_patch(content: str) -> str:
    """Insert the Feishu card cron hook into a safe Hermes cron delivery function."""
    return _apply_cron_patch(content)


def _apply_start_patch(content: str, *, strategy: str) -> str:
    owned_block = _find_owned_block(content)
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = _render_hook_block(indent, newline, strategy=strategy)
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
    hook = _render_hook_block(body_indent, newline, strategy=strategy)
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


def _apply_queued_complete_patch(content: str) -> str:
    owned_block = _find_simple_marker_block(
        content,
        QUEUED_COMPLETE_PATCH_BEGIN,
        QUEUED_COMPLETE_PATCH_END,
        "queued completion patch markers",
    )
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = _render_queued_complete_hook_block(indent, newline)
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    lines = content.splitlines(keepends=True)
    target = 'if first_response and not _already_streamed:'
    for index, line in enumerate(lines):
        if _strip_line_ending(line).strip() != target:
            continue
        previous = lines[index - 1] if index > 0 else ""
        if "first_response = result.get(" not in previous:
            continue
        indent = _leading_whitespace(_strip_line_ending(line))
        newline = _line_ending(line) or _detect_newline(content)
        hook = _render_queued_complete_hook_block(indent, newline)
        return "".join(lines[:index] + hook + lines[index:])
    return content


def _apply_slash_confirm_patch(content: str) -> str:
    owned_block = _find_simple_marker_block(
        content,
        SLASH_CONFIRM_PATCH_BEGIN,
        SLASH_CONFIRM_PATCH_END,
        "slash confirm patch markers",
    )
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = _render_slash_confirm_hook_block(indent, newline)
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    tree = _parse_content(content)
    func = _find_async_function(tree, "_request_slash_confirm")
    if func is None:
        return content
    lines = content.splitlines(keepends=True)
    start = max(func.lineno - 1, 0)
    end = getattr(func, "end_lineno", None)
    if end is None:
        end = len(lines)
    anchor = "_slash_confirm_mod.register(session_key, confirm_id, command, handler)"
    for index in range(start, min(end, len(lines))):
        if _strip_line_ending(lines[index]).strip() != anchor:
            continue
        indent = _leading_whitespace(_strip_line_ending(lines[index]))
        newline = _line_ending(lines[index]) or _detect_newline(content)
        hook = _render_slash_confirm_hook_block(indent, newline)
        return "".join(lines[: index + 1] + hook + lines[index + 1 :])
    return content


def _apply_command_card_adapter_patch(content: str) -> str:
    owned_block = _find_simple_marker_block(
        content,
        COMMAND_CARD_PATCH_BEGIN,
        COMMAND_CARD_PATCH_END,
        "command card patch markers",
    )
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = _render_command_card_adapter_hook_block(indent, newline)
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    tree = _parse_content(content)
    func = _find_async_function(tree, "_handle_message")
    if func is None:
        return content
    lines = content.splitlines(keepends=True)
    start = max(func.lineno - 1, 0)
    end = getattr(func, "end_lineno", None)
    if end is None:
        end = len(lines)
    for index in range(start, min(end, len(lines))):
        if _strip_line_ending(lines[index]).strip() != "source = event.source":
            continue
        indent = _leading_whitespace(_strip_line_ending(lines[index]))
        newline = _line_ending(lines[index]) or _detect_newline(content)
        hook = _render_command_card_adapter_hook_block(indent, newline)
        return "".join(lines[: index + 1] + hook + lines[index + 1 :])
    return content


def _apply_command_card_startup_patch(content: str) -> str:
    owned_block = _find_simple_marker_block(
        content,
        COMMAND_CARD_STARTUP_PATCH_BEGIN,
        COMMAND_CARD_STARTUP_PATCH_END,
        "command card startup patch markers",
    )
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = _render_command_card_startup_hook_block(indent, newline)
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    tree = _parse_content(content)
    func = _find_gateway_runner_method(tree, "start")
    if func is None:
        return content
    drain = _find_recovered_watcher_drain(func)
    if drain is None or drain.lineno is None:
        return content

    lines = content.splitlines(keepends=True)
    insert_at = drain.lineno - 1
    indent = _line_indent(lines, insert_at)
    newline = _line_ending(lines[insert_at]) or _detect_newline(content)
    hook = _render_command_card_startup_hook_block(indent, newline)
    return "".join(lines[:insert_at] + hook + lines[insert_at:])


def _apply_platform_notice_patch(content: str) -> str:
    owned_block = _find_simple_marker_block(
        content,
        PLATFORM_NOTICE_PATCH_BEGIN,
        PLATFORM_NOTICE_PATCH_END,
        "platform notice patch markers",
    )
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = _render_platform_notice_hook_block(indent, newline)
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    tree = _parse_content(content)
    func = _find_async_function(tree, "_deliver_platform_notice")
    if func is None:
        return content
    lines = content.splitlines(keepends=True)
    notice_body = _body_location(func, lines)
    if notice_body is None:
        return content

    newline = _detect_newline(content)
    insert_at, body_indent = notice_body
    hook = _render_platform_notice_hook_block(body_indent, newline)
    return "".join(lines[:insert_at] + hook + lines[insert_at:])


def _apply_hfc_command_patch(content: str) -> str:
    owned_block = _find_simple_marker_block(
        content,
        HFC_COMMAND_PATCH_BEGIN,
        HFC_COMMAND_PATCH_END,
        "hfc command patch markers",
    )
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = _render_hfc_command_hook_block(indent, newline)
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    tree = _parse_content(content)
    func = _find_async_function(tree, "_handle_message")
    if func is None:
        return content
    lines = content.splitlines(keepends=True)
    start = max(func.lineno - 1, 0)
    end = getattr(func, "end_lineno", None)
    if end is None:
        end = len(lines)
    anchor = "_quick_key = self._session_key_for_source(source)"
    for index in range(start, min(end, len(lines))):
        if _strip_line_ending(lines[index]).strip() != anchor:
            continue
        indent = _leading_whitespace(_strip_line_ending(lines[index]))
        newline = _line_ending(lines[index]) or _detect_newline(content)
        hook = _render_hfc_command_hook_block(indent, newline)
        return "".join(lines[: index + 1] + hook + lines[index + 1 :])
    return content


def remove_patch(content: str) -> str:
    """Remove the owned Feishu card hook block from patched Hermes content."""
    content = _remove_cron_patch(content)
    content = _remove_simple_owned_patch(
        content,
        COMMAND_CARD_STARTUP_PATCH_BEGIN,
        COMMAND_CARD_STARTUP_PATCH_END,
        _render_command_card_startup_hook_block,
        "command card startup patch markers",
    )
    content = _remove_simple_owned_patch(
        content,
        COMMAND_CARD_PATCH_BEGIN,
        COMMAND_CARD_PATCH_END,
        _render_command_card_adapter_hook_block,
        "command card patch markers",
    )
    content = _remove_simple_owned_patch(
        content,
        HFC_COMMAND_PATCH_BEGIN,
        HFC_COMMAND_PATCH_END,
        _render_hfc_command_hook_block,
        "hfc command patch markers",
    )
    content = _remove_simple_owned_patch(
        content,
        PLATFORM_NOTICE_PATCH_BEGIN,
        PLATFORM_NOTICE_PATCH_END,
        _render_platform_notice_hook_block,
        "platform notice patch markers",
    )
    content = _remove_simple_owned_patch(
        content,
        SLASH_CONFIRM_PATCH_BEGIN,
        SLASH_CONFIRM_PATCH_END,
        _render_slash_confirm_hook_block,
        "slash confirm patch markers",
    )
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
    content = _remove_simple_owned_patch(
        content,
        CLARIFY_PATCH_BEGIN,
        CLARIFY_PATCH_END,
        _render_clarify_hook_block,
        "clarify patch markers",
    )
    content = _remove_simple_owned_patch(
        content,
        APPROVAL_PATCH_BEGIN,
        APPROVAL_PATCH_END,
        _render_approval_hook_block,
        "approval patch markers",
    )
    content = _remove_simple_owned_patch(
        content,
        QUEUED_COMPLETE_PATCH_BEGIN,
        QUEUED_COMPLETE_PATCH_END,
        _render_queued_complete_hook_block,
        "queued completion patch markers",
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


def remove_cron_patch(content: str) -> str:
    """Remove the owned Feishu card cron hook block from patched Hermes content."""
    return _remove_cron_patch(content)


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
        (CLARIFY_PATCH_BEGIN, CLARIFY_PATCH_END),
        (APPROVAL_PATCH_BEGIN, APPROVAL_PATCH_END),
        (COMMAND_CARD_STARTUP_PATCH_BEGIN, COMMAND_CARD_STARTUP_PATCH_END),
        (COMMAND_CARD_PATCH_BEGIN, COMMAND_CARD_PATCH_END),
        (HFC_COMMAND_PATCH_BEGIN, HFC_COMMAND_PATCH_END),
        (PLATFORM_NOTICE_PATCH_BEGIN, PLATFORM_NOTICE_PATCH_END),
        (SLASH_CONFIRM_PATCH_BEGIN, SLASH_CONFIRM_PATCH_END),
        (QUEUED_COMPLETE_PATCH_BEGIN, QUEUED_COMPLETE_PATCH_END),
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


def _apply_cron_patch(content: str) -> str:
    owned_block = _find_owned_cron_block(content)
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = _render_cron_hook_block(indent, newline)
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    tree = _parse_content(content)
    lines = content.splitlines(keepends=True)
    deliver_body = _find_cron_deliver_body_location(tree, lines)
    if deliver_body is None:
        return content

    newline = _detect_newline(content)
    insert_at, body_indent = deliver_body
    hook = _render_cron_hook_block(body_indent, newline)
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


def _remove_cron_patch(content: str) -> str:
    owned_block = _find_owned_cron_block(content)
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


def _find_cron_deliver_body_location(tree, lines):
    for node in tree.body:
        if _is_cron_deliver(node):
            return _body_location(node, lines)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if _is_cron_deliver(child):
                    return _body_location(child, lines)

    return None


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


def _is_cron_deliver(node) -> bool:
    return isinstance(node, ast.FunctionDef) and node.name == _CRON_DELIVER_NAME


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
    legacy = _render_hook_block(indent, newline, strategy="legacy_gateway_run")
    gateway_013_plus = _render_hook_block(
        indent, newline, strategy="gateway_run_013_plus"
    )
    legacy_without_commands = _render_hook_block_without_commands(
        indent, newline, strategy="legacy_gateway_run"
    )
    gateway_013_plus_without_commands = _render_hook_block_without_commands(
        indent, newline, strategy="gateway_run_013_plus"
    )
    placeholder = _render_placeholder_hook_block(indent, newline)
    legacy_silent = _with_silent_exception_handler(legacy, indent, newline)
    gateway_013_plus_silent = _with_silent_exception_handler(
        gateway_013_plus, indent, newline
    )
    legacy_without_commands_silent = _with_silent_exception_handler(
        legacy_without_commands, indent, newline
    )
    gateway_013_plus_without_commands_silent = _with_silent_exception_handler(
        gateway_013_plus_without_commands, indent, newline
    )
    placeholder_silent = _with_silent_exception_handler(placeholder, indent, newline)
    actual = lines[begin_index : end_index + 1]

    if actual not in (
        legacy,
        gateway_013_plus,
        legacy_without_commands,
        gateway_013_plus_without_commands,
        placeholder,
        legacy_silent,
        gateway_013_plus_silent,
        legacy_without_commands_silent,
        gateway_013_plus_without_commands_silent,
        placeholder_silent,
    ):
        raise ValueError("corrupt patch markers")

    tree = _parse_content_with_markers(content)
    if _has_no_final_newline_sentinel(lines, begin_index):
        _validate_no_final_newline_sentinel(lines, begin_index, end_index, tree)

    handler_body = _find_handler_body_location(tree, lines)
    if handler_body is None:
        raise ValueError("corrupt patch markers")

    first_body_index, _body_indent = handler_body
    expected_begin_index = (
        first_body_index - 2
        if actual
        in (
            gateway_013_plus,
            gateway_013_plus_silent,
            gateway_013_plus_without_commands,
            gateway_013_plus_without_commands_silent,
        )
        else first_body_index - 1
    )
    if begin_index != expected_begin_index:
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
    v400 = _render_v400_complete_hook_block(indent, newline)
    legacy = _render_legacy_complete_hook_block(indent, newline)
    previous_async = _render_previous_async_complete_hook_block(indent, newline)
    previous_async_without_platform = (
        _render_previous_async_complete_hook_block_without_platform_guard(indent, newline)
    )
    expected_silent = _with_silent_exception_handler(expected, indent, newline)
    v400_silent = _with_silent_exception_handler(v400, indent, newline)
    legacy_silent = _with_silent_exception_handler(legacy, indent, newline)
    previous_async_silent = _with_silent_exception_handler(
        previous_async, indent, newline
    )
    previous_async_without_platform_silent = _with_silent_exception_handler(
        previous_async_without_platform, indent, newline
    )
    actual = lines[begin_index : end_index + 1]
    if actual not in (
        expected,
        v400,
        legacy,
        previous_async,
        previous_async_without_platform,
        expected_silent,
        v400_silent,
        legacy_silent,
        previous_async_silent,
        previous_async_without_platform_silent,
    ):
        raise ValueError("corrupt completion patch markers")
    return begin_index, end_index


def _find_owned_cron_block(content: str):
    marker_block = _find_simple_marker_block(
        content,
        CRON_PATCH_BEGIN,
        CRON_PATCH_END,
        "cron patch markers",
    )
    if marker_block is None:
        return None

    lines = content.splitlines(keepends=True)
    begin_index, end_index = marker_block
    indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
    newline = _line_ending(lines[begin_index]) or _detect_newline(content)
    expected = _render_cron_hook_block(indent, newline)
    expected_silent = _with_silent_exception_handler(expected, indent, newline)
    actual = lines[begin_index : end_index + 1]
    if actual not in (expected, expected_silent):
        raise ValueError("corrupt cron patch markers")

    tree = _parse_content_with_markers(content)
    cron_deliver_body = _find_cron_deliver_body_location(tree, lines)
    if cron_deliver_body is None:
        raise ValueError("corrupt cron patch markers")
    first_body_index, _body_indent = cron_deliver_body
    if begin_index != first_body_index - 1:
        raise ValueError("corrupt cron patch markers")
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
    inner = _find_direct_run_agent_node(tree, "_run_agent_inner")
    if inner is not None:
        return inner
    return _find_direct_run_agent_node(tree, "_run_agent")


def _find_direct_run_agent_node(tree, name: str):
    for node in tree.body:
        if _is_run_agent(node, name):
            return node

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if _is_run_agent(child, name):
                    return child

    return None


def _find_async_function(tree, name: str):
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.AsyncFunctionDef) and child.name == name:
                    return child

    return None


def _find_gateway_runner_method(tree, name: str):
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "GatewayRunner":
            continue
        for child in node.body:
            if isinstance(child, ast.AsyncFunctionDef) and child.name == name:
                return child
    return None


def _find_recovered_watcher_drain(func):
    for node in func.body:
        if not isinstance(node, ast.Try):
            continue
        has_pending_watchers = any(
            isinstance(child, ast.Attribute)
            and child.attr == "pending_watchers"
            and isinstance(child.value, ast.Name)
            and child.value.id == "process_registry"
            for child in ast.walk(node)
        )
        has_watcher_call = any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "_run_process_watcher"
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "self"
            for child in ast.walk(node)
        )
        if has_pending_watchers and has_watcher_call:
            return node
    return None


def _is_run_agent(node, name: str = "_run_agent") -> bool:
    return isinstance(node, ast.AsyncFunctionDef) and node.name == name


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
    expected_blocks = [expected]
    if renderer is _render_command_card_adapter_hook_block:
        expected_blocks.append(_render_legacy_command_card_adapter_hook_block(indent, newline))
    actual = lines[begin_index : end_index + 1]
    if actual not in expected_blocks:
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


def _render_hook_exception_handler(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}except Exception as _hfc_exc:{newline}",
        f"{inner_indent}try:{newline}",
        f"{deeper_indent}import sys as _hfc_sys{newline}",
        (
            f"{deeper_indent}print(\"[hermes-feishu-card] hook failed: \" "
            f"+ _hfc_exc.__class__.__name__ + \": \" + str(_hfc_exc), "
            f"file=_hfc_sys.stderr){newline}"
        ),
        f"{inner_indent}except Exception:{newline}",
        f"{deeper_indent}pass{newline}",
    ]


def _render_silent_exception_handler(indent: str, newline: str):
    return [
        f"{indent}except Exception:{newline}",
        f"{_child_indent(indent)}pass{newline}",
    ]


def _with_silent_exception_handler(block: list[str], indent: str, newline: str):
    diagnostic = _render_hook_exception_handler(indent, newline)
    silent = _render_silent_exception_handler(indent, newline)
    result: list[str] = []
    index = 0
    while index < len(block):
        if block[index : index + len(diagnostic)] == diagnostic:
            result.extend(silent)
            index += len(diagnostic)
        else:
            result.append(block[index])
            index += 1
    return result


def _render_hook_block_without_commands(
    indent: str, newline: str, strategy: str = "legacy_gateway_run"
):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    block = [
        f"{indent}{PATCH_BEGIN}{newline}",
    ]
    if strategy == "gateway_run_013_plus":
        block.extend(
            [
                f"{indent}# HERMES_FEISHU_CARD_STRATEGY gateway_run_013_plus{newline}",
                f"{indent}try:{newline}",
                (
                    f"{inner_indent}from hermes_feishu_card.hook_runtime "
                    f"import emit_from_hermes_locals as _hfc_emit{newline}"
                ),
                f"{inner_indent}_hfc_started_message_id = None{newline}",
                f"{inner_indent}try:{newline}",
                f"{deeper_indent}_hfc_started_message_id = self._reply_anchor_for_event(event){newline}",
                f"{inner_indent}except Exception:{newline}",
                f"{deeper_indent}_hfc_started_message_id = getattr(event, \"message_id\", None){newline}",
                f"{inner_indent}_hfc_emit({{**locals(), \"message_id\": _hfc_started_message_id}}){newline}",
            ]
        )
    else:
        block.extend(
            [
                f"{indent}try:{newline}",
                (
                    f"{inner_indent}from hermes_feishu_card.hook_runtime "
                    f"import emit_from_hermes_locals as _hfc_emit{newline}"
                ),
                f"{inner_indent}_hfc_emit(locals()){newline}",
            ]
        )
    block.extend(_render_hook_exception_handler(indent, newline))
    block.append(f"{indent}{PATCH_END}{newline}")
    return block


def _render_hook_block(indent: str, newline: str, strategy: str = "legacy_gateway_run"):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    block = [
        f"{indent}{PATCH_BEGIN}{newline}",
    ]
    if strategy == "gateway_run_013_plus":
        block.extend(
            [
                f"{indent}# HERMES_FEISHU_CARD_STRATEGY gateway_run_013_plus{newline}",
                f"{indent}try:{newline}",
                (
                    f"{inner_indent}from hermes_feishu_card.hook_runtime "
                    f"import emit_from_hermes_locals as _hfc_emit{newline}"
                ),
                (
                    f"{inner_indent}from hermes_feishu_card.hook_runtime "
                    f"import handle_hfc_command_from_hermes_locals as _hfc_handle_command{newline}"
                ),
                f"{inner_indent}_hfc_started_message_id = None{newline}",
                f"{inner_indent}try:{newline}",
                f"{deeper_indent}_hfc_started_message_id = self._reply_anchor_for_event(event){newline}",
                f"{inner_indent}except Exception:{newline}",
                f"{deeper_indent}_hfc_started_message_id = getattr(event, \"message_id\", None){newline}",
                f"{inner_indent}if _hfc_handle_command({{**locals(), \"message_id\": _hfc_started_message_id}}):{newline}",
                f"{deeper_indent}return None{newline}",
                f"{inner_indent}_hfc_emit({{**locals(), \"message_id\": _hfc_started_message_id}}){newline}",
            ]
        )
    else:
        block.extend(
            [
                f"{indent}try:{newline}",
                (
                    f"{inner_indent}from hermes_feishu_card.hook_runtime "
                    f"import emit_from_hermes_locals as _hfc_emit{newline}"
                ),
                (
                    f"{inner_indent}from hermes_feishu_card.hook_runtime "
                    f"import handle_hfc_command_from_hermes_locals as _hfc_handle_command{newline}"
                ),
                f"{inner_indent}if _hfc_handle_command(locals()):{newline}",
                f"{deeper_indent}return None{newline}",
                f"{inner_indent}_hfc_emit(locals()){newline}",
            ]
        )
    block.extend(_render_hook_exception_handler(indent, newline))
    block.append(f"{indent}{PATCH_END}{newline}")
    return block


def _render_complete_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{COMPLETE_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import build_event as _hfc_build_event{newline}"
        ),
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import emit_from_hermes_locals_async as _hfc_emit_async{newline}"
        ),
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import should_suppress_native_response as _hfc_should_suppress{newline}"
        ),
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import native_media_only_response as _hfc_media_only{newline}"
        ),
        f"{inner_indent}_hfc_completed_locals = {{{newline}",
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
        f"{inner_indent}}}{newline}",
        f"{inner_indent}_hfc_completed_event = _hfc_build_event(\"message.completed\", _hfc_completed_locals, preview=True){newline}",
        f"{inner_indent}_hfc_attachments = []{newline}",
        f"{inner_indent}_hfc_native_delivery = \"allowed\"{newline}",
        f"{inner_indent}if _hfc_completed_event is not None:{newline}",
        f"{deeper_indent}_hfc_completed_data = _hfc_completed_event.get(\"data\", {{}}){newline}",
        f"{deeper_indent}_hfc_attachments = _hfc_completed_data.get(\"attachments\", []){newline}",
        f"{deeper_indent}_hfc_native_delivery = _hfc_completed_data.get(\"native_delivery\", \"required\" if _hfc_attachments else \"allowed\"){newline}",
        f"{inner_indent}_hfc_card_delivered = await _hfc_emit_async(_hfc_completed_locals, event_name=\"message.completed\"){newline}",
        f"{inner_indent}_hfc_platform = getattr(source.platform, \"value\", source.platform){newline}",
        f"{inner_indent}if str(_hfc_platform).lower() == \"feishu\" and _hfc_card_delivered and _hfc_native_delivery == \"required\":{newline}",
        f"{deeper_indent}response = _hfc_media_only(response){newline}",
        f"{inner_indent}if _hfc_should_suppress(_hfc_platform, _hfc_card_delivered, _hfc_attachments, _hfc_native_delivery):{newline}",
        f"{deeper_indent}return None{newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{COMPLETE_PATCH_END}{newline}",
    ]


def _render_v400_complete_hook_block(indent: str, newline: str):
    return [
        line
        for line in _render_complete_hook_block(indent, newline)
        if "native_media_only_response as _hfc_media_only" not in line
        and '_hfc_native_delivery == "required"' not in line
        and "response = _hfc_media_only(response)" not in line
    ]


def _render_queued_complete_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{QUEUED_COMPLETE_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import build_event as _hfc_build_event{newline}"
        ),
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import emit_from_hermes_locals_async as _hfc_emit_async{newline}"
        ),
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import should_suppress_native_response as _hfc_should_suppress{newline}"
        ),
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import native_media_only_response as _hfc_media_only{newline}"
        ),
        f"{inner_indent}if first_response and not _already_streamed:{newline}",
        f"{deeper_indent}_hfc_completed_locals = {{{newline}",
        f"{deeper_indent}    **locals(),{newline}",
        f"{deeper_indent}    \"answer\": first_response,{newline}",
        f"{deeper_indent}    \"duration\": result.get(\"duration\", 0.0) if isinstance(result, dict) else 0.0,{newline}",
        f"{deeper_indent}    \"model\": result.get(\"model\", \"\") if isinstance(result, dict) else \"\",{newline}",
        f"{deeper_indent}    \"tokens\": {{{newline}",
        f"{deeper_indent}        \"input_tokens\": result.get(\"input_tokens\", 0) if isinstance(result, dict) else 0,{newline}",
        f"{deeper_indent}        \"output_tokens\": result.get(\"output_tokens\", 0) if isinstance(result, dict) else 0,{newline}",
        f"{deeper_indent}    }},{newline}",
        f"{deeper_indent}    \"context\": {{{newline}",
        f"{deeper_indent}        \"used_tokens\": result.get(\"last_prompt_tokens\", 0) if isinstance(result, dict) else 0,{newline}",
        f"{deeper_indent}        \"max_tokens\": result.get(\"context_length\", 0) if isinstance(result, dict) else 0,{newline}",
        f"{deeper_indent}    }},{newline}",
        f"{deeper_indent}}}{newline}",
        f"{deeper_indent}_hfc_completed_event = _hfc_build_event(\"message.completed\", _hfc_completed_locals, preview=True){newline}",
        f"{deeper_indent}_hfc_attachments = []{newline}",
        f"{deeper_indent}_hfc_native_delivery = \"allowed\"{newline}",
        f"{deeper_indent}if _hfc_completed_event is not None:{newline}",
        f"{deeper_indent}    _hfc_completed_data = _hfc_completed_event.get(\"data\", {{}}){newline}",
        f"{deeper_indent}    _hfc_attachments = _hfc_completed_data.get(\"attachments\", []){newline}",
        f"{deeper_indent}    _hfc_native_delivery = _hfc_completed_data.get(\"native_delivery\", \"required\" if _hfc_attachments else \"allowed\"){newline}",
        f"{deeper_indent}_hfc_card_delivered = await _hfc_emit_async(_hfc_completed_locals, event_name=\"message.completed\"){newline}",
        f"{deeper_indent}_hfc_platform = getattr(source.platform, \"value\", source.platform){newline}",
        f"{deeper_indent}if str(_hfc_platform).lower() == \"feishu\" and _hfc_card_delivered and _hfc_native_delivery == \"required\":{newline}",
        f"{deeper_indent}    first_response = _hfc_media_only(first_response){newline}",
        f"{deeper_indent}if _hfc_should_suppress(_hfc_platform, _hfc_card_delivered, _hfc_attachments, _hfc_native_delivery):{newline}",
        f"{deeper_indent}    _already_streamed = True{newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{QUEUED_COMPLETE_PATCH_END}{newline}",
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
        *_render_hook_exception_handler(indent, newline),
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
        f"{inner_indent}if _hfc_card_delivered and source.platform.value == \"feishu\":{newline}",
        f"{deeper_indent}return None{newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{COMPLETE_PATCH_END}{newline}",
    ]


def _render_previous_async_complete_hook_block_without_platform_guard(
    indent: str, newline: str
):
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
        *_render_hook_exception_handler(indent, newline),
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
        *_render_hook_exception_handler(indent, newline),
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
        *_render_hook_exception_handler(indent, newline),
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
        f"{deeper_indent}    \"mode\": \"append_block\",{newline}",
        f"{deeper_indent}}}, event_name=\"thinking.delta\"):{newline}",
        f"{deeper_indent}    return{newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{THINKING_DELTA_PATCH_END}{newline}",
    ]


def _render_clarify_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{CLARIFY_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import request_clarify_response_from_hermes_locals as _hfc_request_clarify{newline}"
        ),
        f"{inner_indent}from uuid import uuid4 as _hfc_uuid4{newline}",
        f"{inner_indent}if choices and _run_still_current():{newline}",
        f"{deeper_indent}_hfc_clarify_response = _hfc_request_clarify({{{newline}",
        f"{deeper_indent}    **locals(),{newline}",
        f"{deeper_indent}    \"source\": source,{newline}",
        f"{deeper_indent}    \"chat_id\": _status_chat_id,{newline}",
        f"{deeper_indent}    \"conversation_id\": session_key or _status_chat_id,{newline}",
        f"{deeper_indent}    \"message_id\": event_message_id,{newline}",
        f"{deeper_indent}    \"_hfc_loop\": locals().get(\"_loop_for_step\"),{newline}",
        f"{deeper_indent}    \"kind\": \"clarify\",{newline}",
        f"{deeper_indent}}}, interaction_id=\"clarify_\" + _hfc_uuid4().hex[:10], question=question, choices=choices){newline}",
        f"{deeper_indent}if _hfc_clarify_response is not None:{newline}",
        f"{deeper_indent}    return _hfc_clarify_response{newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{CLARIFY_PATCH_END}{newline}",
    ]


def _render_approval_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{APPROVAL_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import request_approval_choice_from_hermes_locals as _hfc_request_approval{newline}"
        ),
        f"{inner_indent}from uuid import uuid4 as _hfc_uuid4{newline}",
        f"{inner_indent}if _run_still_current():{newline}",
        f"{deeper_indent}_hfc_approval_choice = _hfc_request_approval({{{newline}",
        f"{deeper_indent}    **locals(),{newline}",
        f"{deeper_indent}    \"source\": source,{newline}",
        f"{deeper_indent}    \"chat_id\": _status_chat_id,{newline}",
        f"{deeper_indent}    \"conversation_id\": _approval_session_key or _status_chat_id,{newline}",
        f"{deeper_indent}    \"message_id\": event_message_id,{newline}",
        f"{deeper_indent}    \"_hfc_loop\": locals().get(\"_loop_for_step\"),{newline}",
        f"{deeper_indent}}}, approval_data, interaction_id=\"approval_\" + _hfc_uuid4().hex[:10]){newline}",
        f"{deeper_indent}if _hfc_approval_choice:{newline}",
        f"{deeper_indent}    from tools.approval import resolve_gateway_approval as _hfc_resolve_gateway_approval{newline}",
        f"{deeper_indent}    _hfc_resolve_gateway_approval(_approval_session_key, _hfc_approval_choice){newline}",
        f"{deeper_indent}    return{newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{APPROVAL_PATCH_END}{newline}",
    ]


def _render_slash_confirm_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{SLASH_CONFIRM_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import request_slash_confirm_from_hermes_locals_async as _hfc_request_slash_confirm{newline}"
        ),
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import complete_command_card_from_hermes_locals_async as _hfc_complete_command_card{newline}"
        ),
        f"{inner_indent}from hashlib import sha256 as _hfc_sha256{newline}",
        f"{inner_indent}_hfc_slash_reply_to = None{newline}",
        f"{inner_indent}try:{newline}",
        f"{deeper_indent}_hfc_slash_reply_to = self._reply_anchor_for_event(event){newline}",
        f"{inner_indent}except Exception:{newline}",
        f"{deeper_indent}_hfc_slash_reply_to = getattr(event, \"message_id\", None){newline}",
        (
            f"{inner_indent}_hfc_slash_interaction_seed = "
            f"(str(session_key) + \":\" + str(confirm_id)).encode(\"utf-8\")"
            f"{newline}"
        ),
        (
            f"{inner_indent}_hfc_slash_interaction_id = \"slash_\" "
            f"+ _hfc_sha256(_hfc_slash_interaction_seed).hexdigest()[:16]{newline}"
        ),
        f"{inner_indent}_hfc_slash_choice = await _hfc_request_slash_confirm({{{newline}",
        f"{inner_indent}    **locals(),{newline}",
        f"{inner_indent}    \"source\": source,{newline}",
        f"{inner_indent}    \"chat_id\": getattr(source, \"chat_id\", \"\"),{newline}",
        f"{inner_indent}    \"conversation_id\": session_key,{newline}",
        f"{inner_indent}    \"message_id\": _hfc_slash_reply_to,{newline}",
        f"{inner_indent}    \"reply_to_message_id\": _hfc_slash_reply_to,{newline}",
        f"{inner_indent}}}, command=command, title=title, message=message, interaction_id=_hfc_slash_interaction_id){newline}",
        f"{inner_indent}if _hfc_slash_choice in {{\"once\", \"always\", \"cancel\"}}:{newline}",
        f"{deeper_indent}_hfc_slash_result = await handler(_hfc_slash_choice){newline}",
        f"{deeper_indent}if await _hfc_complete_command_card({{{newline}",
        f"{deeper_indent}    \"source\": source,{newline}",
        f"{deeper_indent}    \"chat_id\": getattr(source, \"chat_id\", \"\"),{newline}",
        f"{deeper_indent}    \"conversation_id\": session_key,{newline}",
        f"{deeper_indent}    \"message_id\": _hfc_slash_reply_to,{newline}",
        f"{deeper_indent}}}, answer=_hfc_slash_result):{newline}",
        f"{deeper_indent}    return None{newline}",
        f"{deeper_indent}return _hfc_slash_result{newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{SLASH_CONFIRM_PATCH_END}{newline}",
    ]


def _render_command_card_adapter_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    return [
        f"{indent}{COMMAND_CARD_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import install_feishu_command_card_adapter_methods as _hfc_install_command_cards{newline}"
        ),
        f"{inner_indent}_hfc_install_command_cards(self, event=event){newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{COMMAND_CARD_PATCH_END}{newline}",
    ]


def _render_command_card_startup_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    return [
        f"{indent}{COMMAND_CARD_STARTUP_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import install_feishu_command_card_adapter_methods as _hfc_install_command_cards{newline}"
        ),
        f"{inner_indent}_hfc_install_command_cards(self){newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{COMMAND_CARD_STARTUP_PATCH_END}{newline}",
    ]


def _render_legacy_command_card_adapter_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    return [
        f"{indent}{COMMAND_CARD_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import install_feishu_command_card_adapter_methods as _hfc_install_command_cards{newline}"
        ),
        f"{inner_indent}_hfc_install_command_cards(self){newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{COMMAND_CARD_PATCH_END}{newline}",
    ]


def _render_platform_notice_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{PLATFORM_NOTICE_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import handle_platform_notice_from_hermes as _hfc_handle_platform_notice{newline}"
        ),
        f"{inner_indent}if _hfc_handle_platform_notice(self, source, content):{newline}",
        f"{deeper_indent}return None{newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{PLATFORM_NOTICE_PATCH_END}{newline}",
    ]


def _render_hfc_command_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{HFC_COMMAND_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import handle_hfc_command_from_hermes_locals as _hfc_handle_command{newline}"
        ),
        f"{inner_indent}_hfc_command_message_id = None{newline}",
        f"{inner_indent}try:{newline}",
        f"{deeper_indent}_hfc_command_message_id = self._reply_anchor_for_event(event){newline}",
        f"{inner_indent}except Exception:{newline}",
        f"{deeper_indent}_hfc_command_message_id = getattr(event, \"message_id\", None){newline}",
        f"{inner_indent}if _hfc_handle_command({{**locals(), \"message_id\": _hfc_command_message_id}}):{newline}",
        f"{deeper_indent}return None{newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{HFC_COMMAND_PATCH_END}{newline}",
    ]


def _render_cron_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    return [
        f"{indent}{CRON_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import emit_cron_delivery as _hfc_emit_cron{newline}"
        ),
        f"{inner_indent}_hfc_cron_metadata = {{\"delivery_kind\": \"cron\"}}{newline}",
        f"{inner_indent}# Pre-resolve targets so build_cron_event can discover feishu chat_id{newline}",
        f"{inner_indent}_hfc_resolve_targets = locals().get(\"_resolve_delivery_targets\") or globals().get(\"_resolve_delivery_targets\"){newline}",
        f"{inner_indent}if callable(_hfc_resolve_targets):{newline}",
        f"{_child_indent(inner_indent)}try:{newline}",
        f"{_child_indent(_child_indent(inner_indent))}job[\"_hfc_resolved_targets\"] = _hfc_resolve_targets(job){newline}",
        f"{_child_indent(inner_indent)}except Exception:{newline}",
        f"{_child_indent(_child_indent(inner_indent))}pass{newline}",
        f"{inner_indent}if _hfc_emit_cron(locals()):{newline}",
        f"{_child_indent(inner_indent)}return None{newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{CRON_PATCH_END}{newline}",
    ]


def _render_placeholder_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    return [
        f"{indent}{PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        f"{inner_indent}pass{newline}",
        *_render_hook_exception_handler(indent, newline),
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
