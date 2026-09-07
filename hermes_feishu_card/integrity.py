from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import threading
from typing import Any, Callable

from .install.detect import detect_hermes
from .install.integrity import execute_integrity_repair, plan_integrity_repair
from .runtime_control import RuntimeIntegrityFenceBinding, RuntimeIntegritySupervisor


_OPERATOR_INTEGRITY_MODES = frozenset({"safe", "notify", "off"})
_OPERATOR_INTEGRITY_STATUSES = frozenset(
    {
        "idle",
        "disabled",
        "ready",
        "manual_review_required",
        "restart_required",
        "repair_available",
        "deduplicated",
        "repaired",
    }
)
_OPERATOR_INTEGRITY_REASONS = frozenset(
    {
        "integrity_disabled",
        "runtime_ready",
        "control_auth_unavailable",
        "gateway_restart_required",
        "manual_review_required",
        "integrity_evidence_unavailable",
        "integrity_migration_required",
        "recovery_not_required",
        "recovery_evidence_not_executable",
        "decomposed_upgrade_requires_explicit_install",
        "git_history_not_descendant",
        "owned_backup_invalid",
        "owned_backup_mismatch",
        "git_target_modified",
        "git_root_invalid",
        "git_head_invalid",
        "git_history_unavailable",
        "git_evidence_unavailable",
        "git_blob_invalid",
        "source_not_regular",
        "source_outside_root",
        "source_read_failed",
        "unsupported_anchors",
        "integrity_evidence_invalid",
        "verified_git_upgrade",
        "integrity_repair_refused",
    }
)
_HERMES_TARGET_IDENTITY_DOMAIN = b"hfc-hermes-target-identity-v1\0"
_INTEGRITY_PLAN_FINGERPRINT_DOMAIN = b"hfc-integrity-plan-fingerprint-v1\0"
_UNAVAILABLE_PLAN_FINGERPRINT = "integrity-evidence-unavailable-v1"


