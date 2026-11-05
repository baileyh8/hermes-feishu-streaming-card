"""Pinned 0.22.6 producer lifecycle provenance, never a lifecycle implementation."""
from __future__ import annotations

from contextlib import asynccontextmanager
from hashlib import sha256
import sys
import inspect
from pathlib import Path
import stat
from types import ModuleType

SOURCE_HASHES = {
    "gateway.platforms.base": "ad064118a29bbe6d0d33424fbcde01484305a501aef4aa2584f5808c9b399a3d",
    "gateway.session_ingress": "12466f9c31f57877c6ae8ea6189d76123a278bdc99e5242c13c5c4ecff0f8ae9",
    "gateway.session_authority": "207708e4aeb0bf2d8e577f145110a414d80c12da1c4e69570f0402f7d7f6d1a6",
    "gateway.session_producer_lifecycle": "8c95c5c96bc0634c22cd698068194155dc932125a6cbefaea0039a1dfb8653a8",
    "gateway.platforms.base_post_delivery": "89471cd5ff6daf8001cab6d95321b1284a81b5db4943ca3e866fe21361d79206",
    "gateway.run_turn_followup_ack": "f45f7b93d091ecb57f30d80e18d08bca0aa7aae92a7bb796ae9ce4b499852283",
}
CORE_HASHES = {name: SOURCE_HASHES[name] for name in (
    "gateway.session_ingress", "gateway.session_authority")}
# v0.22.7 (b200f5bc60e31588ba2cb60e288029c4a54ba5fa) keeps the ingress and
# post-delivery sources unchanged but changes authority, producer lifecycle
# and Base. The admission extraction seam itself is byte-identical. Keep a
# separate whole-file cohort so an unknown upstream cannot be accepted by a
# relaxed per-file allowlist.
SOURCE_HASHES_0227 = {
    "gateway.platforms.base": "25bce7af7473b6eb1289dc6e83570062d04fe4338620832889a28d9f598f6229",
    "gateway.session_ingress": "12466f9c31f57877c6ae8ea6189d76123a278bdc99e5242c13c5c4ecff0f8ae9",
    "gateway.session_authority": "739d26a99bb786f0f2fc84f36b5d7fa92ca01b20692100429d845164d4b76379",
    "gateway.session_producer_lifecycle": "e948b9cb02aac2e169abee70c6b2ffc04dc0edeeb22f4b3b9748ee9890ee56e3",
    "gateway.platforms.base_post_delivery": "89471cd5ff6daf8001cab6d95321b1284a81b5db4943ca3e866fe21361d79206",
    "gateway.run_turn_followup_ack": "f45f7b93d091ecb57f30d80e18d08bca0aa7aae92a7bb796ae9ce4b499852283",
}
CORE_HASHES_0227 = {name: SOURCE_HASHES_0227[name] for name in (
    "gateway.session_ingress", "gateway.session_authority")}
TOPOLOGY_HASHES = (SOURCE_HASHES, SOURCE_HASHES_0227)
_FUNCTIONS = {
    "gateway.platforms.base": ("_split_post_delivery_entry",
                               "BasePlatformAdapter.send_typing", "BasePlatformAdapter.on_processing_start"),
    "gateway.session_ingress": (
        "_admission_scope", "_execution_scope", "_launch_body_if_launch_owner",
        "_take_post_delivery", "_hand_back_post_delivery", "fire_post_delivery", "_registry"),
    "gateway.session_authority": ("SessionAuthority._admitted", "SessionAuthority._settle_cancelled"),
    "gateway.session_producer_lifecycle": (
        "_turns", "mark_producer_admission", "_open", "_executed_outcome",
        "producer_turn", "close_producer_turn", "drop_producer_turn",
        "_Bracket.__init__", "_Bracket.stop_typing", "_Bracket.close"),
    "gateway.platforms.base_post_delivery": ("run_post_delivery_callback",),
    "gateway.run_turn_followup_ack": ("_followup_processing_hooks_apply",),
}
_DECORATED = {("gateway.session_ingress", "_admission_scope"),
              ("gateway.session_producer_lifecycle", "producer_turn")}


def source_bytes(
    root: Path, expected: dict[str, str] | None = None
) -> dict[str, bytes] | None:
    try:
        result = {}
        for name, digest in (expected or SOURCE_HASHES).items():
            path = root / (name.replace(".", "/") + ".py")
            before = path.lstat()
            limit = 1024 * 1024 if name == "gateway.platforms.base" else 256 * 1024
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                return None
            raw = path.read_bytes()
            after = path.lstat()
            if name == "gateway.platforms.base":
                from .install.patcher import remove_base_patch
                raw = remove_base_patch(raw.decode("utf-8")).encode("utf-8")
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    or sha256(raw).hexdigest() != digest):
                return None
            result[name] = raw
        return result
    except (OSError, UnicodeError, ValueError, SyntaxError):
        return None


