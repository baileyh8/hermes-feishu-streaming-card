"""Pure AST shape predicates shared by verified source-family contracts.

Inputs are heterogeneous AST nodes. Runtime shape checks remain the authority;
these helpers never import Hermes, execute inspected source or mutate files.
"""
from __future__ import annotations

import ast
from typing import Any, cast

_DELIVER_ATTACHMENTS_PATTERNS = (
    # Hermes 0.21.1+: _deliver_attachments gained record_delivery= kwarg
    "await self._deliver_attachments(event, extracted, _final_thread_metadata, anything_sent=delivery_attempted or _tts_caption_delivered, record_delivery=_record_delivery)",
    # Hermes <= 0.21.0: no record_delivery kwarg
    "await self._deliver_attachments(event, extracted, _final_thread_metadata, anything_sent=delivery_attempted or _tts_caption_delivered)",
)


def _is_split_record_obligation_call(node: Any) -> bool:
    if not isinstance(node, ast.Assign):
        return False
    if _assignment_target_names(node) != ("obligation_id",):
        return False
    value = _assignment_value(node)
    if not isinstance(value, ast.Await) or not isinstance(value.value, ast.Call):
        return False
    call = value.value
    owner, function = _call_function(call)
    return (
        owner == "self"
        and function == "_record_delivery_obligation"
        and not call.keywords
        and len(call.args) == 5
        and all(
            _same_expression(actual, expected)
            for actual, expected in zip(
                call.args,
                (
                    "event",
                    "session_key",
                    "text_content",
                    "delivery_adapter",
                    "is_ephemeral_response",
                ),
            )
        )
    )


def _is_split_final_send_assignment(node: Any) -> bool:
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
    expected = {
        "chat_id": "event.source.chat_id",
        "content": "text_content",
        "reply_to": "reply_to",
        "metadata": "metadata",
    }
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
    return len(call.keywords) == len(expected) and set(keywords) == set(expected) and all(
        _same_expression(keywords[name], expression)
        for name, expression in expected.items()
    )


def _is_split_finalize_obligation_call(node: Any) -> bool:
    if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
        return False
    call = node.value
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "_finalize_delivery_obligation"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "self"
        and not call.keywords
        and len(call.args) == 4
        and _same_expression(call.args[0], "obligation_id")
        and _same_expression(call.args[1], "result")
        and _same_expression(call.args[2], "event")
        and _same_expression(call.args[3], "delivery_adapter")
    )


def _unique_exact_base_node(nodes: Any, predicate: Any) -> Any:
    matches = [node for node in nodes if predicate(node)]
    if len(matches) != 1:
        raise ValueError("could not find safe BasePlatformAdapter contract")
    return matches[0]


def _direct_child_position(body: Any, target: Any) -> Any:
    target_id = id(target)
    for index, statement in enumerate(body):
        if any(id(node) == target_id for node in ast.walk(statement)):
            return index
    return None


def _assignment_target_names(node: Any) -> tuple[str, ...] | None:
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return None
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                if not isinstance(item, ast.Name):
                    return None
                names.append(item.id)
        else:
            return None
    return tuple(names)


def _assignment_value(node: Any) -> Any:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        return node.value
    return None


def _same_expression(node: Any, source: str) -> bool:
    expected = ast.parse(source, mode="eval").body
    return ast.dump(node, include_attributes=False) == ast.dump(
        expected,
        include_attributes=False,
    )


def _call_function(call: Any) -> tuple[str | None, str | None]:
    if not isinstance(call, ast.Call):
        return None, None
    func = call.func
    if isinstance(func, ast.Name):
        return None, func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id, func.attr
    return None, None


def _is_exact_delivery_filter_assignment(
    node: Any,
    *,
    target: str,
    function: str,
) -> bool:
    if _assignment_target_names(node) != (target,):
        return False
    call = _assignment_value(node)
    if not isinstance(call, ast.Call):
        return False
    owner, actual_function = _call_function(call)
    if (
        owner != "self"
        or actual_function != function
        or len(call.args) != 1
        or not _same_expression(call.args[0], target)
    ):
        return False
    if not call.keywords:
        return True
    return (
        len(call.keywords) == 1
        and call.keywords[0].arg == "session_key"
        and _same_expression(call.keywords[0].value, "session_key")
    )


