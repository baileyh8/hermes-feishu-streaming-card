from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import dataclass
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
from typing import Dict, Iterator, List, Optional, Tuple
from uuid import uuid4

from .detect import HermesDetection
from .manifest import (
    CURRENT_INSTALL_MANIFEST_VERSION,
    ManifestStructureError,
    ManifestVersionError,
    UNSUPPORTED_INSTALL_MANIFEST_VERSION_MESSAGE,
    validate_install_manifest,
)
from .patcher import (
    apply_base_patch,
    apply_cron_patch,
    apply_patch,
    remove_base_patch,
    remove_cron_patch,
    remove_patch,
)


BACKUP_SUFFIX = ".hermes_feishu_card.bak"
MANIFEST_NAME = ".hermes_feishu_card_manifest"
KNOWN_STATES = {
    "clean",
    "installed",
    "stale_unpatched",
    "owned_incomplete",
    "corrupt_owned",
    "refused",
}

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_HASH_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_OWNED_GATEWAY_MARKER_LINE_RE = re.compile(
    r"(?m)^[ \t]*# HERMES_FEISHU_CARD_[A-Z0-9_]+_(?:BEGIN|END)"
    r"[ \t]*(?:\r?\n|$)"
)
_STATUS_PREFIX = "!recovery:"
_MANIFEST_ERROR = "_recovery_error"
_LOCK_NAME = ".hermes_feishu_card_recovery.lock"
_LOCK_TIMEOUT_SECONDS = 10.0
_PROCESS_LOCKS: Dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()
_OwnedWriteSnapshot = Tuple[int, int, str]
_DirectoryIdentity = Tuple[int, int]
_SECURE_DIRFD_TRANSACTIONS_SUPPORTED = (
    os.name != "nt"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "fchmod")
    and all(
        operation in getattr(os, "supports_dir_fd", set())
        for operation in (os.open, os.stat, os.unlink, os.rename)
    )
)


def _secure_dirfd_transactions_supported() -> bool:
    return _SECURE_DIRFD_TRANSACTIONS_SUPPORTED


def _require_secure_dirfd_transactions() -> None:
    if not _secure_dirfd_transactions_supported():
        raise RecoveryRefused(
            "secure recovery requires directory-relative filesystem operations "
            "on this platform"
        )


@dataclass
class _StagedText:
    path: Path
    parent_fd: int
    basename: str
    identity: Tuple[int, int]
    target_binding: _TargetBinding | None = None

    def assert_owned(self) -> None:
        current = os.stat(
            self.basename,
            dir_fd=self.parent_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != self.identity
        ):
            raise RecoveryRefused(
                "recovery staged file changed before mutation"
            )

    def release(self) -> None:
        if self.parent_fd < 0:
            return
        os.close(self.parent_fd)
        self.parent_fd = -1

    def cleanup(self) -> None:
        if self.parent_fd < 0:
            return
        try:
            current = os.stat(
                self.basename,
                dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            if (
                stat.S_ISREG(current.st_mode)
                and (current.st_dev, current.st_ino) == self.identity
            ):
                os.unlink(self.basename, dir_fd=self.parent_fd)
        finally:
            self.release()


@dataclass
class _TargetBinding:
    path: Path
    parent_fd: int
    basename: str
    parent_identity: _DirectoryIdentity

    def assert_parent(self) -> None:
        current = os.fstat(self.parent_fd)
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != self.parent_identity
        ):
            raise RecoveryRefused(
                "recovery target parent changed before mutation"
            )

    def release(self) -> None:
        if self.parent_fd < 0:
            return
        os.close(self.parent_fd)
        self.parent_fd = -1

_ACTION_MESSAGES = {
    "restore_verified_backup": "run.py: restored verified backup",
    "reapply_current_hook": "run.py: reapplied current hook",
    "rebuild_backup": "backup: recreated",
    "rebuild_manifest": "manifest: rebuilt",
    "restore_verified_cron_backup": "cron scheduler: restored verified backup",
    "reapply_current_cron_hook": "cron scheduler: reapplied current hook",
    "rebuild_cron_backup": "cron backup: recreated",
    "restore_verified_base_backup": "exact Base: restored verified backup",
    "reapply_current_base_hook": "exact Base: reapplied current hook",
    "rebuild_base_backup": "exact Base backup: recreated",
    "clear_stale_install_state": "install state: cleared stale unpatched state",
}


@dataclass(frozen=True)
class RecoveryFinding:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class RecoveryEvidence:
    current_text: str
    current_sha256: str
    backup_text: Optional[str]
    backup_sha256: str
    manifest: Optional[Dict[str, object]]
    marker_error: str
    cron_current_text: Optional[str]
    cron_current_sha256: str
    cron_backup_text: Optional[str]
    cron_backup_sha256: str
    cron_marker_error: str
    base_current_text: Optional[str]
    base_current_sha256: str
    base_backup_text: Optional[str]
    base_backup_sha256: str
    base_marker_error: str


@dataclass(frozen=True)
class RecoveryClassification:
    state: str
    executable: bool
    fingerprint_parts: Dict[str, str]
    actions: Tuple[str, ...]
    findings: Tuple[RecoveryFinding, ...]


@dataclass(frozen=True)
class RecoveryPlan:
    root: Path
    state: str
    executable: bool
    fingerprint: str
    actions: Tuple[str, ...]
    findings: Tuple[RecoveryFinding, ...]


class RecoveryRefused(ValueError):
    pass


@dataclass(frozen=True)
class RecoveryResult:
    status: str
    plan: RecoveryPlan
    actions: Tuple[str, ...]
    quarantine_name: Optional[str]
    message: str


@dataclass
class _RecoveryState:
    run_text: str
    backup_text: Optional[str]
    cron_text: Optional[str]
    cron_backup_text: Optional[str]
    base_text: Optional[str]
    base_backup_text: Optional[str]
    clear_install_state: bool = False


@contextmanager
def _root_lock(root: Path) -> Iterator[None]:
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    root_key = str(root.resolve())
    with _PROCESS_LOCKS_GUARD:
        process_lock = _PROCESS_LOCKS.setdefault(root_key, threading.Lock())
    remaining = max(0.0, deadline - time.monotonic())
    if not process_lock.acquire(timeout=remaining):
        raise RecoveryRefused("timed out waiting for recovery lock")

    lock_handle = None
    try:
        lock_path = root / _LOCK_NAME
        if lock_path.is_symlink():
            raise RecoveryRefused("recovery lock path must not be a symbolic link")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(str(lock_path), flags, 0o600)
        lock_handle = os.fdopen(descriptor, "r+b")
        _acquire_os_lock(lock_handle, deadline)
        try:
            yield
        finally:
            _release_os_lock(lock_handle)
    finally:
        if lock_handle is not None:
            lock_handle.close()
        process_lock.release()


def execute_recovery(
    detection: HermesDetection,
    expected_fingerprint: Optional[str] = None,
    *,
    accept_hermes_upgrade: bool = False,
) -> RecoveryResult:
    _require_secure_dirfd_transactions()
    with _root_lock(detection.root):
        manifest = _read_manifest_evidence(detection.root / MANIFEST_NAME)
        if manifest is not None and manifest.get(_MANIFEST_ERROR) == "unsupported_version":
            raise RecoveryRefused(UNSUPPORTED_INSTALL_MANIFEST_VERSION_MESSAGE)
        fresh = plan_recovery(
            detection,
            accept_hermes_upgrade=accept_hermes_upgrade,
        )
        if expected_fingerprint and fresh.fingerprint != expected_fingerprint:
            raise RecoveryRefused("recovery evidence changed; rerun diagnosis")
        if not fresh.executable:
            raise RecoveryRefused(_first_refusal(fresh))
        return _execute_fresh_plan(
            detection,
            fresh,
            accept_hermes_upgrade=accept_hermes_upgrade,
        )


def _execute_fresh_plan(
    detection: HermesDetection,
    plan: RecoveryPlan,
    *,
    accept_hermes_upgrade: bool = False,
) -> RecoveryResult:
    evidence = _read_evidence(detection)
    if (
        _plan_from_evidence(
            detection,
            evidence,
            accept_hermes_upgrade=accept_hermes_upgrade,
        ).fingerprint
        != plan.fingerprint
    ):
        raise RecoveryRefused("recovery evidence changed; rerun diagnosis")

    state = _RecoveryState(
        run_text=evidence.current_text,
        backup_text=evidence.backup_text,
        cron_text=evidence.cron_current_text,
        cron_backup_text=evidence.cron_backup_text,
        base_text=evidence.base_current_text,
        base_backup_text=evidence.base_backup_text,
    )
    quarantine_sources: List[Tuple[Path, str]] = []
    for action in plan.actions:
        _apply_recovery_action(
            detection,
            evidence,
            state,
            action,
            quarantine_sources,
        )

    _validate_recovery_state(detection, state)
    changes, quarantine_name = _build_recovery_changes(
        detection,
        evidence,
        state,
        quarantine_sources,
    )
    _commit_recovery_changes(
        detection,
        plan,
        changes,
        accept_hermes_upgrade=accept_hermes_upgrade,
    )
    status = "cleared" if state.clear_install_state else "repaired"
    message = (
        "Stale install state cleared."
        if state.clear_install_state
        else "Verified recovery completed."
    )
    return RecoveryResult(
        status=status,
        plan=plan,
        actions=tuple(_ACTION_MESSAGES[action] for action in plan.actions),
        quarantine_name=quarantine_name,
        message=message,
    )


def _first_refusal(plan: RecoveryPlan) -> str:
    finding_codes = {finding.code for finding in plan.findings}
    if "manifest_missing" in finding_codes:
        return "install state incomplete; manifest missing; refusing to repair"
    legacy_messages = {
        "current_hash_mismatch": "run.py changed since install; refusing to repair",
        "current_patch_mismatch": "run.py changed since install; refusing to repair",
        "backup_hash_mismatch": "backup changed since install; refusing to repair",
        "backup_source_mismatch": "run.py changed since install; refusing to repair",
        "cron_current_hash_mismatch": "cron scheduler changed since install; refusing to repair",
        "cron_current_patch_mismatch": "cron scheduler changed since install; refusing to repair",
        "cron_backup_hash_mismatch": "cron backup changed since install; refusing to repair",
        "cron_backup_source_mismatch": "cron scheduler changed since install; refusing to repair",
        "manifest_backup_hash_invalid": "manifest missing backup sha256; refusing to repair",
        "manifest_current_hash_invalid": "manifest missing patched sha256; refusing to repair",
    }
    for finding in plan.findings:
        if finding.severity == "error":
            return legacy_messages.get(finding.code, finding.message)
    if not plan.actions:
        return "No recovery is required."
    return "Recovery evidence is not safe to execute."


