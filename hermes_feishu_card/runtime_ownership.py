"""Bounded authenticated runtime observations, separate from Gateway authority."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Any


def runtime_target_identity(root: str | Path) -> str:
    canonical = os.path.normcase(str(Path(root).expanduser().resolve()))
    return hashlib.sha256(b'hfc-runtime-target-v1\0' + os.fsencode(canonical)).hexdigest()


@dataclass
class Observation:
    event: Any
    received_at: float
    gateway_hello_seen: bool = False
    closed: bool = False
    retired: bool = False


class RuntimeOwners:
    """Called under the supervisor lock; never infer authority from observer counts."""
    def __init__(self, target: str | None, stale_seconds: float, capacity: int = 64):
        self.target = target
        self.stale_seconds = stale_seconds
        self.capacity = capacity
        self.records: dict[str, Observation] = {}
        self.owner_id = ''
        self.overflow = False

    def record(self, event: Any, now: float) -> bool:
        if event.runtime_role == 'gateway' and self.target and event.target_identity != self.target:
            return False
        old = self.records.get(event.runtime_id)
        if old and (old.closed or event.sequence <= old.event.sequence or event.created_at < old.event.created_at):
            return False
        # Closed senders retain replay tombstones beyond the transport proof age.
        for key, row in list(self.records.items()):
            if row.closed and now - row.received_at > self.stale_seconds and key != self.owner_id:
                del self.records[key]
        if old is None and len(self.records) >= self.capacity:
            self.overflow = True
            return False
        self.records[event.runtime_id] = Observation(
            event, now,
            gateway_hello_seen=bool((old and old.gateway_hello_seen) or
                (event.runtime_role == 'gateway' and event.event == 'runtime.hello')),
            closed=event.event == 'runtime.goodbye',
            retired=bool(old and old.retired),
        )
        return True

    def view(self, now: float) -> dict[str, Any]:
        live_gateways = {
            key: row for key, row in self.records.items()
            if not row.closed and now - row.received_at <= self.stale_seconds
            and row.event.runtime_role == 'gateway' and row.event.drain_home_verified is True
        }
        candidates = {key: row for key, row in live_gateways.items() if not row.retired}
        current = self.records.get(self.owner_id)
        if self.owner_id not in candidates and len(candidates) == 1:
            key = next(iter(candidates))
            if current and key != self.owner_id:
                current.retired = True
            self.owner_id = key
            current = candidates[key]
        conflict = len(live_gateways) > 1
        owner_valid = self.owner_id in candidates and not conflict and not self.overflow
        total = 0
        complete = not self.overflow and not conflict
        observer_count = 0
        observer_active = 0
        observer_unknown = 0
        for key, row in self.records.items():
            if row.closed:
                continue
            event = row.event
            active = event.active_sessions
            known = (event.runtime_role in {'gateway', 'observer'} and type(active) is int
                and event.active_work_count_complete is True)
            fresh = now - row.received_at <= self.stale_seconds
            if type(active) is int:
                total = min(1_000_000, total + active)
            if key == self.owner_id:
                complete = complete and known and fresh and owner_valid
                continue
            observer_count += 1
            if type(active) is int:
                observer_active = min(1_000_000, observer_active + active)
            # A silent observer is not evidence that its task stopped. Only an
            # authenticated goodbye (or a fresh complete observation) resolves it.
            if not known or not fresh:
                observer_unknown += 1
                complete = False
        return dict(owner=current, owner_valid=owner_valid, owner_conflict=conflict,
            active_sessions=total, activity_complete=bool(current) and complete,
            observer_count=observer_count, observer_active_sessions=observer_active,
            observer_unknown_count=observer_unknown, owner_capacity_exceeded=self.overflow)
