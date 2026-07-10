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
import tempfile
import threading
import time
from typing import Dict, Iterator, List, Optional, Tuple
from uuid import uuid4

from .detect import HermesDetection
from .patcher import (
    apply_cron_patch,
    apply_patch,
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
_STATUS_PREFIX = "!recovery:"
_MANIFEST_ERROR = "_recovery_error"
_LOCK_NAME = ".hermes_feishu_card_recovery.lock"
_LOCK_TIMEOUT_SECONDS = 10.0
_PROCESS_LOCKS: Dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()

_ACTION_MESSAGES = {
    "restore_verified_backup": "run.py: restored verified backup",
    "reapply_current_hook": "run.py: reapplied current hook",
    "rebuild_backup": "backup: recreated",
    "rebuild_manifest": "manifest: rebuilt",
    "restore_verified_cron_backup": "cron scheduler: restored verified backup",
    "reapply_current_cron_hook": "cron scheduler: reapplied current hook",
    "rebuild_cron_backup": "cron backup: recreated",
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
) -> RecoveryResult:
    with _root_lock(detection.root):
        fresh = plan_recovery(detection)
        if expected_fingerprint and fresh.fingerprint != expected_fingerprint:
            raise RecoveryRefused("recovery evidence changed; rerun diagnosis")
        if not fresh.executable:
            raise RecoveryRefused(_first_refusal(fresh))
        return _execute_fresh_plan(detection, fresh)


def _execute_fresh_plan(
    detection: HermesDetection, plan: RecoveryPlan
) -> RecoveryResult:
    evidence = _read_evidence(detection)
    if _plan_from_evidence(detection, evidence).fingerprint != plan.fingerprint:
        raise RecoveryRefused("recovery evidence changed; rerun diagnosis")

    state = _RecoveryState(
        run_text=evidence.current_text,
        backup_text=evidence.backup_text,
        cron_text=evidence.cron_current_text,
        cron_backup_text=evidence.cron_backup_text,
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
    _commit_recovery_changes(detection, plan, changes)
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
    elif action == "rebuild_manifest":
        pass
    elif action == "clear_stale_install_state":
        state.backup_text = None
        state.cron_backup_text = None
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

    old_manifest = _read_text(manifest_path) if manifest_path.exists() else None
    new_manifest = None
    if not state.clear_install_state:
        new_manifest = _render_manifest(detection, state)
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


def _render_manifest(detection: HermesDetection, state: _RecoveryState) -> str:
    if state.backup_text is None:
        raise RecoveryRefused("Gateway backup is required for the install manifest.")
    backup_path = detection.run_py.with_name(
        f"{detection.run_py.name}{BACKUP_SUFFIX}"
    )
    manifest = {
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
    return json.dumps(manifest, sort_keys=True) + "\n"


def _commit_recovery_changes(
    detection: HermesDetection,
    plan: RecoveryPlan,
    changes: List[Tuple[Path, Optional[str]]],
) -> None:
    ordered_changes = [change for change in changes if change[1] is not None]
    ordered_changes.extend(change for change in changes if change[1] is None)
    staged: Dict[Path, Path] = {}
    rollback: Dict[Path, Path] = {}
    originals: Dict[Path, Optional[str]] = {}
    changed: List[Path] = []
    try:
        for target, contents in ordered_changes:
            original = _read_text(target) if target.exists() else None
            originals[target] = original
            if original is not None:
                rollback[target] = _stage_text(target, original)
            if contents is not None:
                staged[target] = _stage_text(target, contents)

        if plan_recovery(detection).fingerprint != plan.fingerprint:
            raise RecoveryRefused("recovery evidence changed; rerun diagnosis")

        for target, contents in ordered_changes:
            changed.append(target)
            if contents is None:
                target.unlink(missing_ok=True)
            else:
                _atomic_replace(staged[target], target)
                staged.pop(target, None)
    except Exception as exc:
        rollback_error = _rollback_recovery_changes(changed, originals, rollback)
        if rollback_error is not None:
            raise RecoveryRefused(
                "recovery rollback failed; install state requires manual review"
            ) from exc
        raise
    finally:
        for temp_path in list(staged.values()) + list(rollback.values()):
            temp_path.unlink(missing_ok=True)


def _rollback_recovery_changes(
    changed: List[Path],
    originals: Dict[Path, Optional[str]],
    rollback: Dict[Path, Path],
) -> Optional[Exception]:
    first_error = None
    for target in reversed(changed):
        try:
            original = originals[target]
            if original is None:
                target.unlink(missing_ok=True)
            else:
                os.replace(str(rollback[target]), str(target))
                rollback.pop(target, None)
        except OSError as exc:
            if first_error is None:
                first_error = exc
    return first_error


def _stage_text(target: Path, contents: str) -> Path:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            os.chmod(str(temp_path), target.stat().st_mode)
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _atomic_replace(staged: Path, target: Path) -> None:
    os.replace(str(staged), str(target))


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
    detection: HermesDetection, evidence: RecoveryEvidence
) -> RecoveryPlan:
    classification = _classify_evidence(detection, evidence)
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
    )


def _classify_evidence(
    detection: HermesDetection, evidence: RecoveryEvidence
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
    if read_findings:
        return _classification("refused", False, (), read_findings, parts)

    gateway = _classify_gateway_evidence(detection, evidence)
    cron = _classify_cron_evidence(detection, evidence, gateway.state)
    return _merge_classifications(gateway, cron, parts)


def _classify_gateway_evidence(
    detection: HermesDetection, evidence: RecoveryEvidence
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
            if backup_checks.valid and not source_matches:
                findings.append(_finding("backup_source_mismatch", "error"))
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

        executable = bool(current_valid and source_matches and manifest_safe)
        return _classification(state, executable, actions, findings, parts)

    if marker_corrupt:
        findings.insert(0, _finding("marker_error", "error"))
        actions = ("restore_verified_backup", "reapply_current_hook")
        reapply = _validate_reapplication(detection, evidence.backup_text)
        if reapply == "unsupported_anchors":
            findings.append(_finding("unsupported_anchors", "error"))
        elif reapply:
            findings.append(_finding("reapplication_invalid", "error"))
        executable = bool(
            manifest_checks.valid
            and manifest_checks.current_matches
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
    if reapply_error == "unsupported_anchors":
        findings.append(_finding("unsupported_anchors", "error"))
    elif reapply_error:
        findings.append(_finding("reapplication_invalid", "error"))
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
        and candidate_matches
    )
    return _classification(state, executable, actions, findings, parts)


def plan_recovery(detection: HermesDetection) -> RecoveryPlan:
    return _plan_from_evidence(detection, _read_evidence(detection))


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
        if backup_present and backup_checks.valid and not source_matches:
            findings.append(_finding("cron_backup_source_mismatch", "error"))
        if not source_valid:
            findings.append(_finding("cron_unsupported_anchors", "error"))
        reapply_error = _validate_cron_reapplication(current)
        if reapply_error == "unsupported_anchors":
            findings.append(_finding("cron_unsupported_anchors", "error"))
        elif reapply_error:
            findings.append(_finding("cron_reapplication_invalid", "error"))
        executable = bool(
            source_valid
            and source_matches
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
    parts: Dict[str, str],
) -> RecoveryClassification:
    states = {gateway.state, cron.state}
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

    actions, actions_safe = _merge_actions(gateway, cron)
    findings = gateway.findings + cron.findings
    healthy = {"clean", "installed"}
    executable = bool(
        state not in healthy
        and actions_safe
        and gateway.state != "refused"
        and cron.state != "refused"
        and (gateway.state in healthy or gateway.executable)
        and (cron.state in healthy or cron.executable)
    )
    return _classification(state, executable, actions, findings, parts)


def _merge_actions(
    gateway: RecoveryClassification,
    cron: RecoveryClassification,
) -> Tuple[Tuple[str, ...], bool]:
    clear_action = "clear_stale_install_state"
    if clear_action not in gateway.actions:
        return tuple(dict.fromkeys(gateway.actions + cron.actions)), True

    if cron.state in {"clean", "stale_unpatched"}:
        return (clear_action,), True
    if cron.state == "installed" or (
        cron.state == "corrupt_owned" and cron.executable
    ):
        return ("restore_verified_cron_backup", clear_action), True
    return (), False


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
        run_path_matches = "false"
        backup_path_matches = "false"
        cron_path_matches = "false"
        cron_backup_path_matches = "false"
    elif manifest.get(_MANIFEST_ERROR):
        manifest_state = str(manifest[_MANIFEST_ERROR])
        manifest_current_hash = ""
        manifest_backup_hash = ""
        manifest_cron_current_hash = ""
        manifest_cron_backup_hash = ""
        run_path_matches = "false"
        backup_path_matches = "false"
        cron_path_matches = "false"
        cron_backup_path_matches = "false"
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

    return {
        "backup_path_matches": backup_path_matches,
        "backup_sha256": evidence.backup_sha256,
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
        return str(path.relative_to(root))
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
        "manifest_path_mismatch": "Manifest ownership paths do not match the detected install.",
        "marker_error": "Owned hook markers are incomplete or invalid.",
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