def build_runtime_integrity_fence_binding(
    hermes_root: str | Path,
    plan_fingerprint: str,
) -> RuntimeIntegrityFenceBinding:
    """Bind a fence to an exact Hermes root identity and integrity plan."""
    fingerprint = str(plan_fingerprint or "")
    if (
        not fingerprint
        or len(fingerprint) > 256
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in fingerprint
        )
    ):
        raise ValueError("integrity plan fingerprint is invalid")
    try:
        resolved = Path(hermes_root).expanduser().resolve(strict=True)
        before = resolved.lstat()
        after = resolved.lstat()
    except (OSError, RuntimeError) as exc:
        raise ValueError("Hermes target identity is unavailable") from exc
    if (
        not stat.S_ISDIR(before.st_mode)
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
    ):
        raise ValueError("Hermes target identity is unavailable")
    target_evidence = json.dumps(
        {
            "device": int(before.st_dev),
            "inode": int(before.st_ino),
            "path": os.path.normcase(str(resolved)),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return RuntimeIntegrityFenceBinding(
        target_identity=hashlib.sha256(
            _HERMES_TARGET_IDENTITY_DOMAIN + target_evidence
        ).hexdigest(),
        plan_fingerprint=hashlib.sha256(
            _INTEGRITY_PLAN_FINGERPRINT_DOMAIN + fingerprint.encode("utf-8")
        ).hexdigest(),
    )


def sanitize_integrity_snapshot(snapshot: Any) -> dict[str, Any]:
    source = snapshot if isinstance(snapshot, dict) else {}
    mode = str(source.get("mode") or "")
    status = str(source.get("last_status") or "")
    reason = str(source.get("last_reason") or "")

    def bounded_count(name: str) -> int:
        value = source.get(name)
        if not isinstance(value, int) or isinstance(value, bool):
            return 0
        return min(max(value, 0), 1_000_000_000)

    return {
        "mode": mode if mode in _OPERATOR_INTEGRITY_MODES else "unknown",
        "last_status": (
            status if status in _OPERATOR_INTEGRITY_STATUSES else "unknown"
        ),
        "last_reason": (
            reason
            if reason in _OPERATOR_INTEGRITY_REASONS
            else ("none" if not reason else "unknown")
        ),
        "repair_attempts": bounded_count("repair_attempts"),
        "repair_successes": bounded_count("repair_successes"),
        "repair_refusals": bounded_count("repair_refusals"),
    }


class RuntimeIntegrityCoordinator:
    """Coordinate strict hook recovery without controlling Gateway lifecycle."""

    def __init__(
        self,
        *,
        mode: str,
        hermes_root: str | Path,
        supervisor: RuntimeIntegritySupervisor,
        detector: Callable[[str | Path], Any] = detect_hermes,
        planner: Callable[[Any], Any] = plan_integrity_repair,
        executor: Callable[..., Any] = execute_integrity_repair,
    ):
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in {"safe", "notify", "off"}:
            raise ValueError("integrity mode is invalid")
        self.mode = normalized_mode
        self.hermes_root = Path(hermes_root).expanduser()
        self.supervisor = supervisor
        self._detector = detector
        self._planner = planner
        self._executor = executor
        self._lock = threading.Lock()
        self._last_evidence = ""
        self._last_refusal_evidence = ""
        self._last_status = "idle"
        self._last_reason = ""
        self._repair_attempts = 0
        self._repair_successes = 0
        self._repair_refusals = 0

    def check_once(self) -> dict[str, Any]:
        with self._lock:
            return self._check_once_locked()

    def _check_once_locked(self) -> dict[str, Any]:
        if self.mode == "off":
            return self._record("disabled", "integrity_disabled", attempted=False)
        readiness = self.supervisor.snapshot()
        readiness_status = str(readiness.get("status") or "")
        readiness_reason = str(readiness.get("reason") or "")
        if readiness_status == "ready" or readiness_reason == "runtime_ready":
            return self._record("ready", "runtime_ready", attempted=False)
        if readiness_reason == "control_auth_unavailable":
            return self._record(
                "manual_review_required",
                "control_auth_unavailable",
                attempted=False,
            )
        if readiness_reason == "gateway_restart_required":
            return self._record(
                "restart_required",
                "gateway_restart_required",
                attempted=False,
            )
        if readiness_reason == "manual_review_required":
            return self._record(
                "manual_review_required",
                "manual_review_required",
                attempted=False,
            )

        try:
            detection = self._detector(self.hermes_root)
            plan = self._planner(detection)
        except Exception:
            self._repair_refusals += 1
            self.supervisor.mark_manual_review_required(
                binding=self._fence_binding(_UNAVAILABLE_PLAN_FINGERPRINT)
            )
            return self._record(
                "manual_review_required",
                "integrity_evidence_unavailable",
                attempted=False,
            )

        if plan.state == "installed":
            if readiness_reason in {
                "runtime_heartbeat_waiting",
                "runtime_heartbeat_missing",
                "runtime_heartbeat_stale",
            }:
                return self._record(
                    "idle",
                    "recovery_not_required",
                    attempted=False,
                )
            self.supervisor.mark_restart_required(
                binding=self._fence_binding(str(plan.fingerprint))
            )
            return self._record(
                "restart_required",
                "gateway_restart_required",
                attempted=False,
            )
        if not plan.executable:
            refusal_evidence = str(getattr(plan, "fingerprint", ""))
            if refusal_evidence and refusal_evidence == self._last_refusal_evidence:
                return self._record(
                    "manual_review_required",
                    str(getattr(plan, "reason", "integrity_evidence_invalid")),
                    attempted=False,
                )
            self._last_refusal_evidence = refusal_evidence
            self._repair_refusals += 1
            self.supervisor.mark_manual_review_required(
                binding=self._fence_binding(str(plan.fingerprint))
            )
            return self._record(
                "manual_review_required",
                str(getattr(plan, "reason", "integrity_evidence_invalid")),
                attempted=False,
            )
        if self.mode == "notify":
            self._last_evidence = str(plan.fingerprint)
            return self._record(
                "repair_available",
                str(plan.reason),
                attempted=False,
            )
        if str(plan.fingerprint) == self._last_evidence:
            return self._record("deduplicated", str(plan.reason), attempted=False)

        self._last_evidence = str(plan.fingerprint)
        self._repair_attempts += 1
        try:
            result = self._executor(
                detection,
                expected_fingerprint=str(plan.fingerprint),
            )
        except Exception:
            self._repair_refusals += 1
            self.supervisor.mark_manual_review_required(
                binding=self._fence_binding(str(plan.fingerprint))
            )
            return self._record(
                "manual_review_required",
                "integrity_repair_refused",
                attempted=True,
            )
        if getattr(result, "restart_required", False):
            self.supervisor.mark_restart_required(
                binding=self._fence_binding(str(plan.fingerprint))
            )
        self._repair_successes += 1
        return self._record("repaired", "gateway_restart_required", attempted=True)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self.mode,
                "last_status": self._last_status,
                "last_reason": self._last_reason,
                "repair_attempts": self._repair_attempts,
                "repair_successes": self._repair_successes,
                "repair_refusals": self._repair_refusals,
            }

    def _fence_binding(
        self,
        plan_fingerprint: str,
    ) -> RuntimeIntegrityFenceBinding | None:
        try:
            return build_runtime_integrity_fence_binding(
                self.hermes_root,
                plan_fingerprint,
            )
        except (OSError, ValueError):
            return None

    def _record(
        self,
        status: str,
        reason: str,
        *,
        attempted: bool,
    ) -> dict[str, Any]:
        self._last_status = status
        self._last_reason = reason
        return {"status": status, "reason": reason, "attempted": attempted}