def _apply_recovery_action(
    detection: HermesDetection,
    evidence: RecoveryEvidence,
    state: _RecoveryState,
    action: str,
    quarantine_sources: List[Tuple[Path, str]],
) -> None:
    if action == "restore_verified_backup":
        if state.backup_text is None:
            raise RecoveryRefused("Verified gateway backup is unavailable.")
        if evidence.marker_error and not any(
            path == detection.run_py for path, _text in quarantine_sources
        ):
            quarantine_sources.append((detection.run_py, state.run_text))
        state.run_text = state.backup_text
    elif action == "reapply_current_hook":
        source = state.backup_text
        if source is None:
            source = remove_patch(state.run_text)
        state.run_text = apply_patch(
            source, strategy=detection.hook_strategy or "legacy_gateway_run"
        )
    elif action == "rebuild_backup":
        state.backup_text = remove_patch(state.run_text)
    elif action == "restore_verified_cron_backup":
        if state.cron_text is None or state.cron_backup_text is None:
            raise RecoveryRefused("Verified cron backup is unavailable.")
        if evidence.cron_marker_error and detection.cron_py is not None and not any(
            path == detection.cron_py for path, _text in quarantine_sources
        ):
            quarantine_sources.append((detection.cron_py, state.cron_text))
        state.cron_text = state.cron_backup_text
    elif action == "reapply_current_cron_hook":
        if state.cron_text is None:
            raise RecoveryRefused("Cron source is unavailable.")
        source = state.cron_backup_text
        if source is None:
            source = remove_cron_patch(state.cron_text)
        state.cron_text = apply_cron_patch(source)
    elif action == "rebuild_cron_backup":
        if state.cron_text is None:
            raise RecoveryRefused("Cron source is unavailable.")
        state.cron_backup_text = remove_cron_patch(state.cron_text)
    elif action == "restore_verified_base_backup":
        if state.base_text is None or state.base_backup_text is None:
            raise RecoveryRefused("Verified exact Base backup is unavailable.")
        state.base_text = state.base_backup_text
    elif action == "reapply_current_base_hook":
        if state.base_text is None:
            raise RecoveryRefused("Exact Base source is unavailable.")
        source = state.base_backup_text
        if source is None:
            source = remove_base_patch(state.base_text)
        state.base_text = apply_base_patch(source)
    elif action == "rebuild_base_backup":
        if state.base_text is None:
            raise RecoveryRefused("Exact Base source is unavailable.")
        state.base_backup_text = remove_base_patch(state.base_text)
    elif action == "rebuild_manifest":
        pass
    elif action == "clear_stale_install_state":
        state.backup_text = None
        state.cron_backup_text = None
        state.base_backup_text = None
        state.clear_install_state = True
    else:
        raise RecoveryRefused("Unknown recovery action; refusing to mutate files.")


def _validate_recovery_state(
    detection: HermesDetection, state: _RecoveryState
) -> None:
    try:
        ast.parse(state.run_text)
        unpatched = remove_patch(state.run_text)
        if state.clear_install_state:
            if unpatched != state.run_text:
                raise ValueError("owned gateway patch remains")
        else:
            if state.backup_text is None:
                raise ValueError("gateway backup missing")
            ast.parse(state.backup_text)
            if remove_patch(state.backup_text) != state.backup_text:
                raise ValueError("gateway backup contains owned patch")
            expected = apply_patch(
                state.backup_text,
                strategy=detection.hook_strategy or "legacy_gateway_run",
            )
            if state.run_text != expected or remove_patch(state.run_text) != state.backup_text:
                raise ValueError("gateway candidate does not match verified source")

        if state.cron_text is not None:
            ast.parse(state.cron_text)
            cron_unpatched = remove_cron_patch(state.cron_text)
            if state.clear_install_state:
                if cron_unpatched != state.cron_text:
                    raise ValueError("owned cron patch remains")
            else:
                if state.cron_backup_text is None:
                    raise ValueError("cron backup missing")
                ast.parse(state.cron_backup_text)
                if remove_cron_patch(state.cron_backup_text) != state.cron_backup_text:
                    raise ValueError("cron backup contains owned patch")
                expected_cron = apply_cron_patch(state.cron_backup_text)
                if (
                    state.cron_text != expected_cron
                    or remove_cron_patch(state.cron_text) != state.cron_backup_text
                ):
                    raise ValueError("cron candidate does not match verified source")

        if state.base_text is not None:
            ast.parse(state.base_text)
            base_unpatched = remove_base_patch(state.base_text)
            if state.clear_install_state:
                if base_unpatched != state.base_text:
                    raise ValueError("owned exact Base patch remains")
            elif state.base_backup_text is not None:
                ast.parse(state.base_backup_text)
                if remove_base_patch(state.base_backup_text) != state.base_backup_text:
                    raise ValueError("exact Base backup contains owned patch")
                expected_base = apply_base_patch(state.base_backup_text)
                if (
                    state.base_text != expected_base
                    or remove_base_patch(state.base_text) != state.base_backup_text
                ):
                    raise ValueError(
                        "exact Base candidate does not match verified source"
                    )
    except (SyntaxError, ValueError) as exc:
        raise RecoveryRefused(
            "staged recovery validation failed; no files were changed"
        ) from exc


def _build_recovery_changes(
    detection: HermesDetection,
    evidence: RecoveryEvidence,
    state: _RecoveryState,
    quarantine_sources: List[Tuple[Path, str]],
) -> Tuple[List[Tuple[Path, Optional[str]]], Optional[str]]:
    changes: List[Tuple[Path, Optional[str]]] = []
    quarantine_name = None
    for source_path, source_text in quarantine_sources:
        quarantine_path = _new_quarantine_path(source_path)
        if quarantine_name is None:
            quarantine_name = quarantine_path.name
        changes.append((quarantine_path, source_text))
        existing = sorted(
            source_path.parent.glob(f"{source_path.name}.hfc-corrupt-*"),
            key=_quarantine_sort_key,
        )
        for stale in existing[:-2]:
            changes.append((stale, None))

    backup_path = detection.run_py.with_name(
        f"{detection.run_py.name}{BACKUP_SUFFIX}"
    )
    manifest_path = detection.root / MANIFEST_NAME
    _append_text_change(changes, detection.run_py, evidence.current_text, state.run_text)
    _append_optional_change(
        changes, backup_path, evidence.backup_text, state.backup_text
    )

    if detection.cron_py is not None:
        cron_backup_path = detection.cron_py.with_name(
            f"{detection.cron_py.name}{BACKUP_SUFFIX}"
        )
        _append_optional_change(
            changes,
            detection.cron_py,
            evidence.cron_current_text,
            state.cron_text,
        )
        _append_optional_change(
            changes,
            cron_backup_path,
            evidence.cron_backup_text,
            state.cron_backup_text,
        )

    if detection.base_py is not None:
        base_backup_path = detection.base_py.with_name(
            f"{detection.base_py.name}{BACKUP_SUFFIX}"
        )
        _append_optional_change(
            changes,
            detection.base_py,
            evidence.base_current_text,
            state.base_text,
        )
        _append_optional_change(
            changes,
            base_backup_path,
            evidence.base_backup_text,
            state.base_backup_text,
        )

    old_manifest = _read_text(manifest_path) if manifest_path.exists() else None
    new_manifest = None
    if not state.clear_install_state:
        new_manifest = _render_manifest(detection, state, evidence.manifest)
    _append_optional_change(changes, manifest_path, old_manifest, new_manifest)
    return changes, quarantine_name


def _append_text_change(
    changes: List[Tuple[Path, Optional[str]]],
    path: Path,
    before: str,
    after: str,
) -> None:
    if before != after:
        changes.append((path, after))


def _append_optional_change(
    changes: List[Tuple[Path, Optional[str]]],
    path: Path,
    before: Optional[str],
    after: Optional[str],
) -> None:
    if before != after:
        changes.append((path, after))


def _render_manifest(
    detection: HermesDetection,
    state: _RecoveryState,
    previous_manifest: Optional[Dict[str, object]] = None,
) -> str:
    if state.backup_text is None:
        raise RecoveryRefused("Gateway backup is required for the install manifest.")
    backup_path = detection.run_py.with_name(
        f"{detection.run_py.name}{BACKUP_SUFFIX}"
    )
    manifest = {
        "manifest_version": CURRENT_INSTALL_MANIFEST_VERSION,
        "run_py": _relative_path(detection.root, detection.run_py),
        "patched_sha256": _text_sha256(state.run_text),
        "backup": _relative_path(detection.root, backup_path),
        "backup_sha256": _text_sha256(state.backup_text),
    }
    if detection.cron_py is not None and state.cron_text is not None:
        if state.cron_backup_text is None:
            raise RecoveryRefused("Cron backup is required for the install manifest.")
        cron_backup_path = detection.cron_py.with_name(
            f"{detection.cron_py.name}{BACKUP_SUFFIX}"
        )
        manifest.update(
            {
                "cron_py": _relative_path(detection.root, detection.cron_py),
                "cron_patched_sha256": _text_sha256(state.cron_text),
                "cron_backup": _relative_path(detection.root, cron_backup_path),
                "cron_backup_sha256": _text_sha256(state.cron_backup_text),
            }
        )
    has_base = bool(
        detection.base_required
        and detection.base_py is not None
        and state.base_text is not None
        and state.base_backup_text is not None
        and remove_base_patch(state.base_text) != state.base_text
    )
    if has_base:
        base_py = detection.base_py
        if base_py is None or state.base_backup_text is None:
            raise RecoveryRefused(
                "Exact Base backup is required for the install manifest."
            )
        base_backup_path = base_py.with_name(f"{base_py.name}{BACKUP_SUFFIX}")
        manifest.update(
            {
                "base_py": _relative_path(detection.root, base_py),
                "base_patched_sha256": _text_sha256(state.base_text or ""),
                "base_backup": _relative_path(detection.root, base_backup_path),
                "base_backup_sha256": _text_sha256(state.base_backup_text),
            }
        )
    integrity = _preserved_integrity_provenance(
        previous_manifest,
        state,
        has_cron=detection.cron_py is not None and state.cron_text is not None,
        has_base=has_base,
    )
    if integrity is not None:
        manifest["integrity"] = integrity
    return json.dumps(manifest, sort_keys=True) + "\n"


def _preserved_integrity_provenance(
    manifest: Optional[Dict[str, object]],
    state: _RecoveryState,
    *,
    has_cron: bool,
    has_base: bool,
) -> Optional[Dict[str, object]]:
    if not isinstance(manifest, dict) or state.backup_text is None:
        return None
    value = manifest.get("integrity")
    if not isinstance(value, dict):
        return None
    git_head = value.get("git_head")
    run_hash = value.get("run_blob_sha256")
    if (
        value.get("version") != 2
        or not isinstance(git_head, str)
        or _GIT_HASH_RE.fullmatch(git_head) is None
        or not isinstance(run_hash, str)
        or _HASH_RE.fullmatch(run_hash) is None
        or run_hash != _text_sha256(state.backup_text)
    ):
        return None
    preserved: Dict[str, object] = {
        "version": 2,
        "git_head": git_head,
        "run_blob_sha256": run_hash,
    }
    if has_cron:
        cron_hash = value.get("cron_blob_sha256")
        if (
            state.cron_backup_text is None
            or not isinstance(cron_hash, str)
            or _HASH_RE.fullmatch(cron_hash) is None
            or cron_hash != _text_sha256(state.cron_backup_text)
        ):
            return None
        preserved["cron_blob_sha256"] = cron_hash
    if has_base:
        base_hash = value.get("base_blob_sha256")
        if (
            state.base_backup_text is None
            or not isinstance(base_hash, str)
            or _HASH_RE.fullmatch(base_hash) is None
            or base_hash != _text_sha256(state.base_backup_text)
        ):
            return None
        preserved["base_blob_sha256"] = base_hash
    return preserved


