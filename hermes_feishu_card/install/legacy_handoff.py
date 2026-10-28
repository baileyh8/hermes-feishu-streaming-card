"""Narrow observer-binding handoff for a verified PM generation change."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

from . import legacy_observer, legacy_rebind, plugin, pm_dependency
from . import posix_transaction as io
from .transaction_lock import _root_lock


class HandoffRefused(ValueError):
    pass


def _attach_error_evidence(
    error: BaseException, name: str, value: Any
) -> None:
    try:
        object.__setattr__(error, name, value)
    except (AttributeError, TypeError):
        pass


@dataclass(frozen=True)
class Precondition:
    binding: plugin.HermesRuntimeBinding
    marker: Path
    marker_raw: bytes
    marker_snapshot: Any
    marker_parent_identity: tuple[int, int]
    config: Path
    config_snapshot: Any
    config_parent_identity: tuple[int, int]
    marker_mode: int
    marker_uid: int
    config_mode: int
    config_uid: int
    source_fingerprint: str
    member: Path
    member_identity: Any
    member_owner: dict[str, Any]


@dataclass
class HandoffRegistration:
    """Marker-only rollback in the existing install registration stack."""

    root: Path
    marker: Path
    marker_before: bytes
    marker_after: bytes
    before_snapshot: Any
    after_snapshot: Any
    config: Path
    config_snapshot: Any
    marker_before_mode: int
    marker_before_uid: int
    marker_parent_identity: tuple[int, int]
    config_parent_identity: tuple[int, int]
    config_mode: int
    config_uid: int
    rollback_snapshot: Any | None = None

    def finish(self) -> None:
        return None

    def rollback(self) -> None:
        with _root_lock(self.root):
            self._rollback_locked()

    def _rollback_locked(self) -> None:
        marker_binding = _bind_evidence_at(
            self.marker,
            self.marker_parent_identity,
            "legacy_handoff_marker_changed",
        )
        config_binding = None
        try:
            config_binding = _bind_evidence_at(
                self.config,
                self.config_parent_identity,
                "legacy_handoff_config_changed",
            )
            _assert_bound_evidence(
                marker_binding,
                snapshot=self.after_snapshot,
                mode=self.marker_before_mode,
                uid=self.marker_before_uid,
                payload=self.marker_after,
                refusal="legacy_handoff_marker_changed",
            )
            _assert_bound_evidence(
                config_binding,
                snapshot=self.config_snapshot,
                mode=self.config_mode,
                uid=self.config_uid,
                payload=None,
                refusal="legacy_handoff_config_changed",
            )
            restored = io._atomic_write_text(
                self.marker,
                self.marker_before.decode("utf-8"),
                mode=self.marker_before_mode,
                _binding=marker_binding,
                _expected_before=self.after_snapshot,
            )
            _assert_bound_evidence(
                marker_binding,
                snapshot=restored,
                mode=self.marker_before_mode,
                uid=self.marker_before_uid,
                payload=self.marker_before,
                refusal="legacy_handoff_rollback_failed",
            )
            self.rollback_snapshot = restored
        finally:
            for evidence in (config_binding, marker_binding):
                if evidence is not None:
                    try:
                        evidence.close()
                    except OSError:
                        pass


def _record(raw: bytes) -> dict[str, Any]:
    value = legacy_observer._decode(raw)
    if not isinstance(value, dict):
        raise HandoffRefused("legacy_handoff_record_invalid")
    return value


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def _bind_evidence(path: Path, refusal: str) -> io._CliTargetBinding:
    try:
        return io._bind_cli_target(path)
    except (OSError, ValueError) as exc:
        raise HandoffRefused(refusal) from exc


def _bind_evidence_at(
    path: Path, parent_identity: tuple[int, int], refusal: str
) -> io._CliTargetBinding:
    binding = _bind_evidence(path, refusal)
    if binding.parent_identity != parent_identity:
        try:
            binding.close()
        except OSError:
            pass
        raise HandoffRefused(refusal)
    return binding


def _assert_bound_evidence(
    binding: io._CliTargetBinding,
    *,
    snapshot: Any,
    mode: int,
    uid: int,
    payload: bytes | None,
    refusal: str,
) -> None:
    """Re-read one bound evidence file after slow runtime probing."""
    try:
        io.assert_bound_parent(binding, HandoffRefused, refusal)
        io.assert_path_parent(binding, HandoffRefused, refusal)
        current, current_payload, current_mode = io.read_file_state(
            binding.basename,
            parent_fd=binding.parent_fd,
            missing_allowed=False,
            refused=HandoffRefused,
            non_regular_refusal=refusal,
            changed_refusal=refusal,
            wrap_read_error=True,
        )
        metadata = os.stat(
            binding.basename,
            dir_fd=binding.parent_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise HandoffRefused(refusal) from exc
    if current is None:
        raise HandoffRefused(refusal)
    if (
        current != snapshot
        or current_mode != mode
        or metadata.st_mode & 0o7777 != mode
        or (metadata.st_dev, metadata.st_ino) != current[:2]
        or metadata.st_uid != uid
        or (payload is not None and current_payload != payload)
    ):
        raise HandoffRefused(refusal)


def prepare(
    detection: Any,
    *,
    hermes_home: Path | None,
    profile_id: str | None,
) -> Precondition | None:
    """Capture an already healthy observer before any PM mutation."""
    if not getattr(detection, "decomposed", False) or not legacy_observer.available(detection.root):
        return None
    marker = detection.root / legacy_observer.MARKER
    raw = legacy_observer._read(marker)
    if raw is None:
        return None
    binding = plugin.resolve_runtime_binding(
        checkout_root=detection.root,
        hermes_home=hermes_home,
        profile_id=profile_id,
    )
    record = _record(raw)
    if record.get("python_identity") != binding.python_identity:
        raise HandoffRefused("legacy_observer_runtime_binding_changed")
    evidence = legacy_observer.inspect(detection.root, home=binding.hermes_home)
    if (
        evidence is None
        or legacy_observer._config_state(evidence, detection.root) != "installed"
    ):
        raise HandoffRefused("legacy_handoff_restore_incomplete")
    member = binding.hermes_home / "plugins" / pm_dependency.NAME
    try:
        member.lstat()
    except FileNotFoundError:
        try:
            config = plugin._load_config_mapping(evidence[4])
        except ValueError as exc:
            raise HandoffRefused("legacy_handoff_config_invalid") from exc
        plugins = config.get("plugins")
        if plugins is not None and not isinstance(plugins, dict):
            raise HandoffRefused("legacy_handoff_config_invalid") from None
        enabled = plugins.get("enabled") if isinstance(plugins, dict) else None
        if enabled is not None and not isinstance(enabled, list):
            raise HandoffRefused("legacy_handoff_config_invalid") from None
        if isinstance(enabled, list) and pm_dependency.NAME in enabled:
            raise HandoffRefused("legacy_handoff_member_owner_missing") from None
        # A legacy observer can exist without a PM dependency member. Only
        # skip the handoff when official config does not select that member.
        return None
    except OSError as exc:
        raise HandoffRefused("legacy_handoff_member_uninspectable") from exc
    try:
        owner = pm_dependency._owned(member)
    except ValueError as exc:
        raise HandoffRefused("legacy_handoff_member_owner_invalid") from exc
    if owner is None:
        raise HandoffRefused("legacy_handoff_member_owner_missing")
    if not isinstance(owner, dict) or not owner:
        raise HandoffRefused("legacy_handoff_member_owner_missing")
    source = legacy_rebind._source_fingerprint(detection.root)
    legacy_rebind._verify_runtime(binding)
    marker_snapshot = io.read_path_snapshot(
        marker, HandoffRefused, "legacy_handoff_marker_changed"
    )
    marker_parent_identity = io.read_directory_identity(
        marker.parent, HandoffRefused, "legacy_handoff_marker_changed"
    )
    marker_metadata = marker.lstat()
    if marker.read_bytes() != raw:
        raise HandoffRefused("legacy_handoff_marker_changed")
    config_snapshot = io.read_path_snapshot(
        binding.config_path, HandoffRefused, "legacy_handoff_config_changed"
    )
    config_parent_identity = io.read_directory_identity(
        binding.config_path.parent,
        HandoffRefused,
        "legacy_handoff_config_changed",
    )
    config_metadata = binding.config_path.lstat()
    return Precondition(
        binding=binding,
        marker=marker,
        marker_raw=raw,
        marker_snapshot=marker_snapshot,
        marker_parent_identity=marker_parent_identity,
        config=binding.config_path,
        config_snapshot=config_snapshot,
        config_parent_identity=config_parent_identity,
        marker_mode=marker_metadata.st_mode & 0o7777,
        marker_uid=marker_metadata.st_uid,
        config_mode=config_metadata.st_mode & 0o7777,
        config_uid=config_metadata.st_uid,
        source_fingerprint=source,
        member=member,
        member_identity=io.read_directory_identity(
            member, HandoffRefused, "legacy_handoff_member_changed"
        ),
        member_owner=owner,
    )


def _validate_registration(
    registration: Any,
    *,
    binding: plugin.HermesRuntimeBinding,
    detection_root: Path,
    rollback_python: Path,
) -> None:
    if type(registration) is not pm_dependency.Registration:
        raise HandoffRefused("legacy_handoff_registration_required")
    if registration.adoption is not None:
        raise HandoffRefused("legacy_handoff_adoption_unsupported")
    registration._assert_current()
    if (
        not _same_path(registration.root, detection_root)
        or not _same_path(registration.home, binding.hermes_home)
        or not _same_path(registration.python, rollback_python)
        or registration.post_python is None
        or not _same_path(registration.post_python, binding.runtime_python)
        or not _same_path(
            registration.member,
            binding.hermes_home / "plugins" / pm_dependency.NAME,
        )
        or pm_dependency._owned(registration.member) != registration.owner
    ):
        raise HandoffRefused("legacy_handoff_registration_mismatch")


def _assert_precondition_current(
    precondition: Precondition,
    root: Path,
    *,
    marker_snapshot: Any | None = None,
    marker_raw: bytes | None = None,
    config_snapshot: Any | None = None,
) -> None:
    expected_marker = (
        precondition.marker_snapshot if marker_snapshot is None else marker_snapshot
    )
    expected_config = (
        precondition.config_snapshot if config_snapshot is None else config_snapshot
    )
    expected_marker_raw = precondition.marker_raw if marker_raw is None else marker_raw
    if (
        io.read_path_snapshot(
            precondition.marker, HandoffRefused, "legacy_handoff_marker_changed"
        )
        != expected_marker
        or precondition.marker.read_bytes() != expected_marker_raw
    ):
        raise HandoffRefused("legacy_handoff_marker_changed")
    if (
        io.read_path_snapshot(
            precondition.config, HandoffRefused, "legacy_handoff_config_changed"
        )
        != expected_config
    ):
        raise HandoffRefused("legacy_handoff_config_changed")
    marker_metadata = precondition.marker.lstat()
    config_metadata = precondition.config.lstat()
    if (
        marker_metadata.st_mode & 0o7777 != precondition.marker_mode
        or marker_metadata.st_uid != precondition.marker_uid
        or config_metadata.st_mode & 0o7777 != precondition.config_mode
        or config_metadata.st_uid != precondition.config_uid
    ):
        raise HandoffRefused("legacy_handoff_metadata_changed")
    if legacy_rebind._source_fingerprint(root) != precondition.source_fingerprint:
        raise HandoffRefused("legacy_handoff_source_changed")


def _config_snapshot_after_pm(
    precondition: Precondition, binding: io._CliTargetBinding
) -> Any:
    """Re-capture config only when PM rewrote it with identical content."""
    refusal = "legacy_handoff_config_changed"
    try:
        io.assert_bound_parent(binding, HandoffRefused, refusal)
        io.assert_path_parent(binding, HandoffRefused, refusal)
        current, _payload, mode = io.read_file_state(
            binding.basename,
            parent_fd=binding.parent_fd,
            missing_allowed=False,
            refused=HandoffRefused,
            non_regular_refusal=refusal,
            changed_refusal=refusal,
            wrap_read_error=True,
        )
        metadata = os.stat(
            binding.basename,
            dir_fd=binding.parent_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise HandoffRefused(refusal) from exc
    if (
        current is None
        or binding.parent_identity != precondition.config_parent_identity
        or current[2] != precondition.config_snapshot[2]
        or mode != precondition.config_mode
        or metadata.st_mode & 0o7777 != precondition.config_mode
        or metadata.st_uid != precondition.config_uid
        or (metadata.st_dev, metadata.st_ino) != current[:2]
    ):
        raise HandoffRefused(refusal)
    return current


def _marker_identity_swap(
    raw: bytes, old_identity: str, new_identity: str
) -> bytes:
    record = _record(raw)
    if record.get("python_identity") != old_identity:
        raise HandoffRefused("legacy_handoff_marker_changed")
    old_token = json.dumps(old_identity).encode("utf-8")
    new_token = json.dumps(new_identity).encode("utf-8")
    if raw.count(old_token) != 1:
        raise HandoffRefused("legacy_handoff_marker_ambiguous")
    marker_after = raw.replace(old_token, new_token, 1)
    expected_record = {**record, "python_identity": new_identity}
    if _record(marker_after) != expected_record:
        raise HandoffRefused("legacy_handoff_marker_ambiguous")
    return marker_after


def _assert_rollback_member(precondition: Precondition) -> None:
    if (
        io.read_directory_identity(
            precondition.member,
            HandoffRefused,
            "legacy_handoff_rollback_member_changed",
        )
        != precondition.member_identity
        or pm_dependency._owned(precondition.member) != precondition.member_owner
    ):
        raise HandoffRefused("legacy_handoff_rollback_member_changed")


def _assert_rollback_runtime(
    precondition: Precondition,
    expected: Any,
    marker_binding: io._CliTargetBinding,
    config_binding: io._CliTargetBinding,
    *,
    marker_snapshot: Any,
    marker_raw: bytes,
    config_snapshot: Any,
) -> None:
    if marker_binding.parent_identity != precondition.marker_parent_identity:
        raise HandoffRefused("legacy_handoff_marker_changed")
    current = plugin.resolve_runtime_binding(
        checkout_root=precondition.binding.checkout_root,
        hermes_home=precondition.binding.hermes_home,
        profile_id=None,
    )
    if current != expected:
        raise HandoffRefused("legacy_handoff_rollback_binding_changed")
    _assert_rollback_member(precondition)
    _assert_bound_evidence(
        marker_binding,
        snapshot=marker_snapshot,
        mode=precondition.marker_mode,
        uid=precondition.marker_uid,
        payload=marker_raw,
        refusal="legacy_handoff_marker_changed",
    )
    _assert_bound_evidence(
        config_binding,
        snapshot=config_snapshot,
        mode=precondition.config_mode,
        uid=precondition.config_uid,
        payload=None,
        refusal="legacy_handoff_config_changed",
    )
    if legacy_rebind._source_fingerprint(
        precondition.binding.checkout_root
    ) != precondition.source_fingerprint:
        raise HandoffRefused("legacy_handoff_source_changed")


def apply(
    precondition: Precondition,
    binding: plugin.HermesRuntimeBinding,
    registration: Any,
) -> HandoffRegistration:
    root = precondition.binding.checkout_root
    with _root_lock(root):
        marker_binding = _bind_evidence(
            precondition.marker, "legacy_handoff_marker_changed"
        )
        config_binding = None
        try:
            config_binding = _bind_evidence(
                precondition.config, "legacy_handoff_config_changed"
            )
            return _apply_locked(
                precondition,
                binding,
                registration,
                marker_binding=marker_binding,
                config_binding=config_binding,
            )
        finally:
            for evidence in (config_binding, marker_binding):
                if evidence is not None:
                    try:
                        evidence.close()
                    except OSError:
                        pass


def _apply_locked(
    precondition: Precondition,
    binding: plugin.HermesRuntimeBinding,
    registration: Any,
    *,
    marker_binding: io._CliTargetBinding,
    config_binding: io._CliTargetBinding,
) -> HandoffRegistration:
    """Atomically change only the marker's runtime identity."""
    root = precondition.binding.checkout_root
    if (
        not _same_path(precondition.binding.checkout_root, binding.checkout_root)
        or not _same_path(precondition.binding.hermes_home, binding.hermes_home)
        or not _same_path(precondition.binding.config_path, binding.config_path)
    ):
        raise HandoffRefused("legacy_handoff_scope_changed")
    if (
        marker_binding.initial_snapshot != precondition.marker_snapshot
        or marker_binding.initial_bytes != precondition.marker_raw
        or marker_binding.initial_mode != precondition.marker_mode
        or marker_binding.parent_identity != precondition.marker_parent_identity
        or config_binding.initial_snapshot is None
        or config_binding.initial_snapshot[2] != precondition.config_snapshot[2]
        or config_binding.initial_mode != precondition.config_mode
        or config_binding.parent_identity != precondition.config_parent_identity
    ):
        raise HandoffRefused("legacy_handoff_evidence_changed")
    fresh = plugin.resolve_runtime_binding(
        checkout_root=root,
        hermes_home=binding.hermes_home,
        profile_id=None,
    )
    if fresh != binding or fresh.python_identity == precondition.binding.python_identity:
        raise HandoffRefused("legacy_handoff_binding_changed")
    _validate_registration(
        registration,
        binding=binding,
        detection_root=root,
        rollback_python=precondition.binding.runtime_python,
    )
    if (
        registration.backup is None
        or io.read_directory_identity(
            registration.backup, HandoffRefused, "legacy_handoff_member_changed"
        )
        != precondition.member_identity
        or pm_dependency._owned(registration.backup) != precondition.member_owner
    ):
        raise HandoffRefused("legacy_handoff_member_changed")
    config_snapshot = _config_snapshot_after_pm(precondition, config_binding)
    _assert_precondition_current(
        precondition, root, config_snapshot=config_snapshot
    )
    legacy_rebind._verify_runtime(binding)
    # Runtime probing launches child processes. Re-read both evidence files
    # through their original directory descriptors, then re-check source
    # ownership immediately before publishing the marker.
    _assert_bound_evidence(
        marker_binding,
        snapshot=precondition.marker_snapshot,
        mode=precondition.marker_mode,
        uid=precondition.marker_uid,
        payload=precondition.marker_raw,
        refusal="legacy_handoff_marker_changed",
    )
    _assert_bound_evidence(
        config_binding,
        snapshot=config_snapshot,
        mode=precondition.config_mode,
        uid=precondition.config_uid,
        payload=None,
        refusal="legacy_handoff_config_changed",
    )
    if legacy_rebind._source_fingerprint(root) != precondition.source_fingerprint:
        raise HandoffRefused("legacy_handoff_source_changed")
    marker_after = _marker_identity_swap(
        precondition.marker_raw,
        precondition.binding.python_identity,
        binding.python_identity,
    )
    after_snapshot = io._atomic_write_text(
        precondition.marker,
        marker_after.decode("utf-8"),
        _binding=marker_binding,
        _expected_before=precondition.marker_snapshot,
    )
    handoff = HandoffRegistration(
        root=root,
        marker=precondition.marker,
        marker_before=precondition.marker_raw,
        marker_after=marker_after,
        before_snapshot=precondition.marker_snapshot,
        after_snapshot=after_snapshot,
        config=precondition.config,
        config_snapshot=config_snapshot,
        marker_before_mode=precondition.marker_mode,
        marker_before_uid=precondition.marker_uid,
        marker_parent_identity=precondition.marker_parent_identity,
        config_parent_identity=precondition.config_parent_identity,
        config_mode=precondition.config_mode,
        config_uid=precondition.config_uid,
    )
    try:
        legacy_observer.inspect(root, home=binding.hermes_home)
        if io.read_path_snapshot(
            precondition.marker, HandoffRefused, "legacy_handoff_marker_changed"
        ) != after_snapshot:
            raise HandoffRefused("legacy_handoff_marker_changed")
    except BaseException as original_error:
        try:
            handoff._rollback_locked()
        except BaseException as rollback_error:
            _attach_error_evidence(original_error, "rollback_error", rollback_error)
            raise original_error from rollback_error
        _attach_error_evidence(original_error, "marker_written", True)
        _attach_error_evidence(
            original_error, "rollback_snapshot", handoff.rollback_snapshot
        )
        raise
    return handoff


