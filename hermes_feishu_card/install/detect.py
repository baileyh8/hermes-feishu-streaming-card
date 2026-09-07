from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
import re
import subprocess

from . import patcher
from .native_hooks import (
    FIXED_TAG_COMMIT,
    NativeHookCapabilityProbe,
    probe_native_hook_capabilities,
)
from .patcher import apply_base_patch, remove_base_patch
from ..integration import (
    IntegrationDecision,
    PatchCapabilities,
    select_integration_mode,
)


MIN_SUPPORTED_VERSION = "v2026.4.23"
HANDLER_NAME = "_handle_message_with_agent"
CORE_CAPABILITIES = ("message_handler", "completion_return")
OPTIONAL_CAPABILITIES = (
    "run_agent",
    "tool_callback",
    "answer_delta_callback",
    "thinking_delta_callback",
    "status_callback",
    "cron_delivery",
    "reply_context",
    "attachment_delivery",
)
_VERSION_RE = re.compile(r"(?<!\d)v?(\d+(?:\.\d+)+)(?!\d)")
_HERMES_PROJECT_RE = re.compile(r"(?im)^\s*Project:\s*(.+?)\s*$")


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
    base_py: Path | None = None
    base_py_exists: bool = False
    base_hook_strategy: str = ""
    base_required: bool = False
    compatibility: str = "unsupported"
    capabilities: dict[str, bool] = field(default_factory=dict)
    capability_locations: dict[str, tuple[str, ...]] = field(default_factory=dict)
    gateway_files: tuple[str, ...] = ()
    decomposed: bool = False
    suggested_root: Path | None = None
    suggestion_reason: str = ""


@dataclass(frozen=True)
class FixedTagIntegrationDetection:
    native_probe: NativeHookCapabilityProbe
    decision: IntegrationDecision


def detect_fixed_tag_integration(
    root: str | Path,
    *,
    runtime_python: str | Path,
) -> FixedTagIntegrationDetection:
    native_probe = probe_native_hook_capabilities(
        root,
        expected_commit=FIXED_TAG_COMMIT,
        runtime_python=runtime_python,
    )
    patch_capabilities = PatchCapabilities.from_names(
        patcher.HYBRID_PATCH_REGISTRY.available_groups
    )
    decision = select_integration_mode(
        native_probe.capabilities,
        patch_capabilities,
    )
    return FixedTagIntegrationDetection(
        native_probe=native_probe,
        decision=decision,
    )


def detect_native_hook_capabilities(
    root: str | Path,
    *,
    expected_commit: str,
    runtime_python: str | Path,
) -> NativeHookCapabilityProbe:
    """Produce fixed-source and real PluginManager facts; never infer hooks."""
    return probe_native_hook_capabilities(
        root,
        expected_commit=expected_commit,
        runtime_python=runtime_python,
    )