def _commit_recovery_changes(
    detection: HermesDetection,
    plan: RecoveryPlan,
    changes: List[Tuple[Path, Optional[str]]],
    *,
    accept_hermes_upgrade: bool = False,
) -> None:
    _require_secure_dirfd_transactions()
    ordered_changes = [change for change in changes if change[1] is not None]
    ordered_changes.extend(change for change in changes if change[1] is None)
    target_ancestries, directory_snapshots = _snapshot_controlled_ancestries(
        detection.root, [target for target, _contents in ordered_changes]
    )
    staged: Dict[Path, _StagedText] = {}
    rollback: Dict[Path, _StagedText] = {}
    target_bindings: Dict[Path, _TargetBinding] = {}
    originals: Dict[Path, Optional[str]] = {}
    pre_write_snapshots: Dict[Path, Optional[_OwnedWriteSnapshot]] = {}
    post_write_snapshots: Dict[Path, Optional[_OwnedWriteSnapshot]] = {}
    changed: List[Path] = []
    try:
        for target, contents in ordered_changes:
            target_bindings[target] = _bind_target(target)
            binding = target_bindings[target]
            _assert_controlled_ancestry_unchanged(
                target, target_ancestries, directory_snapshots
            )
            pre_write_snapshots[target] = _snapshot_prewrite_target(target)
            bound_snapshot, original = _read_bound_target_text(
                binding,
                "recovery target changed during mutation",
            )
            if bound_snapshot != pre_write_snapshots[target]:
                raise RecoveryRefused(
                    "recovery target changed during mutation"
                )
            _assert_controlled_ancestry_unchanged(
                target, target_ancestries, directory_snapshots
            )
            _assert_prewrite_target_unchanged(
                target, pre_write_snapshots[target]
            )
            originals[target] = original
            if original is not None:
                rollback[target] = _stage_text(
                    target, original, binding=binding
                )
                rollback[target].target_binding = target_bindings[target]
            if contents is not None:
                staged[target] = _stage_text(
                    target, contents, binding=binding
                )
                staged[target].target_binding = target_bindings[target]

        if (
            plan_recovery(
                detection,
                accept_hermes_upgrade=accept_hermes_upgrade,
            ).fingerprint
            != plan.fingerprint
        ):
            raise RecoveryRefused("recovery evidence changed; rerun diagnosis")

        for target, contents in ordered_changes:
            _assert_controlled_ancestry_unchanged(
                target, target_ancestries, directory_snapshots
            )
            _assert_prewrite_target_unchanged(
                target, pre_write_snapshots[target]
            )
            binding = target_bindings[target]
            binding.assert_parent()
            _assert_bound_target_unchanged(
                binding, pre_write_snapshots[target]
            )
            _assert_controlled_ancestry_unchanged(
                target, target_ancestries, directory_snapshots
            )
            if contents is None:
                os.unlink(binding.basename, dir_fd=binding.parent_fd)
                changed.append(target)
                post_write_snapshots[target] = None
                _assert_bound_target_unchanged(binding, None)
            else:
                _atomic_replace(staged[target], target)
                staged.pop(target, None)
                changed.append(target)
                post_write_snapshots[target] = _snapshot_bound_write(
                    binding, contents
                )
                _snapshot_owned_write(target, contents)
            _assert_controlled_ancestry_unchanged(
                target, target_ancestries, directory_snapshots
            )
        for target, _contents in ordered_changes:
            _assert_controlled_ancestry_unchanged(
                target, target_ancestries, directory_snapshots
            )
            binding = target_bindings[target]
            binding.assert_parent()
            _assert_bound_target_unchanged(
                binding, post_write_snapshots[target]
            )
    except Exception as exc:
        rollback_error = _rollback_recovery_changes(
            changed,
            originals,
            rollback,
            post_write_snapshots,
            target_ancestries,
            directory_snapshots,
            target_bindings,
            pre_write_snapshots,
        )
        if rollback_error is not None:
            raise RecoveryRefused(
                "recovery rollback failed; install state requires manual review"
            ) from exc
        raise
    finally:
        for temp_path in list(staged.values()) + list(rollback.values()):
            temp_path.cleanup()
        for binding in target_bindings.values():
            binding.release()


def _rollback_recovery_changes(
    changed: List[Path],
    originals: Dict[Path, Optional[str]],
    rollback: Dict[Path, _StagedText],
    post_write_snapshots: Dict[Path, Optional[_OwnedWriteSnapshot]],
    target_ancestries: Dict[Path, Tuple[Path, ...]],
    directory_snapshots: Dict[Path, _DirectoryIdentity],
    target_bindings: Dict[Path, _TargetBinding],
    pre_write_snapshots: Dict[Path, Optional[_OwnedWriteSnapshot]],
) -> Optional[Exception]:
    first_error = None
    for target in reversed(changed):
        try:
            if target not in post_write_snapshots:
                raise RecoveryRefused("recovery lost write ownership")
            binding = target_bindings[target]
            binding.assert_parent()
            _assert_bound_target_unchanged(
                binding, post_write_snapshots[target]
            )
            original = originals[target]
            if original is None:
                os.unlink(binding.basename, dir_fd=binding.parent_fd)
            else:
                rollback_file = rollback[target]
                rollback_file.assert_owned()
                os.replace(
                    rollback_file.basename,
                    binding.basename,
                    src_dir_fd=rollback_file.parent_fd,
                    dst_dir_fd=binding.parent_fd,
                )
                rollback_file.release()
                rollback.pop(target, None)
            _assert_bound_target_restored(
                binding, pre_write_snapshots[target]
            )
        except (OSError, RecoveryRefused) as exc:
            if first_error is None:
                first_error = exc
    return first_error


def _bind_target(target: Path) -> _TargetBinding:
    parent_fd = _open_staging_parent(target.parent)
    opened = os.fstat(parent_fd)
    return _TargetBinding(
        path=target,
        parent_fd=parent_fd,
        basename=target.name,
        parent_identity=(opened.st_dev, opened.st_ino),
    )


def _snapshot_bound_write(
    binding: _TargetBinding, expected_contents: str
) -> _OwnedWriteSnapshot:
    snapshot = _read_bound_target_snapshot(
        binding, "recovery target changed during mutation"
    )
    if snapshot is None or snapshot[2] != _text_sha256(expected_contents):
        raise RecoveryRefused("recovery could not verify committed write")
    return snapshot


def _assert_bound_target_unchanged(
    binding: _TargetBinding, expected: Optional[_OwnedWriteSnapshot]
) -> None:
    binding.assert_parent()
    current = _read_bound_target_snapshot(
        binding, "recovery target changed during mutation"
    )
    if current != expected:
        raise RecoveryRefused("recovery target changed during mutation")


def _assert_bound_target_restored(
    binding: _TargetBinding, expected: Optional[_OwnedWriteSnapshot]
) -> None:
    binding.assert_parent()
    current = _read_bound_target_snapshot(
        binding, "recovery rollback target changed"
    )
    if expected is None:
        matches = current is None
    else:
        matches = current is not None and current[2] == expected[2]
    if not matches:
        raise RecoveryRefused("recovery rollback target changed")


def _read_bound_target_snapshot(
    binding: _TargetBinding, refusal: str
) -> Optional[_OwnedWriteSnapshot]:
    snapshot, _contents = _read_bound_target_text(binding, refusal)
    return snapshot


