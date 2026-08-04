import ast


PATCH_BEGIN = "# HERMES_FEISHU_CARD_PATCH_BEGIN"
PATCH_END = "# HERMES_FEISHU_CARD_PATCH_END"
COMPLETE_PATCH_BEGIN = "# HERMES_FEISHU_CARD_COMPLETE_PATCH_BEGIN"
COMPLETE_PATCH_END = "# HERMES_FEISHU_CARD_COMPLETE_PATCH_END"
QUEUED_COMPLETE_PATCH_BEGIN = "# HERMES_FEISHU_CARD_QUEUED_COMPLETE_PATCH_BEGIN"
QUEUED_COMPLETE_PATCH_END = "# HERMES_FEISHU_CARD_QUEUED_COMPLETE_PATCH_END"
TOOL_PATCH_BEGIN = "# HERMES_FEISHU_CARD_TOOL_PATCH_BEGIN"
TOOL_PATCH_END = "# HERMES_FEISHU_CARD_TOOL_PATCH_END"
STABLE_TOOL_PATCH_BEGIN = "# HERMES_FEISHU_CARD_STABLE_TOOL_PATCH_BEGIN"
STABLE_TOOL_PATCH_END = "# HERMES_FEISHU_CARD_STABLE_TOOL_PATCH_END"
ANSWER_DELTA_PATCH_BEGIN = "# HERMES_FEISHU_CARD_ANSWER_DELTA_PATCH_BEGIN"
ANSWER_DELTA_PATCH_END = "# HERMES_FEISHU_CARD_ANSWER_DELTA_PATCH_END"
THINKING_DELTA_PATCH_BEGIN = "# HERMES_FEISHU_CARD_THINKING_DELTA_PATCH_BEGIN"
THINKING_DELTA_PATCH_END = "# HERMES_FEISHU_CARD_THINKING_DELTA_PATCH_END"
CLARIFY_PATCH_BEGIN = "# HERMES_FEISHU_CARD_CLARIFY_PATCH_BEGIN"
CLARIFY_PATCH_END = "# HERMES_FEISHU_CARD_CLARIFY_PATCH_END"
APPROVAL_PATCH_BEGIN = "# HERMES_FEISHU_CARD_APPROVAL_PATCH_BEGIN"
APPROVAL_PATCH_END = "# HERMES_FEISHU_CARD_APPROVAL_PATCH_END"
STATUS_PATCH_BEGIN = "# HERMES_FEISHU_CARD_STATUS_PATCH_BEGIN"
STATUS_PATCH_END = "# HERMES_FEISHU_CARD_STATUS_PATCH_END"
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
NATIVE_REDELIVERY_PATCH_BEGIN = "# HERMES_FEISHU_CARD_NATIVE_REDELIVERY_PATCH_BEGIN"
NATIVE_REDELIVERY_PATCH_END = "# HERMES_FEISHU_CARD_NATIVE_REDELIVERY_PATCH_END"
PLATFORM_NOTICE_PATCH_BEGIN = "# HERMES_FEISHU_CARD_PLATFORM_NOTICE_PATCH_BEGIN"
PLATFORM_NOTICE_PATCH_END = "# HERMES_FEISHU_CARD_PLATFORM_NOTICE_PATCH_END"
HFC_COMMAND_PATCH_BEGIN = "# HERMES_FEISHU_CARD_HFC_COMMAND_PATCH_BEGIN"
HFC_COMMAND_PATCH_END = "# HERMES_FEISHU_CARD_HFC_COMMAND_PATCH_END"
EXACT_BASE_NO_TEXT_PATCH_BEGIN = (
    "# HERMES_FEISHU_CARD_EXACT_BASE_NO_TEXT_PATCH_BEGIN"
)
EXACT_BASE_NO_TEXT_PATCH_END = "# HERMES_FEISHU_CARD_EXACT_BASE_NO_TEXT_PATCH_END"
EXACT_BASE_FINAL_DELIVERY_PATCH_BEGIN = (
    "# HERMES_FEISHU_CARD_EXACT_BASE_FINAL_DELIVERY_PATCH_BEGIN"
)
EXACT_BASE_FINAL_DELIVERY_PATCH_END = (
    "# HERMES_FEISHU_CARD_EXACT_BASE_FINAL_DELIVERY_PATCH_END"
)

_HANDLER_NAME = "_handle_message_with_agent"
_CRON_DELIVER_NAME = "_deliver_result"
_NO_FINAL_NEWLINE = "# HERMES_FEISHU_CARD_NO_FINAL_NEWLINE"
_SUPPORTED_STRATEGIES = {"legacy_gateway_run", "gateway_run_013_plus"}


def apply_patch(content: str, strategy: str = "legacy_gateway_run") -> str:
    """Insert the Feishu card hook block into a safe Hermes message handler."""
    if strategy not in _SUPPORTED_STRATEGIES:
        raise ValueError(f"unsupported patch strategy: {strategy}")
    content = _apply_start_patch(content, strategy=strategy)
    content = _apply_complete_patch(content, strategy=strategy)
    content = _apply_queued_complete_patch(content)
    if strategy == "gateway_run_013_plus":
        content = _apply_cron_patch(content)
        content = _apply_command_card_startup_patch(content)
        content = _apply_native_redelivery_patch(content)
        content = _apply_command_card_adapter_patch(content)
        content = _apply_hfc_command_patch(content)
        content = _apply_platform_notice_patch(content)
        content = _apply_slash_confirm_patch(content)
    content = _apply_stable_tool_lifecycle_patch(content)
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
        required_callback_calls=(("_stream_consumer", "on_delta"),),
        allow_turn_context=True,
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
        allow_turn_context=True,
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
        allow_turn_context=True,
    )
    content = _apply_callback_patch(
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
        allow_turn_context=True,
    )
    if strategy == "gateway_run_013_plus":
        content = _apply_callback_patch(
            content,
            callback_name="_status_callback_sync",
            begin_marker=STATUS_PATCH_BEGIN,
            end_marker=STATUS_PATCH_END,
            renderer=_render_status_hook_block,
            required_outer_names=(
                "source",
                "event_message_id",
                "_status_chat_id",
                "_loop_for_step",
                "_run_still_current",
            ),
            required_callback_args=("event_type", "message"),
            allow_turn_context=True,
        )
    return content


def apply_cron_patch(content: str) -> str:
    """Insert the Feishu card cron hook into a safe Hermes cron delivery function."""
    return _apply_cron_patch(content)


def apply_base_patch(content: str) -> str:
    """Patch Hermes' exact final-delivery pipeline without reimplementing it.

    This entry point is intentionally separate from :func:`apply_patch`: it
    operates on ``gateway/platforms/base.py``, while ``apply_patch`` owns
    ``gateway/run.py``.  The structural contract is strict because placing
    either hook on the wrong side of the delivery ledger can create a crash
    window or acknowledge content that Hermes never attempted to send.
    """
    owned = _find_owned_exact_base_blocks(content, strict=True)
    tree = _parse_exact_base_content(content)
    lines = content.splitlines(keepends=True)
    no_text_location, final_location = _find_exact_base_patch_locations(tree, lines)

    if owned is not None:
        _validate_exact_base_owned_locations(
            owned,
            no_text_location=no_text_location,
            final_location=final_location,
        )
        return content

    newline = _detect_newline(content)
    no_text_index, no_text_indent = no_text_location
    final_index, final_indent = final_location
    no_text_hook = _render_exact_base_no_text_hook_block(no_text_indent, newline)
    final_hook = _render_exact_base_final_delivery_hook_block(final_indent, newline)

    # Insert bottom-up so the earlier location is not shifted by the later
    # block. Both anchors are guaranteed to belong to the same exact pipeline.
    lines = lines[:final_index] + final_hook + lines[final_index:]
    lines = lines[:no_text_index] + no_text_hook + lines[no_text_index:]
    return "".join(lines)