def detect_hermes(root: str | Path) -> HermesDetection:
    hermes_root = Path(root)
    run_py = hermes_root / "gateway" / "run.py"
    cron_py = hermes_root / "cron" / "scheduler.py"
    base_py = hermes_root / "gateway" / "platforms" / "base.py"
    gateway_sources = {}
    capability_locations = {}
    decomposed = any((hermes_root / name).exists() for name in patcher.DECOMPOSED_GATEWAY_TARGETS)
    cron_sources = {}
    version, version_error, version_source = _read_version(hermes_root / "VERSION")
    if version == "unknown" and version_error is None:
        package_version = _read_static_package_version(
            hermes_root / "hermes_cli" / "__init__.py"
        )
        if package_version != "unknown":
            version = package_version
            version_source = "hermes_cli.__version__"
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
        suggested_root: Path | None = None,
        suggestion_reason: str = "",
        base_required: bool = False,
        base_hook_strategy: str = "",
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
            base_py=base_py,
            base_py_exists=base_py.exists(),
            base_hook_strategy=base_hook_strategy,
            base_required=base_required,
            compatibility=compatibility,
            capabilities=capabilities or {},
            capability_locations=capability_locations,
            gateway_files=tuple(gateway_sources),
            decomposed=decomposed,
            suggested_root=suggested_root,
            suggestion_reason=suggestion_reason,
        )

    if not run_py.exists():
        suggested_root = _detect_hermes_cli_project_root(hermes_root)
        reason = "gateway/run.py missing"
        suggestion_reason = ""
        if suggested_root is not None:
            reason = f"{reason}; Hermes CLI reports project: {suggested_root}"
            suggestion_reason = "hermes_cli_project"
        return result(
            False,
            reason,
            suggested_root=suggested_root,
            suggestion_reason=suggestion_reason,
        )

    if run_py.is_symlink():
        return result(False, "gateway/run.py must not be a symlink")

    if version_error is not None:
        return result(False, version_error)

    contents, run_py_error = _read_text(run_py, "gateway/run.py")
    if run_py_error is not None:
        return result(False, run_py_error)

    gateway_sources["gateway/run.py"] = contents
    for name in patcher.DECOMPOSED_GATEWAY_TARGETS:
        path = hermes_root / name
        if path.is_symlink():
            return result(False, f"{name} must not be a symlink")
        if path.exists():
            source, error = _read_text(path, name)
            if error:
                return result(False, error)
            gateway_sources[name] = source

    cron_contents = ""
    cron_error = None
    for name in ("cron/scheduler.py", "cron/scheduler_delivery.py"):
        path = hermes_root / name
        if path.is_symlink():
            return result(False, f"{name} must not be a symlink")
        if path.exists():
            source, cron_error = _read_text(path, name)
            if cron_error is not None:
                return result(False, cron_error)
            cron_sources[name] = source
    cron_anchors = [name for name, source in cron_sources.items()
                    if _find_deliver_result_in_contents(source)]
    if len(cron_anchors) > 1:
        return result(False, "ambiguous cron delivery anchors: " + ", ".join(cron_anchors))
    if cron_anchors:
        cron_py = hermes_root / cron_anchors[0]
    cron_contents = cron_sources.get(cron_py.relative_to(hermes_root).as_posix(), "")

    parsed_version = _parse_version(version)
    version_requires_base = bool(
        parsed_version is not None
        and (
            (parsed_version[0] == 0 and parsed_version >= (0, 19, 0))
            or parsed_version >= (2026, 7, 20)
        )
    )
    base_contents = ""
    base_error = None
    if base_py.exists():
        if base_py.is_symlink():
            base_error = "gateway/platforms/base.py must not be a symlink"
        else:
            base_contents, base_error = _read_text(
                base_py, "gateway/platforms/base.py"
            )
    verified_ledger_signals = _has_exact_delivery_ledger_signals(base_contents)
    base_required = decomposed or version_requires_base or verified_ledger_signals
    exact_base_delivery = False
    exact_base_error = ""
    if base_contents and base_error is None:
        exact_base_delivery, exact_base_error = _detect_exact_base_contract(
            base_contents
        )
    elif base_error is not None:
        exact_base_error = base_error
    elif base_required:
        exact_base_error = "gateway/platforms/base.py missing for exact delivery contract"

    if decomposed:
        capabilities, capability_locations, capability_error = _detect_layout_capabilities(gateway_sources, cron_contents, cron_py.relative_to(hermes_root).as_posix())
    else:
        capabilities, capability_error = _detect_capabilities(contents, cron_contents)
        capability_locations = {key: ((cron_py.relative_to(hermes_root).as_posix(),) if key == "cron_delivery" and _find_deliver_result_in_contents(cron_contents) else ("gateway/run.py",)) for key, found in capabilities.items() if found}
    capability_locations["exact_base_delivery"] = ("gateway/platforms/base.py",) if exact_base_delivery else ()
    capabilities["exact_base_delivery"] = exact_base_delivery
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
            compatibility=compatibility,
            capabilities=capabilities,
            base_required=base_required,
        )

    if capability_error != "supported":
        return result(
            False,
            capability_error,
            compatibility=compatibility,
            capabilities=capabilities,
            base_required=base_required,
        )

    if base_required and not exact_base_delivery:
        return result(
            False,
            exact_base_error or "gateway/platforms/base.py exact delivery anchors missing",
            compatibility=compatibility,
            capabilities=capabilities,
            base_required=True,
        )

    if parsed_version is None:
        version_source = (
            "gateway anchors"
            if version == "unknown"
            else f"{version_source} + gateway anchors"
        )
        hook_strategy = _select_hook_strategy_from_capabilities(capabilities)
    else:
        hook_strategy = _select_hook_strategy(version)
        minimum_version = _parse_version(MIN_SUPPORTED_VERSION)
        if (
            hook_strategy == "legacy_gateway_run"
            and minimum_version is not None
            and parsed_version < minimum_version
        ):
            return result(False, f"Hermes version must be at least {MIN_SUPPORTED_VERSION}")

    return result(
        True,
        "supported",
        hook_strategy=hook_strategy,
        compatibility=compatibility,
        capabilities=capabilities,
        base_required=base_required,
        base_hook_strategy=(
            "exact_base_delivery" if exact_base_delivery else ""
        ),
    )


