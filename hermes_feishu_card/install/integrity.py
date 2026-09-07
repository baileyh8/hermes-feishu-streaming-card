from __future__ import annotations

import ast
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Callable
from uuid import uuid4

from .detect import HermesDetection
from .manifest import validate_install_manifest
from .patcher import (
    apply_base_patch,
    apply_cron_patch,
    apply_patch,
    remove_base_patch,
    remove_cron_patch,
    remove_patch,
)
from .recovery import (
    BACKUP_SUFFIX,
    MANIFEST_NAME,
    RecoveryPlan,
    _root_lock,
    plan_recovery,
)


INTEGRITY_MANIFEST_VERSION = 2
_GIT_HASH_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OwnedWriteSnapshot = tuple[int, int, str]
_DirectoryIdentity = tuple[int, int]
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
        raise IntegrityRepairRefused(
            "secure integrity repair requires directory-relative filesystem "
            "operations on this platform"
        )


@dataclass
class _StagedText:
    path: Path
    parent_fd: int
    basename: str
    identity: tuple[int, int]

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
            raise IntegrityRepairRefused(
                "integrity repair staged file changed before mutation"
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
            raise IntegrityRepairRefused(
                "integrity repair target parent changed before mutation"
            )

    def release(self) -> None:
        if self.parent_fd < 0:
            return
        os.close(self.parent_fd)
        self.parent_fd = -1


class IntegrityRepairRefused(ValueError):
    pass


@dataclass(frozen=True)
class IntegrityRepairPlan:
    state: str
    executable: bool
    fingerprint: str
    reason: str
    recovery_plan: RecoveryPlan


@dataclass(frozen=True)
class IntegrityRepairResult:
    status: str
    restart_required: bool
    plan: IntegrityRepairPlan


def integrity_acknowledgement_eligible(
    detection: HermesDetection,
    recovery_plan: RecoveryPlan,
    integrity_plan: IntegrityRepairPlan,
) -> bool:
    return bool(
        detection.supported
        and recovery_plan.state == "installed"
        and not recovery_plan.actions
        and integrity_plan.state == "installed"
        and integrity_plan.executable is False
        and integrity_plan.reason == "recovery_not_required"
        and integrity_plan.recovery_plan == recovery_plan
        and integrity_plan.recovery_plan.state == "installed"
        and not integrity_plan.recovery_plan.actions
    )


def build_integrity_provenance(
    root: str | Path,
    *,
    run_py: str | Path,
    run_source: str,
    cron_py: str | Path | None = None,
    cron_source: str | None = None,
    base_py: str | Path | None = None,
    base_source: str | None = None,
) -> dict[str, Any]:
    if not (Path(root) / ".git").exists():
        return _monolithic_snapshot_provenance(
            run_source=run_source, cron_source=cron_source, base_source=base_source,
        )
    root_path = _exact_git_root(Path(root))
    head = _git_head(root_path)
    run_relative = _relative_regular_path(root_path, Path(run_py))
    if _git_blob(root_path, head, run_relative) != run_source:
        raise IntegrityRepairRefused("gateway source does not match Git HEAD")
    provenance: dict[str, Any] = {
        "version": INTEGRITY_MANIFEST_VERSION,
        "git_head": head,
        "run_blob_sha256": _text_sha256(run_source),
    }
    if cron_py is not None:
        if cron_source is None:
            raise IntegrityRepairRefused("cron provenance is incomplete")
        cron_relative = _relative_regular_path(root_path, Path(cron_py))
        if _git_blob(root_path, head, cron_relative) != cron_source:
            raise IntegrityRepairRefused("cron source does not match Git HEAD")
        provenance["cron_blob_sha256"] = _text_sha256(cron_source)
    if base_py is not None:
        if base_source is None:
            raise IntegrityRepairRefused("exact Base provenance is incomplete")
        base_relative = _relative_regular_path(root_path, Path(base_py))
        if _git_blob(root_path, head, base_relative) != base_source:
            raise IntegrityRepairRefused("exact Base source does not match Git HEAD")
        provenance["base_blob_sha256"] = _text_sha256(base_source)
    return provenance


def plan_integrity_repair(detection: HermesDetection) -> IntegrityRepairPlan:
    base_plan = plan_recovery(detection, accept_hermes_upgrade=True)
    cron_py = _active_cron_py(detection)
    base_py = _active_base_py(detection)
    evidence: dict[str, str] = {
        "base_fingerprint": base_plan.fingerprint,
        "state": base_plan.state,
    }
    reason = "recovery_not_required"
    executable = False

    manifest = _read_manifest(detection.root / MANIFEST_NAME)
    integrity = manifest.get("integrity") if manifest is not None else None
    if manifest is not None and manifest.get("manifest_version") == 4:
        from .decomposed import verified_snapshot_provenance
        # A local snapshot proves only this installation. It does not grant
        # permission to mutate an upstream upgrade, with or without Git.
        if base_plan.state != "installed":
            return _plan(base_plan, False, "decomposed_upgrade_requires_explicit_install", evidence)
        try:
            expected = verified_snapshot_provenance(detection.root)
        except (OSError, ValueError, UnicodeError):
            return _plan(base_plan, False, "integrity_evidence_invalid", evidence)
        evidence["integrity"] = _canonical_hash(integrity)
        if integrity != expected:
            return _plan(base_plan, False, "integrity_migration_required", evidence)
        return _plan(base_plan, False, "recovery_not_required", evidence)

    if isinstance(integrity, dict) and integrity.get("kind") == "verified_owned_snapshot":
        evidence["integrity"] = _canonical_hash(integrity)
        if base_plan.state != "installed":
            return _plan(base_plan, False, "git_history_unavailable", evidence)
        try:
            sources = _verified_monolithic_sources(detection)
            expected = _monolithic_snapshot_provenance(
                run_source=sources["run_source"], cron_source=sources["cron_source"],
                base_source=sources["base_source"],
            )
        except (OSError, ValueError, UnicodeError):
            return _plan(base_plan, False, "integrity_evidence_invalid", evidence)
        reason = "recovery_not_required" if integrity == expected else "integrity_migration_required"
        return _plan(base_plan, False, reason, evidence)

    if not _valid_integrity_manifest(
        integrity, cron_py is not None, base_py is not None
    ):
        reason = "integrity_migration_required"
        evidence["integrity"] = "missing_or_invalid"
        return _plan(base_plan, executable, reason, evidence)
    evidence["integrity"] = _canonical_hash(integrity)

    if base_plan.state != "stale_unpatched" or not base_plan.executable:
        reason = (
            "recovery_not_required"
            if base_plan.state in {"clean", "installed"}
            else "recovery_evidence_not_executable"
        )
        return _plan(base_plan, executable, reason, evidence)

    try:
        root = _exact_git_root(detection.root)
        old_head = str(integrity["git_head"])
        current_head = _git_head(root)
        evidence["current_head"] = current_head
        if not _is_ancestor(root, old_head, current_head):
            reason = "git_history_not_descendant"
            return _plan(base_plan, executable, reason, evidence)

        targets = [
            (
                detection.run_py,
                detection.run_py.with_name(
                    f"{detection.run_py.name}{BACKUP_SUFFIX}"
                ),
                str(integrity["run_blob_sha256"]),
            )
        ]
        if base_py is not None:
            targets.insert(
                0,
                (
                    base_py,
                    base_py.with_name(f"{base_py.name}{BACKUP_SUFFIX}"),
                    str(integrity["base_blob_sha256"]),
                ),
            )
        if cron_py is not None:
            targets.append(
                (
                    cron_py,
                    cron_py.with_name(
                        f"{cron_py.name}{BACKUP_SUFFIX}"
                    ),
                    str(integrity["cron_blob_sha256"]),
                )
            )

        current_sources: dict[Path, str] = {}
        for target, old_backup, old_blob_hash in targets:
            relative = _relative_regular_path(root, target)
            if old_backup.is_symlink() or not old_backup.is_file():
                reason = "owned_backup_invalid"
                return _plan(base_plan, executable, reason, evidence)
            old_blob = _git_blob(root, old_head, relative)
            if (
                _text_sha256(old_blob) != old_blob_hash
                or _text_sha256(_read_text(old_backup)) != old_blob_hash
            ):
                reason = "owned_backup_mismatch"
                return _plan(base_plan, executable, reason, evidence)
            if _git_target_status(root, relative):
                reason = "git_target_modified"
                return _plan(base_plan, executable, reason, evidence)
            current_source = _read_text(target)
            if current_source != _git_blob(root, current_head, relative):
                reason = "git_target_modified"
                return _plan(base_plan, executable, reason, evidence)
            current_sources[target] = current_source
            evidence[f"target_{len(current_sources)}"] = _text_sha256(current_source)

        _validate_reinstall_candidates(
            detection,
            run_source=current_sources[detection.run_py],
            cron_source=current_sources.get(cron_py) if cron_py is not None else None,
            base_source=current_sources.get(base_py) if base_py is not None else None,
        )
    except IntegrityRepairRefused as exc:
        reason = _safe_reason(exc)
        return _plan(base_plan, executable, reason, evidence)

    executable = True
    reason = "verified_git_upgrade"
    return _plan(base_plan, executable, reason, evidence)


def execute_integrity_repair(
    detection: HermesDetection,
    *,
    expected_fingerprint: str,
) -> IntegrityRepairResult:
    _require_secure_dirfd_transactions()
    with _root_lock(detection.root):
        fresh = plan_integrity_repair(detection)
        if fresh.fingerprint != expected_fingerprint:
            raise IntegrityRepairRefused("integrity evidence changed; rerun diagnosis")
        if not fresh.executable:
            raise IntegrityRepairRefused(
                f"integrity repair refused: {fresh.reason}"
            )

        run_source = _read_text(detection.run_py)
        cron_py = _active_cron_py(detection)
        cron_source = (
            _read_text(cron_py)
            if cron_py is not None
            else None
        )
        base_py = _active_base_py(detection)
        base_source = _read_text(base_py) if base_py is not None else None
        run_patched = apply_patch(
            run_source,
            strategy=detection.hook_strategy or "legacy_gateway_run",
        )
        cron_patched = (
            apply_cron_patch(cron_source)
            if cron_source is not None
            else None
        )
        base_patched = (
            apply_base_patch(base_source) if base_source is not None else None
        )
        _validate_reinstall_candidates(
            detection,
            run_source=run_source,
            cron_source=cron_source,
            base_source=base_source,
        )

        run_backup = detection.run_py.with_name(
            f"{detection.run_py.name}{BACKUP_SUFFIX}"
        )
        changes: list[tuple[Path, str]] = []
        base_backup: Path | None = None
        if base_py is not None and base_source is not None and base_patched is not None:
            base_backup = base_py.with_name(f"{base_py.name}{BACKUP_SUFFIX}")
            changes.extend(((base_py, base_patched), (base_backup, base_source)))
        changes.extend(((detection.run_py, run_patched), (run_backup, run_source)))
        cron_backup: Path | None = None
        if cron_py is not None and cron_source is not None and cron_patched is not None:
            cron_backup = cron_py.with_name(
                f"{cron_py.name}{BACKUP_SUFFIX}"
            )
            changes.extend(
                ((cron_py, cron_patched), (cron_backup, cron_source))
            )

        manifest = _install_manifest(
            detection,
            run_source=run_source,
            run_patched=run_patched,
            run_backup=run_backup,
            cron_source=cron_source,
            cron_patched=cron_patched,
            cron_backup=cron_backup,
            base_source=base_source,
            base_patched=base_patched,
            base_backup=base_backup,
        )
        changes.append(
            (
                detection.root / MANIFEST_NAME,
                json.dumps(manifest, sort_keys=True) + "\n",
            )
        )
        def validate_repair_snapshot() -> None:
            latest = plan_integrity_repair(detection)
            if latest.fingerprint != fresh.fingerprint or not latest.executable:
                raise IntegrityRepairRefused(
                    "integrity evidence changed; rerun diagnosis"
                )
            if _read_text(detection.run_py) != run_source:
                raise IntegrityRepairRefused(
                    "integrity evidence changed; rerun diagnosis"
                )
            if cron_py is not None and _read_text(cron_py) != cron_source:
                raise IntegrityRepairRefused(
                    "integrity evidence changed; rerun diagnosis"
                )
            if base_py is not None and _read_text(base_py) != base_source:
                raise IntegrityRepairRefused(
                    "integrity evidence changed; rerun diagnosis"
                )

        def validate_committed_state() -> None:
            installed = plan_recovery(detection)
            if installed.state != "installed" or any(
                finding.severity == "error" for finding in installed.findings
            ):
                raise IntegrityRepairRefused(
                    "integrity repair validation failed after commit"
                )

        _atomic_replace_many(
            changes,
            controlled_root=detection.root,
            pre_commit_validate=validate_repair_snapshot,
            validate=validate_committed_state,
        )
        return IntegrityRepairResult(
            status="repaired",
            restart_required=True,
            plan=fresh,
        )


def render_integrity_manifest_migration(
    detection: HermesDetection,
    manifest_text: str,
) -> tuple[dict[str, Any], str]:
    """Validate and render a migration from an explicit manifest snapshot."""
    _require_secure_dirfd_transactions()
    with _root_lock(detection.root):
        return _render_integrity_manifest_migration(detection, manifest_text)


def migrate_integrity_manifest(detection: HermesDetection) -> dict[str, Any]:
    _require_secure_dirfd_transactions()
    with _root_lock(detection.root):
        manifest_path = detection.root / MANIFEST_NAME
        provenance, contents = _render_integrity_manifest_migration(
            detection,
            _read_text(manifest_path),
        )
        before = _read_text(manifest_path)

        def validate_migration_snapshot():
            if _render_integrity_manifest_migration(detection, before) != (provenance, contents):
                raise IntegrityRepairRefused("integrity evidence changed; rerun diagnosis")

        def validate_migration_commit():
            if _render_integrity_manifest_migration(detection, contents) != (provenance, contents):
                raise IntegrityRepairRefused("integrity evidence changed; rerun diagnosis")

        _atomic_replace_many(
            [(manifest_path, contents)],
            controlled_root=detection.root,
            pre_commit_validate=validate_migration_snapshot,
            validate=validate_migration_commit,
        )
        return provenance


def _render_integrity_manifest_migration(
    detection: HermesDetection,
    manifest_text: str,
) -> tuple[dict[str, Any], str]:
    installed = plan_recovery(detection)
    if installed.state != "installed" or any(
        finding.severity == "error" for finding in installed.findings
    ):
        raise IntegrityRepairRefused(
            "integrity migration requires a healthy installed hook"
        )
    try:
        manifest = json.loads(manifest_text)
        if not isinstance(manifest, dict):
            raise ValueError("manifest is not an object")
        validate_install_manifest(manifest)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IntegrityRepairRefused(
            "integrity migration requires a manifest"
        ) from exc
    if _read_text(detection.root / MANIFEST_NAME) != manifest_text:
        raise IntegrityRepairRefused("integrity evidence changed; rerun diagnosis")
    manifest = dict(manifest)
    if manifest.get("manifest_version") == 4:
        from .decomposed import verified_snapshot_provenance
        try:
            provenance = verified_snapshot_provenance(detection.root)
        except (OSError, ValueError, UnicodeError) as exc:
            raise IntegrityRepairRefused("decomposed ownership is not reversible") from exc
        # Bind the caller's manifest snapshot as well as every sibling target.
        if _read_text(detection.root / MANIFEST_NAME) != manifest_text:
            raise IntegrityRepairRefused("integrity evidence changed; rerun diagnosis")
        manifest["integrity"] = provenance
        return provenance, json.dumps(manifest, sort_keys=True) + "\n"
    provenance = build_integrity_provenance(
        detection.root, **_verified_monolithic_sources(detection),
    )
    manifest["integrity"] = provenance
    return provenance, json.dumps(manifest, sort_keys=True) + "\n"


def _monolithic_snapshot_provenance(*, run_source, cron_source=None, base_source=None):
    result = {"version": 3, "kind": "verified_owned_snapshot",
              "layout": "gateway-monolithic", "run_blob_sha256": _text_sha256(run_source)}
    if cron_source is not None:
        result["cron_blob_sha256"] = _text_sha256(cron_source)
    if base_source is not None:
        result["base_blob_sha256"] = _text_sha256(base_source)
    return result


def _verified_monolithic_sources(detection):
    if detection.run_py.is_symlink():
        raise IntegrityRepairRefused("gateway source must be a regular file")
    run_current = _read_text(detection.run_py)
    run_source = remove_patch(run_current)
    if run_source == run_current or apply_patch(
        run_source,
        strategy=detection.hook_strategy or "legacy_gateway_run",
    ) != run_current:
        raise IntegrityRepairRefused("gateway hook is not reversible")
    run_backup = detection.run_py.with_name(
        f"{detection.run_py.name}{BACKUP_SUFFIX}"
    )
    if run_backup.is_symlink() or _read_text(run_backup) != run_source:
        raise IntegrityRepairRefused("gateway backup is not verified")

    cron_source = None
    cron_py = _active_cron_py(detection)
    if cron_py is not None:
        if cron_py.is_symlink():
            raise IntegrityRepairRefused("cron source must be a regular file")
        cron_current = _read_text(cron_py)
        cron_source = remove_cron_patch(cron_current)
        if cron_source == cron_current or apply_cron_patch(cron_source) != cron_current:
            raise IntegrityRepairRefused("cron hook is not reversible")
        cron_backup = cron_py.with_name(f"{cron_py.name}{BACKUP_SUFFIX}")
        if cron_backup.is_symlink() or _read_text(cron_backup) != cron_source:
            raise IntegrityRepairRefused("cron backup is not verified")

    base_source = None
    base_py = _active_base_py(detection)
    if base_py is not None:
        if base_py.is_symlink():
            raise IntegrityRepairRefused("exact Base source must be a regular file")
        base_current = _read_text(base_py)
        base_source = remove_base_patch(base_current)
        if (
            base_source == base_current
            or apply_base_patch(base_source) != base_current
        ):
            raise IntegrityRepairRefused("exact Base hook is not reversible")
        base_backup = base_py.with_name(f"{base_py.name}{BACKUP_SUFFIX}")
        if base_backup.is_symlink() or _read_text(base_backup) != base_source:
            raise IntegrityRepairRefused("exact Base backup is not verified")

    return dict(run_py=detection.run_py, run_source=run_source,
                cron_py=cron_py, cron_source=cron_source,
                base_py=base_py, base_source=base_source)


def _install_manifest(
    detection: HermesDetection,
    *,
    run_source: str,
    run_patched: str,
    run_backup: Path,
    cron_source: str | None,
    cron_patched: str | None,
    cron_backup: Path | None,
    base_source: str | None,
    base_patched: str | None,
    base_backup: Path | None,
) -> dict[str, Any]:
    cron_py = _active_cron_py(detection)
    base_py = _active_base_py(detection)
    manifest: dict[str, Any] = {
        "manifest_version": INTEGRITY_MANIFEST_VERSION,
        "run_py": detection.run_py.relative_to(detection.root).as_posix(),
        "patched_sha256": _text_sha256(run_patched),
        "backup": run_backup.relative_to(detection.root).as_posix(),
        "backup_sha256": _text_sha256(run_source),
    }
    if (
        cron_py is not None
        and cron_source is not None
        and cron_patched is not None
        and cron_backup is not None
    ):
        manifest.update(
            {
                "cron_py": cron_py.relative_to(detection.root).as_posix(),
                "cron_patched_sha256": _text_sha256(cron_patched),
                "cron_backup": cron_backup.relative_to(detection.root).as_posix(),
                "cron_backup_sha256": _text_sha256(cron_source),
            }
        )
    if (
        base_py is not None
        and base_source is not None
        and base_patched is not None
        and base_backup is not None
    ):
        manifest.update(
            {
                "base_py": base_py.relative_to(detection.root).as_posix(),
                "base_patched_sha256": _text_sha256(base_patched),
                "base_backup": base_backup.relative_to(detection.root).as_posix(),
                "base_backup_sha256": _text_sha256(base_source),
            }
        )
    manifest["integrity"] = build_integrity_provenance(
        detection.root,
        run_py=detection.run_py,
        run_source=run_source,
        cron_py=cron_py,
        cron_source=cron_source,
        base_py=base_py,
        base_source=base_source,
    )
    return manifest


def _validate_reinstall_candidates(
    detection: HermesDetection,
    *,
    run_source: str,
    cron_source: str | None = None,
    base_source: str | None = None,
) -> None:
    if not detection.supported or not run_source:
        raise IntegrityRepairRefused("unsupported_anchors")
    try:
        ast.parse(run_source)
        run_patched = apply_patch(
            run_source,
            strategy=detection.hook_strategy or "legacy_gateway_run",
        )
        ast.parse(run_patched)
        if remove_patch(run_patched) != run_source:
            raise ValueError("gateway roundtrip failed")
        if _active_cron_py(detection) is not None:
            if cron_source is None:
                raise ValueError("cron source missing")
            ast.parse(cron_source)
            cron_patched = apply_cron_patch(cron_source)
            ast.parse(cron_patched)
            if remove_cron_patch(cron_patched) != cron_source:
                raise ValueError("cron roundtrip failed")
        if _active_base_py(detection) is not None:
            if base_source is None:
                raise ValueError("exact Base source missing")
            ast.parse(base_source)
            base_patched = apply_base_patch(base_source)
            ast.parse(base_patched)
            if remove_base_patch(base_patched) != base_source:
                raise ValueError("exact Base roundtrip failed")
    except (SyntaxError, ValueError) as exc:
        raise IntegrityRepairRefused("unsupported_anchors") from exc


def _valid_integrity_manifest(value: Any, has_cron: bool, has_base: bool) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"version", "git_head", "run_blob_sha256"}
    if has_cron:
        required.add("cron_blob_sha256")
    if has_base:
        required.add("base_blob_sha256")
    if not required.issubset(value):
        return False
    return bool(
        value.get("version") == INTEGRITY_MANIFEST_VERSION
        and isinstance(value.get("git_head"), str)
        and _GIT_HASH_RE.fullmatch(str(value["git_head"]))
        and _SHA256_RE.fullmatch(str(value["run_blob_sha256"]))
        and (
            not has_cron
            or _SHA256_RE.fullmatch(str(value["cron_blob_sha256"]))
        )
        and (
            not has_base
            or _SHA256_RE.fullmatch(str(value["base_blob_sha256"]))
        )
    )