def remove_base_patch(content: str) -> str:
    """Strictly remove exact BasePlatformAdapter hooks owned by this project."""
    owned = _find_owned_exact_base_blocks(content, strict=True)
    if owned is None:
        return content

    tree = _parse_exact_base_content(content)
    lines = content.splitlines(keepends=True)
    no_text_location, final_location = _find_exact_base_patch_locations(tree, lines)
    _validate_exact_base_owned_locations(
        owned,
        no_text_location=no_text_location,
        final_location=final_location,
    )
    return _remove_exact_base_blocks(content, owned)


def remove_base_patch_lenient(content: str) -> str:
    """Remove owned Base hooks while accepting older generated block bodies."""
    owned = _find_owned_exact_base_blocks(content, strict=False)
    if owned is None:
        return content
    return _remove_exact_base_blocks(content, owned)


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


def _apply_complete_patch(content: str, *, strategy: str = "legacy_gateway_run") -> str:
    renderer = (
        _render_complete_hook_block_with_reply_anchor
        if strategy == "gateway_run_013_plus"
        else _render_complete_hook_block
    )

    owned_block = _find_owned_complete_block(content)
    if owned_block is not None:
        # Re-apply from a clean slate so a recognised block migrates to the
        # current expected location (for example, from after an
        # `already_sent` early return to before it) and to the current
        # rendering in one pass.
        stripped = _remove_complete_patch(content)
        if stripped != content:
            return _apply_complete_patch(stripped, strategy=strategy)
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
    completion_location = _find_completion_return_location(tree, lines)
    if completion_location is None:
        return content

    newline = _detect_newline(content)
    insert_at, body_indent = completion_location
    hook = renderer(body_indent, newline)
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
        # `first_response = result.get(...)` no longer sits on the immediately
        # preceding line in newer Hermes (a multi-line call to
        # _stream_confirmed_final_delivery is interleaved), so scan a short
        # window above the anchor instead of only lines[index - 1].
        lookback = lines[max(0, index - 12) : index]
        if not any("first_response = result.get(" in item for item in lookback):
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
        stripped = _remove_simple_owned_patch(
            content,
            COMMAND_CARD_STARTUP_PATCH_BEGIN,
            COMMAND_CARD_STARTUP_PATCH_END,
            _render_command_card_startup_hook_block,
            "command card startup patch markers",
        )
        if stripped != content:
            return _apply_command_card_startup_patch(stripped)

    tree = _parse_content(content)
    func = _find_gateway_runner_method(tree, "start")
    if func is None:
        return content
    anchor = _find_redelivery_startup_call(func) or _find_recovered_watcher_drain(func)
    if anchor is None or anchor.lineno is None:
        return content

    lines = content.splitlines(keepends=True)
    insert_at = anchor.lineno - 1
    indent = _line_indent(lines, insert_at)
    newline = _line_ending(lines[insert_at]) or _detect_newline(content)
    hook = _render_command_card_startup_hook_block(indent, newline)
    return "".join(lines[:insert_at] + hook + lines[insert_at:])


def _apply_native_redelivery_patch(content: str) -> str:
    owned_block = _find_simple_marker_block(
        content,
        NATIVE_REDELIVERY_PATCH_BEGIN,
        NATIVE_REDELIVERY_PATCH_END,
        "native redelivery patch markers",
    )
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = _render_native_redelivery_hook_block(indent, newline)
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    tree = _parse_content(content)
    func = _find_gateway_runner_method(tree, "_redeliver_pending_obligations")
    if func is None:
        return content
    send = _find_redelivery_adapter_send(func)
    if send is None or send.lineno is None:
        return content
    lines = content.splitlines(keepends=True)
    insert_at = send.lineno - 1
    indent = _line_indent(lines, insert_at)
    newline = _line_ending(lines[insert_at]) or _detect_newline(content)
    hook = _render_native_redelivery_hook_block(indent, newline)
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
        NATIVE_REDELIVERY_PATCH_BEGIN,
        NATIVE_REDELIVERY_PATCH_END,
        _render_native_redelivery_hook_block,
        "native redelivery patch markers",
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
        STABLE_TOOL_PATCH_BEGIN,
        STABLE_TOOL_PATCH_END,
        _render_stable_tool_lifecycle_hook_block,
        "stable tool lifecycle patch markers",
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
        STATUS_PATCH_BEGIN,
        STATUS_PATCH_END,
        _render_status_hook_block,
        "status callback patch markers",
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
        (STABLE_TOOL_PATCH_BEGIN, STABLE_TOOL_PATCH_END),
        (TOOL_PATCH_BEGIN, TOOL_PATCH_END),
        (ANSWER_DELTA_PATCH_BEGIN, ANSWER_DELTA_PATCH_END),
        (THINKING_DELTA_PATCH_BEGIN, THINKING_DELTA_PATCH_END),
        (CLARIFY_PATCH_BEGIN, CLARIFY_PATCH_END),
        (APPROVAL_PATCH_BEGIN, APPROVAL_PATCH_END),
        (STATUS_PATCH_BEGIN, STATUS_PATCH_END),
        (COMMAND_CARD_STARTUP_PATCH_BEGIN, COMMAND_CARD_STARTUP_PATCH_END),
        (NATIVE_REDELIVERY_PATCH_BEGIN, NATIVE_REDELIVERY_PATCH_END),
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
    required_callback_calls=(),
    allow_turn_context=False,
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
        if required_callback_calls:
            # Hermes may define the same local callback name for multiple
            # mutually-exclusive transports. Rebuild selector-sensitive
            # blocks from the unpatched source so upgrades can relocate an
            # older hook that landed in the wrong callback.
            content = "".join(lines[:begin_index] + lines[end_index + 1 :])
            return _apply_callback_patch(
                content,
                callback_name=callback_name,
                begin_marker=begin_marker,
                end_marker=end_marker,
                renderer=renderer,
                required_outer_names=required_outer_names,
                required_callback_args=required_callback_args,
                required_callback_calls=required_callback_calls,
                allow_turn_context=allow_turn_context,
            )
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = (
            _render_turn_context_hook_block(renderer, indent, newline)
            if allow_turn_context
            and any("_hfc_turn_ctx = ctx" in line for line in lines[begin_index : end_index + 1])
            else renderer(indent, newline)
        )
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
        required_callback_calls=required_callback_calls,
    )
    use_turn_context = False
    if callback_body is None and allow_turn_context:
        callback_body = _find_turn_runner_callback_body_location(
            tree,
            lines,
            callback_name,
            required_callback_args=required_callback_args,
            required_callback_calls=required_callback_calls,
        )
        use_turn_context = callback_body is not None
    if callback_body is None:
        return content

    newline = _detect_newline(content)
    insert_at, body_indent = callback_body
    hook = (
        _render_turn_context_hook_block(renderer, body_indent, newline)
        if use_turn_context
        else renderer(body_indent, newline)
    )
    return "".join(lines[:insert_at] + hook + lines[insert_at:])


def _apply_stable_tool_lifecycle_patch(content: str) -> str:
    owned_block = _find_simple_marker_block(
        content,
        STABLE_TOOL_PATCH_BEGIN,
        STABLE_TOOL_PATCH_END,
        "stable tool lifecycle patch markers",
    )
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        expected = (
            _render_turn_context_hook_block(
                _render_stable_tool_lifecycle_hook_block, indent, newline
            )
            if any("_hfc_turn_ctx = ctx" in line for line in lines[begin_index : end_index + 1])
            else _render_stable_tool_lifecycle_hook_block(indent, newline)
        )
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    tree = _parse_content(content)
    lines = content.splitlines(keepends=True)
    location = _find_stable_tool_lifecycle_location(tree, lines)
    use_turn_context = False
    if location is None:
        location = _find_turn_runner_stable_tool_lifecycle_location(tree, lines)
        use_turn_context = location is not None
    if location is None:
        return content
    insert_at, indent = location
    newline = _detect_newline(content)
    hook = (
        _render_turn_context_hook_block(
            _render_stable_tool_lifecycle_hook_block, indent, newline
        )
        if use_turn_context
        else _render_stable_tool_lifecycle_hook_block(indent, newline)
    )
    return "".join(lines[:insert_at] + hook + lines[insert_at:])


