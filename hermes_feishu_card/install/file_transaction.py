"""Owned file evidence and portable rollback-capable install transactions.

Shared by legacy and decomposed installers. This boundary has no CLI, HTTP,
patch-selection or Hermes runtime dependency. Automatic recovery still uses
its stronger directory-bound transaction; this writer does not relax that gate.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import cast

_RestoreIdentity = tuple[int, int]
_RestoreEvidenceSnapshot = tuple[int, int, str]
_CLI_SNAPSHOT_UNSET = object()
BINARY_FLAG = getattr(os, "O_BINARY", 0)


def _is_reparse(metadata: os.stat_result) -> bool:
    """Windows junctions/mount points are links even when lstat omits S_ISLNK."""
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _snapshot_restore_evidence(
    hermes_root: Path, evidence: dict[str, Path]
) -> tuple[
    dict[Path, _RestoreEvidenceSnapshot | None],
    dict[Path, _RestoreIdentity | None],
]:
    directory_identities: dict[Path, _RestoreIdentity | None] = {}
    for label, path in (
        ("Hermes directory", hermes_root),
        ("gateway directory", hermes_root / "gateway"),
        ("cron directory", hermes_root / "cron"),
        ("gateway/platforms directory", hermes_root / "gateway" / "platforms"),
    ):
        try:
            snapshot = path.lstat()
        except FileNotFoundError:
            directory_identities[path] = None
            continue
        if stat.S_ISLNK(snapshot.st_mode) or _is_reparse(snapshot):
            raise ValueError(f"{label} must not be a symlink")
        if not stat.S_ISDIR(snapshot.st_mode):
            raise ValueError(f"{label} must be a directory")
        directory_identities[path] = (snapshot.st_dev, snapshot.st_ino)

    identities: dict[Path, _RestoreEvidenceSnapshot | None] = {}
    for label, path in evidence.items():
        try:
            snapshot = path.lstat()
        except FileNotFoundError:
            identities[path] = None
            continue
        if stat.S_ISLNK(snapshot.st_mode) or _is_reparse(snapshot):
            raise ValueError(f"{label} must not be a symlink")
        if not stat.S_ISREG(snapshot.st_mode):
            raise ValueError(f"{label} must be a regular file")
        identity = (snapshot.st_dev, snapshot.st_ino)
        contents = _read_restore_text(path, identity)
        identities[path] = (
            snapshot.st_dev,
            snapshot.st_ino,
            _restore_text_sha256(contents),
        )
    return identities, directory_identities


def _restore_text_sha256(contents: str) -> str:
    return hashlib.sha256(contents.encode("utf-8")).hexdigest()


def _read_restore_text(
    path: Path,
    expected_snapshot: _RestoreIdentity | _RestoreEvidenceSnapshot | None,
) -> str:
    if expected_snapshot is None:
        raise ValueError(f"{path.name} is missing; refusing to restore")
    expected_identity = expected_snapshot[:2]
    expected_digest = expected_snapshot[2] if len(expected_snapshot) == 3 else None
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd: int | None = None
    try:
        fd = os.open(path, os.O_RDONLY | nofollow | BINARY_FLAG)
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != expected_identity
        ):
            raise ValueError(f"{path.name} changed during restore; refusing to restore")
        if not nofollow:
            current = path.lstat()
            if (
                stat.S_ISLNK(current.st_mode)
                or _is_reparse(current)
                or (current.st_dev, current.st_ino) != expected_identity
            ):
                raise ValueError(
                    f"{path.name} changed during restore; refusing to restore"
                )
        with os.fdopen(fd, "r", encoding="utf-8", newline="") as handle:
            fd = None
            contents = handle.read()
        if not nofollow:
            current = path.lstat()
            if (
                stat.S_ISLNK(current.st_mode)
                or _is_reparse(current)
                or (current.st_dev, current.st_ino) != expected_identity
            ):
                raise ValueError(
                    f"{path.name} changed during restore; refusing to restore"
                )
        if (
            expected_digest is not None
            and _restore_text_sha256(contents) != expected_digest
        ):
            raise ValueError(f"{path.name} changed during restore; refusing to restore")
        return contents
    finally:
        if fd is not None:
            os.close(fd)


def _assert_restore_evidence_unchanged(
    path: Path, expected_snapshot: _RestoreEvidenceSnapshot | None
) -> None:
    if expected_snapshot is not None:
        try:
            _read_restore_text(path, expected_snapshot)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(
                f"{path.name} changed during restore; refusing to clean up"
            ) from exc
        return
    try:
        current = path.lstat()
    except FileNotFoundError as exc:
        if expected_snapshot is None:
            return
        raise ValueError(f"{path.name} changed during restore; refusing to clean up") from exc
    if (
        expected_snapshot is None
        or stat.S_ISLNK(current.st_mode)
        or _is_reparse(current)
        or not stat.S_ISREG(current.st_mode)
    ):
        raise ValueError(f"{path.name} changed during restore; refusing to clean up")


def _assert_restore_evidence_set_unchanged(
    restore_identities: dict[Path, _RestoreEvidenceSnapshot | None]
) -> None:
    for path, expected_snapshot in restore_identities.items():
        _assert_restore_evidence_unchanged(path, expected_snapshot)


def _assert_restore_directory_ancestry_unchanged(
    path: Path,
    expected_directories: dict[Path, _RestoreIdentity | None],
) -> None:
    for directory, expected_identity in expected_directories.items():
        if directory != path and directory not in path.parents:
            continue
        try:
            current = directory.lstat()
        except FileNotFoundError as exc:
            if expected_identity is None:
                continue
            raise ValueError(
                f"{directory.name} directory changed during restore; "
                "refusing to restore"
            ) from exc
        if (
            expected_identity is None
            or _is_reparse(current)
            or not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != expected_identity
        ):
            raise ValueError(
                f"{directory.name} directory changed during restore; "
                "refusing to restore"
            )


def _portable_target_state(
    path: Path,
) -> tuple[_RestoreEvidenceSnapshot | None, str | None, int | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None, None, None
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
        raise ValueError("refusing to replace a symbolic link")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("refusing to replace a non-regular file")
    identity = (metadata.st_dev, metadata.st_ino)
    contents = _read_restore_text(path, identity)
    return (
        metadata.st_dev,
        metadata.st_ino,
        _restore_text_sha256(contents),
    ), contents, stat.S_IMODE(metadata.st_mode)


def _write_targets_portably(
    changes: list[tuple[Path, str]],
    *,
    expected_identities: dict[Path, _RestoreEvidenceSnapshot | None],
    expected_directories: dict[Path, _RestoreIdentity | None],
    preserve_earlier_writes_on_rollback_failure: bool = False,
) -> dict[Path, _RestoreEvidenceSnapshot | None]:
    current_identities = dict(expected_identities)
    previous_states: dict[Path, tuple[str | None, int | None]] = {}

    def assert_directories_unchanged(path: Path) -> None:
        _assert_restore_directory_ancestry_unchanged(
            path, expected_directories
        )

    _assert_restore_evidence_set_unchanged(current_identities)
    for path, _contents in changes:
        assert_directories_unchanged(path)
        snapshot, previous, previous_mode = _portable_target_state(path)
        if snapshot != current_identities.get(path):
            raise ValueError("refusing to replace a changed target")
        previous_states[path] = previous, previous_mode

    written: list[Path] = []
    post_write_snapshots: dict[Path, _RestoreEvidenceSnapshot] = {}
    try:
        for path, contents in changes:
            _assert_restore_evidence_set_unchanged(current_identities)
            assert_directories_unchanged(path)
            post_write = _atomic_write_text_portable(
                path,
                contents,
                expected_before=current_identities.get(path),
            )
            current_identities[path] = post_write
            post_write_snapshots[path] = post_write
            written.append(path)
            assert_directories_unchanged(path)
        _assert_restore_evidence_set_unchanged(current_identities)
        return dict(current_identities)
    except (OSError, UnicodeError, ValueError) as exc:
        rollback_failed = False
        for path in reversed(written):
            previous, previous_mode = previous_states[path]
            post_write = post_write_snapshots[path]
            try:
                assert_directories_unchanged(path)
                if previous is None:
                    _assert_restore_evidence_unchanged(path, post_write)
                    # Without directory-relative handles, deleting a newly
                    # created path cannot be bound to the verified inode.
                    # Preserve owned evidence for manual recovery instead of
                    # risking deletion of a concurrent replacement.
                    raise ValueError(
                        "portable rollback preserved newly created evidence"
                    )
                else:
                    restored = _atomic_write_text_portable(
                        path,
                        previous,
                        mode=previous_mode,
                        expected_before=post_write,
                    )
                    current_identities[path] = restored
                assert_directories_unchanged(path)
            except (OSError, UnicodeError, ValueError):
                rollback_failed = True
                if preserve_earlier_writes_on_rollback_failure:
                    break
        if rollback_failed:
            raise ValueError(
                "restore transaction rollback failed; manual review required"
            ) from exc
        raise


def _atomic_write_text_portable(
    path: Path,
    contents: str,
    *,
    mode: int | None = None,
    expected_before: _RestoreEvidenceSnapshot | None | object = _CLI_SNAPSHOT_UNSET,
) -> _RestoreEvidenceSnapshot:
    current_snapshot, _current_contents, preserved_mode = _portable_target_state(path)
    selected_expected = (
        current_snapshot
        if expected_before is _CLI_SNAPSHOT_UNSET
        else expected_before
    )
    if current_snapshot != selected_expected:
        raise ValueError("refusing to replace a changed target")

    create = selected_expected is None
    fd = _open_portable_exclusive_fd(path, create=create)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("refusing to replace a non-regular file")
        opened_identity = (opened.st_dev, opened.st_ino)
        current_path = path.lstat()
        if (
            stat.S_ISLNK(current_path.st_mode)
            or _is_reparse(current_path)
            or not stat.S_ISREG(current_path.st_mode)
            or (current_path.st_dev, current_path.st_ino) != opened_identity
        ):
            raise ValueError("refusing to replace a changed target")

        if create:
            opened_snapshot = None
            original_payload = None
        else:
            os.lseek(fd, 0, os.SEEK_SET)
            original_bytes = bytearray()
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                original_bytes.extend(chunk)
            original_payload = bytes(original_bytes)
            original_text = original_payload.decode("utf-8")
            opened_snapshot = (
                opened.st_dev,
                opened.st_ino,
                _restore_text_sha256(original_text),
            )
        if opened_snapshot != selected_expected:
            raise ValueError("refusing to replace a changed target")

        encoded = contents.encode("utf-8")
        selected_mode = mode if mode is not None else preserved_mode
        try:
            _write_portable_exclusive_bytes(fd, encoded, selected_mode)
            committed = _verify_portable_exclusive_bytes(
                fd, path, opened_identity, encoded
            )
        except (OSError, UnicodeError, ValueError) as exc:
            if original_payload is None:
                raise ValueError(
                    "restore transaction rollback failed; manual review required"
                ) from exc
            try:
                _write_portable_exclusive_bytes(
                    fd, original_payload, preserved_mode
                )
                _verify_portable_exclusive_bytes(
                    fd, path, opened_identity, original_payload
                )
            except (OSError, UnicodeError, ValueError) as rollback_exc:
                raise ValueError(
                    "restore transaction rollback failed; manual review required"
                ) from rollback_exc
            raise
        return (
            committed.st_dev,
            committed.st_ino,
            _restore_text_sha256(contents),
        )
    finally:
        os.close(fd)


def _write_portable_exclusive_bytes(
    fd: int, payload: bytes, mode: int | None
) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    written = 0
    while written < len(payload):
        count = os.write(fd, payload[written:])
        if count <= 0:
            raise OSError("portable write made no progress")
        written += count
    os.ftruncate(fd, len(payload))
    if mode is not None and hasattr(os, "fchmod"):
        os.fchmod(fd, mode)
    os.fsync(fd)


def _verify_portable_exclusive_bytes(
    fd: int,
    path: Path,
    expected_identity: _RestoreIdentity,
    expected_payload: bytes,
) -> os.stat_result:
    committed = os.fstat(fd)
    if (
        not stat.S_ISREG(committed.st_mode)
        or (committed.st_dev, committed.st_ino) != expected_identity
    ):
        raise ValueError("portable write lost target ownership")
    os.lseek(fd, 0, os.SEEK_SET)
    committed_bytes = bytearray()
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        committed_bytes.extend(chunk)
    if bytes(committed_bytes) != expected_payload:
        raise ValueError("portable write verification failed")
    committed_path = path.lstat()
    if (
        stat.S_ISLNK(committed_path.st_mode)
        or _is_reparse(committed_path)
        or not stat.S_ISREG(committed_path.st_mode)
        or (committed_path.st_dev, committed_path.st_ino) != expected_identity
    ):
        raise ValueError("portable write lost target ownership")
    return committed


def _open_portable_exclusive_fd(path: Path, *, create: bool) -> int:
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        # These exports exist only in CPython's Windows modules. Host stubs on
        # the non-Windows quality runner omit them; the platform gate is real.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        generic_read = 0x80000000
        generic_write = 0x40000000
        create_new = 1
        open_existing = 3
        file_attribute_normal = 0x00000080
        file_flag_open_reparse_point = 0x00200000
        handle = create_file(
            str(path),
            generic_read | generic_write,
            0,
            None,
            create_new if create else open_existing,
            file_attribute_normal | file_flag_open_reparse_point,
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        try:
            return cast(int, msvcrt.open_osfhandle(  # type: ignore[attr-defined]
                handle,
                os.O_RDWR | BINARY_FLAG,
            ))
        except Exception:
            close_handle(handle)
            raise

    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | BINARY_FLAG
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        try:
            import fcntl
        except ImportError:
            return fd
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_portable_delete_fd(path: Path) -> int:
    """Open a verified Windows file exclusively for read-and-delete."""
    if os.name != "nt":
        raise ValueError("portable deletion requires Windows")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    generic_read = 0x80000000
    delete = 0x00010000
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    handle = create_file(
        str(path),
        generic_read | delete,
        0,
        None,
        open_existing,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    try:
        return cast(
            int,
            msvcrt.open_osfhandle(  # type: ignore[attr-defined]
                handle,
                os.O_RDONLY | BINARY_FLAG,
            ),
        )
    except Exception:
        close_handle(handle)
        raise


def _set_portable_delete(fd: int, enabled: bool) -> None:
    if os.name != "nt":
        raise ValueError("portable deletion requires Windows")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    set_info = kernel32.SetFileInformationByHandle
    set_info.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    set_info.restype = wintypes.BOOL
    disposition = _FileDispositionInfo(DeleteFile=bool(enabled))
    handle = msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined]
    if not set_info(
        handle,
        4,  # FileDispositionInfo
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]


def _remove_targets_portably(
    paths: list[Path],
    *,
    expected_identities: dict[Path, _RestoreEvidenceSnapshot | None],
    expected_directories: dict[Path, _RestoreIdentity | None],
    recovery_contents: dict[Path, str] | None = None,
) -> None:
    """Delete only the exact verified files on Windows, with all handles bound first.

    Every target is opened without sharing and re-read before any disposition is
    set. The handle owns both the identity check and deletion, so a path swap
    cannot redirect cleanup to a replacement file. If closing a bound handle
    fails after an earlier target was deleted, ``recovery_contents`` restores the
    exact family evidence before the error is returned.
    """
    if os.name != "nt":
        raise ValueError("portable deletion requires Windows")
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("portable deletion targets are invalid")
    if recovery_contents is not None and set(recovery_contents) != set(paths):
        raise ValueError("portable deletion recovery evidence is incomplete")
    selected = {path: expected_identities.get(path) for path in paths}
    _assert_restore_evidence_set_unchanged(selected)
    descriptors: dict[Path, int] = {}
    marked: list[Path] = []
    failure: Exception | None = None
    try:
        for path in paths:
            _assert_restore_directory_ancestry_unchanged(path, expected_directories)
            expected = selected[path]
            if expected is None:
                try:
                    path.lstat()
                except FileNotFoundError:
                    continue
                raise ValueError("refusing to remove changed restore evidence")
            fd = _open_portable_delete_fd(path)
            descriptors[path] = fd
            opened = os.fstat(fd)
            current = path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or _is_reparse(current)
                or not stat.S_ISREG(current.st_mode)
                or (opened.st_dev, opened.st_ino) != expected[:2]
                or (current.st_dev, current.st_ino) != expected[:2]
            ):
                raise ValueError("refusing to remove changed restore evidence")
            os.lseek(fd, 0, os.SEEK_SET)
            payload = bytearray()
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                payload.extend(chunk)
            contents = bytes(payload).decode("utf-8")
            if _restore_text_sha256(contents) != expected[2]:
                raise ValueError("refusing to remove changed restore evidence")
        # Every target is now held with share mode 0. Re-opening them here
        # would fail with a Windows sharing violation; verify the bound
        # descriptors and directory identities without acquiring new handles.
        for path, fd in descriptors.items():
            _assert_restore_directory_ancestry_unchanged(path, expected_directories)
            expected = selected[path]
            if expected is None:
                raise ValueError("refusing to remove changed restore evidence")
            opened = os.fstat(fd)
            current = path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or stat.S_ISLNK(current.st_mode)
                or _is_reparse(current)
                or not stat.S_ISREG(current.st_mode)
                or (opened.st_dev, opened.st_ino) != expected[:2]
                or (current.st_dev, current.st_ino) != expected[:2]
            ):
                raise ValueError("refusing to remove changed restore evidence")
        for path in paths:
            _assert_restore_directory_ancestry_unchanged(path, expected_directories)
            delete_fd = descriptors.get(path)
            if delete_fd is None:
                continue
            _set_portable_delete(delete_fd, True)
            marked.append(path)
    except (OSError, UnicodeError, ValueError) as exc:
        failure = exc

    unmark_error: OSError | None = None
    if failure is not None:
        for path in reversed(marked):
            delete_fd = descriptors.get(path)
            if delete_fd is None:
                continue
            try:
                _set_portable_delete(delete_fd, False)
            except OSError as exc:
                unmark_error = unmark_error or exc

    close_error: OSError | None = None
    for path, fd in list(descriptors.items()):
        try:
            os.close(fd)
        except OSError as exc:
            close_error = close_error or exc
            try:
                _set_portable_delete(fd, False)
            except OSError as unmark_exc:
                unmark_error = unmark_error or unmark_exc
            try:
                os.close(fd)
            except OSError:
                pass
        descriptors.pop(path, None)

    if failure is not None or unmark_error is not None or close_error is not None:
        repair_error = _restore_portable_evidence(
            paths, selected, recovery_contents, expected_directories
        )
        if repair_error is not None:
            raise ValueError(
                "restore cleanup rollback failed; manual review required"
            ) from repair_error
        if failure is not None:
            raise failure
        raise ValueError(
            "portable evidence deletion failed; original evidence restored"
        ) from (unmark_error or close_error)

    for path in paths:
        _assert_restore_directory_ancestry_unchanged(path, expected_directories)
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise ValueError("portable deletion left changed evidence")


def _restore_portable_evidence(
    paths: list[Path],
    expected: dict[Path, _RestoreEvidenceSnapshot | None],
    recovery_contents: dict[Path, str] | None,
    expected_directories: dict[Path, _RestoreIdentity | None],
) -> Exception | None:
    """Restore exact evidence after a partial portable deletion."""
    first_error: Exception | None = None
    for path in paths:
        wanted = expected[path]
        try:
            _assert_restore_directory_ancestry_unchanged(path, expected_directories)
            snapshot, _contents, _mode = _portable_target_state(path)
        except (OSError, UnicodeError, ValueError) as exc:
            first_error = first_error or exc
            continue
        if snapshot is not None:
            if wanted is None or snapshot[2] != wanted[2]:
                first_error = first_error or ValueError(
                    "portable evidence changed during cleanup"
                )
            continue
        if wanted is None:
            continue
        if recovery_contents is None or path not in recovery_contents:
            first_error = first_error or ValueError(
                "portable evidence recovery content is missing"
            )
            continue
        try:
            _assert_restore_directory_ancestry_unchanged(path, expected_directories)
            _atomic_write_text_portable(
                path,
                recovery_contents[path],
                expected_before=None,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            first_error = first_error or exc
    return first_error
