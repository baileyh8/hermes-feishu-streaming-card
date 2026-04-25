import ast


PATCH_BEGIN = "# HERMES_FEISHU_CARD_PATCH_BEGIN"
PATCH_END = "# HERMES_FEISHU_CARD_PATCH_END"

_HANDLER_NAME = "_handle_message_with_agent"
_NO_FINAL_NEWLINE = "# HERMES_FEISHU_CARD_NO_FINAL_NEWLINE"


def apply_patch(content: str) -> str:
    """Insert the Feishu card hook block into a safe Hermes message handler."""
    owned_block = _find_owned_block(content)
    if owned_block is not None:
        return content

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


def remove_patch(content: str) -> str:
    """Remove the owned Feishu card hook block from patched Hermes content."""
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
    if begin_count == 0 and end_count == 0:
        return None
    if begin_count != 1 or end_count != 1:
        raise ValueError("corrupt patch markers")

    lines = content.splitlines(keepends=True)
    begin_index = _exact_marker_line_index(lines, PATCH_BEGIN)
    end_index = _exact_marker_line_index(lines, PATCH_END)
    if begin_index is None or end_index is None or begin_index >= end_index:
        raise ValueError("corrupt patch markers")

    _validate_sentinel_marker_adjacency(lines, begin_index)

    indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
    newline = _line_ending(lines[begin_index]) or _detect_newline(content)
    expected = _render_hook_block(indent, newline)
    actual = lines[begin_index : end_index + 1]

    if actual != expected:
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


def _parse_content_with_markers(content: str):
    try:
        return ast.parse(content)
    except SyntaxError as exc:
        raise ValueError("corrupt patch markers") from exc


def _validate_sentinel_marker_adjacency(lines, begin_index: int) -> None:
    sentinel_indexes = [
        index
        for index, line in enumerate(lines)
        if _strip_line_ending(line)
        == _leading_whitespace(_strip_line_ending(line)) + _NO_FINAL_NEWLINE
    ]
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
