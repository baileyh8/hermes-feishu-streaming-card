"""Immutable external PM wheel sources; retained while generation references are unknown.

POSIX walks use directory descriptors and never follow links. Portable platforms
retain lstat/identity checks, without claiming directory-descriptor guarantees.
Windows uses inherited filesystem ACLs; chmod/stat do not prove a private ACL.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import os
import re
from pathlib import Path
import stat
import tempfile
from typing import Any, Callable, Iterator

from . import posix_transaction as io

_REFUSAL = 'PM external wheel ownership cannot be verified'
_LEGACY_REFUSAL = 'legacy PM wheel store ownership cannot be verified'
_POSIX_PERMISSIONS = os.name != 'nt'
_WHEEL_NAME = re.compile(
    r'hermes_feishu_streaming_card-[A-Za-z0-9_.!+]+(?:-[0-9][A-Za-z0-9_]*)?'
    r'-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+[.]whl'
)


def is_reparse(metadata: os.stat_result) -> bool:
    """Junctions/mount points can report directory mode instead of symlink mode."""
    return bool(getattr(metadata, 'st_file_attributes', 0)
                & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


@contextmanager
def _directory(path: Path, home: Path, *, create: bool, legacy: bool = False,
               expected_identities: dict[Path, tuple[int, int]] | None = None) -> Iterator[int | None]:
    identities: dict[Path, tuple[int, int]] = {}
    descriptors: list[int] = []
    try:
        for current in reversed((path, *path.parents)):
            managed = current != home and home in current.parents
            parent_fd = descriptors[-1] if descriptors else None
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                if not create or not managed:
                    raise ValueError(_REFUSAL) from None
                if io.DIRFD_BINDING_SUPPORTED and parent_fd is not None:
                    os.mkdir(current.name, 0o700, dir_fd=parent_fd)
                else:
                    current.mkdir(mode=0o700)
                metadata = current.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or is_reparse(metadata):
                raise ValueError(_REFUSAL)
            if expected_identities is not None and current in expected_identities:
                if (metadata.st_dev, metadata.st_ino) != expected_identities[current]:
                    raise ValueError(_REFUSAL)
            if managed and _POSIX_PERMISSIONS and hasattr(os, 'getuid') and metadata.st_uid != os.getuid():
                raise ValueError(_REFUSAL)
            if managed and _POSIX_PERMISSIONS:
                mode = stat.S_IMODE(metadata.st_mode)
                # PR458 created the flat wheel-store with mkdir() and a normal
                # umask, so 0755 is part of that exact historical layout. Only
                # the exact legacy validator may open that mode; schema-2
                # publication and validation require the parent at 0700.
                allow_group_other_read = legacy and current.name == 'wheel-store'
                refused = mode & (0o022 if allow_group_other_read else 0o077)
                if refused:
                    raise ValueError(
                        _LEGACY_REFUSAL if legacy else 'PM wheel store directory must be private'
                    )
            identities[current] = metadata.st_dev, metadata.st_ino
            if io.DIRFD_BINDING_SUPPORTED:
                descriptor = os.open(str(current) if parent_fd is None else current.name,
                                     os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     **({} if parent_fd is None else {'dir_fd': parent_fd}))
                descriptors.append(descriptor)
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != identities[current]:
                    raise ValueError(_REFUSAL)
        yield descriptors[-1] if descriptors else None
        for current, identity in identities.items():
            if is_reparse(current.lstat()):
                raise ValueError(_REFUSAL)
            io.assert_directory_snapshot(current, identity, ValueError, _REFUSAL,
                                         allow_original_absence=False)
    except OSError as exc:
        raise ValueError(_REFUSAL) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read(path: Path, parent_fd: int | None, *, legacy: bool = False) -> bytes:
    if is_reparse(path.lstat()):
        raise ValueError(_REFUSAL)
    binding = None
    try:
        if io.DIRFD_BINDING_SUPPORTED:
            if parent_fd is None:
                raise ValueError(_REFUSAL)
            metadata = os.fstat(parent_fd)
            binding = io.BoundTarget(path, os.dup(parent_fd),
                                     (metadata.st_dev, metadata.st_ino), ValueError, _REFUSAL)
        _identity, payload, mode = io.read_file_state(
            path.name if binding else path, parent_fd=binding.parent_fd if binding else None,
            missing_allowed=False, refused=ValueError, non_regular_refusal=_REFUSAL,
            changed_refusal=_REFUSAL, wrap_read_error=True)
        if is_reparse(path.lstat()):
            raise ValueError(_REFUSAL)
        if legacy:
            metadata = path.lstat()
            if (_POSIX_PERMISSIONS and hasattr(os, 'getuid')
                    and metadata.st_uid != os.getuid()):
                raise ValueError(_LEGACY_REFUSAL)
            if mode is None or (_POSIX_PERMISSIONS and mode & 0o022) or payload is None:
                raise ValueError(_LEGACY_REFUSAL)
        elif mode is None or (_POSIX_PERMISSIONS and mode & 0o077) or payload is None:
            raise ValueError(_REFUSAL)
        if binding:
            io.assert_path_parent(binding, ValueError, _REFUSAL)
        return payload
    finally:
        if binding:
            binding.close()


def validate(home: Path, reference: Any) -> Path:
    if (not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}
            or not isinstance(reference['path'], str) or not isinstance(reference['sha256'], str)):
        raise ValueError(_REFUSAL)
    digest = reference['sha256']
    path = Path(reference['path'])
    if (len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)
            or path.parent != home / '.hermes_feishu_card' / 'wheel-store' / digest
            or _WHEEL_NAME.fullmatch(path.name) is None):
        raise ValueError(_REFUSAL)
    with _directory(path.parent, home, create=False) as parent_fd:
        if sha256(_read(path, parent_fd)).hexdigest() != digest:
            raise ValueError(_REFUSAL)
    return path


def _legacy_path(home: Path, filename: str, digest: str) -> Path:
    if (not isinstance(filename, str) or Path(filename).name != filename
            or _WHEEL_NAME.fullmatch(filename) is None):
        raise ValueError(_LEGACY_REFUSAL)
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError(_LEGACY_REFUSAL)
    path = home / '.hermes_feishu_card' / 'wheel-store' / filename
    if path.parent != home / '.hermes_feishu_card' / 'wheel-store':
        raise ValueError(_LEGACY_REFUSAL)
    return path


def prove_legacy_parent(home: Path, filename: str, digest: str) -> tuple[int, int, int]:
    """Prove the exact flat wheel through the currently bound parent directory."""
    path = _legacy_path(home, filename, digest)
    with _directory(path.parent, home, create=False, legacy=True) as parent_fd:
        metadata = os.fstat(parent_fd) if parent_fd is not None else path.parent.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or is_reparse(metadata):
            raise ValueError(_LEGACY_REFUSAL)
        if _POSIX_PERMISSIONS and hasattr(os, 'getuid') and metadata.st_uid != os.getuid():
            raise ValueError(_LEGACY_REFUSAL)
        if _POSIX_PERMISSIONS and stat.S_IMODE(metadata.st_mode) & 0o022:
            raise ValueError(_LEGACY_REFUSAL)
        if sha256(_read(path, parent_fd, legacy=True)).hexdigest() != digest:
            raise ValueError(_LEGACY_REFUSAL)
        return metadata.st_dev, metadata.st_ino, stat.S_IMODE(metadata.st_mode)


def validate_legacy(home: Path, filename: str, digest: str) -> Path:
    """Validate only the exact PR458 flat ``wheel-store/<name>.whl`` layout."""
    path = _legacy_path(home, filename, digest)
    prove_legacy_parent(home, filename, digest)
    return path


def tighten_legacy_parent(home: Path, *, expected_identity: tuple[int, int],
                          wheel_name: str, digest: str,
                          reprove: Callable[[], bool]) -> tuple[int, int, int]:
    """Tighten the pre-proven PR458 parent after same-binding re-proof.

    The caller supplies the identity captured before the wheel build. This
    function opens the current path, requires that identity, re-reads the flat
    wheel through the same directory descriptor, and calls ``reprove`` before
    and after chmod. A renamed/replaced path or changed digest refuses before
    changing the replacement's mode.
    """
    path = _legacy_path(home, wheel_name, digest)
    store = path.parent
    with _directory(store, home, create=False, legacy=True,
                    expected_identities={store: expected_identity}) as parent_fd:
        metadata = os.fstat(parent_fd) if parent_fd is not None else store.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or is_reparse(metadata)
                or (metadata.st_dev, metadata.st_ino) != expected_identity):
            raise ValueError(_LEGACY_REFUSAL)
        if _POSIX_PERMISSIONS and hasattr(os, 'getuid') and metadata.st_uid != os.getuid():
            raise ValueError(_LEGACY_REFUSAL)
        previous_mode = stat.S_IMODE(metadata.st_mode)
        if _POSIX_PERMISSIONS and previous_mode & 0o022:
            raise ValueError(_LEGACY_REFUSAL)
        if not reprove():
            raise ValueError(_LEGACY_REFUSAL)
        if sha256(_read(path, parent_fd, legacy=True)).hexdigest() != digest:
            raise ValueError(_LEGACY_REFUSAL)
        if not reprove():
            raise ValueError(_LEGACY_REFUSAL)
        current = store.lstat()
        if (current.st_dev, current.st_ino) != expected_identity:
            raise ValueError(_LEGACY_REFUSAL)
        try:
            if _POSIX_PERMISSIONS and previous_mode != 0o700:
                if parent_fd is not None:
                    os.fchmod(parent_fd, 0o700)
                else:
                    store.chmod(0o700)
            after = os.fstat(parent_fd) if parent_fd is not None else store.lstat()
            if (after.st_dev, after.st_ino) != expected_identity:
                raise ValueError(_LEGACY_REFUSAL)
            if _POSIX_PERMISSIONS and stat.S_IMODE(after.st_mode) != 0o700:
                raise ValueError(_LEGACY_REFUSAL)
            if not reprove():
                raise ValueError(_LEGACY_REFUSAL)
            current = store.lstat()
            if (current.st_dev, current.st_ino) != expected_identity:
                raise ValueError(_LEGACY_REFUSAL)
        except BaseException:
            if _POSIX_PERMISSIONS and previous_mode != 0o700:
                try:
                    if parent_fd is not None:
                        os.fchmod(parent_fd, previous_mode)
                    else:
                        store.chmod(previous_mode)
                except OSError:
                    pass
            raise
        return expected_identity[0], expected_identity[1], previous_mode


def restore_legacy_parent(home: Path, snapshot: tuple[int, int, int]) -> None:
    """Best-effort restore of the proven parent mode on a failed migration."""
    path = home / '.hermes_feishu_card' / 'wheel-store'
    with _directory(path, home, create=False, legacy=True) as parent_fd:
        if parent_fd is not None:
            before = os.fstat(parent_fd)
            if (before.st_dev, before.st_ino) != snapshot[:2]:
                raise ValueError(_LEGACY_REFUSAL)
            if _POSIX_PERMISSIONS and hasattr(os, 'getuid') and before.st_uid != os.getuid():
                raise ValueError(_LEGACY_REFUSAL)
            if _POSIX_PERMISSIONS and stat.S_IMODE(before.st_mode) != snapshot[2]:
                os.fchmod(parent_fd, snapshot[2])
            after = os.fstat(parent_fd)
        else:
            before = path.lstat()
            if ((before.st_dev, before.st_ino) != snapshot[:2]
                    or not stat.S_ISDIR(before.st_mode) or is_reparse(before)):
                raise ValueError(_LEGACY_REFUSAL)
            if _POSIX_PERMISSIONS and hasattr(os, 'getuid') and before.st_uid != os.getuid():
                raise ValueError(_LEGACY_REFUSAL)
            if _POSIX_PERMISSIONS and stat.S_IMODE(before.st_mode) != snapshot[2]:
                path.chmod(snapshot[2])
            after = path.lstat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError(_LEGACY_REFUSAL)
        if _POSIX_PERMISSIONS and stat.S_IMODE(after.st_mode) != snapshot[2]:
            raise ValueError(_LEGACY_REFUSAL)


def publish(home: Path, filename: str, payload: bytes, *,
            expected_parent_identity: tuple[int, int] | None = None) -> dict[str, str]:
    if Path(filename).name != filename or _WHEEL_NAME.fullmatch(filename) is None:
        raise ValueError(_REFUSAL)
    digest = sha256(payload).hexdigest()
    path = home / '.hermes_feishu_card' / 'wheel-store' / digest / filename
    reference = {'path': str(path), 'sha256': digest}
    expected = ({path.parent.parent: expected_parent_identity}
                if expected_parent_identity is not None else None)
    with _directory(path.parent, home, create=True,
                    expected_identities=expected) as parent_fd:
        ancestry = {entry: io.read_directory_identity(entry, ValueError, _REFUSAL)
                    for entry in (path.parent, *path.parent.parents)}
        def check_ancestry() -> None:
            for entry, identity in ancestry.items():
                if is_reparse(entry.lstat()):
                    raise ValueError(_REFUSAL)
                io.assert_directory_snapshot(entry, identity, ValueError, _REFUSAL,
                                             allow_original_absence=False)
        try:
            path.lstat()
        except FileNotFoundError:
            binding = None
            staged = None
            try:
                if io.DIRFD_BINDING_SUPPORTED:
                    if parent_fd is None:
                        raise ValueError(_REFUSAL)
                    metadata = os.fstat(parent_fd)
                    binding = io.BoundTarget(path, os.dup(parent_fd),
                                             (metadata.st_dev, metadata.st_ino), ValueError, _REFUSAL)
                    staged = io.stage_file(path, payload, binding=binding, mode=0o600,
                                           durable_text=False, refused=ValueError,
                                           staging_refusal=_REFUSAL, target_refusal=_REFUSAL,
                                           stage_refusal=_REFUSAL)
                    descriptor = os.open(staged.basename, os.O_RDONLY | os.O_NOFOLLOW,
                                         dir_fd=binding.parent_fd)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    staged.assert_owned()
                    check_ancestry()
                    io.assert_path_parent(binding, ValueError, _REFUSAL)
                    # link is exclusive: another publisher or symlink cannot be overwritten.
                    os.link(staged.basename, path.name, src_dir_fd=binding.parent_fd,
                            dst_dir_fd=binding.parent_fd, follow_symlinks=False)
                else:
                    # Portable path cannot close the final pathname race. It still
                    # stages complete durable bytes and never overwrites a final.
                    check_ancestry()
                    descriptor, temporary = tempfile.mkstemp(prefix='.hfc-wheel-', dir=path.parent)
                    stage = Path(temporary)
                    metadata = os.fstat(descriptor)
                    identity = metadata.st_dev, metadata.st_ino
                    try:
                        with os.fdopen(descriptor, 'wb') as handle:
                            handle.write(payload)
                            handle.flush()
                            os.fsync(handle.fileno())
                        stage.chmod(0o600)
                        check_ancestry()
                        if is_reparse(stage.lstat()):
                            raise ValueError(_REFUSAL)
                        snapshot = io.read_path_snapshot(stage, ValueError, _REFUSAL)
                        if snapshot != (*identity, digest):
                            raise ValueError(_REFUSAL)
                        os.link(stage, path)
                        check_ancestry()
                    finally:
                        # Unknown or replaced stages are evidence, not garbage.
                        check_ancestry()
                        if (not is_reparse(stage.lstat())
                                and io.read_path_snapshot(stage, ValueError, _REFUSAL) == (*identity, digest)):
                            stage.unlink()
            finally:
                if staged:
                    staged.cleanup()
                if binding:
                    binding.close()
        validate(home, reference)
        if parent_fd is not None:
            os.fsync(parent_fd)
    return reference
