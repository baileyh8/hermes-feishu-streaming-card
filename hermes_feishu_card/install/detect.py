from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
import re
import subprocess


MIN_SUPPORTED_VERSION = "v2026.4.23"
HANDLER_NAME = "_handle_message_with_agent"
CORE_CAPABILITIES = ("message_handler", "completion_return")
OPTIONAL_CAPABILITIES = (
    "run_agent",
    "tool_callback",
    "answer_delta_callback",
    "thinking_delta_callback",
    "cron_delivery",
    "reply_context",
    "attachment_delivery",
)
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


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
    hook_strategy: str = ""
    cron_py: Path | None = None
    cron_py_exists: bool = False
    cron_hook_strategy: str = ""
    compatibility: str = "unsupported"
    capabilities: dict[str, bool] = field(default_factory=dict)


def detect_hermes(root: str | Path) -> HermesDetection:
    hermes_root = Path(root)
    run_py = hermes_root / "gateway" / "run.py"
    cron_py = hermes_root / "cron" / "scheduler.py"
    version, version_error, version_source = _read_version(hermes_root / "VERSION")
    if version == "unknown" and version_error is None:
        git_version = _read_git_version(hermes_root)
        if git_version != "unknown":
            version = git_version
            version_source = "git tag"

    def result(
        supported: bool,
        reason: str,
        *,
        hook_strategy: str = "",
        compatibility: str = "unsupported",
        capabilities: dict[str, bool] | None = None,
    ) -> HermesDetection:
        return HermesDetection(
            root=hermes_root,
            version=version,
            version_source=version_source,
            minimum_version=MIN_SUPPORTED_VERSION,
            run_py=run_py,
            run_py_exists=run_py.exists(),
            supported=supported,
            reason=reason,
            hook_strategy=hook_strategy,
            cron_py=cron_py,
            cron_py_exists=cron_py.exists(),
            cron_hook_strategy="cron_scheduler" if cron_py.exists() else "",
            compatibility=compatibility,
            capabilities=capabilities or {},
        )

    if not run_py.exists():
        return result(False, "gateway/run.py missing")

    if run_py.is_symlink():
        return result(False, "gateway/run.py must not be a symlink")

    if version_error is not None:
        return result(False, version_error)

    parsed_version = _parse_version(version)
    if parsed_version is None:
        return result(False, "Hermes VERSION missing, unknown, or invalid")
    hook_strategy = _select_hook_strategy(version)
    minimum_version = _parse_version(MIN_SUPPORTED_VERSION)
    if hook_strategy == "legacy_gateway_run" and minimum_version is not None and parsed_version < minimum_version:
        return result(False, f"Hermes version must be at least {MIN_SUPPORTED_VERSION}")

    contents, run_py_error = _read_text(run_py, "gateway/run.py")
    if run_py_error is not None:
        return result(False, run_py_error)

    cron_contents = ""
    cron_error = None
    if cron_py.exists():
        if cron_py.is_symlink():
            cron_error = "cron/scheduler.py must not be a symlink"
        else:
            cron_contents, cron_error = _read_text(cron_py, "cron/scheduler.py")

    if cron_error is not None:
        return result(False, cron_error)

    capabilities, capability_error = _detect_capabilities(contents, cron_contents)
    core_ok = all(capabilities.get(name, False) for name in CORE_CAPABILITIES)
    optional_ok = all(capabilities.get(name, False) for name in OPTIONAL_CAPABILITIES)
    if core_ok and optional_ok:
        compatibility = "full"
    elif core_ok:
        compatibility = "partial"
    else:
        compatibility = "unsupported"
    if not core_ok:
        return result(
            False,
            capability_error,
            hook_strategy=hook_strategy,
            compatibility=compatibility,
            capabilities=capabilities,
        )

    return result(
        True,
        "supported",
        hook_strategy=hook_strategy,
        compatibility=compatibility,
        capabilities=capabilities,
    )


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


def _select_hook_strategy(version: str) -> str:
    parsed = _parse_version(version)
    if parsed is None:
        return ""
    if (parsed[0] == 0 and parsed >= (0, 13, 0)) or parsed >= (2026, 5, 0):
        return "gateway_run_013_plus"
    return "legacy_gateway_run"


def _detect_capabilities(
    contents: str, cron_contents: str = ""
) -> tuple[dict[str, bool], str]:
    try:
        module = ast.parse(contents)
    except SyntaxError as exc:
        return {}, f"gateway/run.py could not be parsed: {exc.__class__.__name__}"

    handler = _find_supported_handler(module)
    if handler is None:
        completion_return = None
    else:
        completion_return = _find_completion_return(handler)

    capabilities = {
        "message_handler": handler is not None,
        "completion_return": completion_return is not None,
        "run_agent": _find_run_agent(module) is not None,
        "tool_callback": _find_callback(module, "progress_callback") is not None,
        "answer_delta_callback": _find_callback(module, "_stream_delta_cb") is not None,
        "thinking_delta_callback": _find_callback(module, "_interim_assistant_cb") is not None,
        "cron_delivery": _has_cron_delivery(contents, cron_contents),
        "reply_context": "reply_to_message_id" in contents
        or "_reply_anchor_for_event" in contents,
        "attachment_delivery": "extract_media" in contents
        or "_deliver_media_from_response" in contents,
    }

    if not capabilities["message_handler"]:
        return capabilities, f"gateway/run.py missing async anchor function: {HANDLER_NAME}"
    if not capabilities["completion_return"]:
        return capabilities, 'gateway/run.py missing handler anchor: hooks.emit("agent:end", ...)'

    return capabilities, "supported"


def _has_cron_delivery(contents: str, cron_contents: str) -> bool:
    if _find_deliver_result_in_contents(contents):
        return True
    if cron_contents and _find_deliver_result_in_contents(cron_contents):
        return True
    return False


def _find_deliver_result_in_contents(contents: str) -> bool:
    try:
        module = ast.parse(contents)
    except SyntaxError:
        return False
    return _find_function(module, "_deliver_result") is not None


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


def _find_completion_return(handler: ast.AsyncFunctionDef) -> ast.Call | None:
    visitor = _HandlerCompletionVisitor()
    visitor.visit_statements(handler.body)
    return visitor.agent_end_node


def _find_run_agent(module: ast.Module) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    return _find_function(module, "_run_agent")


def _find_function(
    module: ast.Module, name: str
) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    for node in module.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
        if isinstance(node, ast.ClassDef):
            method = _find_direct_class_function(node, name)
            if method is not None:
                return method
    return None


def _find_direct_class_function(
    class_node: ast.ClassDef, name: str
) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    return next(
        (
            node
            for node in class_node.body
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name == name
        ),
        None,
    )


def _find_callback(
    module: ast.Module, name: str
) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    for node in ast.walk(module):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    return None


class _HandlerCompletionVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.agent_end_node: ast.Call | None = None

    def visit_statements(self, statements: list[ast.stmt]) -> None:
        for statement in statements:
            if self.agent_end_node is not None:
                return
            self.visit(statement)
            if isinstance(statement, (ast.Return, ast.Raise)):
                return

    def visit_Call(self, node: ast.Call) -> None:
        if _is_agent_end_emit_call(node):
            self.agent_end_node = node
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