def _is_exact_media_filter_assignment(node: Any) -> bool:
    return _is_exact_delivery_filter_assignment(
        node,
        target="media_files",
        function="filter_media_delivery_paths",
    )


def _is_exact_local_filter_assignment(node: Any) -> bool:
    return _is_exact_delivery_filter_assignment(
        node,
        target="local_files",
        function="filter_local_delivery_paths",
    )


def _is_exact_assignment_call(node: Any, *, targets: tuple[str, ...], owner: str | None, function: str, args: tuple[str, ...]) -> bool:
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


def _is_strip_assignment(node: Any, *, target: str, source: str) -> bool:
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


def _is_exact_name_assignment(node: Any, *, target: str, value: str) -> bool:
    return _assignment_target_names(node) == (target,) and _same_expression(
        _assignment_value(node),
        value,
    )


def _is_exact_constant_assignment(node: Any, *, target: str, value: Any) -> bool:
    assigned = _assignment_value(node)
    return (
        _assignment_target_names(node) == (target,)
        and isinstance(assigned, ast.Constant)
        and assigned.value is value
    )


def _is_exact_compute_obligation_assignment(node: Any) -> bool:
    if _assignment_target_names(node) != ("_obligation_id",):
        return False
    call = _assignment_value(node)
    owner, function = _call_function(call)
    return (
        owner is None
        and function == "compute_obligation_id"
        and len(call.args) == 3
        and not call.keywords
        and _same_expression(call.args[0], "session_key")
        and _same_expression(call.args[2], "text_content")
    )


def _expression_call(node: Any) -> Any:
    if not isinstance(node, ast.Expr):
        return None
    value = node.value
    if isinstance(value, ast.Await):
        value = value.value
    return value if isinstance(value, ast.Call) else None


def _exact_ledger_call_arguments(node: Any, *, function: str) -> Any:
    """Return ledger call arguments for direct or awaited ``to_thread`` calls.

    Hermes 0.20 moved the synchronous delivery-ledger writes behind
    ``await asyncio.to_thread(...)``.  Keep this unwrapping deliberately local
    to the verified ledger anchors: accepting an unawaited coroutine here would
    let the patcher certify a delivery contract that never records its state.
    """
    if not isinstance(node, ast.Expr):
        return None
    value = node.value
    awaited = isinstance(value, ast.Await)
    if isinstance(value, ast.Await):
        value = value.value
    if not isinstance(value, ast.Call):
        return None

    owner, actual_function = _call_function(value)
    if owner is None and actual_function == function:
        return value.args, value.keywords
    if not awaited or (owner, actual_function) != ("asyncio", "to_thread"):
        return None
    if not value.args:
        return None
    target = value.args[0]
    if not isinstance(target, ast.Name) or target.id != function:
        return None
    return value.args[1:], value.keywords


def _is_exact_ledger_call(node: Any, *, function: str, required_keywords: Any) -> bool:
    parts = _exact_ledger_call_arguments(node, function=function)
    if parts is None:
        return False
    args, raw_keywords = parts
    if args:
        return False
    keywords = {
        keyword.arg: keyword.value for keyword in raw_keywords if keyword.arg
    }
    return all(
        name in keywords and _same_expression(keywords[name], expression)
        for name, expression in required_keywords.items()
    )


def _is_exact_positional_call(node: Any, *, function: str, args: Any) -> bool:
    parts = _exact_ledger_call_arguments(node, function=function)
    if parts is None:
        return False
    actual_args, keywords = parts
    return (
        not keywords
        and len(actual_args) == len(args)
        and all(
            _same_expression(arg, expected)
            for arg, expected in zip(actual_args, args)
        )
    )


def _is_exact_final_send_assignment(node: Any) -> bool:
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


def _is_exact_mark_failed_call(node: Any) -> bool:
    parts = _exact_ledger_call_arguments(node, function="mark_failed")
    if parts is None:
        return False
    args, keywords = parts
    return (
        len(args) == 2
        and not keywords
        and _same_expression(args[0], "_obligation_id")
    )


def _line_indent(lines: list[str], index: int) -> str:
    if index < 0 or index >= len(lines):
        return ""
    return _leading_whitespace(_strip_line_ending(lines[index]))


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