def _apply_cron_patch(content: str) -> str:
    owned_block = _find_owned_cron_block(content)
    if owned_block is not None:
        lines = content.splitlines(keepends=True)
        begin_index, end_index, media_aware = owned_block
        indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
        newline = _line_ending(lines[begin_index]) or _detect_newline(content)
        tree = _parse_content_with_markers(content)
        desired_media_aware = _find_cron_media_delivery_location(tree, lines) is not None
        if media_aware != desired_media_aware:
            unpatched = "".join(lines[:begin_index] + lines[end_index + 1 :])
            return _apply_cron_patch(unpatched)
        expected = _render_cron_hook_block(
            indent,
            newline,
            media_aware=media_aware,
        )
        if lines[begin_index : end_index + 1] == expected:
            return content
        return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])

    tree = _parse_content(content)
    lines = content.splitlines(keepends=True)
    media_delivery = _find_cron_media_delivery_location(tree, lines)
    location = media_delivery or _find_cron_deliver_body_location(tree, lines)
    if location is None:
        return content

    newline = _detect_newline(content)
    insert_at, body_indent = location
    hook = _render_cron_hook_block(
        body_indent,
        newline,
        media_aware=media_delivery is not None,
    )
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
    begin_index, end_index, _media_aware = owned_block
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

    already_sent_location = _find_already_sent_early_return_location(handler, lines)
    if already_sent_location is not None:
        return already_sent_location

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


def _find_already_sent_early_return_location(handler, lines):
    """Locate the streaming `already_sent` early-return branch, if present.

    Hermes 0.18.x returns None from the handler before the final
    `return response` when gateway streaming already delivered the text
    (``if agent_result.get("already_sent") and not agent_result.get("failed"):``).
    The completion hook must run before that branch or streamed turns never
    emit ``message.completed``.
    """
    candidates = []
    for node in ast.walk(handler):
        if not isinstance(node, ast.If) or node.lineno is None:
            continue
        try:
            test_source = ast.unparse(node.test)
        except Exception:
            continue
        if "agent_result.get('already_sent')" not in test_source:
            continue
        if "not agent_result.get('failed')" not in test_source:
            continue
        if not _branch_returns(node.body):
            continue
        candidates.append(node)
    if not candidates:
        return None

    target = min(candidates, key=lambda node: node.lineno)
    insert_at = target.lineno - 1
    return insert_at, _line_indent(lines, insert_at)


def _branch_returns(body) -> bool:
    for node in body:
        for child in ast.walk(node):
            if isinstance(child, ast.Return):
                return True
    return False


def _find_cron_deliver_body_location(tree, lines):
    node = _find_cron_deliver_node(tree)
    return _body_location(node, lines) if node is not None else None


def _find_cron_media_delivery_location(tree, lines):
    node = _find_cron_deliver_node(tree)
    if node is None:
        return None

    extract_assignment = None
    filter_assignment = None
    for statement in node.body:
        if not isinstance(statement, ast.Assign):
            continue
        if _assigns_media_and_cleaned_content(statement):
            extract_assignment = statement
        if _assigns_filtered_media_files(statement):
            filter_assignment = statement
    target = filter_assignment or extract_assignment
    if target is None or target.lineno is None:
        return None
    end_lineno = getattr(target, "end_lineno", None) or target.lineno
    return end_lineno, _line_indent(lines, target.lineno - 1)


def _find_cron_deliver_node(tree):
    for node in tree.body:
        if _is_cron_deliver(node):
            return node
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if _is_cron_deliver(child):
                    return child
    return None


def _assigns_media_and_cleaned_content(statement) -> bool:
    names = {
        element.id
        for target in statement.targets
        if isinstance(target, (ast.Tuple, ast.List))
        for element in target.elts
        if isinstance(element, ast.Name)
    }
    if not {"media_files", "cleaned_delivery_content"}.issubset(names):
        return False
    value = statement.value
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "extract_media"
    )


def _assigns_filtered_media_files(statement) -> bool:
    if not any(
        isinstance(target, ast.Name) and target.id == "media_files"
        for target in statement.targets
    ):
        return False
    value = statement.value
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "filter_media_delivery_paths"
    )


def _find_callback_body_location(
    tree,
    lines,
    callback_name: str,
    *,
    required_outer_names=(),
    required_callback_args=(),
    required_callback_calls=(),
):
    run_agent = _find_run_agent_node(tree)
    if run_agent is None:
        return None
    candidates = []
    for node in ast.walk(run_agent):
        if isinstance(node, ast.FunctionDef) and node.name == callback_name:
            if not _has_required_callback_scope(
                run_agent,
                node,
                required_outer_names,
                required_callback_args,
            ):
                continue
            candidates.append(node)
    if required_callback_calls:
        preferred = [
            node
            for node in candidates
            if _has_required_callback_calls(node, required_callback_calls)
        ]
        if preferred:
            candidates = preferred
        elif len(candidates) != 1:
            return None
    if not candidates:
        return None
    return _body_location(candidates[0], lines)


def _find_turn_runner_callback_body_location(
    tree,
    lines,
    callback_name: str,
    *,
    required_callback_args=(),
    required_callback_calls=(),
):
    turn_runner = _find_turn_runner_node(tree)
    if turn_runner is None:
        return None

    if callback_name == "_status_callback_sync":
        candidates = [
            node
            for node in turn_runner.body
            if isinstance(node, ast.FunctionDef) and node.name == callback_name
        ]
    else:
        run_sync = _find_direct_class_function_node(turn_runner, "run_sync")
        if run_sync is None or not _binds_turn_context(run_sync):
            return None
        candidates = [
            node
            for node in ast.walk(run_sync)
            if isinstance(node, ast.FunctionDef) and node.name == callback_name
        ]

    candidates = [
        node
        for node in candidates
        if set(required_callback_args).issubset(_function_argument_names(node))
    ]
    if callback_name == "_status_callback_sync":
        candidates = [node for node in candidates if _binds_turn_context(node)]
    if required_callback_calls:
        preferred = [
            node
            for node in candidates
            if _has_required_callback_calls(node, required_callback_calls)
        ]
        if preferred:
            candidates = preferred
        elif len(candidates) != 1:
            return None
    if not candidates:
        return None
    if callback_name == "_status_callback_sync":
        return _turn_context_binding_location(candidates[0], lines)
    return _body_location(candidates[0], lines)


def _find_stable_tool_lifecycle_location(tree, lines):
    run_agent = _find_run_agent_node(tree)
    if run_agent is None:
        return None
    required_names = {
        "agent",
        "source",
        "event_message_id",
        "_loop_for_step",
        "_run_still_current",
        "progress_callback",
    }
    if not required_names.issubset(_function_scope_names(run_agent)):
        return None
    for node in ast.walk(run_agent):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(_is_agent_callback_target(target, "tool_start_callback") for target in targets):
            continue
        end_lineno = getattr(node, "end_lineno", None) or node.lineno
        return end_lineno, _line_indent(lines, node.lineno - 1)
    return None


def _find_turn_runner_stable_tool_lifecycle_location(tree, lines):
    turn_runner = _find_turn_runner_node(tree)
    if turn_runner is None:
        return None
    run_sync = _find_direct_class_function_node(turn_runner, "run_sync")
    if run_sync is None or not _binds_turn_context(run_sync):
        return None
    if "agent" not in _function_scope_names(run_sync):
        return None
    for node in ast.walk(run_sync):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            _is_agent_callback_target(target, "tool_start_callback")
            for target in targets
        ):
            continue
        end_lineno = getattr(node, "end_lineno", None) or node.lineno
        return end_lineno, _line_indent(lines, node.lineno - 1)
    return None


def _find_turn_runner_node(tree):
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "TurnRunner"
        ),
        None,
    )


def _find_direct_class_function_node(class_node, name: str):
    return next(
        (
            node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ),
        None,
    )


def _binds_turn_context(node) -> bool:
    return _find_turn_context_binding(node) is not None


def _find_turn_context_binding(node):
    for statement in node.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        value = statement.value
        if (
            isinstance(target, ast.Name)
            and target.id == "ctx"
            and isinstance(value, ast.Attribute)
            and value.attr == "_ctx"
            and isinstance(value.value, ast.Name)
            and value.value.id == "self"
        ):
            return statement
    return None