def _plan(
    base_plan: RecoveryPlan,
    executable: bool,
    reason: str,
    evidence: dict[str, str],
) -> IntegrityRepairPlan:
    fingerprint = sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return IntegrityRepairPlan(
        state=base_plan.state,
        executable=executable,
        fingerprint=fingerprint,
        reason=reason,
        recovery_plan=base_plan,
    )


def _exact_git_root(root: Path) -> Path:
    resolved = root.resolve()
    result = _run_git(resolved, "rev-parse", "--show-toplevel")
    try:
        toplevel = Path(result.stdout.decode("utf-8").strip()).resolve()
    except UnicodeError as exc:
        raise IntegrityRepairRefused("git_root_invalid") from exc
    if toplevel != resolved:
        raise IntegrityRepairRefused("git_root_invalid")
    return resolved


def _git_head(root: Path) -> str:
    result = _run_git(root, "rev-parse", "HEAD")
    head = result.stdout.decode("ascii", errors="ignore").strip().lower()
    if _GIT_HASH_RE.fullmatch(head) is None:
        raise IntegrityRepairRefused("git_head_invalid")
    return head


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    if result.returncode not in {0, 1}:
        raise IntegrityRepairRefused("git_history_unavailable")
    return result.returncode == 0


def _git_blob(root: Path, revision: str, relative: str) -> str:
    result = _run_git(root, "show", f"{revision}:{relative}")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeError as exc:
        raise IntegrityRepairRefused("git_blob_invalid") from exc


