from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


MIN_SUPPORTED_VERSION = "v2026.4.23"
HANDLER_NAME = "_handle_message_with_agent"
_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class HermesDetection:
    root: Path
    version: str
    version_source: str
    minimum_version: str
    run_py: Path
    run_py_exists: bool
    supported: bool
    reason: str


def detect_hermes(root: str | Path) -> HermesDetection:
    hermes_root = Path(root)
    run_py = hermes_root / "gateway" / "run.py"
    version, version_error, version_source = _read_version(hermes_root / "VERSION")
    if version == "unknown" and version_error is None:
        git_version = _read_git_version(hermes_root)
        if git_version != "unknown":
            version = git_version
            version_source = "git tag"

    def result(supported: bool, reason: str) -> HermesDetection:
        return HermesDetection(
            root=hermes_root,
            version=version,
            version_source=version_source,
            minimum_version=MIN_SUPPORTED_VERSION,
            run_py=run_py,
            run_py_exists=run_py.exists(),
            supported=supported,
            reason=reason,
        )

    if not run_py.exists():
        return result(False, "gateway/run.py missing")

    if run_py.is_symlink():
        return result(False, "gateway/run.py must not be a symlink")

    if version_error is not None:
        return result(False, version_error)

    parsed_version = _parse_version(version)
    minimum_version = _parse_version(MIN_SUPPORTED_VERSION)
    if parsed_version is None:
        return result(False, "Hermes VERSION missing, unknown, or invalid")
    if minimum_version is not None and parsed_version < minimum_version:
        return result(False, f"Hermes version must be at least {MIN_SUPPORTED_VERSION}")

    contents, run_py_error = _read_text(run_py, "gateway/run.py")
    if run_py_error is not None:
        return result(False, run_py_error)

    has_anchor, anchor_error = _has_supported_handler_anchor(contents)
    if not has_anchor:
        return result(False, anchor_error)

    return result(True, "supported")


def _read_version(path: Path) -> tuple[str, str | None, str]:
    if not path.exists():
        return "unknown", None, "unknown"
    contents, error = _read_text(path, "VERSION")
    if error is not None:
        return "unknown", error, "VERSION"
    return contents.strip() or "unknown", None, "VERSION"


def _read_git_version(root: Path) -> str:
    if _git_toplevel(root) != root.resolve():
        return "unknown"
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _git_toplevel(root: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = result.stdout.strip()
    if not output:
        return None
    return Path(output).resolve()


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

    handler = _find_supported_handler(module)
    if handler is None:
        return False, f"gateway/run.py missing async anchor function: {HANDLER_NAME}"

    if not _function_emits_agent_end(handler):
        return False, 'gateway/run.py missing handler anchor: hooks.emit("agent:end", ...)'

    return True, "supported"


def _find_supported_handler(module: ast.Module) -> ast.AsyncFunctionDef | None:
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == HANDLER_NAME:
            return node
        if isinstance(node, ast.ClassDef):
            method = _find_direct_class_handler(node)
            if method is not None:
                return method
    return None


def _find_direct_class_handler(class_node: ast.ClassDef) -> ast.AsyncFunctionDef | None:
    return next(
        (
            node
            for node in class_node.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == HANDLER_NAME
        ),
        None,
    )


def _function_emits_agent_end(handler: ast.AsyncFunctionDef) -> bool:
    visitor = _HandlerBodyHookVisitor()
    visitor.visit_statements(handler.body)
    return visitor.found


class _HandlerBodyHookVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found = False

    def visit_statements(self, statements: list[ast.stmt]) -> None:
        for statement in statements:
            if self.found:
                return
            self.visit(statement)
            if isinstance(statement, (ast.Return, ast.Raise)):
                return

    def visit_Call(self, node: ast.Call) -> None:
        if _is_agent_end_emit_call(node):
            self.found = True
            return
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        static_value = _static_bool(node.test)
        if static_value is True:
            self.visit_statements(node.body)
        elif static_value is False:
            self.visit_statements(node.orelse)
        else:
            self.visit_statements(node.body)
            self.visit_statements(node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self.visit_statements(node.body)
        self.visit_statements(node.orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_statements(node.body)
        self.visit_statements(node.orelse)

    def visit_While(self, node: ast.While) -> None:
        static_value = _static_bool(node.test)
        if static_value is True:
            self.visit_statements(node.body)
        elif static_value is False:
            self.visit_statements(node.orelse)
        else:
            self.visit_statements(node.body)
            self.visit_statements(node.orelse)

    def visit_Try(self, node: ast.Try) -> None:
        self.visit_statements(node.body)
        for handler in node.handlers:
            self.visit_statements(handler.body)
        self.visit_statements(node.orelse)
        self.visit_statements(node.finalbody)

    def visit_With(self, node: ast.With) -> None:
        self.visit_statements(node.body)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_statements(node.body)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _static_bool(node: ast.expr) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Constant) and node.value in (0, 1):
        return bool(node.value)
    return None


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