def reconcile_after_rollback(
    precondition: Precondition,
    *,
    marker_snapshot: Any | None = None,
    pm_rollback_before: Any | None = None,
    pm_rollback_after: Any | None = None,
) -> str:
    """Point the HFC marker at the runtime PM selected during rollback.

    PM may commit a fresh generation while undoing its member transaction. The
    HFC marker is rolled back first, so it can be stale even though the old
    member, source and config were restored. Only the marker identity changes;
    PM facts, config and source remain PM/HFC-owned evidence.
    """
    root = precondition.binding.checkout_root
    with _root_lock(root):
        return _reconcile_after_rollback_locked(
            precondition,
            marker_snapshot=marker_snapshot,
            pm_rollback_before=pm_rollback_before,
            pm_rollback_after=pm_rollback_after,
        )


def _reconcile_after_rollback_locked(
    precondition: Precondition,
    *,
    marker_snapshot: Any | None,
    pm_rollback_before: Any | None,
    pm_rollback_after: Any | None,
) -> str:
    root = precondition.binding.checkout_root
    current = plugin.resolve_runtime_binding(
        checkout_root=root,
        hermes_home=precondition.binding.hermes_home,
        profile_id=None,
    )
    if pm_rollback_before is None or pm_rollback_after is None:
        raise HandoffRefused("legacy_handoff_rollback_binding_missing")
    if current != pm_rollback_after:
        raise HandoffRefused("legacy_handoff_rollback_binding_changed")
    current_changed = (
        current.python_identity != precondition.binding.python_identity
    )
    if current_changed and pm_rollback_before == pm_rollback_after:
        raise HandoffRefused("legacy_handoff_rollback_generation_unchanged")
    if (
        io.read_directory_identity(
            precondition.member,
            HandoffRefused,
            "legacy_handoff_rollback_member_changed",
        )
        != precondition.member_identity
        or pm_dependency._owned(precondition.member) != precondition.member_owner
    ):
        raise HandoffRefused("legacy_handoff_rollback_member_changed")
    marker_binding = _bind_evidence(
        precondition.marker, "legacy_handoff_marker_changed"
    )
    config_binding = None
    try:
        raw = marker_binding.initial_bytes
        if raw is None:
            raise HandoffRefused("legacy_handoff_marker_changed")
        if marker_binding.parent_identity != precondition.marker_parent_identity:
            raise HandoffRefused("legacy_handoff_marker_changed")
        marker_identity = _record(raw).get("python_identity")
        if marker_identity == current.python_identity:
            marker_is_current = True
            expected_marker_raw = (
                _marker_identity_swap(
                    precondition.marker_raw,
                    precondition.binding.python_identity,
                    current.python_identity,
                )
                if current_changed
                else precondition.marker_raw
            )
            expected_marker = marker_binding.initial_snapshot
        elif marker_identity == precondition.binding.python_identity:
            marker_is_current = False
            expected_marker_raw = precondition.marker_raw
            expected_marker = (
                precondition.marker_snapshot
                if marker_snapshot is None
                else marker_snapshot
            )
        else:
            raise HandoffRefused("legacy_handoff_marker_changed")
        if expected_marker is None or raw != expected_marker_raw:
            raise HandoffRefused("legacy_handoff_marker_changed")
        config_binding = _bind_evidence(
            precondition.config, "legacy_handoff_config_changed"
        )
        _assert_bound_evidence(
            marker_binding,
            snapshot=expected_marker,
            mode=precondition.marker_mode,
            uid=precondition.marker_uid,
            payload=expected_marker_raw,
            refusal="legacy_handoff_marker_changed",
        )
        config_snapshot = _config_snapshot_after_pm(precondition, config_binding)
        _assert_precondition_current(
            precondition,
            root,
            marker_snapshot=expected_marker,
            marker_raw=expected_marker_raw,
            config_snapshot=config_snapshot,
        )
        _assert_rollback_runtime(
            precondition,
            current,
            marker_binding,
            config_binding,
            marker_snapshot=expected_marker,
            marker_raw=expected_marker_raw,
            config_snapshot=config_snapshot,
        )
        legacy_rebind._verify_runtime(current)
        _assert_rollback_runtime(
            precondition,
            current,
            marker_binding,
            config_binding,
            marker_snapshot=expected_marker,
            marker_raw=expected_marker_raw,
            config_snapshot=config_snapshot,
        )
        evidence = legacy_observer.inspect(root, home=current.hermes_home)
        if (
            evidence is None
            or legacy_observer._config_state(evidence, root) != "installed"
        ):
            raise HandoffRefused("legacy_handoff_rollback_config_unverified")
        if marker_is_current or not current_changed:
            return "unchanged"
        marker_after = _marker_identity_swap(
            precondition.marker_raw,
            precondition.binding.python_identity,
            current.python_identity,
        )
        after_snapshot = io._atomic_write_text(
            precondition.marker,
            marker_after.decode("utf-8"),
            _binding=marker_binding,
            _expected_before=expected_marker,
        )
        handoff = HandoffRegistration(
            root=root,
            marker=precondition.marker,
            marker_before=precondition.marker_raw,
            marker_after=marker_after,
            before_snapshot=expected_marker,
            after_snapshot=after_snapshot,
            config=precondition.config,
            config_snapshot=config_snapshot,
            marker_before_mode=precondition.marker_mode,
            marker_before_uid=precondition.marker_uid,
            marker_parent_identity=precondition.marker_parent_identity,
            config_parent_identity=precondition.config_parent_identity,
            config_mode=precondition.config_mode,
            config_uid=precondition.config_uid,
        )
        try:
            refreshed = plugin.resolve_runtime_binding(
                checkout_root=root,
                hermes_home=precondition.binding.hermes_home,
                profile_id=None,
            )
            if refreshed != current:
                raise HandoffRefused("legacy_handoff_rollback_binding_changed")
            _assert_rollback_member(precondition)
            legacy_observer.inspect(root, home=current.hermes_home)
            if (
                io.read_path_snapshot(
                    precondition.marker,
                    HandoffRefused,
                    "legacy_handoff_marker_changed",
                )
                != after_snapshot
            ):
                raise HandoffRefused("legacy_handoff_marker_changed")
            if (
                legacy_rebind._source_fingerprint(root)
                != precondition.source_fingerprint
            ):
                raise HandoffRefused("legacy_handoff_source_changed")
            _assert_bound_evidence(
                config_binding,
                snapshot=config_snapshot,
                mode=precondition.config_mode,
                uid=precondition.config_uid,
                payload=None,
                refusal="legacy_handoff_config_changed",
            )
        except BaseException:
            handoff._rollback_locked()
            raise
        return "rebound"
    finally:
        for bound in (config_binding, marker_binding):
            if bound is not None:
                try:
                    bound.close()
                except OSError:
                    pass