def _git_target_status(root: Path, relative: str) -> str:
    result = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
        "--",
        relative,
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IntegrityRepairRefused("git_evidence_unavailable") from exc
    if result.returncode != 0:
        raise IntegrityRepairRefused("git_evidence_unavailable")
    return result


def _relative_regular_path(root: Path, path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise IntegrityRepairRefused("source_not_regular")
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise IntegrityRepairRefused("source_outside_root") from exc


def _read_manifest(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    try:
        validate_install_manifest(value)
    except ValueError:
        return None
    return value


def _read_text(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise IntegrityRepairRefused("source_not_regular")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise IntegrityRepairRefused("source_read_failed") from exc


def _atomic_replace_many(
    changes: list[tuple[Path, str]],
    *,
    controlled_root: Path | None = None,
    pre_commit_validate: Callable[[], None] | None = None,
    validate: Callable[[], None] | None = None,
) -> None:
    if not changes:
        return
    _require_secure_dirfd_transactions()
    root = controlled_root or Path(
        os.path.commonpath(
            [str(Path(target).absolute().parent) for target, _contents in changes]
        )
    )
    target_ancestries, directory_snapshots = _snapshot_controlled_ancestries(
        root, [target for target, _contents in changes]
    )
    staged: dict[Path, _StagedText] = {}
    rollback: dict[Path, _StagedText | None] = {}
    target_bindings: dict[Path, _TargetBinding] = {}
    pre_write_snapshots: dict[Path, _OwnedWriteSnapshot | None] = {}
    post_write_snapshots: dict[Path, _OwnedWriteSnapshot] = {}
    changed: list[Path] = []
    try:
        for target, contents in changes:
            target_bindings[target] = _bind_target(target)
            binding = target_bindings[target]
            _assert_controlled_ancestry_unchanged(
                target, target_ancestries, directory_snapshots
            )
            pre_write_snapshots[target] = _snapshot_prewrite_target(target)
            bound_snapshot, original = _read_bound_target_text(
                binding,
                "integrity repair target changed during mutation",
            )
            if bound_snapshot != pre_write_snapshots[target]:
                raise IntegrityRepairRefused(
                    "integrity repair target changed during mutation"
                )
            _assert_controlled_ancestry_unchanged(
                target, target_ancestries, directory_snapshots
            )
            rollback[target] = (
                _stage_text(target, original, binding=binding)
                if original is not None
                else None
            )
            _assert_prewrite_target_unchanged(
                target, pre_write_snapshots[target]
            )
            staged[target] = _stage_text(target, contents, binding=binding)
        if pre_commit_validate is not None:
            pre_commit_validate()
        for target, contents in changes:
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
            staged_file = staged[target]
            staged_file.assert_owned()
            os.replace(
                staged_file.basename,
                binding.basename,
                src_dir_fd=staged_file.parent_fd,
                dst_dir_fd=binding.parent_fd,
            )
            staged_file.release()
            staged.pop(target, None)
            changed.append(target)
            post_write_snapshots[target] = _snapshot_bound_write(
                binding, contents
            )
            _snapshot_owned_write(target, contents)
            _assert_controlled_ancestry_unchanged(
                target, target_ancestries, directory_snapshots
            )
        if validate is not None:
            validate()
    except Exception as exc:
        rollback_error: Exception | None = None
        for target in reversed(changed):
            try:
                owned_snapshot = post_write_snapshots.get(target)
                if owned_snapshot is None:
                    raise IntegrityRepairRefused(
                        "integrity repair lost write ownership"
                    )
                binding = target_bindings[target]
                binding.assert_parent()
                _assert_bound_target_unchanged(binding, owned_snapshot)
                original = rollback.get(target)
                if original is None:
                    os.unlink(binding.basename, dir_fd=binding.parent_fd)
                else:
                    original.assert_owned()
                    os.replace(
                        original.basename,
                        binding.basename,
                        src_dir_fd=original.parent_fd,
                        dst_dir_fd=binding.parent_fd,
                    )
                    original.release()
                    rollback[target] = None
                _assert_bound_target_restored(
                    binding, pre_write_snapshots[target]
                )
            except (OSError, IntegrityRepairRefused) as rollback_exc:
                rollback_error = rollback_error or rollback_exc
        if rollback_error is not None:
            raise IntegrityRepairRefused(
                "integrity repair rollback failed; manual review required"
            ) from exc
        if isinstance(exc, IntegrityRepairRefused):
            raise
        raise IntegrityRepairRefused("integrity repair transaction failed") from exc
    finally:
        for path in list(staged.values()) + [
            item for item in rollback.values() if item is not None
        ]:
            path.cleanup()
        for binding in target_bindings.values():
            binding.release()


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
        binding, "integrity repair target changed during mutation"
    )
    if snapshot is None or snapshot[2] != _text_sha256(expected_contents):
        raise IntegrityRepairRefused(
            "integrity repair could not verify committed write"
        )
    return snapshot


def _assert_bound_target_unchanged(
    binding: _TargetBinding, expected: _OwnedWriteSnapshot | None
) -> None:
    binding.assert_parent()
    current = _read_bound_target_snapshot(
        binding, "integrity repair target changed during mutation"
    )
    if current != expected:
        raise IntegrityRepairRefused(
            "integrity repair target changed during mutation"
        )


def _assert_bound_target_restored(
    binding: _TargetBinding, expected: _OwnedWriteSnapshot | None
) -> None:
    binding.assert_parent()
    current = _read_bound_target_snapshot(
        binding, "integrity repair rollback target changed"
    )
    if expected is None:
        matches = current is None
    else:
        matches = current is not None and current[2] == expected[2]
    if not matches:
        raise IntegrityRepairRefused(
            "integrity repair rollback target changed"
        )


def _read_bound_target_snapshot(
    binding: _TargetBinding, refusal: str
) -> _OwnedWriteSnapshot | None:
    snapshot, _contents = _read_bound_target_text(binding, refusal)
    return snapshot


def _read_bound_target_text(
    binding: _TargetBinding, refusal: str
) -> tuple[_OwnedWriteSnapshot | None, str | None]:
    try:
        before = os.stat(
            binding.basename,
            dir_fd=binding.parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None, None
    if not stat.S_ISREG(before.st_mode):
        raise IntegrityRepairRefused(refusal)
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
            raise IntegrityRepairRefused(refusal)
        while chunk := os.read(descriptor, 64 * 1024):
            digest.update(chunk)
            payload.extend(chunk)
    except OSError as exc:
        raise IntegrityRepairRefused(refusal) from exc
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
        raise IntegrityRepairRefused(refusal)
    try:
        contents = payload.decode("utf-8")
    except UnicodeError as exc:
        raise IntegrityRepairRefused(refusal) from exc
    return (before.st_dev, before.st_ino, digest.hexdigest()), contents


def _snapshot_prewrite_target(
    target: Path,
) -> _OwnedWriteSnapshot | None:
    try:
        target.lstat()
    except FileNotFoundError:
        return None
    return _read_regular_file_snapshot(
        target, "integrity repair target is not safe before write"
    )


def _assert_prewrite_target_unchanged(
    target: Path, expected: _OwnedWriteSnapshot | None
) -> None:
    if expected is None:
        try:
            target.lstat()
        except FileNotFoundError:
            return
        raise IntegrityRepairRefused(
            "integrity repair target changed before write"
        )
    current = _read_regular_file_snapshot(
        target, "integrity repair target changed before write"
    )
    if current != expected:
        raise IntegrityRepairRefused(
            "integrity repair target changed before write"
        )


def _snapshot_controlled_ancestries(
    controlled_root: Path, targets: list[Path]
) -> tuple[
    dict[Path, tuple[Path, ...]],
    dict[Path, _DirectoryIdentity],
]:
    target_ancestries: dict[Path, tuple[Path, ...]] = {}
    directory_snapshots: dict[Path, _DirectoryIdentity] = {}
    for target in targets:
        ancestry = _controlled_ancestry_paths(controlled_root, target)
        target_ancestries[target] = ancestry
        for directory in ancestry:
            if directory not in directory_snapshots:
                directory_snapshots[directory] = _read_directory_identity(
                    directory,
                    "integrity repair controlled ancestry is unsafe",
                )
    return target_ancestries, directory_snapshots


def _controlled_ancestry_paths(
    controlled_root: Path, target: Path
) -> tuple[Path, ...]:
    root = Path(os.path.abspath(controlled_root))
    absolute_target = Path(os.path.abspath(target))
    try:
        relative = absolute_target.relative_to(root)
    except ValueError as exc:
        raise IntegrityRepairRefused(
            "integrity repair target is outside controlled root"
        ) from exc
    if not relative.parts:
        raise IntegrityRepairRefused(
            "integrity repair target is outside controlled root"
        )
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
        raise IntegrityRepairRefused(refusal) from exc
    if not stat.S_ISDIR(snapshot.st_mode):
        raise IntegrityRepairRefused(refusal)
    return snapshot.st_dev, snapshot.st_ino


def _assert_controlled_ancestry_unchanged(
    target: Path,
    target_ancestries: dict[Path, tuple[Path, ...]],
    directory_snapshots: dict[Path, _DirectoryIdentity],
) -> None:
    for directory in target_ancestries[target]:
        if _read_directory_identity(
            directory,
            "integrity repair target ancestry changed before write",
        ) != directory_snapshots[directory]:
            raise IntegrityRepairRefused(
                "integrity repair target ancestry changed before write"
            )


def _snapshot_owned_write(
    target: Path, expected_contents: str
) -> _OwnedWriteSnapshot:
    snapshot = _read_owned_write_snapshot(target)
    if snapshot[2] != _text_sha256(expected_contents):
        raise IntegrityRepairRefused(
            "integrity repair could not verify committed write"
        )
    return snapshot


def _assert_owned_write_unchanged(
    target: Path, expected: _OwnedWriteSnapshot
) -> None:
    if _read_owned_write_snapshot(target) != expected:
        raise IntegrityRepairRefused(
            "integrity repair target changed after write"
        )


def _read_owned_write_snapshot(target: Path) -> _OwnedWriteSnapshot:
    return _read_regular_file_snapshot(
        target, "integrity repair target changed after write"
    )


def _read_regular_file_snapshot(
    target: Path, refusal: str
) -> _OwnedWriteSnapshot:
    try:
        before = target.lstat()
    except FileNotFoundError as exc:
        raise IntegrityRepairRefused(refusal) from exc
    if not stat.S_ISREG(before.st_mode):
        raise IntegrityRepairRefused(refusal)
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
            raise IntegrityRepairRefused(refusal)
        while chunk := os.read(descriptor, 64 * 1024):
            digest.update(chunk)
    except OSError as exc:
        raise IntegrityRepairRefused(refusal) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = target.lstat()
    except FileNotFoundError as exc:
        raise IntegrityRepairRefused(refusal) from exc
    if (
        not stat.S_ISREG(after.st_mode)
        or (after.st_dev, after.st_ino) != identity
    ):
        raise IntegrityRepairRefused(refusal)
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
                raise IntegrityRepairRefused(
                    "integrity repair target is not safe before write"
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
        raise IntegrityRepairRefused(
            "integrity repair staging parent is unsafe"
        ) from exc
    try:
        opened = os.fstat(parent_fd)
        current = parent.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (opened.st_dev, opened.st_ino)
            != (current.st_dev, current.st_ino)
        ):
            raise IntegrityRepairRefused(
                "integrity repair staging parent is unsafe"
            )
        return parent_fd
    except Exception:
        os.close(parent_fd)
        raise


def _text_sha256(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _active_cron_py(detection: HermesDetection) -> Path | None:
    cron_py = detection.cron_py
    if cron_py is None or not getattr(detection, "cron_py_exists", cron_py.is_file()):
        return None
    return cron_py


def _active_base_py(detection: HermesDetection) -> Path | None:
    base_py = detection.base_py
    if (
        not detection.base_required
        or base_py is None
        or not getattr(detection, "base_py_exists", base_py.is_file())
    ):
        return None
    return base_py


def _canonical_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _safe_reason(exc: IntegrityRepairRefused) -> str:
    reason = str(exc)
    allowed = {
        "git_root_invalid",
        "git_head_invalid",
        "git_history_unavailable",
        "git_evidence_unavailable",
        "git_blob_invalid",
        "source_not_regular",
        "source_outside_root",
        "source_read_failed",
        "unsupported_anchors",
    }
    return reason if reason in allowed else "integrity_evidence_invalid"
