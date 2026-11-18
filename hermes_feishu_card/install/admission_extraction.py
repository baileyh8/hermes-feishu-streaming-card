"""Optional, exact post-extraction display seam for the pinned admission topology."""
from __future__ import annotations

import ast
from hashlib import sha256
import textwrap

BEGIN = "# HERMES_FEISHU_CARD_ADMISSION_EXTRACT_BEGIN"
END = "# HERMES_FEISHU_CARD_ADMISSION_EXTRACT_END"
# Exact _extract_response_content source hashes. Unknown bodies disable the seam
# instead of inserting on an unverified extractor.
_SOURCE = "4677ef94692c1032aec1c0d7d5fee4bb2ae64c02219cc84e9f319e071c8da5f1"
_SOURCE_0232 = "aa52c9a23e996c57e79c99e9f0b2b3c0e2edf7ab496657881558671de5bcac9b"
_LINES = (
    BEGIN,
    "try:",
    "    from hermes_feishu_card.hook_runtime import capture_admission_base_extraction",
    "    capture_admission_base_extraction(locals())",
    "except Exception:",
    "    pass",
    END,
)
# v0.23.2 returns footer_onto_photo_caption(...). Capture the delivered fields
# after that wrap, otherwise a footer-only photo caption would ACK stale text.
_RETURN_0232_FIRST = (
    "        return footer_onto_photo_caption(self, _ExtractedResponse("
    "text_content=text_content, images=images, media_files=media_files,\n"
)
_RETURN_0232_REST = (
    "            local_files=local_files, force_document_attachments=force_document, "
    "pre_extract=pre_extract), _IMAGE_EXTS)  # #74547\n"
)
_RETURN_0232 = _RETURN_0232_FIRST + _RETURN_0232_REST
_ASSIGN_0232 = _RETURN_0232.replace("        return ", "        _hfc_extracted = ", 1)
_LINES_0232 = (
    BEGIN,
    "try:",
    "    from hermes_feishu_card.hook_runtime import capture_admission_base_extraction",
    "    capture_admission_base_extraction({",
    "        **locals(),",
    "        \"text_content\": _hfc_extracted.text_content,",
    "        \"images\": _hfc_extracted.images,",
    "        \"media_files\": _hfc_extracted.media_files,",
    "        \"local_files\": _hfc_extracted.local_files,",
    "        \"force_document_attachments\": _hfc_extracted.force_document_attachments,",
    "        \"pre_extract\": _hfc_extracted.pre_extract,",
    "    })",
    "except Exception:",
    "    pass",
    END,
)
_RETURN_EXTRACTED = "        return _hfc_extracted\n"


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
    hook = [line.rstrip("\r\n") for line in lines[start : end + 1]]
    if hook == [indent + line for line in _LINES_0232]:
        return _remove_0232(content, lines, start, end)
    if hook != [indent + line for line in _LINES]:
        raise ValueError("unsafe admission extraction block")
    del lines[start : end + 1]
    restored = "".join(lines)
    if _anchor(restored) is None:
        raise ValueError("unsafe admission extraction source")
    return restored


def _remove_0232(content: str, lines: list[str], start: int, end: int) -> str:
    assign_start = _assignment_start(lines, start)
    if assign_start is None:
        raise ValueError("unsafe admission extraction block")
    if end + 1 >= len(lines) or lines[end + 1] != _RETURN_EXTRACTED:
        raise ValueError("unsafe admission extraction block")
    if "".join(lines[assign_start:start]) != _ASSIGN_0232:
        raise ValueError("unsafe admission extraction block")
    lines[assign_start : end + 2] = [_RETURN_0232] if False else list(
        # Keep newline form of the original two-line return.
        [_RETURN_0232_FIRST, _RETURN_0232_REST]
        if content.endswith("\n") or True
        else [_RETURN_0232]
    )
    # The slice assignment above always uses the two original physical lines.
    restored = "".join(lines)
    if _anchor(restored) != "footer_caption":
        raise ValueError("unsafe admission extraction source")
    return restored


def _assignment_start(lines: list[str], hook_start: int) -> int | None:
    prefix = "".join(lines[:hook_start])
    if not prefix.endswith(_ASSIGN_0232):
        return None
    # Count lines in the exact assignment text.
    assign_lines = _ASSIGN_0232.splitlines(keepends=True)
    start = hook_start - len(assign_lines)
    if start < 0 or "".join(lines[start:hook_start]) != _ASSIGN_0232:
        return None
    return start


def _anchor(content: str) -> str | None:
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
    digest = sha256(source.encode()).hexdigest()
    if digest == _SOURCE:
        return "inline"
    if digest == _SOURCE_0232:
        last = "".join(lines[method.body[-1].lineno - 1 : method.body[-1].end_lineno])
        if last != _RETURN_0232:
            return None
        return "footer_caption"
    return None


def apply(content: str) -> str:
    restored = remove(content)
    variant = _anchor(restored)
    if variant is None:
        return content  # Ordinary Base remains supported; admission handoff stays disabled.
    if variant == "inline":
        return _apply_inline(restored)
    return _apply_0232(restored)


def _apply_inline(restored: str) -> str:
    tree = ast.parse(restored)
    method = [
        n
        for owner in tree.body
        if isinstance(owner, ast.ClassDef) and owner.name == "BasePlatformAdapter"
        for n in owner.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_extract_response_content"
    ][0]
    lines = restored.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in restored else "\n"
    anchor = method.body[-1].lineno - 1
    indent = lines[anchor][: len(lines[anchor]) - len(lines[anchor].lstrip())]
    lines[anchor:anchor] = [indent + line + newline for line in _LINES]
    return "".join(lines)


def _apply_0232(restored: str) -> str:
    tree = ast.parse(restored)
    method = [
        n
        for owner in tree.body
        if isinstance(owner, ast.ClassDef) and owner.name == "BasePlatformAdapter"
        for n in owner.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_extract_response_content"
    ][0]
    start = method.body[-1].lineno - 1
    end = method.body[-1].end_lineno
    lines = restored.splitlines(keepends=True)
    if "".join(lines[start:end]) != _RETURN_0232:
        return restored
    newline = "\r\n" if "\r\n" in restored else "\n"
    indent = "        "
    hook = [indent + line + newline for line in _LINES_0232]
    replacement = [*_ASSIGN_0232.splitlines(keepends=True), *hook, _RETURN_EXTRACTED]
    if newline == "\r\n":
        replacement = [line.replace("\n", "\r\n") if not line.endswith("\r\n") else line for line in replacement]
    lines[start:end] = replacement
    return "".join(lines)