def _turn_context_binding_location(node, lines):
    binding = _find_turn_context_binding(node)
    if binding is None:
        return None
    end_lineno = getattr(binding, "end_lineno", None) or binding.lineno
    return end_lineno, _line_indent(lines, binding.lineno - 1)


def _is_agent_callback_target(node, attribute: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attribute
        and isinstance(node.value, ast.Name)
        and node.value.id == "agent"
    )


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


def _has_required_callback_calls(callback, required_callback_calls) -> bool:
    if not required_callback_calls:
        return True
    calls = {
        (child.func.value.id, child.func.attr)
        for child in ast.walk(callback)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and isinstance(child.func.value, ast.Name)
    }
    return set(required_callback_calls).issubset(calls)


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
    expected_with_anchor = _render_complete_hook_block_with_reply_anchor(indent, newline)
    expected = _render_complete_hook_block(indent, newline)
    v400 = _render_v400_complete_hook_block(indent, newline)
    pre_exact_with_anchor = _render_pre_exact_complete_hook_block_with_reply_anchor(
        indent, newline
    )
    pre_exact = _render_pre_exact_complete_hook_block(indent, newline)
    pre_exact_v400 = _render_pre_exact_v400_complete_hook_block(indent, newline)
    legacy = _render_legacy_complete_hook_block(indent, newline)
    previous_async = _render_previous_async_complete_hook_block(indent, newline)
    previous_async_without_platform = (
        _render_previous_async_complete_hook_block_without_platform_guard(indent, newline)
    )
    expected_with_anchor_silent = _with_silent_exception_handler(
        expected_with_anchor, indent, newline
    )
    expected_silent = _with_silent_exception_handler(expected, indent, newline)
    v400_silent = _with_silent_exception_handler(v400, indent, newline)
    pre_exact_with_anchor_silent = _with_silent_exception_handler(
        pre_exact_with_anchor, indent, newline
    )
    pre_exact_silent = _with_silent_exception_handler(pre_exact, indent, newline)
    pre_exact_v400_silent = _with_silent_exception_handler(
        pre_exact_v400, indent, newline
    )
    legacy_silent = _with_silent_exception_handler(legacy, indent, newline)
    previous_async_silent = _with_silent_exception_handler(
        previous_async, indent, newline
    )
    previous_async_without_platform_silent = _with_silent_exception_handler(
        previous_async_without_platform, indent, newline
    )
    actual = lines[begin_index : end_index + 1]
    if actual not in (
        expected_with_anchor,
        expected,
        v400,
        pre_exact_with_anchor,
        pre_exact,
        pre_exact_v400,
        legacy,
        previous_async,
        previous_async_without_platform,
        expected_with_anchor_silent,
        expected_silent,
        v400_silent,
        pre_exact_with_anchor_silent,
        pre_exact_silent,
        pre_exact_v400_silent,
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
    actual = lines[begin_index : end_index + 1]
    pre_media = _render_cron_hook_block(indent, newline, media_aware=False)
    post_media = _render_cron_hook_block(indent, newline, media_aware=True)
    pre_media_silent = _with_silent_exception_handler(pre_media, indent, newline)
    post_media_silent = _with_silent_exception_handler(post_media, indent, newline)
    if actual in (post_media, post_media_silent):
        media_aware = True
    elif actual in (pre_media, pre_media_silent):
        media_aware = False
    else:
        raise ValueError("corrupt cron patch markers")

    tree = _parse_content_with_markers(content)
    location = (
        _find_cron_media_delivery_location(tree, lines)
        if media_aware
        else _find_cron_deliver_body_location(tree, lines)
    )
    if location is None:
        raise ValueError("corrupt cron patch markers")
    insert_at, _body_indent = location
    expected_begin_index = insert_at if media_aware else insert_at - 1
    if begin_index != expected_begin_index:
        raise ValueError("corrupt cron patch markers")
    return begin_index, end_index, media_aware


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


def _parse_exact_base_content(content: str):
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise ValueError("could not find safe BasePlatformAdapter contract") from exc
    return tree


def _find_exact_base_patch_locations(tree, lines):
    """Return the two insertion locations after validating Hermes' pipeline."""
    method = _find_exact_base_process_method(tree)
    method_nodes = list(ast.walk(method))

    extract_media = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("media_files", "response"),
            owner="self",
            function="extract_media",
            args=("response",),
        ),
    )
    filter_media = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("media_files",),
            owner="self",
            function="filter_media_delivery_paths",
            args=("media_files",),
        ),
    )
    extract_images = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("images", "text_content"),
            owner="self",
            function="extract_images",
            args=("response",),
        ),
    )
    strip_text = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_strip_assignment(
            node,
            target="text_content",
            source="text_content",
        ),
    )
    extract_local = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("local_files", "text_content"),
            owner="self",
            function="extract_local_files",
            args=("text_content",),
        ),
    )
    filter_local = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("local_files",),
            owner="self",
            function="filter_local_delivery_paths",
            args=("local_files",),
        ),
    )
    recovered = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_strip_assignment(
            node,
            target="_recovered",
            source="response",
        ),
    )
    restore_text = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_name_assignment(
            node,
            target="text_content",
            value="_recovered",
        ),
    )
    final_metadata = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("_final_thread_metadata",),
            owner=None,
            function="_mark_notify_metadata",
            args=("_thread_metadata",),
        ),
    )
    tts_caption_default = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_constant_assignment(
            node,
            target="_tts_caption_delivered",
            value=False,
        ),
    )
    text_guard = _unique_exact_base_node(
        method_nodes,
        lambda node: isinstance(node, ast.If)
        and _same_expression(
            node.test,
            "text_content and not _tts_caption_delivered",
        ),
    )

    guard_nodes = list(ast.walk(text_guard))
    delivery_adapter = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("delivery_adapter",),
            owner="self",
            function="_final_delivery_adapter",
            args=("event.source",),
        ),
    )
    reply_anchor = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("_reply_anchor",),
            owner=None,
            function="_reply_anchor_for_event",
            args=("event",),
        ),
    )
    compute_obligation = _unique_exact_base_node(
        guard_nodes,
        _is_exact_compute_obligation_assignment,
    )
    obligation_default = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_constant_assignment(
            node,
            target="_obligation_id",
            value=None,
        )
        and node.lineno < compute_obligation.lineno,
    )
    record_obligation = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_ledger_call(
            node,
            function="record_obligation",
            required_keywords={
                "obligation_id": "_obligation_id",
                "content": "text_content",
            },
        ),
    )
    mark_attempting = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_positional_call(
            node,
            function="mark_attempting",
            args=("_obligation_id",),
        ),
    )
    send = _unique_exact_base_node(guard_nodes, _is_exact_final_send_assignment)
    record_delivery = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_positional_call(
            node,
            function="_record_delivery",
            args=("result",),
        ),
    )
    mark_delivered = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_positional_call(
            node,
            function="mark_delivered",
            args=("_obligation_id",),
        ),
    )
    mark_failed = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_mark_failed_call(node),
    )

    # Hermes 0.20 keeps the ledger writes in a nested pre-send guard (and
    # routes the blocking calls through asyncio.to_thread). The important
    # invariant is that every ledger operation completes before `_send_with_retry`
    # and that the send is not nested inside the ledger block; requiring all
    # ledger nodes to precede the send preserves that crash-window boundary.
    send_position = _direct_child_position(text_guard.body, send)
    ledger_positions = {
        _direct_child_position(text_guard.body, node)
        for node in (compute_obligation, record_obligation, mark_attempting)
    }
    if (
        send_position is None
        or None in ledger_positions
        or any(position >= send_position for position in ledger_positions)
    ):
        raise ValueError("could not find safe BasePlatformAdapter contract")

    ordered = (
        extract_media,
        filter_media,
        extract_images,
        strip_text,
        extract_local,
        filter_local,
        recovered,
        restore_text,
        final_metadata,
        tts_caption_default,
        text_guard,
        delivery_adapter,
        reply_anchor,
        obligation_default,
        compute_obligation,
        record_obligation,
        mark_attempting,
        send,
        record_delivery,
        mark_delivered,
        mark_failed,
    )
    if any(getattr(node, "lineno", None) is None for node in ordered):
        raise ValueError("could not find safe BasePlatformAdapter contract")
    line_numbers = [node.lineno for node in ordered]
    if line_numbers != sorted(line_numbers) or len(set(line_numbers)) != len(line_numbers):
        raise ValueError("could not find safe BasePlatformAdapter contract")

    no_text_index = text_guard.lineno - 1
    send_index = send.lineno - 1
    return (
        (no_text_index, _line_indent(lines, no_text_index)),
        (send_index, _line_indent(lines, send_index)),
    )