def _has_exact_delivery_ledger_signals(contents: str) -> bool:
    return all(
        signal in contents
        for signal in (
            "compute_obligation_id",
            "record_obligation",
            "mark_attempting",
            "mark_delivered",
            "mark_failed",
        )
    )


def _detect_exact_base_contract(contents: str) -> tuple[bool, str]:
    try:
        patched = apply_base_patch(contents)
        original = remove_base_patch(patched)
        if apply_base_patch(original) != patched:
            return False, "gateway/platforms/base.py exact delivery patch is not reversible"
    except ValueError:
        return False, "gateway/platforms/base.py exact delivery anchors are unsupported"
    return True, ""


def _read_version(path: Path) -> tuple[str, str | None, str]:
    if not path.exists():
        return "unknown", None, "unknown"
    contents, error = _read_text(path, "VERSION")
    if error is not None:
        return "unknown", error, "VERSION"
    return contents.strip() or "unknown", None, "VERSION"


def _read_static_package_version(path: Path) -> str:
    """Read Hermes' literal package version without importing its code."""
    if not path.exists():
        return "unknown"
    contents, error = _read_text(path, "hermes_cli/__init__.py")
    if error is not None:
        return "unknown"
    try:
        tree = ast.parse(contents)
    except SyntaxError:
        return "unknown"
    versions: list[str] = []
    for node in tree.body:
        value = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "__version__"
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__version__"
        ):
            value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            versions.append(value.value.strip())
    if len(versions) != 1 or _parse_version(versions[0]) is None:
        return "unknown"
    return versions[0]


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


