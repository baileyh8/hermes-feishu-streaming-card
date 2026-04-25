PATCH_BEGIN = "# HERMES_FEISHU_CARD_PATCH_BEGIN"
PATCH_END = "# HERMES_FEISHU_CARD_PATCH_END"

_HANDLER_NAME = "_handle_message_with_agent"


def apply_patch(content: str) -> str:
    """Insert the Feishu card hook block into a Hermes message handler."""
    has_begin = PATCH_BEGIN in content
    has_end = PATCH_END in content
    if has_begin and has_end:
        return content
    if has_begin or has_end:
        raise ValueError("corrupt patch markers")

    lines = content.splitlines(keepends=True)
    def_line_index = _find_handler_line(lines)
    if def_line_index is None:
        raise ValueError("could not find safe handler")

    def_indent = _leading_spaces(lines[def_line_index])
    body_indent = def_indent + " " * 4
    insert_at = def_line_index + 1
    hook = _render_hook_block(body_indent)

    return "".join(lines[:insert_at] + hook + lines[insert_at:])


def remove_patch(content: str) -> str:
    """Remove the Feishu card hook block from patched Hermes content."""
    lines = content.splitlines(keepends=True)
    begin_indexes = [index for index, line in enumerate(lines) if PATCH_BEGIN in line]
    end_indexes = [index for index, line in enumerate(lines) if PATCH_END in line]

    if not begin_indexes and not end_indexes:
        return content
    if len(begin_indexes) != 1 or len(end_indexes) != 1:
        raise ValueError("corrupt patch markers")

    begin_index = begin_indexes[0]
    end_index = end_indexes[0]
    if begin_index >= end_index:
        raise ValueError("corrupt patch markers")

    return "".join(lines[:begin_index] + lines[end_index + 1 :])


def _find_handler_line(lines):
    for index, line in enumerate(lines):
        if _is_module_level_handler(line):
            return index

    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_module_level_class(line):
            class_indent_len = len(_leading_spaces(line))
            method_indent = " " * (class_indent_len + 4)
            index += 1
            while index < len(lines):
                candidate = lines[index]
                if _ends_module_level_class(candidate, class_indent_len):
                    break
                if _is_handler_def(candidate, method_indent):
                    return index
                index += 1
            continue
        index += 1
    return None


def _render_hook_block(indent: str):
    inner_indent = indent + " " * 4
    return [
        f"{indent}{PATCH_BEGIN}\n",
        f"{indent}try:\n",
        f"{inner_indent}pass\n",
        f"{indent}except Exception:\n",
        f"{inner_indent}pass\n",
        f"{indent}{PATCH_END}\n",
    ]


def _is_module_level_handler(line: str) -> bool:
    return _is_handler_def(line, "")


def _is_module_level_class(line: str) -> bool:
    stripped = line.lstrip(" ")
    return stripped.startswith("class ") and _leading_spaces(line) == ""


def _ends_module_level_class(line: str, class_indent_len: int) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    return len(_leading_spaces(line)) <= class_indent_len


def _is_handler_def(line: str, indent: str) -> bool:
    stripped = line.lstrip(" ")
    return (
        line.startswith(indent)
        and not line.startswith(indent + " ")
        and stripped.startswith(f"async def {_HANDLER_NAME}(")
    )


def _leading_spaces(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" "))]
