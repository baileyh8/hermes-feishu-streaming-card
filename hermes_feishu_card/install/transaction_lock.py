"""Shared checkout lock used by installer, recovery and legacy transactions."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, Iterator

from .errors import RecoveryRefused
from .file_transaction import BINARY_FLAG

_LOCK_NAME = ".hermes_feishu_card_recovery.lock"
_LOCK_TIMEOUT_SECONDS = 10.0
_PROCESS_LOCKS: Dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


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
        flags = os.O_RDWR | os.O_CREAT | BINARY_FLAG
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



def _acquire_os_lock(handle: Any, deadline: float) -> None:
    while True:
        try:
            if os.name == "nt":
                import msvcrt

                windows_lock: Any = msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                windows_lock.locking(handle.fileno(), windows_lock.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if not _is_lock_contention(exc):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Keep the existing implicit OS-error context on timeout.
                raise RecoveryRefused("timed out waiting for recovery lock")  # noqa: B904
            time.sleep(min(0.05, remaining))


def _is_lock_contention(exc: OSError) -> bool:
    contention_errors = {errno.EACCES, errno.EAGAIN}
    if os.name == "nt":
        contention_errors.add(errno.EDEADLK)
    return exc.errno in contention_errors


def _release_os_lock(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        windows_lock: Any = msvcrt
        handle.seek(0)
        windows_lock.locking(handle.fileno(), windows_lock.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