def _detect_hermes_cli_project_root(current_root: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["hermes", "-V"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = f"{result.stdout}\n{result.stderr}"
    match = _HERMES_PROJECT_RE.search(output)
    if match is None:
        return None
    candidate = Path(match.group(1).strip()).expanduser()
    try:
        same_root = candidate.resolve() == current_root.resolve()
    except OSError:
        same_root = False
    if same_root:
        return None
    if not (candidate / "gateway" / "run.py").exists():
        return None
    return candidate


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


def _parse_version(version: str) -> tuple[int, ...] | None:
    match = _VERSION_RE.search(version.strip())
    if match is None:
        return None
    # Treat components as semantic numeric fields, not calendar month/day bounds.
    return tuple(int(part) for part in match.group(1).split("."))


def _select_hook_strategy(version: str) -> str:
    parsed = _parse_version(version)
    if parsed is None:
        return ""
    if (parsed[0] == 0 and parsed >= (0, 13, 0)) or parsed >= (2026, 5, 0):
        return "gateway_run_013_plus"
    return "legacy_gateway_run"


def _select_hook_strategy_from_capabilities(capabilities: dict[str, bool]) -> str:
    modern_anchors = (
        "run_agent",
        "tool_callback",
        "answer_delta_callback",
        "thinking_delta_callback",
        "cron_delivery",
        "reply_context",
        "attachment_delivery",
    )
    if any(capabilities.get(name, False) for name in modern_anchors):
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

    callback_capabilities, callback_error = _detect_callback_patchability(
        contents, module
    )
    capabilities = {
        "message_handler": handler is not None,
        "completion_return": completion_return is not None,
        "run_agent": _find_run_agent(module) is not None,
        **callback_capabilities,
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
    if callback_error:
        return capabilities, callback_error

    return capabilities, "supported"


def _detect_callback_patchability(
    contents: str, module: ast.Module
) -> tuple[dict[str, bool], str]:
    try:
        patched = patcher.apply_patch(contents, strategy="gateway_run_013_plus")
    except ValueError:
        # Detection also runs while the recovery planner is inspecting an
        # already-installed file with damaged markers. Preserve the legacy
        # structural capability report here so recovery, not compatibility
        # detection, remains responsible for classifying that state.
        return {
            "tool_callback": _find_callback(module, "progress_callback") is not None,
            "answer_delta_callback": _find_callback(module, "_stream_delta_cb")
            is not None,
            "thinking_delta_callback": _find_callback(
                module, "_interim_assistant_cb"
            )
            is not None,
            "status_callback": _has_patchable_status_callback(module),
        }, ""

    capabilities = {
        "tool_callback": (
            patcher.STABLE_TOOL_PATCH_BEGIN in patched
            or patcher.TOOL_PATCH_BEGIN in patched
        ),
        "answer_delta_callback": patcher.ANSWER_DELTA_PATCH_BEGIN in patched,
        "thinking_delta_callback": patcher.THINKING_DELTA_PATCH_BEGIN in patched,
        "status_callback": patcher.STATUS_PATCH_BEGIN in patched,
    }

    turn_runner = _find_turn_runner(module)
    if turn_runner is None:
        return capabilities, ""

    required_markers = []
    if _turn_runner_assigns_agent_callback(turn_runner, "tool_start_callback"):
        required_markers.append(
            ("tool lifecycle", patcher.STABLE_TOOL_PATCH_BEGIN)
        )
    for callback_name, label, marker in (
        ("_stream_delta_cb", "answer delta", patcher.ANSWER_DELTA_PATCH_BEGIN),
        (
            "_interim_assistant_cb",
            "thinking delta",
            patcher.THINKING_DELTA_PATCH_BEGIN,
        ),
        ("_clarify_callback_sync", "clarify", patcher.CLARIFY_PATCH_BEGIN),
        ("_approval_notify_sync", "approval", patcher.APPROVAL_PATCH_BEGIN),
        ("_status_callback_sync", "status", patcher.STATUS_PATCH_BEGIN),
    ):
        if _turn_runner_has_callback(turn_runner, callback_name):
            required_markers.append((label, marker))

    missing = [label for label, marker in required_markers if marker not in patched]
    if missing:
        return capabilities, (
            "gateway/run.py TurnRunner hooks are not safely patchable: "
            + ", ".join(missing)
        )
    return capabilities, ""


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


def _find_turn_runner(module: ast.Module) -> ast.ClassDef | None:
    return next(
        (
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "TurnRunner"
        ),
        None,
    )


def _turn_runner_has_callback(turn_runner: ast.ClassDef, name: str) -> bool:
    return any(
        isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == name
        for node in ast.walk(turn_runner)
    )


def _turn_runner_assigns_agent_callback(
    turn_runner: ast.ClassDef, attribute: str
) -> bool:
    for node in ast.walk(turn_runner):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(
            isinstance(target, ast.Attribute)
            and target.attr == attribute
            and isinstance(target.value, ast.Name)
            and target.value.id == "agent"
            for target in targets
        ):
            return True
    return False


def _has_patchable_status_callback(module: ast.Module) -> bool:
    run_agent = _find_function(module, "_run_agent_inner") or _find_function(
        module, "_run_agent"
    )
    if run_agent is None:
        return False
    callback = next(
        (
            node
            for node in ast.walk(run_agent)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_status_callback_sync"
        ),
        None,
    )
    if callback is None:
        return False
    required_outer_names = {
        "source",
        "event_message_id",
        "_status_chat_id",
        "_loop_for_step",
        "_run_still_current",
    }
    required_callback_args = {"event_type", "message"}
    return required_outer_names.issubset(_function_scope_names(run_agent)) and (
        required_callback_args.issubset(_function_argument_names(callback))
    )


def _function_scope_names(node: ast.AsyncFunctionDef | ast.FunctionDef) -> set[str]:
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
    return names


def _function_argument_names(node: ast.AsyncFunctionDef | ast.FunctionDef) -> set[str]:
    arguments = node.args
    names = {
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        )
    }
    if arguments.vararg is not None:
        names.add(arguments.vararg.arg)
    if arguments.kwarg is not None:
        names.add(arguments.kwarg.arg)
    return names


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


_LAYOUT_MARKERS = {
    "message_handler": patcher.PATCH_BEGIN,
    "completion_return": patcher.COMPLETE_PATCH_BEGIN,
    "tool_callback": patcher.STABLE_TOOL_PATCH_BEGIN,
    "answer_delta_callback": patcher.ANSWER_DELTA_PATCH_BEGIN,
    "thinking_delta_callback": patcher.THINKING_DELTA_PATCH_BEGIN,
    "status_callback": patcher.STATUS_PATCH_BEGIN,
    "clarify_callback": patcher.CLARIFY_PATCH_BEGIN,
    "approval_callback": patcher.APPROVAL_PATCH_BEGIN,
    "command_card": patcher.COMMAND_CARD_PATCH_BEGIN,
    "hfc_command": patcher.HFC_COMMAND_PATCH_BEGIN,
    "slash_confirm": patcher.SLASH_CONFIRM_PATCH_BEGIN,
    "startup": patcher.COMMAND_CARD_STARTUP_PATCH_BEGIN,
    "redelivery": patcher.NATIVE_REDELIVERY_PATCH_BEGIN,
    "platform_notice": patcher.PLATFORM_NOTICE_PATCH_BEGIN,
}


def _detect_layout_capabilities(sources, cron_contents, cron_target):
    locations = {name: [] for name in (*_LAYOUT_MARKERS, "run_agent", "reply_context", "attachment_delivery", "cron_delivery")}
    errors = []
    for target, content in sources.items():
        try:
            clean = patcher.remove_patch(content)
            tree = ast.parse(clean)
            rendered = patcher.apply_gateway_fragment(clean, target)
            for name, marker in _LAYOUT_MARKERS.items():
                if marker in rendered:
                    locations[name].append(target)
            if _find_run_agent(tree) is not None:
                locations["run_agent"].append(target)
            if any(isinstance(node, ast.Attribute) and node.attr in {
                "reply_to_message_id", "_reply_anchor_for_event"
            } for node in ast.walk(tree)):
                locations["reply_context"].append(target)
            if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr in {"extract_media", "_deliver_media_from_response"}
                   for node in ast.walk(tree)):
                locations["attachment_delivery"].append(target)
            handler = patcher._find_handler_node(tree)
            if handler is not None and patcher._find_decomposed_completion_node(tree) is None:
                errors.append(f"{target}: decomposed completion/agent:end contract missing")
        except (ValueError, SyntaxError) as exc:
            errors.append(f"{target}: unsafe anchors or ownership ({exc})")
    try:
        if patcher.CRON_PATCH_BEGIN in patcher.apply_cron_patch(cron_contents):
            locations["cron_delivery"].append(cron_target)
        else:
            errors.append(f"{cron_target}: missing cron delivery anchor")
    except ValueError:
        errors.append(f"{cron_target}: unsafe cron anchors")
    for name in _LAYOUT_MARKERS:
        if len(locations[name]) > 1:
            errors.append(f"{name}: ambiguous anchors in {', '.join(locations[name])}")
    capabilities = {key: bool(value) for key, value in locations.items()}
    # Status remains optional exactly as on the legacy layout. All other
    # decomposed delivery/interaction seams are required to preserve features.
    missing = [key for key in _LAYOUT_MARKERS if key != "status_callback" and not capabilities[key]]
    if missing:
        errors.append("missing decomposed anchors: " + ", ".join(missing))
    return capabilities, {key: tuple(value) for key, value in locations.items()}, "; ".join(errors) or "supported"