def _read_bound_target_text(
    binding: _TargetBinding, refusal: str
) -> Tuple[Optional[_OwnedWriteSnapshot], Optional[str]]:
    try:
        before = os.stat(
            binding.basename,
            dir_fd=binding.parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None, None
    if not stat.S_ISREG(before.st_mode):
        raise RecoveryRefused(refusal)
    identity = (before.st_dev, before.st_ino)
    descriptor = -1
    digest = sha256()
    payload = bytearray()
    try:
        descriptor = os.open(
            binding.basename,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=binding.parent_fd,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != identity
        ):
            raise RecoveryRefused(refusal)
        while chunk := os.read(descriptor, 64 * 1024):
            digest.update(chunk)
            payload.extend(chunk)
    except OSError as exc:
        raise RecoveryRefused(refusal) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    after = os.stat(
        binding.basename,
        dir_fd=binding.parent_fd,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(after.st_mode)
        or (after.st_dev, after.st_ino) != identity
    ):
        raise RecoveryRefused(refusal)
    try:
        contents = payload.decode("utf-8")
    except UnicodeError as exc:
        raise RecoveryRefused(refusal) from exc
    return (before.st_dev, before.st_ino, digest.hexdigest()), contents


def _snapshot_prewrite_target(
    target: Path,
) -> Optional[_OwnedWriteSnapshot]:
    try:
        target.lstat()
    except FileNotFoundError:
        return None
    return _read_regular_file_snapshot(
        target, "recovery target is not safe before write"
    )


def _assert_prewrite_target_unchanged(
    target: Path, expected: Optional[_OwnedWriteSnapshot]
) -> None:
    if expected is None:
        try:
            target.lstat()
        except FileNotFoundError:
            return
        raise RecoveryRefused("recovery target changed before write")
    current = _read_regular_file_snapshot(
        target, "recovery target changed before write"
    )
    if current != expected:
        raise RecoveryRefused("recovery target changed before write")


def _snapshot_controlled_ancestries(
    controlled_root: Path, targets: List[Path]
) -> Tuple[
    Dict[Path, Tuple[Path, ...]],
    Dict[Path, _DirectoryIdentity],
]:
    target_ancestries: Dict[Path, Tuple[Path, ...]] = {}
    directory_snapshots: Dict[Path, _DirectoryIdentity] = {}
    for target in targets:
        ancestry = _controlled_ancestry_paths(controlled_root, target)
        target_ancestries[target] = ancestry
        for directory in ancestry:
            if directory not in directory_snapshots:
                directory_snapshots[directory] = _read_directory_identity(
                    directory,
                    "recovery controlled ancestry is unsafe",
                )
    return target_ancestries, directory_snapshots


def _controlled_ancestry_paths(
    controlled_root: Path, target: Path
) -> Tuple[Path, ...]:
    root = Path(os.path.abspath(controlled_root))
    absolute_target = Path(os.path.abspath(target))
    try:
        relative = absolute_target.relative_to(root)
    except ValueError as exc:
        raise RecoveryRefused(
            "recovery target is outside controlled root"
        ) from exc
    if not relative.parts:
        raise RecoveryRefused("recovery target is outside controlled root")
    directories = [root]
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        directories.append(current)
    return tuple(directories)


def _read_directory_identity(
    directory: Path, refusal: str
) -> _DirectoryIdentity:
    try:
        snapshot = directory.lstat()
    except FileNotFoundError as exc:
        raise RecoveryRefused(refusal) from exc
    if not stat.S_ISDIR(snapshot.st_mode):
        raise RecoveryRefused(refusal)
    return snapshot.st_dev, snapshot.st_ino


def _assert_controlled_ancestry_unchanged(
    target: Path,
    target_ancestries: Dict[Path, Tuple[Path, ...]],
    directory_snapshots: Dict[Path, _DirectoryIdentity],
) -> None:
    for directory in target_ancestries[target]:
        if _read_directory_identity(
            directory,
            "recovery target ancestry changed before write",
        ) != directory_snapshots[directory]:
            raise RecoveryRefused(
                "recovery target ancestry changed before write"
            )


def _snapshot_owned_write(
    target: Path, expected_contents: str
) -> _OwnedWriteSnapshot:
    snapshot = _read_owned_write_snapshot(target)
    if snapshot[2] != _text_sha256(expected_contents):
        raise RecoveryRefused("recovery could not verify committed write")
    return snapshot


def _assert_owned_write_unchanged(
    target: Path, expected: Optional[_OwnedWriteSnapshot]
) -> None:
    if expected is None:
        try:
            target.lstat()
        except FileNotFoundError:
            return
        raise RecoveryRefused("recovery target changed after write")
    if _read_owned_write_snapshot(target) != expected:
        raise RecoveryRefused("recovery target changed after write")


def _read_owned_write_snapshot(target: Path) -> _OwnedWriteSnapshot:
    return _read_regular_file_snapshot(
        target, "recovery target changed after write"
    )


def _read_regular_file_snapshot(
    target: Path, refusal: str
) -> _OwnedWriteSnapshot:
    try:
        before = target.lstat()
    except FileNotFoundError as exc:
        raise RecoveryRefused(refusal) from exc
    if not stat.S_ISREG(before.st_mode):
        raise RecoveryRefused(refusal)
    identity = (before.st_dev, before.st_ino)
    descriptor: int | None = None
    digest = sha256()
    try:
        descriptor = os.open(
            target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != identity
        ):
            raise RecoveryRefused(refusal)
        while chunk := os.read(descriptor, 64 * 1024):
            digest.update(chunk)
    except OSError as exc:
        raise RecoveryRefused(refusal) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = target.lstat()
    except FileNotFoundError as exc:
        raise RecoveryRefused(refusal) from exc
    if (
        not stat.S_ISREG(after.st_mode)
        or (after.st_dev, after.st_ino) != identity
    ):
        raise RecoveryRefused(refusal)
    return before.st_dev, before.st_ino, digest.hexdigest()


def _stage_text(
    target: Path,
    contents: str,
    *,
    binding: _TargetBinding | None = None,
) -> _StagedText:
    if binding is None:
        parent_fd = _open_staging_parent(target.parent)
        target_name = target.name
    else:
        binding.assert_parent()
        parent_fd = os.dup(binding.parent_fd)
        target_name = binding.basename
    descriptor = -1
    staged: _StagedText | None = None
    try:
        basename = f".{target_name}.{uuid4().hex}.tmp"
        descriptor = os.open(
            basename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent_fd,
        )
        staged = _StagedText(
            path=target.parent / basename,
            parent_fd=parent_fd,
            basename=basename,
            identity=(os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino),
        )
        parent_fd = -1
        try:
            target_snapshot = os.stat(
                target_name,
                dir_fd=staged.parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            mode = 0o600
        else:
            if not stat.S_ISREG(target_snapshot.st_mode):
                raise RecoveryRefused(
                    "recovery target is not safe before write"
                )
            mode = stat.S_IMODE(target_snapshot.st_mode)
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        descriptor = -1
        with handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        return staged
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        if staged is not None:
            staged.cleanup()
        elif parent_fd >= 0:
            os.close(parent_fd)
        raise


def _open_staging_parent(parent: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(parent, flags)
    except OSError as exc:
        raise RecoveryRefused("recovery staging parent is unsafe") from exc
    try:
        opened = os.fstat(parent_fd)
        current = parent.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (opened.st_dev, opened.st_ino)
            != (current.st_dev, current.st_ino)
        ):
            raise RecoveryRefused("recovery staging parent is unsafe")
        return parent_fd
    except Exception:
        os.close(parent_fd)
        raise


def _atomic_replace(staged: _StagedText, target: Path) -> None:
    binding = staged.target_binding
    if binding is None:
        raise RecoveryRefused("recovery target binding is missing")
    staged.assert_owned()
    binding.assert_parent()
    os.replace(
        staged.basename,
        binding.basename,
        src_dir_fd=staged.parent_fd,
        dst_dir_fd=binding.parent_fd,
    )
    staged.release()


def _new_quarantine_path(source_path: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    unique = f"{os.getpid()}-{uuid4().hex[:8]}"
    return source_path.with_name(
        f"{source_path.name}.hfc-corrupt-{stamp}-{unique}"
    )


def _quarantine_sort_key(path: Path) -> Tuple[int, str]:
    if path.is_symlink():
        raise RecoveryRefused(
            "recovery quarantine path must not be a symbolic link"
        )
    return path.stat().st_mtime_ns, path.name


def _plan_from_evidence(
    detection: HermesDetection,
    evidence: RecoveryEvidence,
    *,
    accept_hermes_upgrade: bool = False,
) -> RecoveryPlan:
    classification = _classify_evidence(
        detection,
        evidence,
        accept_hermes_upgrade=accept_hermes_upgrade,
    )
    return RecoveryPlan(
        root=detection.root,
        state=classification.state,
        executable=classification.executable,
        fingerprint=_fingerprint(classification.fingerprint_parts),
        actions=classification.actions,
        findings=classification.findings,
    )


def _acquire_os_lock(handle, deadline: float) -> None:
    while True:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if not _is_lock_contention(exc):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RecoveryRefused("timed out waiting for recovery lock")
            time.sleep(min(0.05, remaining))


def _is_lock_contention(exc: OSError) -> bool:
    contention_errors = {errno.EACCES, errno.EAGAIN}
    if os.name == "nt":
        contention_errors.add(errno.EDEADLK)
    return exc.errno in contention_errors


def _release_os_lock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fingerprint(parts: Dict[str, str]) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256(encoded).hexdigest()


def _read_evidence(detection: HermesDetection) -> RecoveryEvidence:
    run_py = detection.run_py
    backup_path = run_py.with_name(f"{run_py.name}{BACKUP_SUFFIX}")
    manifest_path = detection.root / MANIFEST_NAME
    manifest = _read_manifest_evidence(manifest_path)
    (
        cron_current_text,
        cron_current_sha256,
        cron_backup_text,
        cron_backup_sha256,
        cron_marker_error,
    ) = _read_cron_evidence(detection)
    (
        base_current_text,
        base_current_sha256,
        base_backup_text,
        base_backup_sha256,
        base_marker_error,
    ) = _read_base_evidence(detection)
    if not detection.base_required and not _base_manifest_fields(manifest):
        base_current_text = None
        base_current_sha256 = ""
        base_backup_text = None
        base_backup_sha256 = ""
        base_marker_error = ""

    if run_py.is_symlink():
        return RecoveryEvidence(
            current_text="",
            current_sha256="",
            backup_text=None,
            backup_sha256="",
            manifest=manifest,
            marker_error="symlink_refused",
            cron_current_text=cron_current_text,
            cron_current_sha256=cron_current_sha256,
            cron_backup_text=cron_backup_text,
            cron_backup_sha256=cron_backup_sha256,
            cron_marker_error=cron_marker_error,
            base_current_text=base_current_text,
            base_current_sha256=base_current_sha256,
            base_backup_text=base_backup_text,
            base_backup_sha256=base_backup_sha256,
            base_marker_error=base_marker_error,
        )

    try:
        current_text = _read_text(run_py)
    except (OSError, UnicodeError):
        return RecoveryEvidence(
            current_text="",
            current_sha256="",
            backup_text=None,
            backup_sha256="",
            manifest=manifest,
            marker_error="current_read_error",
            cron_current_text=cron_current_text,
            cron_current_sha256=cron_current_sha256,
            cron_backup_text=cron_backup_text,
            cron_backup_sha256=cron_backup_sha256,
            cron_marker_error=cron_marker_error,
            base_current_text=base_current_text,
            base_current_sha256=base_current_sha256,
            base_backup_text=base_backup_text,
            base_backup_sha256=base_backup_sha256,
            base_marker_error=base_marker_error,
        )

    marker_error = ""
    try:
        remove_patch(current_text)
    except ValueError:
        marker_error = "corrupt_patch_markers"

    backup_text: Optional[str] = None
    backup_sha256 = ""
    if backup_path.is_symlink():
        backup_sha256 = f"{_STATUS_PREFIX}symlink"
    elif backup_path.exists():
        try:
            backup_text = _read_text(backup_path)
            backup_sha256 = _text_sha256(backup_text)
        except (OSError, UnicodeError):
            backup_sha256 = f"{_STATUS_PREFIX}read_error"

    return RecoveryEvidence(
        current_text=current_text,
        current_sha256=_text_sha256(current_text),
        backup_text=backup_text,
        backup_sha256=backup_sha256,
        manifest=manifest,
        marker_error=marker_error,
        cron_current_text=cron_current_text,
        cron_current_sha256=cron_current_sha256,
        cron_backup_text=cron_backup_text,
        cron_backup_sha256=cron_backup_sha256,
        cron_marker_error=cron_marker_error,
        base_current_text=base_current_text,
        base_current_sha256=base_current_sha256,
        base_backup_text=base_backup_text,
        base_backup_sha256=base_backup_sha256,
        base_marker_error=base_marker_error,
    )


def _classify_evidence(
    detection: HermesDetection,
    evidence: RecoveryEvidence,
    *,
    accept_hermes_upgrade: bool = False,
) -> RecoveryClassification:
    parts = _fingerprint_parts(detection, evidence)
    read_findings = []
    if evidence.marker_error == "symlink_refused":
        read_findings.append(_finding("symlink_refused", "error"))
    elif evidence.marker_error == "current_read_error":
        read_findings.append(_finding("current_read_error", "error"))
    if evidence.cron_marker_error == "symlink_refused":
        read_findings.append(_finding("cron_symlink_refused", "error"))
    elif evidence.cron_marker_error == "current_read_error":
        read_findings.append(_finding("cron_current_read_error", "error"))
    if evidence.base_marker_error == "symlink_refused":
        read_findings.append(_finding("base_symlink_refused", "error"))
    elif evidence.base_marker_error == "current_read_error":
        read_findings.append(_finding("base_current_read_error", "error"))
    if read_findings:
        return _classification("refused", False, (), read_findings, parts)

    gateway = _classify_gateway_evidence(
        detection,
        evidence,
        accept_hermes_upgrade=accept_hermes_upgrade,
    )
    cron = _classify_cron_evidence(
        detection,
        evidence,
        gateway.state,
        accept_hermes_upgrade=accept_hermes_upgrade,
    )
    base = _classify_base_evidence(
        detection,
        evidence,
        gateway.state,
        accept_hermes_upgrade=accept_hermes_upgrade,
    )
    return _merge_classifications(gateway, cron, base, parts)


def _classify_base_evidence(
    detection: HermesDetection,
    evidence: RecoveryEvidence,
    gateway_state: str,
    *,
    accept_hermes_upgrade: bool,
) -> RecoveryClassification:
    parts = _fingerprint_parts(detection, evidence)
    findings = []
    manifest = evidence.manifest
    manifest_fields = _base_manifest_fields(manifest)
    manifest_has_any = bool(manifest_fields)
    manifest_complete = len(manifest_fields) == 4
    backup_present = evidence.base_backup_text is not None
    backup_error = evidence.base_backup_sha256.startswith(_STATUS_PREFIX)
    current = evidence.base_current_text
    artifacts_present = manifest_has_any or backup_present or backup_error

    if evidence.base_marker_error in {"symlink_refused", "current_read_error"}:
        findings.append(_finding("base_evidence_unavailable", "error"))
        return _classification("refused", False, (), findings, parts)
    if current is None:
        if detection.base_required or artifacts_present:
            findings.append(_finding("base_source_missing", "error"))
            return _classification("refused", False, (), findings, parts)
        return _classification("clean", False, (), findings, parts)
    if manifest_has_any and not manifest_complete:
        findings.append(_finding("base_manifest_incomplete", "error"))
        return _classification("owned_incomplete", False, (), findings, parts)
    if backup_error:
        findings.append(_finding("base_backup_unavailable", "error"))
        return _classification("refused", False, (), findings, parts)

    marker_corrupt = evidence.base_marker_error == "corrupt_patch_markers"
    unpatched = current
    has_patch = False
    if not marker_corrupt:
        try:
            unpatched = remove_base_patch(current)
            has_patch = unpatched != current
        except ValueError:
            marker_corrupt = True

    if not artifacts_present and not has_patch:
        if not detection.base_required:
            return _classification("clean", False, (), findings, parts)
        findings.append(_finding("base_install_state_incomplete", "error"))
        reapply_error = _validate_base_reapplication(current)
        if reapply_error:
            findings.append(_finding("base_source_mismatch", "error"))
        can_extend_owned_install = gateway_state in {
            "installed",
            "owned_incomplete",
            "corrupt_owned",
        }
        actions = (
            "rebuild_base_backup",
            "reapply_current_base_hook",
            "rebuild_manifest",
        )
        return _classification(
            "owned_incomplete",
            bool(can_extend_owned_install and not reapply_error),
            actions if can_extend_owned_install else (),
            findings,
            parts,
        )
    checks = _check_base_manifest(detection, evidence)
    findings.extend(checks.findings)
    backup_checks = _check_base_backup(evidence)
    findings.extend(backup_checks.findings)

    if marker_corrupt:
        executable = bool(
            manifest_complete
            and checks.valid
            and checks.current_matches
            and checks.backup_matches
            and backup_checks.valid
        )
        findings.append(_finding("base_marker_error", "error"))
        return _classification(
            "corrupt_owned",
            executable,
            (
                "restore_verified_base_backup",
                "reapply_current_base_hook",
                "rebuild_manifest",
            ),
            findings,
            parts,
        )

    if has_patch:
        candidate_matches = False
        if backup_checks.valid and evidence.base_backup_text is not None:
            try:
                candidate_matches = (
                    apply_base_patch(evidence.base_backup_text) == current
                    and unpatched == evidence.base_backup_text
                )
            except ValueError:
                candidate_matches = False
        complete = bool(
            manifest_complete
            and checks.valid
            and checks.current_matches
            and checks.backup_matches
            and backup_checks.valid
            and candidate_matches
        )
        if complete:
            return _classification("installed", False, (), findings, parts)
        findings.append(_finding("base_patch_mismatch", "error"))
        return _classification("owned_incomplete", False, (), findings, parts)

    if not manifest_complete or not backup_checks.valid or not checks.valid:
        findings.append(_finding("base_install_state_incomplete", "error"))
        return _classification("owned_incomplete", False, (), findings, parts)
    reapply_error = _validate_base_reapplication(current)
    source_matches = evidence.base_backup_text == current
    accepted_replacement = bool(
        accept_hermes_upgrade and detection.supported and not reapply_error
    )
    executable = bool(not reapply_error and (source_matches or accepted_replacement))
    if not executable:
        findings.append(_finding("base_source_mismatch", "error"))
    elif accepted_replacement and not source_matches:
        findings.append(_finding("hermes_upgrade_base_source_accepted", "warning"))
    actions = ["reapply_current_base_hook", "rebuild_manifest"]
    if not source_matches:
        actions.insert(0, "rebuild_base_backup")
    return _classification(
        "stale_unpatched",
        executable,
        tuple(actions),
        findings,
        parts,
    )


def _classify_gateway_evidence(
    detection: HermesDetection,
    evidence: RecoveryEvidence,
    *,
    accept_hermes_upgrade: bool = False,
) -> RecoveryClassification:
    parts = _fingerprint_parts(detection, evidence)
    findings = []

    if evidence.marker_error == "symlink_refused":
        findings.append(_finding("symlink_refused", "error"))
        return _classification("refused", False, (), findings, parts)
    if evidence.marker_error == "current_read_error":
        findings.append(_finding("current_read_error", "error"))
        return _classification("refused", False, (), findings, parts)

    manifest = evidence.manifest
    manifest_present = manifest is not None
    manifest_invalid = bool(manifest and manifest.get(_MANIFEST_ERROR))
    manifest_usable = manifest_present and not manifest_invalid
    backup_present = evidence.backup_text is not None
    backup_status_error = evidence.backup_sha256.startswith(_STATUS_PREFIX)
    artifacts_present = manifest_present or backup_present or backup_status_error

    marker_corrupt = bool(evidence.marker_error)
    unpatched = evidence.current_text
    has_owned_patch = False
    if not marker_corrupt:
        try:
            unpatched = remove_patch(evidence.current_text)
            has_owned_patch = unpatched != evidence.current_text
        except ValueError:
            marker_corrupt = True

    if marker_corrupt:
        state = "corrupt_owned"
    elif has_owned_patch:
        state = "installed"
    elif artifacts_present:
        state = "stale_unpatched"
    else:
        state = "clean"

    if state == "clean":
        if not detection.supported and _is_anchor_refusal(detection.reason):
            findings.append(_finding("unsupported_anchors", "warning"))
        return _classification(state, False, (), findings, parts)

    manifest_checks = _check_manifest(detection, evidence, state)
    findings.extend(manifest_checks.findings)
    backup_checks = _check_backup(evidence)
    findings.extend(backup_checks.findings)

    if state == "stale_unpatched":
        actions = ("clear_stale_install_state",)
        current_valid = _is_valid_python(evidence.current_text)
        if not current_valid:
            findings.append(_finding("unsupported_anchors", "error"))

        if backup_present:
            source_matches = (
                backup_checks.valid
                and evidence.backup_text == evidence.current_text
            )
        else:
            expected_backup = manifest_checks.backup_hash
            source_matches = bool(
                manifest_checks.valid
                and expected_backup
                and evidence.current_sha256 == expected_backup
            )
            if not backup_status_error:
                findings.append(_finding("backup_missing", "warning"))

        if manifest is None:
            findings.append(_finding("manifest_missing", "warning"))
            manifest_safe = backup_present and backup_checks.valid
        else:
            manifest_safe = manifest_checks.valid

        reapply_error = (
            _validate_reapplication(detection, evidence.current_text)
            if accept_hermes_upgrade and current_valid
            else ""
        )
        accepted_replacement = bool(
            accept_hermes_upgrade
            and current_valid
            and detection.supported
            and not reapply_error
            and backup_present
            and backup_checks.valid
            and manifest_checks.valid
            and manifest_checks.backup_matches
            and not manifest_invalid
            and not backup_status_error
        )
        if accepted_replacement:
            findings.append(
                _finding("hermes_upgrade_source_accepted", "warning")
            )
        elif backup_present and backup_checks.valid and not source_matches:
            findings.append(_finding("backup_source_mismatch", "error"))
        if reapply_error == "unsupported_anchors":
            findings.append(_finding("unsupported_anchors", "error"))
        elif reapply_error:
            findings.append(_finding("reapplication_invalid", "error"))

        executable = bool(
            current_valid
            and (source_matches or accepted_replacement)
            and manifest_safe
        )
        return _classification(state, executable, actions, findings, parts)

    if marker_corrupt:
        marker_only_damage = _matches_owned_gateway_marker_damage(
            detection,
            evidence,
            manifest_checks=manifest_checks,
            backup_checks=backup_checks,
        )
        if marker_only_damage:
            findings = [
                finding
                for finding in findings
                if finding.code != "current_hash_mismatch"
            ]
            findings.append(_finding("owned_marker_damage", "warning"))
        findings.insert(0, _finding("marker_error", "error"))
        actions = ("restore_verified_backup", "reapply_current_hook")
        reapply = _validate_reapplication(detection, evidence.backup_text)
        if reapply == "unsupported_anchors":
            findings.append(_finding("unsupported_anchors", "error"))
        elif reapply:
            findings.append(_finding("reapplication_invalid", "error"))
        executable = bool(
            manifest_checks.valid
            and (manifest_checks.current_matches or marker_only_damage)
            and backup_checks.valid
            and manifest_checks.backup_matches
            and not reapply
        )
        return _classification(state, executable, actions, findings, parts)

    source_text = evidence.backup_text if backup_checks.valid else unpatched
    source_matches = not backup_present or evidence.backup_text == unpatched
    if backup_present and backup_checks.valid and not source_matches:
        findings.append(_finding("backup_source_mismatch", "error"))

    reapply_error = _validate_reapplication(detection, source_text)
    candidate_matches = False
    if not reapply_error and source_text is not None:
        candidate_matches = (
            apply_patch(source_text, strategy=detection.hook_strategy)
            == evidence.current_text
        )
    verified_owned_upgrade = bool(
        not candidate_matches
        and not reapply_error
        and manifest_checks.valid
        and manifest_checks.current_matches
        and manifest_checks.backup_matches
        and backup_checks.valid
        and source_matches
    )
    if reapply_error == "unsupported_anchors":
        findings.append(_finding("unsupported_anchors", "error"))
    elif reapply_error:
        findings.append(_finding("reapplication_invalid", "error"))
    elif verified_owned_upgrade:
        findings.append(_finding("owned_patch_upgrade", "warning"))
    elif not candidate_matches:
        findings.append(_finding("current_patch_mismatch", "error"))

    complete = bool(
        manifest_checks.valid
        and manifest_checks.current_matches
        and manifest_checks.backup_matches
        and backup_checks.valid
        and source_matches
        and candidate_matches
    )
    if complete:
        return _classification("installed", False, (), findings, parts)

    state = "owned_incomplete"
    actions = _incomplete_actions(
        manifest_present=manifest_present,
        manifest_valid=manifest_checks.valid,
        backup_present=backup_present,
        candidate_matches=candidate_matches,
    )
    if not backup_present and not backup_status_error:
        findings.append(_finding("backup_missing", "warning"))
    if manifest is None:
        findings.append(_finding("manifest_missing", "warning"))

    manifest_safe = manifest is None or manifest_checks.valid
    if manifest_usable and not manifest_checks.current_matches:
        manifest_safe = bool(
            manifest_checks.paths_valid
            and manifest_checks.backup_hash
            and manifest_checks.backup_matches
        )
    if not manifest_present and not backup_present:
        manifest_safe = True

    derived_backup_matches = bool(
        not manifest_usable
        or not manifest_checks.backup_hash
        or _text_sha256(unpatched) == manifest_checks.backup_hash
    )
    executable = bool(
        manifest_safe
        and not manifest_invalid
        and not backup_status_error
        and (not backup_present or backup_checks.valid)
        and source_matches
        and derived_backup_matches
        and (candidate_matches or verified_owned_upgrade)
    )
    return _classification(state, executable, actions, findings, parts)


def plan_recovery(
    detection: HermesDetection,
    *,
    accept_hermes_upgrade: bool = False,
) -> RecoveryPlan:
    return _plan_from_evidence(
        detection,
        _read_evidence(detection),
        accept_hermes_upgrade=accept_hermes_upgrade,
    )


def sanitize_recovery_plan(plan: RecoveryPlan) -> Dict[str, object]:
    return {
        "state": plan.state,
        "executable": plan.executable,
        "fingerprint": plan.fingerprint[:12],
        "actions": list(plan.actions),
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "message": _safe_message(finding.code),
            }
            for finding in plan.findings
        ],
    }


@dataclass(frozen=True)
class _ManifestChecks:
    valid: bool
    paths_valid: bool
    current_matches: bool
    backup_matches: bool
    backup_hash: str
    findings: Tuple[RecoveryFinding, ...]


@dataclass(frozen=True)
class _BackupChecks:
    valid: bool
    findings: Tuple[RecoveryFinding, ...]


def _classify_cron_evidence(
    detection: HermesDetection,
    evidence: RecoveryEvidence,
    gateway_state: str,
    *,
    accept_hermes_upgrade: bool = False,
) -> RecoveryClassification:
    parts = _fingerprint_parts(detection, evidence)
    findings = []
    manifest = evidence.manifest
    manifest_invalid = bool(manifest and manifest.get(_MANIFEST_ERROR))
    manifest_has_cron = _manifest_has_cron_evidence(manifest)
    backup_present = evidence.cron_backup_text is not None
    backup_status_error = evidence.cron_backup_sha256.startswith(_STATUS_PREFIX)
    artifacts_present = manifest_has_cron or backup_present or backup_status_error

    if evidence.cron_current_text is None:
        if not artifacts_present:
            return _classification("clean", False, (), findings, parts)
        findings.append(_finding("cron_source_missing", "error"))
        findings.extend(_check_cron_backup(evidence).findings)
        findings.extend(_check_cron_manifest(detection, evidence, False).findings)
        return _classification("owned_incomplete", False, (), findings, parts)

    current = evidence.cron_current_text
    marker_corrupt = bool(evidence.cron_marker_error)
    unpatched = current
    has_owned_patch = False
    if not marker_corrupt:
        try:
            unpatched = remove_cron_patch(current)
            has_owned_patch = unpatched != current
        except ValueError:
            marker_corrupt = True

    manifest_checks = _check_cron_manifest(
        detection, evidence, marker_corrupt or has_owned_patch
    )
    backup_checks = _check_cron_backup(evidence)
    findings.extend(manifest_checks.findings)
    findings.extend(backup_checks.findings)

    if marker_corrupt:
        findings.insert(0, _finding("cron_marker_error", "error"))
        reapply_error = _validate_cron_reapplication(evidence.cron_backup_text)
        if reapply_error == "unsupported_anchors":
            findings.append(_finding("cron_unsupported_anchors", "error"))
        elif reapply_error:
            findings.append(_finding("cron_reapplication_invalid", "error"))
        executable = bool(
            manifest_has_cron
            and manifest_checks.valid
            and manifest_checks.current_matches
            and backup_checks.valid
            and manifest_checks.backup_matches
            and not reapply_error
        )
        return _classification(
            "corrupt_owned",
            executable,
            ("restore_verified_cron_backup", "reapply_current_cron_hook"),
            findings,
            parts,
        )

    if has_owned_patch:
        source_text = evidence.cron_backup_text if backup_checks.valid else unpatched
        source_matches = not backup_present or evidence.cron_backup_text == unpatched
        if backup_present and backup_checks.valid and not source_matches:
            findings.append(_finding("cron_backup_source_mismatch", "error"))

        reapply_error = _validate_cron_reapplication(source_text)
        candidate_matches = False
        if not reapply_error and source_text is not None:
            candidate_matches = apply_cron_patch(source_text) == current
        if reapply_error == "unsupported_anchors":
            findings.append(_finding("cron_unsupported_anchors", "error"))
        elif reapply_error:
            findings.append(_finding("cron_reapplication_invalid", "error"))
        elif not candidate_matches:
            findings.append(_finding("cron_current_patch_mismatch", "error"))

        complete = bool(
            manifest_has_cron
            and manifest_checks.valid
            and manifest_checks.current_matches
            and manifest_checks.backup_matches
            and backup_checks.valid
            and source_matches
            and candidate_matches
        )
        if complete:
            return _classification("installed", False, (), findings, parts)

        actions = []
        if not backup_present:
            actions.append("rebuild_cron_backup")
        if not manifest_has_cron or not manifest_checks.valid or not backup_present:
            actions.append("rebuild_manifest")
        if not candidate_matches:
            actions.append("reapply_current_cron_hook")

        derived_backup_matches = bool(
            not manifest_has_cron
            or not manifest_checks.backup_hash
            or _text_sha256(unpatched) == manifest_checks.backup_hash
        )
        manifest_safe = bool(
            not manifest_invalid
            and (
                not manifest_has_cron
                or (
                    manifest_checks.valid
                    and manifest_checks.current_matches
                    and (
                        not backup_present
                        or manifest_checks.backup_matches
                    )
                )
            )
        )
        executable = bool(
            manifest_safe
            and not backup_status_error
            and (not backup_present or backup_checks.valid)
            and source_matches
            and derived_backup_matches
            and candidate_matches
        )
        return _classification(
            "owned_incomplete",
            executable,
            tuple(actions or ["rebuild_manifest"]),
            findings,
            parts,
        )

    if artifacts_present:
        source_valid = _is_valid_python(current)
        source_matches = bool(
            (backup_present and backup_checks.valid and evidence.cron_backup_text == current)
            or (
                not backup_present
                and manifest_checks.backup_hash
                and evidence.cron_current_sha256 == manifest_checks.backup_hash
            )
        )
        if not source_valid:
            findings.append(_finding("cron_unsupported_anchors", "error"))
        reapply_error = _validate_cron_reapplication(current)
        accepted_replacement = bool(
            accept_hermes_upgrade
            and source_valid
            and not reapply_error
            and backup_present
            and backup_checks.valid
            and manifest_has_cron
            and manifest_checks.valid
            and manifest_checks.backup_matches
            and not manifest_invalid
            and not backup_status_error
        )
        if accepted_replacement:
            findings.append(
                _finding("hermes_upgrade_cron_source_accepted", "warning")
            )
        elif backup_present and backup_checks.valid and not source_matches:
            findings.append(_finding("cron_backup_source_mismatch", "error"))
        optional_unsupported = bool(
            reapply_error == "unsupported_anchors"
            and source_valid
            and source_matches
            and manifest_has_cron
            and manifest_checks.valid
            and manifest_checks.current_matches
            and manifest_checks.backup_matches
            and backup_checks.valid
            and not manifest_invalid
            and not backup_status_error
        )
        if optional_unsupported:
            return _classification("clean", False, (), findings, parts)
        if reapply_error == "unsupported_anchors":
            findings.append(_finding("cron_unsupported_anchors", "error"))
        elif reapply_error:
            findings.append(_finding("cron_reapplication_invalid", "error"))
        executable = bool(
            source_valid
            and (source_matches or accepted_replacement)
            and manifest_has_cron
            and manifest_checks.valid
            and not manifest_invalid
            and not backup_status_error
            and not reapply_error
        )
        actions = (
            ("clear_stale_install_state",)
            if gateway_state in {"clean", "stale_unpatched"}
            else ("reapply_current_cron_hook", "rebuild_manifest")
        )
        return _classification(
            "stale_unpatched", executable, actions, findings, parts
        )

    if gateway_state == "clean":
        return _classification("clean", False, (), findings, parts)

    reapply_error = _validate_cron_reapplication(current)
    if reapply_error == "unsupported_anchors":
        findings.append(_finding("cron_unsupported_anchors", "error"))
    elif reapply_error:
        findings.append(_finding("cron_reapplication_invalid", "error"))
    findings.append(_finding("cron_manifest_missing", "warning"))
    return _classification(
        "owned_incomplete",
        not reapply_error and not manifest_invalid,
        (
            "rebuild_cron_backup",
            "reapply_current_cron_hook",
            "rebuild_manifest",
        ),
        findings,
        parts,
    )


def _merge_classifications(
    gateway: RecoveryClassification,
    cron: RecoveryClassification,
    base: RecoveryClassification,
    parts: Dict[str, str],
) -> RecoveryClassification:
    states = {gateway.state, cron.state, base.state}
    if "refused" in states:
        state = "refused"
    elif "corrupt_owned" in states:
        state = "corrupt_owned"
    elif "owned_incomplete" in states:
        state = "owned_incomplete"
    elif "stale_unpatched" in states:
        state = "stale_unpatched"
    elif "installed" in states:
        state = "installed"
    else:
        state = "clean"

    actions, actions_safe = _merge_actions(gateway, cron, base)
    findings = gateway.findings + cron.findings + base.findings
    healthy = {"clean", "installed"}
    executable = bool(
        state not in healthy
        and actions_safe
        and gateway.state != "refused"
        and cron.state != "refused"
        and base.state != "refused"
        and (gateway.state in healthy or gateway.executable)
        and (cron.state in healthy or cron.executable)
        and (base.state in healthy or base.executable)
    )
    return _classification(state, executable, actions, findings, parts)


def _merge_actions(
    gateway: RecoveryClassification,
    cron: RecoveryClassification,
    base: RecoveryClassification,
) -> Tuple[Tuple[str, ...], bool]:
    clear_action = "clear_stale_install_state"
    if clear_action not in gateway.actions:
        return tuple(
            dict.fromkeys(gateway.actions + cron.actions + base.actions)
        ), True

    restore_actions = []
    if cron.state == "installed" or (
        cron.state == "corrupt_owned" and cron.executable
    ):
        restore_actions.append("restore_verified_cron_backup")
    elif cron.state not in {"clean", "stale_unpatched"}:
        return (), False
    if base.state == "installed" or (
        base.state == "corrupt_owned" and base.executable
    ):
        restore_actions.append("restore_verified_base_backup")
    elif base.state not in {"clean", "stale_unpatched"}:
        return (), False
    return tuple((*restore_actions, clear_action)), True


def _check_manifest(
    detection: HermesDetection, evidence: RecoveryEvidence, state: str
) -> _ManifestChecks:
    manifest = evidence.manifest
    if manifest is None:
        return _ManifestChecks(False, False, False, False, "", ())
    if manifest.get(_MANIFEST_ERROR):
        return _ManifestChecks(
            False,
            False,
            False,
            False,
            "",
            (_finding("manifest_invalid", "error"),),
        )

    findings = []
    expected_run = _relative_path(detection.root, detection.run_py)
    backup_path = detection.run_py.with_name(
        f"{detection.run_py.name}{BACKUP_SUFFIX}"
    )
    expected_backup = _relative_path(detection.root, backup_path)
    paths_valid = bool(
        manifest.get("run_py") == expected_run
        and manifest.get("backup") == expected_backup
    )
    if not paths_valid:
        findings.append(_finding("manifest_path_mismatch", "error"))

    current_hash = _manifest_hash(manifest, "patched_sha256")
    backup_hash = _manifest_hash(manifest, "backup_sha256")
    if not current_hash:
        findings.append(_finding("manifest_current_hash_invalid", "error"))
    if not backup_hash:
        findings.append(_finding("manifest_backup_hash_invalid", "error"))

    current_matches = bool(current_hash and evidence.current_sha256 == current_hash)
    backup_matches = bool(
        backup_hash
        and evidence.backup_text is not None
        and evidence.backup_sha256 == backup_hash
    )
    if state != "stale_unpatched" and current_hash and not current_matches:
        findings.append(_finding("current_hash_mismatch", "error"))
    if evidence.backup_text is not None and backup_hash and not backup_matches:
        findings.append(_finding("backup_hash_mismatch", "error"))

    valid = bool(paths_valid and current_hash and backup_hash)
    return _ManifestChecks(
        valid,
        paths_valid,
        current_matches,
        backup_matches,
        backup_hash,
        tuple(findings),
    )


def _matches_owned_gateway_marker_damage(
    detection: HermesDetection,
    evidence: RecoveryEvidence,
    *,
    manifest_checks: _ManifestChecks,
    backup_checks: _BackupChecks,
) -> bool:
    manifest = evidence.manifest
    backup_text = evidence.backup_text
    if (
        manifest is None
        or manifest.get(_MANIFEST_ERROR)
        or backup_text is None
        or not manifest_checks.valid
        or not manifest_checks.backup_matches
        or not backup_checks.valid
        or not detection.hook_strategy
    ):
        return False
    try:
        expected_patched = apply_patch(
            backup_text,
            strategy=detection.hook_strategy,
        )
    except (SyntaxError, ValueError):
        return False
    if _manifest_hash(manifest, "patched_sha256") != _text_sha256(
        expected_patched
    ):
        return False
    return _normalize_owned_gateway_markers(
        evidence.current_text
    ) == _normalize_owned_gateway_markers(expected_patched)


def _normalize_owned_gateway_markers(text: str) -> str:
    return _OWNED_GATEWAY_MARKER_LINE_RE.sub("", text)


def _check_cron_manifest(
    detection: HermesDetection,
    evidence: RecoveryEvidence,
    require_current_hash_match: bool,
) -> _ManifestChecks:
    manifest = evidence.manifest
    if manifest is None or not _manifest_has_cron_evidence(manifest):
        return _ManifestChecks(False, False, False, False, "", ())
    if manifest.get(_MANIFEST_ERROR):
        return _ManifestChecks(False, False, False, False, "", ())

    findings = []
    cron_py = detection.cron_py
    if cron_py is None:
        paths_valid = False
    else:
        backup_path = cron_py.with_name(f"{cron_py.name}{BACKUP_SUFFIX}")
        paths_valid = bool(
            manifest.get("cron_py") == _relative_path(detection.root, cron_py)
            and manifest.get("cron_backup")
            == _relative_path(detection.root, backup_path)
        )
    if not paths_valid:
        findings.append(_finding("cron_manifest_path_mismatch", "error"))

    current_hash = _manifest_hash(manifest, "cron_patched_sha256")
    backup_hash = _manifest_hash(manifest, "cron_backup_sha256")
    if not current_hash:
        findings.append(_finding("cron_manifest_current_hash_invalid", "error"))
    if not backup_hash:
        findings.append(_finding("cron_manifest_backup_hash_invalid", "error"))

    current_matches = bool(
        current_hash and evidence.cron_current_sha256 == current_hash
    )
    backup_matches = bool(
        backup_hash
        and evidence.cron_backup_text is not None
        and evidence.cron_backup_sha256 == backup_hash
    )
    if require_current_hash_match and current_hash and not current_matches:
        findings.append(_finding("cron_current_hash_mismatch", "error"))
    if evidence.cron_backup_text is not None and backup_hash and not backup_matches:
        findings.append(_finding("cron_backup_hash_mismatch", "error"))

    valid = bool(paths_valid and current_hash and backup_hash)
    return _ManifestChecks(
        valid,
        paths_valid,
        current_matches,
        backup_matches,
        backup_hash,
        tuple(findings),
    )


def _base_manifest_fields(
    manifest: Optional[Dict[str, object]],
) -> set[str]:
    if manifest is None or manifest.get(_MANIFEST_ERROR):
        return set()
    keys = {
        "base_py",
        "base_patched_sha256",
        "base_backup",
        "base_backup_sha256",
    }
    return keys.intersection(manifest)


def _check_base_manifest(
    detection: HermesDetection,
    evidence: RecoveryEvidence,
) -> _ManifestChecks:
    manifest = evidence.manifest
    if manifest is None or len(_base_manifest_fields(manifest)) != 4:
        return _ManifestChecks(False, False, False, False, "", ())
    base_py = detection.base_py
    if base_py is None:
        return _ManifestChecks(False, False, False, False, "", ())
    backup_path = base_py.with_name(f"{base_py.name}{BACKUP_SUFFIX}")
    paths_valid = bool(
        manifest.get("base_py") == _relative_path(detection.root, base_py)
        and manifest.get("base_backup")
        == _relative_path(detection.root, backup_path)
    )
    findings = []
    if not paths_valid:
        findings.append(_finding("base_manifest_path_mismatch", "error"))
    current_hash = _manifest_hash(manifest, "base_patched_sha256")
    backup_hash = _manifest_hash(manifest, "base_backup_sha256")
    if not current_hash:
        findings.append(_finding("base_manifest_current_hash_invalid", "error"))
    if not backup_hash:
        findings.append(_finding("base_manifest_backup_hash_invalid", "error"))
    current_matches = bool(
        current_hash and evidence.base_current_sha256 == current_hash
    )
    backup_matches = bool(
        backup_hash
        and evidence.base_backup_text is not None
        and evidence.base_backup_sha256 == backup_hash
    )
    if evidence.base_backup_text is not None and backup_hash and not backup_matches:
        findings.append(_finding("base_backup_hash_mismatch", "error"))
    return _ManifestChecks(
        bool(paths_valid and current_hash and backup_hash),
        paths_valid,
        current_matches,
        backup_matches,
        backup_hash,
        tuple(findings),
    )


def _check_backup(evidence: RecoveryEvidence) -> _BackupChecks:
    if evidence.backup_sha256 == f"{_STATUS_PREFIX}symlink":
        return _BackupChecks(False, (_finding("symlink_refused", "error"),))
    if evidence.backup_sha256 == f"{_STATUS_PREFIX}read_error":
        return _BackupChecks(False, (_finding("backup_read_error", "error"),))
    if evidence.backup_text is None:
        return _BackupChecks(False, ())
    try:
        ast.parse(evidence.backup_text)
        if remove_patch(evidence.backup_text) != evidence.backup_text:
            raise ValueError("owned patch in backup")
        if remove_cron_patch(evidence.backup_text) != evidence.backup_text:
            raise ValueError("owned cron patch in backup")
    except (SyntaxError, ValueError):
        return _BackupChecks(False, (_finding("backup_invalid", "error"),))
    return _BackupChecks(True, ())


def _check_cron_backup(evidence: RecoveryEvidence) -> _BackupChecks:
    if evidence.cron_backup_sha256 == f"{_STATUS_PREFIX}symlink":
        return _BackupChecks(False, (_finding("cron_symlink_refused", "error"),))
    if evidence.cron_backup_sha256 == f"{_STATUS_PREFIX}read_error":
        return _BackupChecks(
            False, (_finding("cron_backup_read_error", "error"),)
        )
    if evidence.cron_backup_text is None:
        return _BackupChecks(False, ())
    try:
        ast.parse(evidence.cron_backup_text)
        if remove_cron_patch(evidence.cron_backup_text) != evidence.cron_backup_text:
            raise ValueError("owned cron patch in backup")
    except (SyntaxError, ValueError):
        return _BackupChecks(False, (_finding("cron_backup_invalid", "error"),))
    return _BackupChecks(True, ())


def _check_base_backup(evidence: RecoveryEvidence) -> _BackupChecks:
    if evidence.base_backup_sha256 == f"{_STATUS_PREFIX}symlink":
        return _BackupChecks(False, (_finding("base_backup_symlink_refused", "error"),))
    if evidence.base_backup_sha256 == f"{_STATUS_PREFIX}read_error":
        return _BackupChecks(False, (_finding("base_backup_read_error", "error"),))
    if evidence.base_backup_text is None:
        return _BackupChecks(False, ())
    try:
        ast.parse(evidence.base_backup_text)
        if remove_base_patch(evidence.base_backup_text) != evidence.base_backup_text:
            raise ValueError("owned exact Base patch in backup")
        apply_base_patch(evidence.base_backup_text)
    except (SyntaxError, ValueError):
        return _BackupChecks(False, (_finding("base_backup_invalid", "error"),))
    return _BackupChecks(True, ())


def _validate_reapplication(
    detection: HermesDetection, source_text: Optional[str]
) -> str:
    if source_text is None or not detection.hook_strategy:
        return "unsupported_anchors"
    try:
        ast.parse(source_text)
        candidate = apply_patch(source_text, strategy=detection.hook_strategy)
        ast.parse(candidate)
        if remove_patch(candidate) != source_text:
            return "marker_validation"
    except (SyntaxError, ValueError):
        return "unsupported_anchors"
    return ""


def _validate_cron_reapplication(source_text: Optional[str]) -> str:
    if source_text is None:
        return "unsupported_anchors"
    try:
        ast.parse(source_text)
        candidate = apply_cron_patch(source_text)
        if candidate == source_text:
            return "unsupported_anchors"
        ast.parse(candidate)
        if remove_cron_patch(candidate) != source_text:
            return "marker_validation"
    except (SyntaxError, ValueError):
        return "unsupported_anchors"
    return ""


def _validate_base_reapplication(source_text: Optional[str]) -> str:
    if source_text is None:
        return "unsupported_anchors"
    try:
        ast.parse(source_text)
        candidate = apply_base_patch(source_text)
        if candidate == source_text:
            return "unsupported_anchors"
        ast.parse(candidate)
        if remove_base_patch(candidate) != source_text:
            return "marker_validation"
    except (SyntaxError, ValueError):
        return "unsupported_anchors"
    return ""


def _incomplete_actions(
    *,
    manifest_present: bool,
    manifest_valid: bool,
    backup_present: bool,
    candidate_matches: bool,
) -> Tuple[str, ...]:
    actions = []
    if not backup_present:
        actions.append("rebuild_backup")
    if not manifest_present or not manifest_valid or not backup_present:
        actions.append("rebuild_manifest")
    if not candidate_matches:
        actions.append("reapply_current_hook")
    if not actions:
        actions.append("rebuild_manifest")
    return tuple(actions)


def _classification(
    state: str,
    executable: bool,
    actions: Tuple[str, ...],
    findings,
    parts: Dict[str, str],
) -> RecoveryClassification:
    if state not in KNOWN_STATES:
        raise ValueError("unknown recovery state")
    return RecoveryClassification(
        state=state,
        executable=executable,
        fingerprint_parts=parts,
        actions=actions,
        findings=_deduplicate_findings(findings),
    )


def _fingerprint_parts(
    detection: HermesDetection, evidence: RecoveryEvidence
) -> Dict[str, str]:
    manifest = evidence.manifest
    if manifest is None:
        manifest_state = "missing"
        manifest_current_hash = ""
        manifest_backup_hash = ""
        manifest_cron_current_hash = ""
        manifest_cron_backup_hash = ""
        manifest_base_current_hash = ""
        manifest_base_backup_hash = ""
        run_path_matches = "false"
        backup_path_matches = "false"
        cron_path_matches = "false"
        cron_backup_path_matches = "false"
        base_path_matches = "false"
        base_backup_path_matches = "false"
    elif manifest.get(_MANIFEST_ERROR):
        manifest_state = str(manifest[_MANIFEST_ERROR])
        manifest_current_hash = ""
        manifest_backup_hash = ""
        manifest_cron_current_hash = ""
        manifest_cron_backup_hash = ""
        manifest_base_current_hash = ""
        manifest_base_backup_hash = ""
        run_path_matches = "false"
        backup_path_matches = "false"
        cron_path_matches = "false"
        cron_backup_path_matches = "false"
        base_path_matches = "false"
        base_backup_path_matches = "false"
    else:
        manifest_state = "present"
        manifest_current_hash = _manifest_hash(manifest, "patched_sha256")
        manifest_backup_hash = _manifest_hash(manifest, "backup_sha256")
        manifest_cron_current_hash = _manifest_hash(
            manifest, "cron_patched_sha256"
        )
        manifest_cron_backup_hash = _manifest_hash(
            manifest, "cron_backup_sha256"
        )
        manifest_base_current_hash = _manifest_hash(
            manifest, "base_patched_sha256"
        )
        manifest_base_backup_hash = _manifest_hash(
            manifest, "base_backup_sha256"
        )
        expected_backup = detection.run_py.with_name(
            f"{detection.run_py.name}{BACKUP_SUFFIX}"
        )
        run_path_matches = str(
            manifest.get("run_py") == _relative_path(detection.root, detection.run_py)
        ).lower()
        backup_path_matches = str(
            manifest.get("backup") == _relative_path(detection.root, expected_backup)
        ).lower()
        cron_py = detection.cron_py
        if cron_py is None:
            cron_path_matches = "false"
            cron_backup_path_matches = "false"
        else:
            expected_cron_backup = cron_py.with_name(
                f"{cron_py.name}{BACKUP_SUFFIX}"
            )
            cron_path_matches = str(
                manifest.get("cron_py")
                == _relative_path(detection.root, cron_py)
            ).lower()
            cron_backup_path_matches = str(
                manifest.get("cron_backup")
                == _relative_path(detection.root, expected_cron_backup)
            ).lower()
        base_py = detection.base_py
        if base_py is None:
            base_path_matches = "false"
            base_backup_path_matches = "false"
        else:
            expected_base_backup = base_py.with_name(
                f"{base_py.name}{BACKUP_SUFFIX}"
            )
            base_path_matches = str(
                manifest.get("base_py")
                == _relative_path(detection.root, base_py)
            ).lower()
            base_backup_path_matches = str(
                manifest.get("base_backup")
                == _relative_path(detection.root, expected_base_backup)
            ).lower()

    return {
        "backup_path_matches": backup_path_matches,
        "backup_sha256": evidence.backup_sha256,
        "base_backup_path_matches": base_backup_path_matches,
        "base_backup_sha256": evidence.base_backup_sha256,
        "base_current_sha256": evidence.base_current_sha256,
        "base_marker_error": evidence.base_marker_error,
        "base_path_matches": base_path_matches,
        "cron_backup_path_matches": cron_backup_path_matches,
        "cron_backup_sha256": evidence.cron_backup_sha256,
        "cron_current_sha256": evidence.cron_current_sha256,
        "cron_marker_error": evidence.cron_marker_error,
        "cron_path_matches": cron_path_matches,
        "current_sha256": evidence.current_sha256,
        "hook_strategy": detection.hook_strategy,
        "manifest_backup_sha256": manifest_backup_hash,
        "manifest_cron_backup_sha256": manifest_cron_backup_hash,
        "manifest_cron_current_sha256": manifest_cron_current_hash,
        "manifest_base_backup_sha256": manifest_base_backup_hash,
        "manifest_base_current_sha256": manifest_base_current_hash,
        "manifest_current_sha256": manifest_current_hash,
        "manifest_state": manifest_state,
        "marker_error": evidence.marker_error,
        "run_path_matches": run_path_matches,
        "supported": str(detection.supported).lower(),
    }


def _read_cron_evidence(
    detection: HermesDetection,
) -> Tuple[Optional[str], str, Optional[str], str, str]:
    cron_py = detection.cron_py
    if cron_py is None:
        return None, "", None, "", ""

    backup_path = cron_py.with_name(f"{cron_py.name}{BACKUP_SUFFIX}")
    backup_text: Optional[str] = None
    backup_sha256 = ""
    if backup_path.is_symlink():
        backup_sha256 = f"{_STATUS_PREFIX}symlink"
    elif backup_path.exists():
        try:
            backup_text = _read_text(backup_path)
            backup_sha256 = _text_sha256(backup_text)
        except (OSError, UnicodeError):
            backup_sha256 = f"{_STATUS_PREFIX}read_error"

    if cron_py.is_symlink():
        return None, "", backup_text, backup_sha256, "symlink_refused"
    if not cron_py.exists():
        return None, "", backup_text, backup_sha256, ""
    try:
        current_text = _read_text(cron_py)
    except (OSError, UnicodeError):
        return None, "", backup_text, backup_sha256, "current_read_error"

    marker_error = ""
    try:
        remove_cron_patch(current_text)
    except ValueError:
        marker_error = "corrupt_patch_markers"
    return (
        current_text,
        _text_sha256(current_text),
        backup_text,
        backup_sha256,
        marker_error,
    )


def _read_base_evidence(
    detection: HermesDetection,
) -> Tuple[Optional[str], str, Optional[str], str, str]:
    base_py = detection.base_py
    if base_py is None:
        return None, "", None, "", ""
    backup_path = base_py.with_name(f"{base_py.name}{BACKUP_SUFFIX}")
    backup_text: Optional[str] = None
    backup_sha256 = ""
    if backup_path.is_symlink():
        backup_sha256 = f"{_STATUS_PREFIX}symlink"
    elif backup_path.exists():
        try:
            backup_text = _read_text(backup_path)
            backup_sha256 = _text_sha256(backup_text)
        except (OSError, UnicodeError):
            backup_sha256 = f"{_STATUS_PREFIX}read_error"

    if base_py.is_symlink():
        return None, "", backup_text, backup_sha256, "symlink_refused"
    if not base_py.exists():
        return None, "", backup_text, backup_sha256, ""
    try:
        current_text = _read_text(base_py)
    except (OSError, UnicodeError):
        return None, "", backup_text, backup_sha256, "current_read_error"
    marker_error = ""
    try:
        remove_base_patch(current_text)
    except ValueError:
        marker_error = "corrupt_patch_markers"
    return (
        current_text,
        _text_sha256(current_text),
        backup_text,
        backup_sha256,
        marker_error,
    )


def _manifest_has_cron_evidence(
    manifest: Optional[Dict[str, object]],
) -> bool:
    if manifest is None or manifest.get(_MANIFEST_ERROR):
        return False
    return any(
        key in manifest
        for key in (
            "cron_py",
            "cron_patched_sha256",
            "cron_backup",
            "cron_backup_sha256",
        )
    )


def _read_manifest_evidence(path: Path) -> Optional[Dict[str, object]]:
    if path.is_symlink():
        return {_MANIFEST_ERROR: "symlink"}
    if not path.exists():
        return None
    try:
        value = json.loads(_read_text(path))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {_MANIFEST_ERROR: "invalid"}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return {_MANIFEST_ERROR: "invalid"}
    try:
        validate_install_manifest(value)
    except ManifestVersionError:
        return {_MANIFEST_ERROR: "unsupported_version"}
    except ManifestStructureError:
        return {_MANIFEST_ERROR: "invalid"}
    return value


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _manifest_hash(manifest: Dict[str, object], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str):
        return ""
    normalized = value.lower()
    return normalized if _HASH_RE.fullmatch(normalized) else ""


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return ""


def _is_valid_python(value: str) -> bool:
    try:
        ast.parse(value)
    except SyntaxError:
        return False
    return True


def _is_anchor_refusal(reason: str) -> bool:
    lowered = reason.lower()
    return any(
        marker in lowered
        for marker in ("anchor", "parse", "handler", "unsupported")
    )


def _finding(code: str, severity: str) -> RecoveryFinding:
    return RecoveryFinding(code, severity, _safe_message(code))


def _safe_message(code: str) -> str:
    messages = {
        "backup_hash_mismatch": "Backup evidence does not match the install manifest.",
        "backup_invalid": "The backup is not valid unpatched source.",
        "backup_missing": "The owned hook backup is missing.",
        "backup_read_error": "The owned hook backup could not be read.",
        "backup_source_mismatch": "Backup source does not match the owned hook source.",
        "current_hash_mismatch": "Current hook evidence does not match the install manifest.",
        "current_patch_mismatch": "The current owned hook cannot be reproduced safely.",
        "current_read_error": "Current hook source could not be read.",
        "cron_backup_hash_mismatch": "Cron backup evidence does not match the install manifest.",
        "cron_backup_invalid": "The cron backup is not valid unpatched source.",
        "cron_backup_read_error": "The cron backup could not be read.",
        "cron_backup_source_mismatch": "Cron backup source does not match the owned hook source.",
        "cron_current_hash_mismatch": "Current cron evidence does not match the install manifest.",
        "cron_current_patch_mismatch": "The current cron hook cannot be reproduced safely.",
        "cron_current_read_error": "Current cron source could not be read.",
        "cron_manifest_backup_hash_invalid": "The manifest cron backup fingerprint is missing or invalid.",
        "cron_manifest_current_hash_invalid": "The manifest cron fingerprint is missing or invalid.",
        "cron_manifest_missing": "The owned cron manifest evidence is missing.",
        "cron_manifest_path_mismatch": "Cron manifest ownership paths do not match the detected install.",
        "cron_marker_error": "Owned cron hook markers are incomplete or invalid.",
        "cron_reapplication_invalid": "The current cron hook cannot be validated in memory.",
        "cron_source_missing": "The owned cron source is missing.",
        "cron_symlink_refused": "Recovery does not operate on cron symbolic links.",
        "cron_unsupported_anchors": "Verified cron source does not support the current hook strategy.",
        "manifest_backup_hash_invalid": "The manifest backup fingerprint is missing or invalid.",
        "manifest_current_hash_invalid": "The manifest current fingerprint is missing or invalid.",
        "manifest_invalid": "The install manifest is invalid.",
        "manifest_missing": "The owned hook manifest is missing.",
        "hermes_upgrade_cron_source_accepted": "The current unpatched cron source was explicitly accepted as an intentional Hermes upgrade.",
        "hermes_upgrade_source_accepted": "The current unpatched Gateway source was explicitly accepted as an intentional Hermes upgrade.",
        "owned_patch_upgrade": "A verified older owned hook can be upgraded safely.",
        "manifest_path_mismatch": "Manifest ownership paths do not match the detected install.",
        "marker_error": "Owned hook markers are incomplete or invalid.",
        "owned_marker_damage": "Only owned hook marker lines differ from the verified install.",
        "reapplication_invalid": "The current hook cannot be validated in memory.",
        "symlink_refused": "Recovery does not operate on symbolic links.",
        "unsupported_anchors": "Verified source does not support the current hook strategy.",
    }
    return messages.get(code, "Recovery evidence requires review.")


def _deduplicate_findings(findings) -> Tuple[RecoveryFinding, ...]:
    result = []
    seen = set()
    for finding in findings:
        key = (finding.code, finding.severity)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return tuple(result)
