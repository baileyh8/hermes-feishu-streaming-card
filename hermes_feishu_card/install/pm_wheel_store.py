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
from typing import Any, Iterator

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


def _has_flat_legacy_wheel(path: Path) -> bool:
    """True only when the shared store still contains a PR458 flat wheel."""
    try:
        for entry in path.iterdir():
            if not entry.name.endswith('.whl'):
                continue
            metadata = entry.lstat()
            if stat.S_ISREG(metadata.st_mode) and not is_reparse(metadata):
                return True
    except OSError:
        return False
    return False


@contextmanager
def _directory(path: Path, home: Path, *, create: bool, legacy: bool = False) -> Iterator[int | None]:
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
            if managed and _POSIX_PERMISSIONS and hasattr(os, 'getuid') and metadata.st_uid != os.getuid():
                raise ValueError(_REFUSAL)
            if managed and _POSIX_PERMISSIONS:
                mode = stat.S_IMODE(metadata.st_mode)
                # PR458 created the flat wheel-store with mkdir() and a normal
                # umask, so 0755 is part of that exact historical layout. The
                # relaxation is limited to a shared parent that still contains
                # a flat legacy wheel; a fresh schema-2 store, or one without
                # that evidence, must stay 0700. Digest subdirectories are 0700.
                allow_group_other_read = (
                    current.name == 'wheel-store'
                    and (legacy or _has_flat_legacy_wheel(current))
                )
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


def validate_legacy(home: Path, filename: str, digest: str) -> Path:
    """Validate only the exact PR458 flat ``wheel-store/<name>.whl`` layout.

    The historical writer did not record a digest directory and created the
    flat store with the process umask. This accepts that one format while
    requiring the current user's ownership, no reparse point, no group/world
    write permission and the member owner's wheel digest. It is not a general
    external-file trust helper.
    """
    if (not isinstance(filename, str) or Path(filename).name != filename
            or _WHEEL_NAME.fullmatch(filename) is None):
        raise ValueError(_LEGACY_REFUSAL)
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError(_LEGACY_REFUSAL)
    path = home / '.hermes_feishu_card' / 'wheel-store' / filename
    if path.parent != home / '.hermes_feishu_card' / 'wheel-store':
        raise ValueError(_LEGACY_REFUSAL)
    with _directory(path.parent, home, create=False, legacy=True) as parent_fd:
        if sha256(_read(path, parent_fd, legacy=True)).hexdigest() != digest:
            raise ValueError(_LEGACY_REFUSAL)
    return path


def publish(home: Path, filename: str, payload: bytes) -> dict[str, str]:
    if Path(filename).name != filename or _WHEEL_NAME.fullmatch(filename) is None:
        raise ValueError(_REFUSAL)
    digest = sha256(payload).hexdigest()
    path = home / '.hermes_feishu_card' / 'wheel-store' / digest / filename
    reference = {'path': str(path), 'sha256': digest}
    with _directory(path.parent, home, create=True) as parent_fd:
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
