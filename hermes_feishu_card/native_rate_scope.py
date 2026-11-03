"""Borrow native result identity at its verified Gateway projection boundary."""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import inspect
from pathlib import Path
import stat
import sys
from types import CodeType
from typing import Any

from .install.rate_scope import COHORTS

_FIELD = "_hfc_observed_rate_scope"


def _read(path: Path) -> bytes:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > 2 * 1024 * 1024:
        raise ValueError("unverified rate producer")
    raw = path.read_bytes()
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("rate producer changed")
    return raw


@lru_cache(maxsize=8)
def _compiled(raw: bytes, filename: str) -> CodeType:
    return compile(raw, filename, "exec", dont_inherit=True)


@lru_cache(maxsize=128)
def _expected_shape(code: CodeType) -> Any:
    from .admission_completion import _shape
    return _shape(code)


@lru_cache(maxsize=4)
def _clean_runner(raw: bytes) -> str:
    from .install.patcher import remove_patch
    return remove_patch(raw.decode("utf-8")).replace("\r\n", "\n")



def _verified(root: Path, caller: Any, frozen_call: Any = None, *, defer_unloaded: bool = False) -> bool:
    # The code-shape helper compiles source without running its imports.
    from .admission_completion import _compiled_code, _shape

    raw_runner = _read(root / "gateway/run_turn_runner.py")
    clean_runner = _clean_runner(raw_runner)
    candidates = [row for row in COHORTS if row["gateway/run_turn_runner.py"] ==
                  sha256(clean_runner.encode()).hexdigest()]
    runner = sys.modules.get("gateway.run_turn_runner")
    if (not candidates or runner is None or caller.f_globals is not vars(runner)
            or caller.f_code.co_name not in {"run_sync", "_run_sync_scoped", "_run_conversation_with_approval"}):
        return False
    if _shape(caller.f_code) != _expected_shape(_compiled_code(
            _compiled(raw_runner, str(root / "gateway/run_turn_runner.py")), caller.f_code.co_name, "TurnRunner")):
        return False
    functions = {
        "agent.turn_context": ("export_current_turn_boundary", "reanchor_current_turn_user_idx",
                               "_bind_turn_identity", "build_turn_context"),
        "agent.conversation_loop": ("run_conversation", "_run_conversation_turn"),
        "agent.turn_finalizer": ("finalize_turn",),
        "agent.turn_facade": ("TurnFacadeMixin.run_conversation",),
    }
    if "gateway/session_cli_turn.py" in candidates[0]:
        functions["gateway.session_cli_turn"] = ("run_classic_cli_turn", "classic_cli_turn", "take_cli_result_fields")
    for name, names in functions.items():
        path = root / (name.replace(".", "/") + ".py")
        raw = _read(path)
        digest = sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
        candidates = [row for row in candidates if row[path.relative_to(root).as_posix()] == digest]
        if not candidates:
            return False
        module = sys.modules.get(name)
        if (module is None and defer_unloaded
                and name in {"agent.turn_context", "agent.conversation_loop", "agent.turn_finalizer"}):
            continue  # The frozen native facade imports these on its first call.
        filename = getattr(module, "__file__", None)
        if not isinstance(filename, str) or Path(filename).resolve() != path.resolve():
            return False
        for qualified in names:
            owner, _, function_name = qualified.rpartition(".")
            function = getattr(getattr(module, owner, None) if owner else module, function_name, None)
            if (not inspect.isfunction(function) or function.__globals__ is not vars(module)
                    or Path(function.__code__.co_filename).resolve() != path.resolve()
                    or _shape(function.__code__) != _expected_shape(_compiled_code(
                        _compiled(raw, str(path)), function_name, owner or None))):
                return False
    loop = sys.modules.get("agent.conversation_loop")
    finalizer = sys.modules.get("agent.turn_finalizer")
    context = sys.modules.get("agent.turn_context")
    if loop is not None:
        if (finalizer is None or context is None or loop.finalize_turn is not finalizer.finalize_turn
                or loop.build_turn_context is not context.build_turn_context):
            return False
    elif not defer_unloaded:
        return False
    values = caller.f_locals
    receiver, ctx = values.get("self"), values.get("ctx")
    if receiver is None or getattr(receiver, "_ctx", None) is not ctx:
        return False
    if frozen_call is not None:
        facade = sys.modules["agent.turn_facade"].TurnFacadeMixin.run_conversation
        agent = values.get("agent")
        holder = getattr(ctx, "agent_holder", None)
        if (not inspect.ismethod(frozen_call) or frozen_call.__func__ is not facade
                or frozen_call.__self__ is not agent or values.get("_hfc_rate_call") is not frozen_call
                or type(holder) is not list or not holder or holder[0] is not agent):
            return False
    if caller.f_code.co_name != "_run_conversation_with_approval" and "gateway.session_cli_turn" in functions:
        if values.get("run_classic_cli_turn") is not sys.modules["gateway.session_cli_turn"].run_classic_cli_turn:
            return False
    return True


