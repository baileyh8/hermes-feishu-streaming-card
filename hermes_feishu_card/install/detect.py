from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re


MIN_SUPPORTED_VERSION = "v2026.4.23"
HANDLER_NAME = "_handle_message_with_agent"
_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class HermesDetection:
    root: Path
    version: str
    run_py: Path
    supported: bool
    reason: str


def detect_hermes(root: str | Path) -> HermesDetection:
    hermes_root = Path(root)
    run_py = hermes_root / "gateway" / "run.py"
    version, version_error = _read_version(hermes_root / "VERSION")

    if not run_py.exists():
        return HermesDetection(
            root=hermes_root,
            version=version,
            run_py=run_py,
            supported=False,
            reason="gateway/run.py missing",
        )

    if version_error is not None:
        return HermesDetection(
            root=hermes_root,
            version=version,
            run_py=run_py,
            supported=False,
            reason=version_error,
        )

    parsed_version = _parse_version(version)
    minimum_version = _parse_version(MIN_SUPPORTED_VERSION)
    if parsed_version is None:
        return HermesDetection(
            root=hermes_root,
            version=version,
            run_py=run_py,
            supported=False,
            reason="Hermes VERSION missing, unknown, or invalid",
        )
    if minimum_version is not None and parsed_version < minimum_version:
        return HermesDetection(
            root=hermes_root,
            version=version,
            run_py=run_py,
            supported=False,
            reason=f"Hermes version must be at least {MIN_SUPPORTED_VERSION}",
        )

    contents, run_py_error = _read_text(run_py, "gateway/run.py")
    if run_py_error is not None:
        return HermesDetection(
            root=hermes_root,
            version=version,
            run_py=run_py,
            supported=False,
            reason=run_py_error,
        )

    has_anchor, anchor_error = _has_supported_handler_anchor(contents)
    if not has_anchor:
        return HermesDetection(
            root=hermes_root,
            version=version,
            run_py=run_py,
            supported=False,
            reason=anchor_error,
        )

    return HermesDetection(
        root=hermes_root,
        version=version,
        run_py=run_py,
        supported=True,
        reason="supported",
    )


def _read_version(path: Path) -> tuple[str, str | None]:
    if not path.exists():
        return "unknown", None
    contents, error = _read_text(path, "VERSION")
    if error is not None:
        return "unknown", error
    return contents.strip() or "unknown", None


def _read_text(path: Path, label: str) -> tuple[str, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeError) as exc:
        return "", f"{label} could not be read: {exc.__class__.__name__}"


def _parse_version(version: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.match(version.strip())
    if match is None:
        return None
    # Treat components as semantic numeric fields, not calendar month/day bounds.
    return tuple(int(part) for part in match.groups())


def _has_supported_handler_anchor(contents: str) -> tuple[bool, str]:
    try:
        module = ast.parse(contents)
    except SyntaxError as exc:
        return False, f"gateway/run.py could not be parsed: {exc.__class__.__name__}"

    handler = next(
        (
            node
            for node in ast.walk(module)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == HANDLER_NAME
        ),
        None,
    )
    if handler is None:
        return False, f"gateway/run.py missing async anchor function: {HANDLER_NAME}"

    if not _function_emits_agent_end(handler):
        return False, 'gateway/run.py missing handler anchor: hooks.emit("agent:end", ...)'

    return True, "supported"


def _function_emits_agent_end(handler: ast.AsyncFunctionDef) -> bool:
    visitor = _HandlerBodyHookVisitor()
    for statement in handler.body:
        visitor.visit(statement)
    return visitor.found


class _HandlerBodyHookVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found = False

    def visit_Call(self, node: ast.Call) -> None:
        if _is_agent_end_emit_call(node):
            self.found = True
            return
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _is_agent_end_emit_call(node: ast.Call) -> bool:
    return (
        _is_hooks_emit(node.func)
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "agent:end"
    )


def _is_hooks_emit(func: ast.expr) -> bool:
    if not isinstance(func, ast.Attribute) or func.attr != "emit":
        return False

    receiver = func.value
    if isinstance(receiver, ast.Name):
        return receiver.id == "hooks"

    return (
        isinstance(receiver, ast.Attribute)
        and receiver.attr == "hooks"
        and isinstance(receiver.value, ast.Name)
        and receiver.value.id == "self"
    )
