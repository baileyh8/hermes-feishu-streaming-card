"""Process-lifetime HOME-scoped denial for a 9b904 legacy uninstall."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
from threading import Lock
from typing import Any


PROTOCOL = "hfc-9b904-home-uninstall-v1"
PLUGIN_KEY = "hermes-feishu-card"
DEPENDENCY_KEY = "hfc-runtime-dependency"
_CACHE_LIMIT = 128
_CACHE: dict[tuple[Path, Path], bool] = {}
_CACHE_LOCK = Lock()


def root_key(root: Path) -> str:
    return sha256(str(root.resolve()).encode('utf-8')).hexdigest()


def ticket_path(root: Path, home: Path) -> Path:
    return home / '.hermes_feishu_card' / 'install' / ('hfc-uninstall-' + root_key(root) + '.json')


def _file_identity(value: Any) -> bool:
    return (type(value) is list and len(value) == 3
            and all(type(item) is int and item >= 0 for item in value[:2])
            and type(value[2]) is str and re.fullmatch(r'[0-9a-f]{64}', value[2]) is not None)


def _directory_identity(value: Any) -> bool:
    return type(value) is list and len(value) == 2 and all(type(item) is int and item >= 0 for item in value)


def _graph(value: Any) -> bool:
    fields = {'generation', 'python_identity', 'member', 'owner_sha256', 'facts_path', 'facts',
              'facts_version', 'lock_path', 'lock', 'lock_version'}
    if type(value) is not dict or set(value) != fields:
        return False
    def version(item: Any) -> bool:
        return type(item) is list and len(item) == 5 and all(type(part) is int and part >= 0 for part in item)
    return (all(type(value[key]) is str and Path(value[key]).is_absolute()
                for key in ('generation', 'facts_path', 'lock_path'))
            and type(value['python_identity']) is str
            and re.fullmatch(r'sha256:[0-9a-f]{64}', value['python_identity']) is not None
            and _directory_identity(value['member']) and type(value['owner_sha256']) is str
            and re.fullmatch(r'[0-9a-f]{64}', value['owner_sha256']) is not None
            and _file_identity(value['facts']) and _file_identity(value['lock'])
            and version(value['facts_version']) and version(value['lock_version']))


def decode(raw: bytes, root: Path, home: Path) -> dict[str, Any]:
    """Strictly decode the ticket envelope; PM facts are re-proved by the CLI."""
    value = json.loads(raw)
    fields = {'protocol', 'phase', 'root_key', 'home', 'marker', 'backup', 'journal', 'before',
              'root_dir', 'home_dir', 'state_dir', 'after_sha256', 'pm_before', 'pm_after'}
    if (type(value) is not dict or set(value) != fields or value['protocol'] != PROTOCOL
            or value['phase'] not in ('planned', 'completed') or value['root_key'] != root_key(root)
            or value['home'] != str(home)
            or not all(_file_identity(value[key]) for key in ('marker', 'backup', 'journal', 'before'))
            or not all(_directory_identity(value[key]) for key in ('root_dir', 'home_dir', 'state_dir'))
            or type(value['after_sha256']) is not str
            or re.fullmatch(r'[0-9a-f]{64}', value['after_sha256']) is None
            or not _graph(value['pm_before'])
            or (value['phase'] == 'planned' and value['pm_after'] is not None)
            or (value['phase'] == 'completed' and not _graph(value['pm_after']))):
        raise ValueError('legacy HOME revocation evidence is invalid')
    return value


def _read_regular(path: Path, *, limit: int) -> bytes | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError('legacy HOME revocation evidence is unsafe')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('legacy HOME revocation evidence changed')
        chunks = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise ValueError('legacy HOME revocation evidence is unsafe')
        finished = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = path.lstat()
    def signature(item: os.stat_result) -> tuple[int, int, int, int, int]:
        return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns
    if signature(before) != signature(opened) or signature(opened) != signature(finished) or signature(finished) != signature(after):
        raise ValueError('legacy HOME revocation evidence changed')
    return bytes(chunks)


def inspect(root: Path, home: Path) -> str:
    """A missing ticket leaves old behavior; any uncertain owned ticket denies."""
    path = ticket_path(root, home)
    try:
        raw = _read_regular(path, limit=64 * 1024)
        if raw is None:
            return 'absent'
        for parent in (home, home / '.hermes_feishu_card', path.parent):
            metadata = parent.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                return 'review'
        ticket = decode(raw, root, home)
        if ticket['phase'] == 'planned':
            return 'pending'
        config = _read_regular(home / 'config.yaml', limit=2 * 1024 * 1024)
        if config is None or sha256(config).hexdigest() != ticket['after_sha256']:
            return 'review'
        from . import plugin_config_delta
        enabled, disabled, _entry = plugin_config_delta._parts(plugin_config_delta._parse(config))
        if PLUGIN_KEY in enabled or PLUGIN_KEY not in disabled or DEPENDENCY_KEY not in enabled:
            return 'review'
        return 'disabled'
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return 'review'


def _authoritative_binding() -> tuple[Path, Path] | None:
    from ..instance_binding import gateway_process_binding

    process = gateway_process_binding()
    constants = sys.modules.get('hermes_constants')
    resolver = getattr(constants, 'get_hermes_home', None)
    if process is None or not callable(resolver):
        return None
    try:
        current = Path(resolver()).expanduser()
        if not current.is_absolute():
            return None
        current = current.resolve(strict=True)
        if not current.is_dir():
            return None
        return process[0], current
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _cached_denial(root: Path, home: Path) -> bool:
    key = (root, home)
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]
        # Keep already-accepted HOME decisions for this process; evicting one
        # mid-turn could suppress its terminal event after a later uninstall.
        if len(_CACHE) >= _CACHE_LIMIT:
            return inspect(root, home) != 'absent'
        denied = inspect(root, home) != 'absent'
        _CACHE[key] = denied
        return denied


def hook_disabled() -> bool:
    """Cache one decision per authoritative Hermes HOME until process restart."""
    binding = _authoritative_binding()
    return binding is not None and _cached_denial(*binding)
