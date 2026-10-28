"""Bound source restore and PM member transactions for 9b904 adoption."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable


def prepare_source_restore(root: Path, home: Path, marker: tuple[int, int, str]) -> tuple[Callable[[], None], Callable[[], None]]:
    """Capture a v2 no-delta receipt while the caller holds the recovery lock.

    The returned checks are read-only. They never authorize PM or config
    writes, and a failed check must not prevent recovery's source rollback.
    """
    from . import legacy_9b904 as legacy, legacy_observer, plugin, pm_dependency, posix_transaction as io

    root = root.absolute()
    home = home.absolute()
    io.assert_path_snapshot(root / legacy_observer.MARKER, marker, ValueError,
                            "legacy adoption receipt changed before source restore")
    binding = plugin.resolve_runtime_binding(checkout_root=root, hermes_home=home, profile_id=None)
    member = home / "plugins" / pm_dependency.NAME
    owner = pm_dependency._owned(member)
    probe = pm_dependency._verified_call(root, binding.runtime_python, home, {"operation": "probe"},
                                         legacy.ADAPTER_ID)
    evidence = legacy.capture_admission(root, binding.runtime_python, home, probe, owner)
    if (evidence.marker_identity != marker or evidence.graph is None or owner is None
            or owner.get("schema") != 3 or evidence.sdk_mode != "owned"):
        raise legacy.LegacyAdoptionRefused("legacy adoption source restore evidence is unverified")
    graph = evidence.graph
    versions = {}
    for path, expected in {**evidence.marker_files, home / "config.yaml": evidence.config_identity}.items():
        identity, _payload, version = legacy._read_versioned_evidence(path, "legacy adoption evidence changed")
        if identity != expected:
            raise legacy.LegacyAdoptionRefused("legacy adoption evidence changed")
        versions[path] = identity, version

    def guard() -> None:
        legacy.verify_unmanaged_scope()
        legacy.verify_unmanaged_environment(evidence.environment)
        io.assert_directory_snapshot(home, evidence.home_identity, legacy.LegacyAdoptionRefused,
                                     "legacy adoption home changed", allow_original_absence=False)
        if legacy.verify_plugin_namespace(home) != evidence.plugin_identity:
            raise legacy.LegacyAdoptionRefused("legacy adoption plugin directory changed")
        io.assert_directory_snapshot(member, evidence.old_member_identity, legacy.LegacyAdoptionRefused,
                                     "legacy adoption member changed", allow_original_absence=False)
        if pm_dependency._owned(member) != evidence.old_owner:
            raise legacy.LegacyAdoptionRefused("legacy adoption member changed")
        io.assert_path_snapshot(home / "config.yaml", evidence.config_identity, legacy.LegacyAdoptionRefused,
                                "legacy adoption config changed")
        for path, (identity, version) in versions.items():
            legacy._assert_versioned_evidence(path, identity, version, "legacy adoption evidence changed")
        legacy.verify_boot(evidence.boot)
        legacy.verify_existing_receipt(root, home, evidence)
        for path, directory_identity in graph.directories.items():
            io.assert_directory_snapshot(path, directory_identity, legacy.LegacyAdoptionRefused,
                                         "legacy adoption PM graph directory changed", allow_original_absence=False)
        legacy._assert_versioned_evidence(graph.facts_path, graph.facts_identity, graph.facts_version,
                                          "legacy adoption PM facts changed")
        legacy._assert_versioned_evidence(graph.lock_path, graph.lock_identity, graph.lock_version,
                                          "legacy adoption PM resolved lock changed")

    def finish() -> None:
        guard()
        legacy.verify_source(root)
        current = plugin.resolve_runtime_binding(checkout_root=root, hermes_home=home, profile_id=None)
        if current != binding:
            raise legacy.LegacyAdoptionRefused("legacy adoption runtime changed")
        legacy.verify_runtime(current, version=owner["version"])
        legacy.verify_pm_graph(root, current.runtime_python, home, owner, evidence.generation,
                               evidence.config.digest, expected=evidence.graph)
        guard()

    guard()
    return guard, finish


def remove_member(home: Path, member: Path, identity: tuple[int, int],
                            parent_identity: tuple[int, int], owner: dict[str, Any]) -> None:
    """Remove only the exact member files through the existing bound cleanup path."""
    from . import posix_transaction as io
    from .pm_dependency import OWNER, PMAdmissionOutcomeUncertain, _owned

    if not io._cli_dirfd_binding_supported() or os.rmdir not in os.supports_dir_fd:
        raise PMAdmissionOutcomeUncertain("legacy adoption cleanup needs secure filesystem operations")
    binding = io.bind_target(member, PMAdmissionOutcomeUncertain,
                             staging_refusal="legacy adoption cleanup directory changed",
                             parent_refusal="legacy adoption cleanup directory changed", wrap_open_error=True)
    descriptor = -1
    try:
        if binding.parent_identity != parent_identity:
            raise PMAdmissionOutcomeUncertain("legacy adoption cleanup directory changed")
        binding.assert_parent()
        io.assert_path_parent(binding, PMAdmissionOutcomeUncertain, "legacy adoption cleanup directory changed")
        descriptor = os.open(member.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=binding.parent_fd)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != identity or _owned(member) != owner:
            raise PMAdmissionOutcomeUncertain("legacy adoption cleanup member changed")
        paths = [member / name for name in [*owner['files'], OWNER]]
        _ancestries, directories = io.snapshot_controlled_ancestries(
            home, paths, PMAdmissionOutcomeUncertain,
            outside_refusal="legacy adoption cleanup is outside home",
            unsafe_refusal="legacy adoption cleanup directory changed",
        )
        if directories.get(member) != identity or directories.get(member.parent) != parent_identity:
            raise PMAdmissionOutcomeUncertain("legacy adoption cleanup member changed")
        snapshots: dict[Path, io.FileIdentity | None] = {}
        for path in paths:
            snapshot, payload, _mode = io.read_file_state(
                path.name, parent_fd=descriptor, missing_allowed=False, refused=PMAdmissionOutcomeUncertain,
                non_regular_refusal="legacy adoption cleanup file changed",
                changed_refusal="legacy adoption cleanup file changed", wrap_read_error=True,
            )
            if snapshot is None or payload is None:
                raise PMAdmissionOutcomeUncertain("legacy adoption cleanup file changed")
            if path.name == OWNER:
                if json.loads(payload) != owner:
                    raise PMAdmissionOutcomeUncertain("legacy adoption cleanup owner changed")
            elif sha256(payload).hexdigest() != owner['files'][path.name]:
                raise PMAdmissionOutcomeUncertain("legacy adoption cleanup file changed")
            snapshots[path] = snapshot
        cleanup_directories: dict[Path, io.DirectoryIdentity | None] = dict(directories)
        io._remove_verified_restore_evidence(paths, snapshots, cleanup_directories)
        binding.assert_parent()
        io.assert_path_parent(binding, PMAdmissionOutcomeUncertain, "legacy adoption cleanup directory changed")
        current = os.stat(member.name, dir_fd=binding.parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != identity:
            raise PMAdmissionOutcomeUncertain("legacy adoption cleanup member changed")
        os.rmdir(member.name, dir_fd=binding.parent_fd)
    except (OSError, ValueError) as exc:
        if isinstance(exc, PMAdmissionOutcomeUncertain):
            raise
        raise PMAdmissionOutcomeUncertain("legacy adoption cleanup evidence retained") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        binding.close()


def move_member(source: Path, destination: Path, identity: tuple[int, int],
                          parent_identity: tuple[int, int], owner: dict[str, Any]) -> None:
    """Move a verified member under the held HFC lock; preserve uncertain results."""
    from . import posix_transaction as io
    from .pm_dependency import PMAdmissionOutcomeUncertain, _owned

    if not io._cli_dirfd_binding_supported() or source.parent != destination.parent:
        raise PMAdmissionOutcomeUncertain("legacy adoption move needs secure filesystem operations")
    binding = io.bind_target(source, PMAdmissionOutcomeUncertain,
                             staging_refusal="legacy adoption move directory changed",
                             parent_refusal="legacy adoption move directory changed", wrap_open_error=True)
    descriptor = -1
    try:
        if binding.parent_identity != parent_identity:
            raise PMAdmissionOutcomeUncertain("legacy adoption move directory changed")
        binding.assert_parent()
        io.assert_path_parent(binding, PMAdmissionOutcomeUncertain, "legacy adoption move directory changed")
        descriptor = os.open(source.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=binding.parent_fd)
        opened = os.fstat(descriptor)
        current = os.stat(source.name, dir_fd=binding.parent_fd, follow_symlinks=False)
        if ((opened.st_dev, opened.st_ino) != identity
                or (current.st_dev, current.st_ino) != identity or _owned(source) != owner):
            raise PMAdmissionOutcomeUncertain("legacy adoption move source changed")
        try:
            os.stat(destination.name, dir_fd=binding.parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise PMAdmissionOutcomeUncertain("legacy adoption move target already exists")
        binding.assert_parent()
        io.assert_path_parent(binding, PMAdmissionOutcomeUncertain, "legacy adoption move directory changed")
        os.rename(source.name, destination.name, src_dir_fd=binding.parent_fd, dst_dir_fd=binding.parent_fd)
        moved = os.stat(destination.name, dir_fd=binding.parent_fd, follow_symlinks=False)
        if ((moved.st_dev, moved.st_ino) != identity or _owned(destination) != owner):
            raise PMAdmissionOutcomeUncertain("legacy adoption move outcome is uncertain")
        binding.assert_parent()
        io.assert_path_parent(binding, PMAdmissionOutcomeUncertain, "legacy adoption move directory changed")
    except (OSError, ValueError) as exc:
        if isinstance(exc, PMAdmissionOutcomeUncertain):
            raise
        raise PMAdmissionOutcomeUncertain("legacy adoption move outcome is uncertain") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        binding.close()