def _find_exact_base_process_method(tree):
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BasePlatformAdapter"
    ]
    if len(classes) != 1:
        raise ValueError("could not find safe BasePlatformAdapter contract")
    methods = [
        node
        for node in classes[0].body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_process_message_background"
    ]
    if len(methods) != 1:
        raise ValueError("could not find safe BasePlatformAdapter contract")
    return methods[0]


def _unique_exact_base_node(nodes, predicate):
    matches = [node for node in nodes if predicate(node)]
    if len(matches) != 1:
        raise ValueError("could not find safe BasePlatformAdapter contract")
    return matches[0]


def _direct_child_position(body, target):
    target_id = id(target)
    for index, statement in enumerate(body):
        if any(id(node) == target_id for node in ast.walk(statement)):
            return index
    return None


def _assignment_target_names(node):
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return None
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            if not all(isinstance(item, ast.Name) for item in target.elts):
                return None
            names.extend(item.id for item in target.elts)
        else:
            return None
    return tuple(names)


def _assignment_value(node):
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        return node.value
    return None


def _same_expression(node, source: str) -> bool:
    expected = ast.parse(source, mode="eval").body
    return ast.dump(node, include_attributes=False) == ast.dump(
        expected,
        include_attributes=False,
    )


def _unwrap_asyncio_to_thread_call(call):
    """Return the wrapped call for Hermes' blocking ledger adapters."""
    if not isinstance(call, ast.Call):
        return None
    if not (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "asyncio"
        and call.func.attr == "to_thread"
        and call.args
        and isinstance(call.args[0], ast.Name)
    ):
        return call
    return ast.Call(
        func=call.args[0],
        args=call.args[1:],
        keywords=call.keywords,
    )


def _call_function(call):
    call = _unwrap_asyncio_to_thread_call(call)
    if not isinstance(call, ast.Call):
        return None, None
    func = call.func
    if isinstance(func, ast.Name):
        return None, func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id, func.attr
    return None, None


def _is_exact_assignment_call(node, *, targets, owner, function, args):
    if _assignment_target_names(node) != targets:
        return False
    value = _assignment_value(node)
    if isinstance(value, ast.Await):
        value = value.value
    actual_owner, actual_function = _call_function(value)
    if (actual_owner, actual_function) != (owner, function):
        return False
    if value.keywords or len(value.args) != len(args):
        return False
    return all(_same_expression(arg, expected) for arg, expected in zip(value.args, args))


def _is_strip_assignment(node, *, target: str, source: str) -> bool:
    if _assignment_target_names(node) != (target,):
        return False
    outer = _assignment_value(node)
    if not (
        isinstance(outer, ast.Call)
        and isinstance(outer.func, ast.Attribute)
        and outer.func.attr == "strip"
        and not outer.args
        and not outer.keywords
    ):
        return False
    inner = outer.func.value
    return (
        isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Name)
        and inner.func.id == "_strip_media_directives"
        and len(inner.args) == 1
        and not inner.keywords
        and _same_expression(inner.args[0], source)
    )


def _is_exact_name_assignment(node, *, target: str, value: str) -> bool:
    return _assignment_target_names(node) == (target,) and _same_expression(
        _assignment_value(node),
        value,
    )


def _is_exact_constant_assignment(node, *, target: str, value) -> bool:
    assigned = _assignment_value(node)
    return (
        _assignment_target_names(node) == (target,)
        and isinstance(assigned, ast.Constant)
        and assigned.value is value
    )


def _is_exact_compute_obligation_assignment(node) -> bool:
    if _assignment_target_names(node) != ("_obligation_id",):
        return False
    call = _unwrap_asyncio_to_thread_call(_assignment_value(node))
    if call is None:
        return False
    owner, function = _call_function(call)
    return (
        owner is None
        and function == "compute_obligation_id"
        and len(call.args) == 3
        and not call.keywords
        and _same_expression(call.args[0], "session_key")
        and _same_expression(call.args[2], "text_content")
    )


def _expression_call(node):
    if not isinstance(node, ast.Expr):
        return None
    value = node.value
    if isinstance(value, ast.Await):
        value = value.value
    return value if isinstance(value, ast.Call) else None


def _is_exact_ledger_call(node, *, function: str, required_keywords) -> bool:
    call = _unwrap_asyncio_to_thread_call(_expression_call(node))
    if call is None:
        return False
    owner, actual_function = _call_function(call)
    if owner is not None or actual_function != function or call.args:
        return False
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
    return all(
        name in keywords and _same_expression(keywords[name], expression)
        for name, expression in required_keywords.items()
    )


def _is_exact_positional_call(node, *, function: str, args) -> bool:
    call = _unwrap_asyncio_to_thread_call(_expression_call(node))
    if call is None:
        return False
    owner, actual_function = _call_function(call)
    return (
        owner is None
        and actual_function == function
        and not call.keywords
        and len(call.args) == len(args)
        and all(
            _same_expression(arg, expected)
            for arg, expected in zip(call.args, args)
        )
    )


def _is_exact_final_send_assignment(node) -> bool:
    if _assignment_target_names(node) != ("result",):
        return False
    value = _assignment_value(node)
    if not isinstance(value, ast.Await) or not isinstance(value.value, ast.Call):
        return False
    call = value.value
    if not (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "_send_with_retry"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "delivery_adapter"
        and not call.args
    ):
        return False
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
    expected = {
        "chat_id": "event.source.chat_id",
        "content": "text_content",
        "reply_to": "_reply_anchor",
        "metadata": "_final_thread_metadata",
    }
    return set(keywords) == set(expected) and all(
        _same_expression(keywords[name], expression)
        for name, expression in expected.items()
    )


def _is_exact_mark_failed_call(node) -> bool:
    call = _unwrap_asyncio_to_thread_call(_expression_call(node))
    if call is None:
        return False
    owner, function = _call_function(call)
    return (
        owner is None
        and function == "mark_failed"
        and len(call.args) == 2
        and not call.keywords
        and _same_expression(call.args[0], "_obligation_id")
    )


def _find_redelivery_startup_call(func):
    for node in func.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Await):
            continue
        call = node.value.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "_redeliver_pending_obligations"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self"
        ):
            return node
    return None


def _find_redelivery_adapter_send(func):
    matches = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Await):
            continue
        call = node.value.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "send"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "adapter"
            and any(
                isinstance(target, ast.Name) and target.id == "result"
                for target in node.targets
            )
        ):
            matches.append(node)
    return matches[0] if len(matches) == 1 else None


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
    if renderer in (
        _render_stable_tool_lifecycle_hook_block,
        _render_answer_delta_hook_block,
        _render_thinking_delta_hook_block,
        _render_clarify_hook_block,
        _render_approval_hook_block,
        _render_status_hook_block,
    ):
        expected_blocks.append(
            _render_turn_context_hook_block(renderer, indent, newline)
        )
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