def verified(
    root: Path, ingress: ModuleType, authority: ModuleType,
    expected: dict[str, str] | None = None,
) -> bool:
    """Bind actual loaded helpers, including asynccontextmanager closure identity."""
    from .admission_completion import _claim_frame, _compiled_code, _shape

    sources = source_bytes(root, expected)
    if sources is None:
        return False
    modules = {"gateway.session_ingress": ingress, "gateway.session_authority": authority}
    try:
        for name, raw in sources.items():
            module = modules.get(name) or sys.modules.get(name)
            # The native producer path imports this helper before entering the
            # admitted handler. Ordinary turns need not load it at all. Check
            # its pinned source now and its live binding whenever present;
            # provenance inspection must never execute an unknown loader.
            if module is None and name == "gateway.run_turn_followup_ack":
                claim = _claim_frame(ingress)
                if claim is not None:
                    turns = getattr(claim.get("authority"), "producer_turns", {})
                    row = claim.get("row")
                    if (isinstance(turns, dict) and isinstance(row, dict)
                            and row.get("admission_id") in turns):
                        return False
                continue
            if not isinstance(module, ModuleType):
                return False
            modules[name] = module
            path = root / (name.replace(".", "/") + ".py")
            filename = getattr(module, "__file__", None)
            if not isinstance(filename, str) or Path(filename).resolve() != path.resolve():
                return False
            compiled = compile(raw, str(path), "exec", dont_inherit=True)
            for qualified in _FUNCTIONS[name]:
                owner, _, function_name = qualified.rpartition(".")
                function = getattr(getattr(module, owner) if owner else module, function_name)
                if (name, qualified) in _DECORATED:
                    wrapped = getattr(function, "__wrapped__", None)
                    if not inspect.isasyncgenfunction(wrapped):
                        return False
                    expected_wrapper = asynccontextmanager(wrapped)
                    if (function.__code__ is not expected_wrapper.__code__
                            or function.__globals__ is not expected_wrapper.__globals__
                            or function.__defaults__ != expected_wrapper.__defaults__
                            or function.__kwdefaults__ != expected_wrapper.__kwdefaults__
                            or function.__closure__ is None
                            or len(function.__closure__) != 1
                            or function.__closure__[0].cell_contents is not wrapped):
                        return False
                    function = wrapped
                expected_defaults = (("cancelled",) if qualified == "SessionAuthority._settle_cancelled"
                                     else (None,) if qualified == "BasePlatformAdapter.send_typing" else None)
                if (not inspect.isfunction(function)
                        or function.__defaults__ != expected_defaults
                        or function.__kwdefaults__ not in (None, {})
                        or function.__globals__ is not vars(module)
                        or Path(function.__code__.co_filename).resolve() != path.resolve()
                        or _shape(function.__code__) != _shape(
                            _compiled_code(compiled, function_name, owner or None))):
                    return False
        lifecycle = modules["gateway.session_producer_lifecycle"]
        if any(getattr(ingress, n, None) is not getattr(lifecycle, n)
               for n in ("producer_turn", "close_producer_turn")):
            return False
        if any(getattr(authority, n, None) is not getattr(lifecycle, n)
               for n in ("mark_producer_admission", "drop_producer_turn")):
            return False
        base = modules["gateway.platforms.base"]
        event = sys.modules.get("gateway.platforms.event")
        followup = modules.get("gateway.run_turn_followup_ack")
        if (event is None or ingress.ProcessingOutcome is not event.ProcessingOutcome
                or lifecycle.ProcessingOutcome is not event.ProcessingOutcome
                or (followup is not None and followup.BasePlatformAdapter
                    is not base.BasePlatformAdapter)):
            return False
        if (type(base._POST_DELIVERY_CALLBACK_TIMEOUT_SECONDS) not in (int, float)
                or base._POST_DELIVERY_CALLBACK_TIMEOUT_SECONDS != 30.0):
            return False
        if base.run_post_delivery_callback is not modules[
                "gateway.platforms.base_post_delivery"].run_post_delivery_callback:
            return False
        return True
    except (OSError, AttributeError, ImportError, TypeError, ValueError, SyntaxError, StopIteration):
        return False
