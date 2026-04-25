import ast


PATCH_BEGIN = "# HERMES_FEISHU_CARD_PATCH_BEGIN"
PATCH_END = "# HERMES_FEISHU_CARD_PATCH_END"

_HANDLER_NAME = "_handle_message_with_agent"


def apply_patch(content: str) -> str:
    """Insert the Feishu card hook block into a safe Hermes message handler."""
    owned_block = _find_owned_block(content)
    if owned_block is not None:
        return content

    tree = _parse_content(content)
    handler_body = _find_handler_body_location(tree)
    if handler_body is None:
        raise ValueError("could not find safe handler")

    newline = _detect_newline(content)
    lines = content.splitlines(keepends=True)
    insert_at, body_indent = handler_body
    hook = _render_hook_block(body_indent, newline)

    return "".join(lines[:insert_at] + hook + lines[insert_at:])


def remove_patch(content: str) -> str:
    """Remove the owned Feishu card hook block from patched Hermes content."""
    owned_block = _find_owned_block(content)
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


def _find_handler_body_location(tree):
    for node in tree.body:
        if _is_handler(node):
            return _body_location(node)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if _is_handler(child):
                    return _body_location(child)

    return None


def _body_location(node):
    if not node.body:
        return None

    insert_before = _first_patchable_body_node(node)
    if insert_before is None or insert_before.lineno is None:
        return None
    insert_at = insert_before.lineno - 1
    body_indent = getattr(insert_before, "col_offset", node.col_offset + 4)
    return insert_at, " " * body_indent


def _first_patchable_body_node(node):
    if not _is_docstring_expr(node.body[0]):
        return node.body[0]
    if len(node.body) < 2:
        return None
    return node.body[1]


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

    indent = _leading_spaces(_strip_line_ending(lines[begin_index]))
    newline = _line_ending(lines[begin_index]) or _detect_newline(content)
    expected = _render_hook_block(indent, newline)
    actual = lines[begin_index : end_index + 1]

    if actual != expected:
        raise ValueError("corrupt patch markers")

    tree = _parse_content_with_markers(content)
    handler_body = _find_handler_body_location(tree)
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


def _exact_marker_line_index(lines, marker: str):
    for index, line in enumerate(lines):
        body = _strip_line_ending(line)
        if body == _leading_spaces(body) + marker:
            return index
    return None


def _render_hook_block(indent: str, newline: str):
    inner_indent = indent + " " * 4
    return [
        f"{indent}{PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}except Exception:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}{PATCH_END}{newline}",
    ]


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


def _leading_spaces(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" "))]
