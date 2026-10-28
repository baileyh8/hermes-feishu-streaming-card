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
    source = legacy_rebind._source_fingerprint(detection.root)
    legacy_rebind._verify_runtime(binding)
    member = binding.hermes_home / "plugins" / pm_dependency.NAME
    owner = pm_dependency._owned(member)
    if not isinstance(owner, dict) or not owner:
        raise HandoffRefused("legacy_handoff_member_owner_missing")
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
) -> None:
    if type(registration) is not pm_dependency.Registration:
        raise HandoffRefused("legacy_handoff_registration_required")
    if registration.adoption is not None:
        raise HandoffRefused("legacy_handoff_adoption_unsupported")
    registration._assert_current()
    if (
        not _same_path(registration.root, detection_root)
        or not _same_path(registration.home, binding.hermes_home)
        or not _same_path(registration.python, binding.runtime_python)
        or not _same_path(
            registration.member,
            binding.hermes_home / "plugins" / pm_dependency.NAME,
        )
        or pm_dependency._owned(registration.member) != registration.owner
    ):
        raise HandoffRefused("legacy_handoff_registration_mismatch")


def _assert_precondition_current(precondition: Precondition, root: Path) -> None:
    if (
        io.read_path_snapshot(
            precondition.marker, HandoffRefused, "legacy_handoff_marker_changed"
        )
        != precondition.marker_snapshot
        or precondition.marker.read_bytes() != precondition.marker_raw
    ):
        raise HandoffRefused("legacy_handoff_marker_changed")
    if (
        io.read_path_snapshot(
            precondition.config, HandoffRefused, "legacy_handoff_config_changed"
        )
        != precondition.config_snapshot
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
        or config_binding.initial_snapshot != precondition.config_snapshot
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
    _assert_precondition_current(precondition, root)
    _validate_registration(
        registration, binding=binding, detection_root=root
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
        snapshot=precondition.config_snapshot,
        mode=precondition.config_mode,
        uid=precondition.config_uid,
        payload=None,
        refusal="legacy_handoff_config_changed",
    )
    if legacy_rebind._source_fingerprint(root) != precondition.source_fingerprint:
        raise HandoffRefused("legacy_handoff_source_changed")
    record = _record(precondition.marker_raw)
    old_identity = record.get("python_identity")
    if old_identity != precondition.binding.python_identity:
        raise HandoffRefused("legacy_handoff_marker_changed")
    old_token = json.dumps(old_identity).encode("utf-8")
    new_token = json.dumps(binding.python_identity).encode("utf-8")
    if precondition.marker_raw.count(old_token) != 1:
        raise HandoffRefused("legacy_handoff_marker_ambiguous")
    marker_after = precondition.marker_raw.replace(old_token, new_token, 1)
    expected_record = {**record, "python_identity": binding.python_identity}
    if _record(marker_after) != expected_record:
        raise HandoffRefused("legacy_handoff_marker_ambiguous")
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
        config_snapshot=precondition.config_snapshot,
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
    except BaseException:
        handoff._rollback_locked()
        raise
    return handoff