def _find_owned_exact_base_blocks(content: str, *, strict: bool):
    error_label = "exact base patch markers"
    no_text = _find_simple_marker_block(
        content,
        EXACT_BASE_NO_TEXT_PATCH_BEGIN,
        EXACT_BASE_NO_TEXT_PATCH_END,
        error_label,
    )
    final = _find_simple_marker_block(
        content,
        EXACT_BASE_FINAL_DELIVERY_PATCH_BEGIN,
        EXACT_BASE_FINAL_DELIVERY_PATCH_END,
        error_label,
    )
    if no_text is None and final is None:
        return None
    if no_text is None or final is None:
        raise ValueError("corrupt exact base patch markers")

    if no_text[1] >= final[0]:
        raise ValueError("corrupt exact base patch markers")
    if strict:
        lines = content.splitlines(keepends=True)
        no_text_indent = _leading_whitespace(
            _strip_line_ending(lines[no_text[0]])
        )
        no_text_newline = _line_ending(lines[no_text[0]]) or _detect_newline(content)
        final_indent = _leading_whitespace(_strip_line_ending(lines[final[0]]))
        final_newline = _line_ending(lines[final[0]]) or _detect_newline(content)
        if lines[no_text[0] : no_text[1] + 1] != _render_exact_base_no_text_hook_block(
            no_text_indent,
            no_text_newline,
        ):
            raise ValueError("corrupt exact base patch markers")
        if lines[final[0] : final[1] + 1] != _render_exact_base_final_delivery_hook_block(
            final_indent,
            final_newline,
        ):
            raise ValueError("corrupt exact base patch markers")
    return no_text, final


def _validate_exact_base_owned_locations(
    owned,
    *,
    no_text_location,
    final_location,
) -> None:
    no_text, final = owned
    if (
        no_text[1] + 1 != no_text_location[0]
        or final[1] + 1 != final_location[0]
    ):
        raise ValueError("corrupt exact base patch markers")


def _remove_exact_base_blocks(content: str, owned) -> str:
    lines = content.splitlines(keepends=True)
    for begin_index, end_index in sorted(owned, reverse=True):
        lines[begin_index : end_index + 1] = []
    return "".join(lines)


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


def _render_exact_base_no_text_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{EXACT_BASE_NO_TEXT_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import finalize_exact_base_no_text as "
            f"_hfc_finalize_exact_base_no_text{newline}"
        ),
        f"{inner_indent}if not text_content or _tts_caption_delivered:{newline}",
        f"{deeper_indent}await _hfc_finalize_exact_base_no_text({{{newline}",
        f"{deeper_indent}    **locals(),{newline}",
        f"{deeper_indent}    \"source\": event.source,{newline}",
        f"{deeper_indent}}}){newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{EXACT_BASE_NO_TEXT_PATCH_END}{newline}",
    ]


def _render_exact_base_final_delivery_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    return [
        f"{indent}{EXACT_BASE_FINAL_DELIVERY_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import prepare_exact_base_final_delivery as "
            f"_hfc_prepare_exact_base_final_delivery{newline}"
        ),
        (
            f"{inner_indent}delivery_adapter, text_content, _reply_anchor, "
            f"_final_thread_metadata = await "
            f"_hfc_prepare_exact_base_final_delivery({{{newline}"
        ),
        f"{inner_indent}    **locals(),{newline}",
        f"{inner_indent}    \"source\": event.source,{newline}",
        f"{inner_indent}    \"delivery_adapter\": delivery_adapter,{newline}",
        f"{inner_indent}    \"content\": text_content,{newline}",
        f"{inner_indent}    \"obligation_id\": _obligation_id,{newline}",
        f"{inner_indent}    \"reply_to\": _reply_anchor,{newline}",
        f"{inner_indent}    \"metadata\": _final_thread_metadata,{newline}",
        f"{inner_indent}}}){newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{EXACT_BASE_FINAL_DELIVERY_PATCH_END}{newline}",
    ]


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
                # Use the REAL incoming message id for message.started so every
                # new user message opens its own card session. The reply anchor
                # (topic root / quoted message) must NOT be used here: in Feishu
                # threads, consecutive quoted replies to the same message would
                # otherwise share one message_id and overwrite the same card.
                # Stream events still carry the reply anchor, and the sidecar
                # resolves them back to this session via the reply_to alias.
                f"{deeper_indent}_hfc_started_message_id = getattr(event, \"message_id\", None) or self._reply_anchor_for_event(event){newline}",
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
                # Use the REAL incoming message id for message.started so every
                # new user message opens its own card session. The reply anchor
                # (topic root / quoted message) must NOT be used here: in Feishu
                # threads, consecutive quoted replies to the same message would
                # otherwise share one message_id and overwrite the same card.
                # Stream events still carry the reply anchor, and the sidecar
                # resolves them back to this session via the reply_to alias.
                f"{deeper_indent}_hfc_started_message_id = getattr(event, \"message_id\", None) or self._reply_anchor_for_event(event){newline}",
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
    deepest_indent = _child_indent(deeper_indent)
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
            f"import can_stage_exact_base_completion as _hfc_can_stage_exact{newline}"
        ),
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import stage_message_completed_from_hermes_locals_async as _hfc_stage_exact{newline}"
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
        f"{inner_indent}_hfc_exact_staged = False{newline}",
        f"{inner_indent}if _hfc_can_stage_exact(_hfc_completed_locals):{newline}",
        f"{deeper_indent}_hfc_exact_staged = await _hfc_stage_exact(_hfc_completed_locals){newline}",
        f"{inner_indent}if not _hfc_exact_staged:{newline}",
        f"{deeper_indent}_hfc_completed_event = _hfc_build_event(\"message.completed\", _hfc_completed_locals, preview=True){newline}",
        f"{deeper_indent}_hfc_attachments = []{newline}",
        f"{deeper_indent}_hfc_native_delivery = \"allowed\"{newline}",
        f"{deeper_indent}if _hfc_completed_event is not None:{newline}",
        f"{deepest_indent}_hfc_completed_data = _hfc_completed_event.get(\"data\", {{}}){newline}",
        f"{deepest_indent}_hfc_attachments = _hfc_completed_data.get(\"attachments\", []){newline}",
        f"{deepest_indent}_hfc_native_delivery = _hfc_completed_data.get(\"native_delivery\", \"required\" if _hfc_attachments else \"allowed\"){newline}",
        f"{deeper_indent}_hfc_card_delivered = await _hfc_emit_async(_hfc_completed_locals, event_name=\"message.completed\"){newline}",
        f"{deeper_indent}_hfc_platform = getattr(source.platform, \"value\", source.platform){newline}",
        f"{deeper_indent}if str(_hfc_platform).lower() == \"feishu\" and _hfc_card_delivered and _hfc_native_delivery == \"required\":{newline}",
        f"{deepest_indent}response = _hfc_media_only(response){newline}",
        f"{deeper_indent}if _hfc_should_suppress(_hfc_platform, _hfc_card_delivered, _hfc_attachments, _hfc_native_delivery):{newline}",
        f"{deepest_indent}return None{newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{COMPLETE_PATCH_END}{newline}",
    ]


def _render_complete_hook_block_with_reply_anchor(indent: str, newline: str):
    """Completion hook for gateway_run_013_plus handlers.

    Derives an explicit message_id from the same reply anchor the started and
    delta hooks use, so the terminal event always lands on the session that
    owns the card instead of relying on the ambiguous terminal fallback cache
    (which can make build_event return None on streamed turns).
    """
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    block = list(_render_complete_hook_block(indent, newline))
    anchor_lines = [
        f"{inner_indent}_hfc_completed_message_id = None{newline}",
        f"{inner_indent}try:{newline}",
        f"{deeper_indent}_hfc_completed_message_id = self._reply_anchor_for_event(event){newline}",
        f"{inner_indent}except Exception:{newline}",
        f"{deeper_indent}_hfc_completed_message_id = getattr(event, \"message_id\", None){newline}",
    ]
    import_index = next(
        index
        for index, line in enumerate(block)
        if "native_media_only_response as _hfc_media_only" in line
    )
    block[import_index + 1 : import_index + 1] = anchor_lines
    locals_index = next(
        index for index, line in enumerate(block) if "**locals()," in line
    )
    block[locals_index + 1 : locals_index + 1] = [
        f"{deeper_indent}\"message_id\": _hfc_completed_message_id,{newline}"
    ]
    return block


def _render_pre_exact_complete_hook_block(indent: str, newline: str):
    """Render the last V4.1 block for narrow, owned upgrade migration only."""
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


