"""Optional, exact post-extraction display seam for the pinned admission topology."""
from __future__ import annotations

import ast
from hashlib import sha256
import textwrap

BEGIN = "# HERMES_FEISHU_CARD_ADMISSION_EXTRACT_BEGIN"
END = "# HERMES_FEISHU_CARD_ADMISSION_EXTRACT_END"
_SOURCE = "4677ef94692c1032aec1c0d7d5fee4bb2ae64c02219cc84e9f319e071c8da5f1"
_LINES = (
    BEGIN,
    "try:",
    "    from hermes_feishu_card.hook_runtime import capture_admission_base_extraction",
    "    capture_admission_base_extraction(locals())",
    "except Exception:",
    "    pass",
    END,
)


def remove(content: str) -> str:
    lines = content.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if not starts and not ends:
        return content
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise ValueError("unsafe admission extraction block")
    start, end = starts[0], ends[0]
    indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
    if [line.rstrip("\r\n") for line in lines[start : end + 1]] != [
        indent + line for line in _LINES
    ]:
        raise ValueError("unsafe admission extraction block")
    del lines[start : end + 1]
    restored = "".join(lines)
    if _anchor(restored) is None:
        raise ValueError("unsafe admission extraction source")
    return restored


def _anchor(content: str) -> int | None:
    tree = ast.parse(content)
    methods = [
        n
        for owner in tree.body
        if isinstance(owner, ast.ClassDef) and owner.name == "BasePlatformAdapter"
        for n in owner.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_extract_response_content"
    ]
    if len(methods) != 1:
        return None
    method = methods[0]
    lines = content.splitlines(keepends=True)
    source = textwrap.dedent(
        "".join(lines[method.lineno - 1 : method.end_lineno])
    ).replace("\r\n", "\n")
    if sha256(source.encode()).hexdigest() != _SOURCE:
        return None
    return method.body[-1].lineno - 1


def apply(content: str) -> str:
    restored = remove(content)
    anchor = _anchor(restored)
    if anchor is None:
        return content  # Ordinary Base remains supported; admission handoff stays disabled.
    lines = restored.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in restored else "\n"
    indent = lines[anchor][: len(lines[anchor]) - len(lines[anchor].lstrip())]
    lines[anchor:anchor] = [indent + line + newline for line in _LINES]
    return "".join(lines)
