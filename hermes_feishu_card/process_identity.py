"""Read-only, same-boot/PID-namespace evidence for authenticated runtime exit."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def _digest(value: str) -> str:
    return hashlib.sha256(('hfc-process-v1\0' + value).encode()).hexdigest()


def host_identity() -> str | None:
    try:
        if sys.platform == 'darwin':
            value = subprocess.check_output(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'],
                stderr=subprocess.DEVNULL, text=True, timeout=1).strip()
        elif sys.platform.startswith('linux'):
            value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            value += ':' + os.readlink('/proc/self/ns/pid')
        else:
            return None
        return _digest(value) if value else None
    except (OSError, subprocess.SubprocessError):
        return None


def _start_identity(pid: int) -> str | None:
    try:
        if sys.platform.startswith('linux'):
            fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            return _digest(fields[19])
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'lstart='],
            capture_output=True, text=True, timeout=.2,
            env={**os.environ, 'LC_ALL': 'C', 'TZ': 'UTC'})
        value = result.stdout.strip()
        return _digest(value) if result.returncode == 0 and value else None
    except (OSError, IndexError, subprocess.SubprocessError):
        return None


def current_process_identity() -> dict[str, Any] | None:
    host = host_identity()
    start = _start_identity(os.getpid()) if host else None
    if host and start:
        return {'pid': os.getpid(), 'host': host, 'start': start}
    return None


class LocalProcessMonitor:
    """Unknown evidence never means exited; cache only bounds repeated ps probes."""
    def __init__(self):
        self._host: str | None = None
        self._host_checked = False
        self._cache: OrderedDict[tuple[int, str], tuple[float, str]] = OrderedDict()

    def state(self, identity: dict[str, Any] | None, now: float, *, check_start: bool = False) -> str:
        if identity is None:
            return 'unknown'
        if not self._host_checked:
            self._host = host_identity()
            self._host_checked = True
        if self._host is None or identity['host'] != self._host:
            return 'unknown'
        pid = identity['pid']
        # PID reuse is distinguished by the birth marker below, never by PID alone.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return 'exited'
        except OSError:
            return 'unknown'
        if not check_start:
            # A fresh authenticated heartbeat is already liveness evidence.
            # Only stale, still-existing PIDs need an incarnation probe.
            return 'alive'
        key = (pid, identity['start'])
        cached = self._cache.get(key)
        if cached is not None and 0 <= now - cached[0] < 15:
            return cached[1]
        start = _start_identity(pid)
        result = 'unknown' if start is None else 'alive' if start == identity['start'] else 'exited'
        self._cache[key] = (now, result)
        self._cache.move_to_end(key)
        while len(self._cache) > 64:
            self._cache.popitem(last=False)
        return result