def _render_pre_exact_complete_hook_block_with_reply_anchor(
    indent: str,
    newline: str,
):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    block = list(_render_pre_exact_complete_hook_block(indent, newline))
    import_index = next(
        index
        for index, line in enumerate(block)
        if "native_media_only_response as _hfc_media_only" in line
    )
    block[import_index + 1 : import_index + 1] = [
        f"{inner_indent}_hfc_completed_message_id = None{newline}",
        f"{inner_indent}try:{newline}",
        f"{deeper_indent}_hfc_completed_message_id = self._reply_anchor_for_event(event){newline}",
        f"{inner_indent}except Exception:{newline}",
        f"{deeper_indent}_hfc_completed_message_id = getattr(event, \"message_id\", None){newline}",
    ]
    locals_index = next(
        index for index, line in enumerate(block) if "**locals()," in line
    )
    block[locals_index + 1 : locals_index + 1] = [
        f"{deeper_indent}\"message_id\": _hfc_completed_message_id,{newline}"
    ]
    return block


def _render_pre_exact_v400_complete_hook_block(indent: str, newline: str):
    return [
        line
        for line in _render_pre_exact_complete_hook_block(indent, newline)
        if "native_media_only_response as _hfc_media_only" not in line
        and '_hfc_native_delivery == "required"' not in line
        and "response = _hfc_media_only(response)" not in line
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


def _render_turn_context_hook_block(renderer, indent: str, newline: str):
    """Adapt a legacy closure hook to Hermes' ``TurnRunner`` context seam."""
    block = renderer(indent, newline)
    replacements = (
        ("_run_still_current()", "_hfc_turn_ctx._run_still_current()"),
        ('"source": source,', '"source": _hfc_turn_ctx.source,'),
        (
            '"message_id": event_message_id,',
            '"message_id": _hfc_turn_ctx.event_message_id,',
        ),
        ('"_hfc_loop": _loop_for_step,', '"_hfc_loop": _hfc_turn_ctx._loop_for_step,'),
        (
            '"_hfc_loop": locals().get("_loop_for_step"),',
            '"_hfc_loop": _hfc_turn_ctx._loop_for_step,',
        ),
        ('"chat_id": _status_chat_id,', '"chat_id": _hfc_turn_ctx._status_chat_id,'),
        (
            '"conversation_id": session_key or _status_chat_id,',
            '"conversation_id": _hfc_turn_ctx.session_key or _hfc_turn_ctx._status_chat_id,',
        ),
        (
            '"conversation_id": _approval_session_key or _status_chat_id,',
            '"conversation_id": _approval_session_key or _hfc_turn_ctx._status_chat_id,',
        ),
    )
    adapted = []
    for line in block:
        for old, new in replacements:
            line = line.replace(old, new)
        adapted.append(line)
    adapted.insert(1, f"{indent}_hfc_turn_ctx = ctx{newline}")
    return adapted


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
        f"{inner_indent}_hfc_stable_tool_callbacks = False{newline}",
        f"{inner_indent}_hfc_force_tool_progress_fallback = bool(kwargs.get(\"_hfc_force_tool_progress_fallback\")){newline}",
        f"{inner_indent}if not _hfc_force_tool_progress_fallback:{newline}",
        f"{deeper_indent}try:{newline}",
        f"{deeper_indent}    _hfc_stable_tool_callbacks = bool({newline}",
        f"{deeper_indent}        getattr(agent.tool_start_callback, \"_hfc_stable_wrapper\", False){newline}",
        f"{deeper_indent}        and getattr(agent.tool_complete_callback, \"_hfc_stable_wrapper\", False){newline}",
        f"{deeper_indent}    ){newline}",
        f"{deeper_indent}except (NameError, AttributeError):{newline}",
        f"{deeper_indent}    pass{newline}",
        f"{deeper_indent}if not _hfc_stable_tool_callbacks:{newline}",
        f"{deeper_indent}    try:{newline}",
        f"{deeper_indent}        _hfc_stable_tool_callbacks = bool(_hfc_stable_tool_callbacks_available[0]){newline}",
        f"{deeper_indent}    except (NameError, TypeError, IndexError):{newline}",
        f"{deeper_indent}        pass{newline}",
        f"{inner_indent}if event_type in (\"tool.started\", \"tool.completed\") and _run_still_current():{newline}",
        f"{deeper_indent}if _hfc_stable_tool_callbacks:{newline}",
        f"{deeper_indent}    if event_type == \"tool.started\":{newline}",
        f"{deeper_indent}        _hfc_tool_key = tool_name or \"tool\"{newline}",
        f"{deeper_indent}        _hfc_pending_tool_previews.setdefault(_hfc_tool_key, []).append(preview or \"\"){newline}",
        f"{deeper_indent}    return{newline}",
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


def _render_stable_tool_lifecycle_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    callback_indent = _child_indent(deeper_indent)
    payload_indent = _child_indent(callback_indent)
    return [
        f"{indent}{STABLE_TOOL_PATCH_BEGIN}{newline}",
        f"{indent}_hfc_stable_tool_callbacks_available = [False]{newline}",
        f"{indent}_hfc_pending_tool_previews = {{}}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import emit_from_hermes_locals_threadsafe as _hfc_emit_stable_threadsafe{newline}"
        ),
        f"{inner_indent}_hfc_original_tool_progress_callback = getattr(agent, \"tool_progress_callback\", None){newline}",
        f"{inner_indent}if getattr(_hfc_original_tool_progress_callback, \"_hfc_stable_wrapper\", False):{newline}",
        f"{deeper_indent}_hfc_original_tool_progress_callback = getattr(_hfc_original_tool_progress_callback, \"_hfc_original_callback\", None){newline}",
        f"{inner_indent}_hfc_original_tool_start_callback = getattr(agent, \"tool_start_callback\", None){newline}",
        f"{inner_indent}if getattr(_hfc_original_tool_start_callback, \"_hfc_stable_wrapper\", False):{newline}",
        f"{deeper_indent}_hfc_original_tool_start_callback = getattr(_hfc_original_tool_start_callback, \"_hfc_original_callback\", None){newline}",
        f"{inner_indent}_hfc_original_tool_complete_callback = getattr(agent, \"tool_complete_callback\", None){newline}",
        f"{inner_indent}if getattr(_hfc_original_tool_complete_callback, \"_hfc_stable_wrapper\", False):{newline}",
        f"{deeper_indent}_hfc_original_tool_complete_callback = getattr(_hfc_original_tool_complete_callback, \"_hfc_original_callback\", None){newline}",
        f"{inner_indent}def _hfc_tool_progress_callback(event_type, tool_name=None, preview=None, args=None, **kwargs):{newline}",
        f"{deeper_indent}if event_type in (\"tool.started\", \"tool.completed\") and _run_still_current():{newline}",
        f"{callback_indent}if event_type == \"tool.started\":{newline}",
        f"{payload_indent}_hfc_tool_key = tool_name or \"tool\"{newline}",
        f"{payload_indent}_hfc_pending_tool_previews.setdefault(_hfc_tool_key, []).append(preview or \"\"){newline}",
        f"{callback_indent}return None{newline}",
        f"{deeper_indent}if callable(_hfc_original_tool_progress_callback):{newline}",
        f"{callback_indent}return _hfc_original_tool_progress_callback(event_type, tool_name, preview, args, **kwargs){newline}",
        f"{deeper_indent}return None{newline}",
        f"{inner_indent}def _hfc_tool_start_callback(call_id, tool_name, args):{newline}",
        f"{deeper_indent}try:{newline}",
        f"{callback_indent}if callable(_hfc_original_tool_start_callback):{newline}",
        f"{payload_indent}_hfc_original_tool_start_callback(call_id, tool_name, args){newline}",
        f"{deeper_indent}except Exception:{newline}",
        f"{callback_indent}pass{newline}",
        f"{deeper_indent}_hfc_tool_key = tool_name or \"tool\"{newline}",
        f"{deeper_indent}_hfc_preview_queue = _hfc_pending_tool_previews.get(_hfc_tool_key) or []{newline}",
        f"{deeper_indent}_hfc_tool_preview = _hfc_preview_queue.pop(0) if _hfc_preview_queue else \"\"{newline}",
        f"{deeper_indent}if not _hfc_preview_queue:{newline}",
        f"{callback_indent}_hfc_pending_tool_previews.pop(_hfc_tool_key, None){newline}",
        f"{deeper_indent}if not _run_still_current():{newline}",
        f"{callback_indent}return{newline}",
        f"{deeper_indent}_hfc_delivered = _hfc_emit_stable_threadsafe({{{newline}",
        f"{callback_indent}**locals(),{newline}",
        f"{callback_indent}\"source\": source,{newline}",
        f"{callback_indent}\"message_id\": event_message_id,{newline}",
        f"{callback_indent}\"_hfc_loop\": _loop_for_step,{newline}",
        f"{callback_indent}\"tool_id\": str(call_id or tool_name or \"tool\"),{newline}",
        f"{callback_indent}\"name\": tool_name or \"tool\",{newline}",
        f"{callback_indent}\"status\": \"running\",{newline}",
        f"{callback_indent}\"detail\": _hfc_tool_preview,{newline}",
        f"{callback_indent}\"arguments\": args,{newline}",
        f"{deeper_indent}}}, event_name=\"tool.updated\"){newline}",
        f"{deeper_indent}if not _hfc_delivered:{newline}",
        f"{callback_indent}_hfc_stable_tool_callbacks_available[0] = False{newline}",
        f"{callback_indent}try:{newline}",
        f"{payload_indent}if callable(_hfc_original_tool_progress_callback):{newline}",
        f"{payload_indent}    _hfc_original_tool_progress_callback(\"tool.started\", tool_name, _hfc_tool_preview, args, _hfc_force_tool_progress_fallback=True){newline}",
        f"{callback_indent}finally:{newline}",
        f"{payload_indent}_hfc_stable_tool_callbacks_available[0] = True{newline}",
        f"{inner_indent}def _hfc_tool_complete_callback(call_id, tool_name, args, result):{newline}",
        f"{deeper_indent}try:{newline}",
        f"{callback_indent}if callable(_hfc_original_tool_complete_callback):{newline}",
        f"{payload_indent}_hfc_original_tool_complete_callback(call_id, tool_name, args, result){newline}",
        f"{deeper_indent}except Exception:{newline}",
        f"{callback_indent}pass{newline}",
        f"{deeper_indent}if not _run_still_current():{newline}",
        f"{callback_indent}return{newline}",
        f"{deeper_indent}_hfc_delivered = _hfc_emit_stable_threadsafe({{{newline}",
        f"{callback_indent}**locals(),{newline}",
        f"{callback_indent}\"source\": source,{newline}",
        f"{callback_indent}\"message_id\": event_message_id,{newline}",
        f"{callback_indent}\"_hfc_loop\": _loop_for_step,{newline}",
        f"{callback_indent}\"tool_id\": str(call_id or tool_name or \"tool\"),{newline}",
        f"{callback_indent}\"name\": tool_name or \"tool\",{newline}",
        f"{callback_indent}\"status\": \"completed\",{newline}",
        f"{callback_indent}\"detail\": \"\",{newline}",
        f"{deeper_indent}}}, event_name=\"tool.updated\"){newline}",
        f"{deeper_indent}if not _hfc_delivered:{newline}",
        f"{callback_indent}_hfc_stable_tool_callbacks_available[0] = False{newline}",
        f"{callback_indent}try:{newline}",
        f"{payload_indent}if callable(_hfc_original_tool_progress_callback):{newline}",
        f"{payload_indent}    _hfc_original_tool_progress_callback(\"tool.completed\", tool_name, None, None, _hfc_force_tool_progress_fallback=True){newline}",
        f"{callback_indent}finally:{newline}",
        f"{payload_indent}_hfc_stable_tool_callbacks_available[0] = True{newline}",
        f"{inner_indent}_hfc_tool_progress_callback._hfc_stable_wrapper = True{newline}",
        f"{inner_indent}_hfc_tool_progress_callback._hfc_original_callback = _hfc_original_tool_progress_callback{newline}",
        f"{inner_indent}_hfc_tool_start_callback._hfc_stable_wrapper = True{newline}",
        f"{inner_indent}_hfc_tool_start_callback._hfc_original_callback = _hfc_original_tool_start_callback{newline}",
        f"{inner_indent}_hfc_tool_complete_callback._hfc_stable_wrapper = True{newline}",
        f"{inner_indent}_hfc_tool_complete_callback._hfc_original_callback = _hfc_original_tool_complete_callback{newline}",
        f"{inner_indent}agent.tool_progress_callback = _hfc_tool_progress_callback{newline}",
        f"{inner_indent}agent.tool_start_callback = _hfc_tool_start_callback{newline}",
        f"{inner_indent}agent.tool_complete_callback = _hfc_tool_complete_callback{newline}",
        f"{inner_indent}_hfc_stable_tool_callbacks_available[0] = True{newline}",
        f"{indent}except Exception:{newline}",
        f"{inner_indent}_hfc_stable_tool_callbacks_available[0] = False{newline}",
        f"{indent}{STABLE_TOOL_PATCH_END}{newline}",
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


def _render_status_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    deeper_indent = _child_indent(inner_indent)
    return [
        f"{indent}{STATUS_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import handle_status_from_hermes_locals as _hfc_handle_status{newline}"
        ),
        f"{inner_indent}if _run_still_current():{newline}",
        f"{deeper_indent}_hfc_handle_status({{{newline}",
        f"{deeper_indent}    **locals(),{newline}",
        f"{deeper_indent}    \"source\": source,{newline}",
        f"{deeper_indent}    \"chat_id\": _status_chat_id,{newline}",
        f"{deeper_indent}    \"message_id\": event_message_id,{newline}",
        f"{deeper_indent}    \"_hfc_loop\": _loop_for_step,{newline}",
        f"{deeper_indent}}}, event_type=event_type, message=message){newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{STATUS_PATCH_END}{newline}",
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


def _render_native_redelivery_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    return [
        f"{indent}{NATIVE_REDELIVERY_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import prepare_native_handoff_recovery as _hfc_prepare_native_handoff_recovery{newline}"
        ),
        f"{inner_indent}await _hfc_prepare_native_handoff_recovery({newline}",
        f"{inner_indent}    adapter=adapter,{newline}",
        f"{inner_indent}    obligation_id=row.get(\"obligation_id\"),{newline}",
        f"{inner_indent}    chat_id=row.get(\"chat_id\"),{newline}",
        f"{inner_indent}    content=content,{newline}",
        f"{inner_indent}    original_content=row.get(\"content\"),{newline}",
        f"{inner_indent}    thread_id=row.get(\"thread_id\") or \"\",{newline}",
        f"{inner_indent}){newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{NATIVE_REDELIVERY_PATCH_END}{newline}",
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
            f"import maintenance_admission_from_hermes_locals as _hfc_enforce_maintenance_admission{newline}"
        ),
        f"{inner_indent}if await _hfc_enforce_maintenance_admission(locals()):{newline}",
        f"{deeper_indent}return None{newline}",
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


def _render_cron_hook_block(
    indent: str,
    newline: str,
    *,
    media_aware: bool = False,
):
    inner_indent = _child_indent(indent)
    if media_aware:
        return [
            f"{indent}{CRON_PATCH_BEGIN}{newline}",
            f"{indent}try:{newline}",
            (
                f"{inner_indent}from hermes_feishu_card.hook_runtime "
                f"import emit_cron_delivery as _hfc_emit_cron{newline}"
            ),
            f"{inner_indent}_hfc_cron_metadata = {{\"delivery_kind\": \"cron\"}}{newline}",
            (
                f"{inner_indent}if _hfc_emit_cron({{**locals(), "
                f"\"_hfc_resolved_targets\": locals().get(\"targets\", [])}}):{newline}"
            ),
            f"{_child_indent(inner_indent)}if media_files:{newline}",
            f"{_child_indent(_child_indent(inner_indent))}cleaned_delivery_content = \"\"{newline}",
            f"{_child_indent(inner_indent)}else:{newline}",
            f"{_child_indent(_child_indent(inner_indent))}return None{newline}",
            *_render_hook_exception_handler(indent, newline),
            f"{indent}{CRON_PATCH_END}{newline}",
        ]
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
