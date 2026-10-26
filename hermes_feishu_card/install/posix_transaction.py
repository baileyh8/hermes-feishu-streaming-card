"""Directory-bound file writes and rollback-capable POSIX transactions.

CLI chooses the install/restore plan. This module owns directory descriptors,
file snapshots, staged bytes, exclusive replacement and rollback verification.
It never loads CLI, Hermes detection, configuration or network services.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat
from typing import Callable
from uuid import uuid4

from .file_transaction import (
    BINARY_FLAG,
    _RestoreIdentity,
    _RestoreEvidenceSnapshot,
    _CLI_SNAPSHOT_UNSET,
    _atomic_write_text_portable,
)


# All three secure writers use these physical operations. Their adapters keep
# planner evidence, transaction ordering and the original error vocabulary.
DirectoryIdentity = tuple[int, int]
FileIdentity = tuple[int, int, str]
DIRFD_BINDING_SUPPORTED = (
    os.name != "nt"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "fchmod")
    and all(operation in getattr(os, "supports_dir_fd", set())
            for operation in (os.open, os.stat, os.unlink, os.rename))
)


@dataclass
class BoundTarget:
    path: Path
    parent_fd: int
    parent_identity: DirectoryIdentity
    refused: type[ValueError] = field(default=ValueError, repr=False)
    parent_refusal: str = "bound target directory changed during write"

    @property
    def basename(self) -> str:
        return self.path.name

    def assert_parent(self) -> None:
        assert_bound_parent(self, self.refused, self.parent_refusal)

    def release(self) -> None:
        if self.parent_fd >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1

    close = release


@dataclass
class BoundStage:
    path: Path
    parent_fd: int
    basename: str
    identity: DirectoryIdentity
    owns_parent_fd: bool
    refused: type[ValueError] = field(default=ValueError, repr=False)
    ownership_refusal: str = "refusing to replace a changed staged file"
    suppress_cleanup_errors: bool = False
    consumed: bool = False
    digest: str = ""
    mode: int = 0o600
    # Recovery's private replacement adapter carries its original destination.
    # This is a borrowed resource reference, never a plan/authorization claim.
    target_binding: BoundTarget | None = field(default=None, repr=False)

    def assert_owned(self) -> None:
        current = os.stat(self.basename, dir_fd=self.parent_fd, follow_symlinks=False)
        if (not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != self.identity):
            raise self.refused(self.ownership_refusal)

    def release(self) -> None:
        if self.owns_parent_fd and self.parent_fd >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1

    def cleanup(self) -> None:
        cleanup_stage(self)


def open_bound_parent(
    parent: Path, refused: type[ValueError], refusal: str, *, wrap_open_error: bool,
) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(parent, flags)
    except OSError as exc:
        if wrap_open_error:
            raise refused(refusal) from exc
        raise
    try:
        opened = os.fstat(parent_fd)
        current = parent.lstat()
        if (not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(current.st_mode)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            raise refused(refusal)
        return parent_fd
    except Exception:
        os.close(parent_fd)
        raise


def bind_target(
    target: Path, refused: type[ValueError], *, staging_refusal: str,
    parent_refusal: str, wrap_open_error: bool,
) -> BoundTarget:
    parent_fd = open_bound_parent(
        target.parent, refused, staging_refusal, wrap_open_error=wrap_open_error,
    )
    try:
        opened = os.fstat(parent_fd)
        return BoundTarget(target, parent_fd, (opened.st_dev, opened.st_ino), refused, parent_refusal)
    except Exception:
        os.close(parent_fd)
        raise


def assert_bound_parent(binding: BoundTarget, refused: type[ValueError], refusal: str) -> None:
    current = os.fstat(binding.parent_fd)
    if (not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != binding.parent_identity):
        raise refused(refusal)


def assert_path_parent(binding: BoundTarget, refused: type[ValueError], refusal: str) -> None:
    try:
        current = binding.path.parent.lstat()
    except FileNotFoundError as exc:
        raise refused(refusal) from exc
    if (not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != binding.parent_identity):
        raise refused(refusal)


def read_file_state(
    target: Path | str, *, parent_fd: int | None, missing_allowed: bool,
    refused: type[ValueError], non_regular_refusal: str, changed_refusal: str,
    wrap_read_error: bool, collect_payload: bool = True,
) -> tuple[FileIdentity | None, bytes | None, int | None]:
    def snapshot() -> os.stat_result:
        if parent_fd is None:
            return (target if isinstance(target, Path) else Path(target)).lstat()
        return os.stat(target, dir_fd=parent_fd, follow_symlinks=False)

    try:
        before = snapshot()
    except FileNotFoundError as exc:
        if missing_allowed:
            return None, None, None
        raise refused(changed_refusal) from exc
    if not stat.S_ISREG(before.st_mode):
        raise refused(non_regular_refusal)
    identity = (before.st_dev, before.st_ino)
    descriptor = -1
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | BINARY_FLAG
        descriptor = (os.open(target, flags) if parent_fd is None
                      else os.open(target, flags, dir_fd=parent_fd))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != identity:
            raise refused(changed_refusal)
        while chunk := os.read(descriptor, 64 * 1024):
            digest.update(chunk)
            if collect_payload:
                chunks.append(chunk)
    except OSError as exc:
        if wrap_read_error:
            raise refused(changed_refusal) from exc
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        after = snapshot()
    except FileNotFoundError as exc:
        # Bound reads originally propagate a post-read disappearance; lexical
        # evidence reads translate it into their domain refusal.
        if parent_fd is None:
            raise refused(changed_refusal) from exc
        raise
    if not stat.S_ISREG(after.st_mode) or (after.st_dev, after.st_ino) != identity:
        raise refused(changed_refusal)
    return (before.st_dev, before.st_ino, digest.hexdigest()), (
        b"".join(chunks) if collect_payload else None
    ), stat.S_IMODE(before.st_mode)


def read_bound_text(
    binding: BoundTarget, refused: type[ValueError], refusal: str,
) -> tuple[FileIdentity | None, str | None]:
    snapshot, payload, _mode = read_file_state(
        binding.basename, parent_fd=binding.parent_fd, missing_allowed=True,
        refused=refused, non_regular_refusal=refusal, changed_refusal=refusal,
        wrap_read_error=True,
    )
    if payload is None:
        return snapshot, None
    try:
        return snapshot, payload.decode("utf-8")
    except UnicodeError as exc:
        raise refused(refusal) from exc


def read_path_snapshot(target: Path, refused: type[ValueError], refusal: str) -> FileIdentity:
    snapshot, _payload, _mode = read_file_state(
        target, parent_fd=None, missing_allowed=False, refused=refused,
        non_regular_refusal=refusal, changed_refusal=refusal, wrap_read_error=True,
        collect_payload=False,
    )
    if snapshot is None:
        raise refused(refusal)
    return snapshot


def assert_restored_snapshot(
    current: FileIdentity | None, expected: FileIdentity | None,
    refused: type[ValueError], refusal: str,
) -> None:
    matches = current is None if expected is None else current is not None and current[2] == expected[2]
    if not matches:
        raise refused(refusal)


def read_directory_identity(directory: Path, refused: type[ValueError], refusal: str) -> DirectoryIdentity:
    try:
        snapshot = directory.lstat()
    except FileNotFoundError as exc:
        raise refused(refusal) from exc
    if not stat.S_ISDIR(snapshot.st_mode):
        raise refused(refusal)
    return snapshot.st_dev, snapshot.st_ino


def controlled_ancestry_paths(
    controlled_root: Path, target: Path, refused: type[ValueError], refusal: str,
) -> tuple[Path, ...]:
    root = Path(os.path.abspath(controlled_root))
    absolute_target = Path(os.path.abspath(target))
    try:
        relative = absolute_target.relative_to(root)
    except ValueError as exc:
        raise refused(refusal) from exc
    if not relative.parts:
        raise refused(refusal)
    directories = [root]
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        directories.append(current)
    return tuple(directories)


def read_prewrite_snapshot(target: Path, refused: type[ValueError], refusal: str) -> FileIdentity | None:
    try:
        target.lstat()
    except FileNotFoundError:
        return None
    return read_path_snapshot(target, refused, refusal)


def assert_path_snapshot(
    target: Path, expected: FileIdentity | None, refused: type[ValueError], refusal: str,
) -> None:
    if expected is None:
        try:
            target.lstat()
        except FileNotFoundError:
            return
        raise refused(refusal)
    if read_path_snapshot(target, refused, refusal) != expected:
        raise refused(refusal)


def snapshot_controlled_ancestries(
    controlled_root: Path, targets: list[Path], refused: type[ValueError], *,
    outside_refusal: str, unsafe_refusal: str,
) -> tuple[dict[Path, tuple[Path, ...]], dict[Path, DirectoryIdentity]]:
    ancestries: dict[Path, tuple[Path, ...]] = {}
    identities: dict[Path, DirectoryIdentity] = {}
    for target in targets:
        ancestry = controlled_ancestry_paths(controlled_root, target, refused, outside_refusal)
        ancestries[target] = ancestry
        for directory in ancestry:
            if directory not in identities:
                identities[directory] = read_directory_identity(directory, refused, unsafe_refusal)
    return ancestries, identities


def assert_directory_snapshot(
    directory: Path, expected: DirectoryIdentity | None, refused: type[ValueError],
    refusal: str, *, allow_original_absence: bool,
) -> None:
    try:
        current = directory.lstat()
    except FileNotFoundError as exc:
        if expected is None and allow_original_absence:
            return
        raise refused(refusal) from exc
    if (expected is None or not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != expected):
        raise refused(refusal)


def assert_controlled_ancestry(
    target: Path, ancestries: dict[Path, tuple[Path, ...]],
    identities: dict[Path, DirectoryIdentity], refused: type[ValueError], refusal: str,
) -> None:
    for directory in ancestries[target]:
        assert_directory_snapshot(directory, identities[directory], refused, refusal, allow_original_absence=False)


def _assert_restore_directory_ancestry_unchanged(
    path: Path, expected_directories: dict[Path, _RestoreIdentity | None],
) -> None:
    # CLI restore permits a directory that was absent in its original evidence;
    # automatic recovery never enables this policy.
    for directory, expected in expected_directories.items():
        if directory != path and directory not in path.parents:
            continue
        assert_directory_snapshot(
            directory, expected, ValueError,
            f"{directory.name} directory changed during restore; refusing to restore",
            allow_original_absence=True,
        )


def stage_file(
    target: Path, contents: str | bytes, *, binding: BoundTarget | None,
    mode: int | None, durable_text: bool, refused: type[ValueError],
    staging_refusal: str, target_refusal: str, stage_refusal: str,
) -> BoundStage:
    owns_parent = durable_text
    if binding is None:
        parent_fd = open_bound_parent(target.parent, refused, staging_refusal, wrap_open_error=True)
        owns_parent = True
        target_name = target.name
    else:
        binding.assert_parent()
        parent_fd = os.dup(binding.parent_fd) if owns_parent else binding.parent_fd
        target_name = binding.basename
    descriptor = -1
    staged: BoundStage | None = None
    try:
        basename = f".{target_name}.{uuid4().hex}.tmp"
        descriptor = os.open(
            basename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | BINARY_FLAG,
            0o600,
            dir_fd=parent_fd,
        )
        snapshot = os.fstat(descriptor)
        staged = BoundStage(
            target.parent / basename, parent_fd, basename, (snapshot.st_dev, snapshot.st_ino),
            owns_parent, refused, stage_refusal, suppress_cleanup_errors=not durable_text,
        )
        if owns_parent:
            parent_fd = -1
        if durable_text:
            try:
                target_snapshot = os.stat(target_name, dir_fd=staged.parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                selected_mode = 0o600
            else:
                if not stat.S_ISREG(target_snapshot.st_mode):
                    raise refused(target_refusal)
                selected_mode = stat.S_IMODE(target_snapshot.st_mode)
            if not isinstance(contents, str):
                raise TypeError("durable text stage requires text")
            handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
            descriptor = -1
            with handle:
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), selected_mode)
        else:
            if not isinstance(contents, bytes):
                raise TypeError("byte stage requires bytes")
            selected_mode = mode if mode is not None else 0o600
            staged.digest = hashlib.sha256(contents).hexdigest()
            offset = 0
            while offset < len(contents):
                offset += os.write(descriptor, contents[offset:])
            os.fchmod(descriptor, selected_mode)
            os.close(descriptor)
            descriptor = -1
        staged.mode = selected_mode
        return staged
    except Exception:
        if durable_text and descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        if staged is not None:
            staged.cleanup()
        elif owns_parent and parent_fd >= 0:
            os.close(parent_fd)
        raise
    finally:
        # CLI byte staging cleans up before closing its still-open file handle.
        if descriptor >= 0:
            os.close(descriptor)


def replace_stage(binding: BoundTarget, staged: BoundStage, *, check_parent: bool) -> None:
    staged.assert_owned()
    if check_parent:
        binding.assert_parent()
    os.replace(staged.basename, binding.basename, src_dir_fd=staged.parent_fd, dst_dir_fd=binding.parent_fd)
    staged.consumed = True
    staged.release()


def unlink_bound_target(binding: BoundTarget) -> None:
    os.unlink(binding.basename, dir_fd=binding.parent_fd)


def cleanup_stage(staged: BoundStage) -> None:
    if staged.parent_fd < 0:
        return
    if staged.consumed:
        staged.release()
        return
    try:
        try:
            current = os.stat(staged.basename, dir_fd=staged.parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == staged.identity:
            os.unlink(staged.basename, dir_fd=staged.parent_fd)
            staged.consumed = True
    except OSError:
        if not staged.suppress_cleanup_errors:
            raise
    finally:
        staged.release()




def cleanup_transaction_resources(
    stages: Iterable[BoundStage], bindings: Iterable[BoundTarget],
) -> None:
    """Release other acquired resources before propagating a cleanup failure.

    Stage ownership rules and the first original exception are preserved. A
    failed stat/unlink must not prevent later stages or bound fds being closed.
    """
    first_error: Exception | None = None
    for staged in stages:
        try:
            staged.cleanup()
        except Exception as exc:
            first_error = first_error or exc
    for binding in bindings:
        try:
            binding.release()
        except Exception as exc:
            first_error = first_error or exc
    if first_error is not None:
        raise first_error


class _CliTargetBinding(BoundTarget):
    def __init__(
        self, path: Path, parent_fd: int, parent_identity: _RestoreIdentity,
        initial_snapshot: _RestoreEvidenceSnapshot | None,
        initial_bytes: bytes | None, initial_mode: int | None,
    ) -> None:
        super().__init__(path, parent_fd, parent_identity)
        self.initial_snapshot = initial_snapshot
        self.initial_bytes = initial_bytes
        self.initial_mode = initial_mode


_CliStagedText = BoundStage


_CLI_DIRFD_BINDING_SUPPORTED = DIRFD_BINDING_SUPPORTED


def _cli_dirfd_binding_supported() -> bool:
    return _CLI_DIRFD_BINDING_SUPPORTED


def _atomic_write_text(
    path: Path,
    contents: str,
    *,
    mode: int | None = None,
    _binding: _CliTargetBinding | None = None,
    _expected_before: _RestoreEvidenceSnapshot | None | object = _CLI_SNAPSHOT_UNSET,
    _enforce_path_parent: bool = True,
) -> _RestoreEvidenceSnapshot:
    if not _cli_dirfd_binding_supported():
        if _binding is not None:
            raise ValueError(
                "secure bound write requires directory-relative filesystem "
                "operations on this platform"
            )
        return _atomic_write_text_portable(
            path,
            contents,
            mode=mode,
            expected_before=_expected_before,
        )
    owns_binding = _binding is None
    binding = _binding if _binding is not None else _bind_cli_target(path)
    if binding.path != path:
        if owns_binding:
            binding.close()
        raise ValueError("atomic write binding does not match target")
    expected_before = (
        binding.initial_snapshot
        if _expected_before is _CLI_SNAPSHOT_UNSET
        else _expected_before
    )
    rollback_stage: _CliStagedText | None = None
    write_stage: _CliStagedText | None = None
    try:
        _assert_bound_cli_parent(binding)
        current_snapshot, current_bytes, current_mode = _read_bound_cli_target_state(
            binding.parent_fd, binding.basename
        )
        if current_snapshot != expected_before:
            raise ValueError("refusing to replace a changed target")
        if current_bytes is not None:
            rollback_stage = _stage_bound_cli_bytes(
                binding,
                current_bytes,
                current_mode if current_mode is not None else 0o600,
            )
        selected_mode = mode if mode is not None else current_mode
        write_stage = _stage_bound_cli_bytes(
            binding,
            contents.encode("utf-8"),
            selected_mode if selected_mode is not None else 0o600,
        )
        committed = _replace_bound_cli_stage(
            binding,
            write_stage,
            expected_before=current_snapshot,
        )
        if _enforce_path_parent:
            try:
                _assert_cli_path_parent_bound(binding)
            except (OSError, ValueError) as exc:
                try:
                    if current_snapshot is None:
                        _unlink_bound_cli_target(binding, committed)
                    else:
                        if rollback_stage is None:
                            raise ValueError("atomic write lost rollback stage")
                        restored = _replace_bound_cli_stage(
                            binding,
                            rollback_stage,
                            expected_before=committed,
                        )
                        if restored[2] != current_snapshot[2]:
                            raise ValueError("atomic write rollback verification failed")
                except (OSError, ValueError) as rollback_exc:
                    raise ValueError(
                        "atomic write rollback failed; manual review required"
                    ) from rollback_exc
                raise ValueError("directory changed during write") from exc
        return committed
    finally:
        stages = [staged for staged in (write_stage, rollback_stage) if staged is not None]
        for staged in stages:
            staged.parent_fd = binding.parent_fd
        cleanup_transaction_resources(stages, [binding] if owns_binding else [])


def _bind_cli_target(path: Path) -> _CliTargetBinding:
    parent_fd = open_bound_parent(
        path.parent, ValueError, "refusing to stage through a changed directory", wrap_open_error=False,
    )
    try:
        opened = os.fstat(parent_fd)
        snapshot, payload, mode = _read_bound_cli_target_state(parent_fd, path.name)
        return _CliTargetBinding(path, parent_fd, (opened.st_dev, opened.st_ino), snapshot, payload, mode)
    except Exception:
        os.close(parent_fd)
        raise


def _assert_bound_cli_parent(binding: _CliTargetBinding) -> None:
    assert_bound_parent(binding, ValueError, "bound target directory changed during write")


def _assert_cli_path_parent_bound(binding: _CliTargetBinding) -> None:
    assert_path_parent(binding, ValueError, "directory changed during write")


def _stage_bound_cli_bytes(binding: _CliTargetBinding, payload: bytes, mode: int) -> _CliStagedText:
    return stage_file(
        binding.path, payload, binding=binding, mode=mode, durable_text=False, refused=ValueError,
        staging_refusal="refusing to stage through a changed directory",
        target_refusal="refusing to use a non-regular target",
        stage_refusal="refusing to replace a changed staged file",
    )


def _replace_bound_cli_stage(
    binding: _CliTargetBinding, staged: _CliStagedText, *,
    expected_before: _RestoreEvidenceSnapshot | None,
) -> _RestoreEvidenceSnapshot:
    _assert_bound_cli_parent(binding)
    if _read_bound_cli_target(binding.parent_fd, binding.basename) != expected_before:
        raise ValueError("refusing to replace a changed target")
    replace_stage(binding, staged, check_parent=False)
    committed = _read_bound_cli_target(binding.parent_fd, binding.basename)
    expected_committed = (*staged.identity, staged.digest)
    if committed != expected_committed:
        raise ValueError("could not verify committed target")
    return expected_committed


def _unlink_bound_cli_target(binding: _CliTargetBinding, expected_snapshot: _RestoreEvidenceSnapshot) -> None:
    _assert_bound_cli_parent(binding)
    if _read_bound_cli_target(binding.parent_fd, binding.basename) != expected_snapshot:
        raise ValueError("refusing to unlink a changed target")
    unlink_bound_target(binding)
    if _read_bound_cli_target(binding.parent_fd, binding.basename) is not None:
        raise ValueError("could not verify removed target")


def _cleanup_bound_cli_stage(binding: _CliTargetBinding, staged: _CliStagedText) -> None:
    # CLI stages borrow this binding's descriptor; recovery stages own a dup.
    staged.parent_fd = binding.parent_fd
    cleanup_stage(staged)


def _read_bound_cli_target(
    parent_fd: int, basename: str
) -> tuple[int, int, str] | None:
    snapshot, _payload, _mode = _read_bound_cli_target_state(parent_fd, basename)
    return snapshot


def _read_bound_cli_target_state(
    parent_fd: int, basename: str,
) -> tuple[_RestoreEvidenceSnapshot | None, bytes | None, int | None]:
    return read_file_state(
        basename, parent_fd=parent_fd, missing_allowed=True, refused=ValueError,
        non_regular_refusal="refusing to use a non-regular target",
        changed_refusal="target changed while being verified", wrap_read_error=False,
    )

def _write_targets_transactionally(
    changes: list[tuple[Path, str]],
    *,
    expected_identities: dict[Path, _RestoreEvidenceSnapshot | None] | None = None,
    expected_directories: dict[Path, _RestoreIdentity | None] | None = None,
    preserve_earlier_writes_on_rollback_failure: bool = False,
    pre_commit_validate: Callable[[], None] | None = None,
    validate: Callable[[], None] | None = None,
) -> None:
    if not _cli_dirfd_binding_supported():
        raise ValueError(
            "secure restore transaction requires directory-relative "
            "filesystem operations on this platform"
        )
    bindings: dict[Path, _CliTargetBinding] = {}
    snapshots: list[tuple[Path, str | None]] = []
    try:
        for path, _contents in changes:
            if expected_directories is not None:
                _assert_restore_directory_ancestry_unchanged(
                    path, expected_directories
                )
            binding = _bind_cli_target(path)
            bindings[path] = binding
            if expected_directories is not None:
                _assert_restore_directory_ancestry_unchanged(
                    path, expected_directories
                )
                expected_parent = expected_directories.get(path.parent)
                if (
                    expected_parent is not None
                    and binding.parent_identity != expected_parent
                ):
                    raise ValueError(
                        f"{path.parent.name} directory changed during restore; "
                        "refusing to restore"
                    )
            if expected_identities is not None:
                expected_snapshot = expected_identities.get(path)
                if binding.initial_snapshot != expected_snapshot:
                    raise ValueError(
                        f"{path.name} changed during restore; refusing to clean up"
                    )
            snapshots.append(
                (
                    path,
                    binding.initial_bytes.decode("utf-8")
                    if binding.initial_bytes is not None
                    else None,
                )
            )

        if pre_commit_validate is not None:
            pre_commit_validate()
        written: list[Path] = []
        post_write_snapshots: dict[Path, _RestoreEvidenceSnapshot] = {}
        try:
            for path, contents in changes:
                binding = bindings[path]
                post_write_snapshots[path] = _atomic_write_text(
                    path,
                    contents,
                    _binding=binding,
                    _expected_before=binding.initial_snapshot,
                )
                written.append(path)
            for path in written:
                if expected_directories is not None:
                    _assert_restore_directory_ancestry_unchanged(
                        path, expected_directories
                    )
                binding = bindings[path]
                _assert_cli_path_parent_bound(binding)
                if (
                    _read_bound_cli_target(binding.parent_fd, binding.basename)
                    != post_write_snapshots[path]
                ):
                    raise ValueError("restore transaction lost write ownership")
            if validate is not None:
                validate()
        except (OSError, UnicodeError, ValueError) as exc:
            rollback_failed = False
            snapshot_by_path = dict(snapshots)
            for path in reversed(written):
                previous = snapshot_by_path[path]
                try:
                    post_write_snapshot = post_write_snapshots.get(path)
                    if post_write_snapshot is None:
                        raise ValueError("restore transaction lost write ownership")
                    binding = bindings[path]
                    if previous is None:
                        _unlink_bound_cli_target(binding, post_write_snapshot)
                    else:
                        _atomic_write_text(
                            path,
                            previous,
                            _binding=binding,
                            _expected_before=post_write_snapshot,
                            _enforce_path_parent=False,
                        )
                except (OSError, UnicodeError, ValueError):
                    rollback_failed = True
                    if preserve_earlier_writes_on_rollback_failure:
                        break
            if rollback_failed:
                raise ValueError(
                    "restore transaction rollback failed; manual review required"
                ) from exc
            raise
    finally:
        for binding in bindings.values():
            try:
                binding.close()
            except OSError:
                pass


def _remove_verified_restore_evidence(
    paths_to_remove: list[Path],
    restore_identities: dict[Path, _RestoreEvidenceSnapshot | None],
    restore_directory_identities: dict[Path, _RestoreIdentity | None] | None,
) -> None:
    """Bind and verify every planned target before ordered evidence removal.

    The caller chooses the paths and owns the recovery plan. Removal is not
    atomic across files; all acquired directory descriptors are closed on exit.
    """
    if not _cli_dirfd_binding_supported():
        raise ValueError(
            "secure restore cleanup requires directory-relative filesystem "
            "operations on this platform"
        )
    bindings: dict[Path, _CliTargetBinding] = {}
    try:
        for path in paths_to_remove:
            if restore_directory_identities is not None:
                _assert_restore_directory_ancestry_unchanged(
                    path, restore_directory_identities
                )
            binding = _bind_cli_target(path)
            bindings[path] = binding
            expected_snapshot = restore_identities.get(path)
            if binding.initial_snapshot != expected_snapshot:
                raise ValueError(
                    f"{path.name} changed during restore; refusing to clean up"
                )
            if restore_directory_identities is not None:
                _assert_restore_directory_ancestry_unchanged(
                    path, restore_directory_identities
                )
                expected_parent = restore_directory_identities.get(path.parent)
                if (
                    expected_parent is not None
                    and binding.parent_identity != expected_parent
                ):
                    raise ValueError(
                        f"{path.parent.name} directory changed during restore; "
                        "refusing to restore"
                    )
        for path in paths_to_remove:
            _safe_unlink_restore_evidence(
                bindings[path], restore_identities.get(path)
            )
    finally:
        for binding in bindings.values():
            try:
                binding.close()
            except OSError:
                pass


def _safe_unlink_restore_evidence(
    binding: _CliTargetBinding,
    expected_snapshot: _RestoreEvidenceSnapshot | None,
) -> None:
    if expected_snapshot is not None:
        _unlink_bound_cli_target(binding, expected_snapshot)
    elif _read_bound_cli_target(binding.parent_fd, binding.basename) is not None:
        raise ValueError(
            f"{binding.basename} changed during restore; refusing to clean up"
        )
