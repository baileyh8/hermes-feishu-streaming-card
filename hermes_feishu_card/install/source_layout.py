"""Fixed source-family identities; layout alone does not prove hook support."""

from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Mapping

DECOMPOSED_GATEWAY_TARGETS = (
    "gateway/run_background.py",
    "gateway/run_turn.py", "gateway/run_turn_persistence.py", "gateway/run_turn_prepare.py", "gateway/run_turn_hygiene.py", "gateway/run_turn_runner.py", "gateway/run_inbound.py",
    "gateway/run_busy.py", "gateway/run_startup.py", "gateway/run_notifications.py",
)
DECOMPOSED_TARGETS = (*DECOMPOSED_GATEWAY_TARGETS,
                      "cron/scheduler_delivery.py", "gateway/platforms/base.py")


DECOMPOSED_MANIFEST_VERSION = 4
DECOMPOSED_LAYOUT = "gateway-decomposed-v1"
MANIFEST_NAME = ".hermes_feishu_card_manifest"
BACKUP_SUFFIX = ".hermes_feishu_card.bak"
DECOMPOSED_SOURCE_TARGETS = (
    "gateway/run.py", *DECOMPOSED_GATEWAY_TARGETS,
    "cron/scheduler.py", "cron/scheduler_delivery.py", "gateway/platforms/base.py",
)

# Read-only contract peers. These are never managed, backed up, or patched.
DECOMPOSED_PEER_SOURCES = (
    "gateway/run_turn_setup_guard.py",
    "gateway/run_turn_runner_approval_transport.py",
    "gateway/platforms/base_delivery_ledger.py",
    "gateway/platforms/base_session_command.py",
)


def read_peer_contract_bytes(root: Path) -> dict[str, bytes | None]:
    """Capture the exact bytes or absence of each read-only contract peer."""
    sources: dict[str, bytes | None] = {}
    for name in DECOMPOSED_PEER_SOURCES:
        path = root / name
        try:
            for directory in path.relative_to(root).parents:
                metadata = (root / directory).lstat()
                if not stat.S_ISDIR(metadata.st_mode) or _reparse(metadata):
                    raise ValueError(f"{name}: unsafe peer source directory")
            before = path.lstat()
        except FileNotFoundError:
            sources[name] = None
            continue
        limit = 256 * 1024
        if not stat.S_ISREG(before.st_mode) or _reparse(before) or before.st_size > limit:
            raise ValueError(f"{name}: unsafe peer source")
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if not stat.S_ISREG(opened.st_mode) or _reparse(opened):
                raise ValueError(f"{name}: unsafe open peer source")
            raw = source.read(limit + 1)
            finished = os.fstat(source.fileno())
        after = path.lstat()
        identities = {_peer_signature(metadata) for metadata in (before, opened, finished, after)}
        if len(raw) > limit or len(identities) != 1:
            raise ValueError(f"{name}: peer source changed during read")
        sources[name] = raw
    return sources


def _reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _peer_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_size, metadata.st_mtime_ns)


def peer_contract_sources(snapshot: Mapping[str, bytes | None]) -> dict[str, str]:
    return {name: raw.decode("utf-8") for name in DECOMPOSED_PEER_SOURCES
            if (raw := snapshot[name]) is not None}


def read_peer_contract_sources(root: Path) -> dict[str, str]:
    return peer_contract_sources(read_peer_contract_bytes(root))