def _exact_deliver_attachments(process: Any) -> Any:
    """Match the _deliver_attachments call site, accepting both pre- and
    post-0.21.1 signatures (with/without ``record_delivery=`` kwarg)."""
    calls = [node for node in ast.walk(process)
             if isinstance(node, ast.Call)
             and _call_function(node) == ("self", "_deliver_attachments")]
    if len(calls) != 1:
        raise ValueError("could not find safe BasePlatformAdapter contract")
    patterns = {ast.dump(ast.parse(pattern).body[0]) for pattern in _DELIVER_ATTACHMENTS_PATTERNS}
    # Count across both spellings. Trying them independently would accept two
    # different attachment sends and silently admit a changed delivery pipeline.
    return _unique_exact_base_node(
        ast.walk(process), lambda node: ast.dump(node) in patterns
    )


def _is_split_compute_obligation_assignment(node: Any) -> bool:
    if _assignment_target_names(node) != ("obligation_id",):
        return False
    call = _assignment_value(node)
    if not isinstance(call, ast.Call):
        return False
    owner, function = _call_function(call)
    return (
        owner is None
        and function == "compute_obligation_id"
        and not call.keywords
        and len(call.args) == 3
        and _same_expression(call.args[0], "session_key")
        and (
            _same_expression(call.args[1], "str(_ledger_id or \"\")")
            or _same_expression(call.args[1], "_ledger_id")
        )
        and _same_expression(call.args[2], "text_content")
    )


def _body_location(node: Any, lines: list[str]) -> tuple[int, str] | None:
    if not node.body:
        return None

    if _is_docstring_expr(node.body[0]):
        return _body_location_after_docstring(node, lines)

    insert_before = node.body[0]
    if _is_unsafe_one_line_body(node, insert_before):
        return None
    insert_at = insert_before.lineno - 1
    return insert_at, _line_indent(lines, insert_at)


def _body_location_after_docstring(node: Any, lines: list[str]) -> tuple[int, str] | None:
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


def _is_unsafe_one_line_body(handler: Any, body_node: Any) -> bool:
    return body_node.lineno is None or body_node.lineno == handler.lineno


def _is_docstring_expr(node: Any) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(getattr(node, "value", None), ast.Constant)
        and isinstance(cast(ast.Constant, node.value).value, str)
    )


def find_slash_confirm_register(content: str) -> ast.Expr | None:
    """Find the sole reviewed register statement without rewriting authority.

    Only a direct statement in the unique async handler is an insertion seam.
    Unknown arguments, nested calls and same-line statements remain unsupported.
    This shape predicate does not establish a trusted upstream source cohort.
    """
    functions = [node for node in ast.walk(ast.parse(content))
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == "_request_slash_confirm"]
    if len(functions) != 1 or not isinstance(functions[0], ast.AsyncFunctionDef):
        return None
    function = functions[0]
    calls = [node for node in ast.walk(function)
             if isinstance(node, ast.Call)
             and _same_expression(node.func, "_slash_confirm_mod.register")]
    if len(calls) != 1:
        return None
    call = calls[0]
    if (len(call.args) != 4 or not all(
        _same_expression(actual, expected) for actual, expected in zip(
            call.args, ("session_key", "confirm_id", "command", "handler")))):
        return None
    if call.keywords:
        authority = (
            "prompt_authority(str(event.sender_id() or ''), "
            "str(actor.user_name or ''), str(getattr(source, 'chat_type', None) or ''))"
        )
        if (len(call.keywords) != 1 or call.keywords[0].arg != "authority"
                or not _same_expression(call.keywords[0].value, authority)):
            return None
    statements = [node for node in function.body
                  if isinstance(node, ast.Expr) and node.value is call]
    if len(statements) != 1:
        return None
    statement = statements[0]
    if statement.end_lineno is None or statement.end_col_offset is None:
        return None
    lines = content.splitlines()
    prefix = lines[statement.lineno - 1].encode("utf-8")[:statement.col_offset]
    suffix = lines[statement.end_lineno - 1].encode("utf-8")[statement.end_col_offset:]
    if prefix.strip() or (suffix.strip() and not suffix.lstrip().startswith(b"#")):
        return None
    return statement