class _Scope(dict[str, str]):
    """JSON-compatible ephemeral metadata; never retains the native result."""
    def __init__(self, result: dict[str, Any], session: str, turn: str):
        super().__init__(session_id=session, turn_id=turn)
        self.result_id = id(result)
        self.pair = (session, turn)


def _frame_matches(caller: Any, values: dict[str, Any], names: tuple[str, ...]) -> bool:
    return caller is not None and all(values.get(name) is caller.f_locals.get(name) for name in names)


def _feishu(values: dict[str, Any]) -> bool:
    platform = getattr(getattr(values.get("ctx"), "source", None), "platform", None)
    return getattr(platform, "value", platform) == "feishu"


def observe_call(frozen_call: Any, values: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
    """Call the borrowed native method exactly once; observe only its returned envelope."""
    frame = inspect.currentframe()
    caller = frame.f_back if frame is not None else None
    verified = False
    root = None
    try:
        try:
            if caller is not None and _feishu(values) and _frame_matches(
                    caller, values, ("agent", "ctx", "self", "_hfc_rate_call")):
                path = Path(caller.f_code.co_filename)
                if path.name == "run_turn_runner.py" and path.parent.name == "gateway":
                    root = path.parent.parent
                    verified = _verified(root, caller, frozen_call, defer_unloaded=True)
        except Exception:
            pass
        # Native exceptions/cancellation propagate unchanged; there is no retry.
        result = frozen_call(*args, **kwargs)
        try:
            if not _feishu(values) or type(result) is not dict:
                return result
            result.pop(_FIELD, None)
            session, turn = result.get("session_id"), result.get("turn_id")
            if (verified and root is not None and _verified(root, caller, frozen_call)
                    and type(session) is str and type(turn) is str
                    and all(0 < len(v) <= 256 and v.strip() for v in (session, turn))
                    and result.get("turn_exit_reason") != "context_compression_timeout"):
                result[_FIELD] = _Scope(result, session, turn)
        except Exception:
            pass
        return result
    finally:
        del caller, frame


def project(values: dict[str, Any]) -> None:
    """Copy A's scope only from that very raw result, never from mutable Agent state."""
    frame = inspect.currentframe()
    caller = frame.f_back if frame is not None else None
    try:
        common, result = values.get("common"), values.get("result")
        if not _feishu(values) or type(common) is not dict:
            return
        common.pop(_FIELD, None)
        if type(result) is not dict:
            return
        scope = result.pop(_FIELD, None)
        if (caller is None or type(scope) is not _Scope or scope.result_id != id(result)
                or not _frame_matches(caller, values, ("common", "result", "ctx", "self"))):
            return
        path = Path(caller.f_code.co_filename)
        if path.name == "run_turn_runner.py" and path.parent.name == "gateway" and _verified(path.parent.parent, caller):
            common[_FIELD] = {"session_id": scope.pair[0], "turn_id": scope.pair[1]}
    except Exception:
        return
    finally:
        del caller, frame
