from __future__ import annotations

import base64
from dataclasses import dataclass
import hmac
import json
import secrets
from hashlib import sha256
import threading
import time
from typing import Callable

from .diagnostics import DiagnosticReport


_TOKEN_MAX_CHARS = 2048
_TRANSPORT_PROOF_MAX_AGE_SECONDS = 30
_INFLIGHT_STATES = frozenset({"preparing", "executing", "restarting"})
_MUTATION_ACTIONS = {
    "repair",
    "confirm_repair",
    "restart",
    "confirm_restart",
}
_FINDING_COPY = {
    "hermes_unsupported": ("当前 Hermes 版本不受支持", "请使用受支持的 Hermes 版本后重新检测。"),
    "hermes_compatibility_partial": ("Hermes 兼容性不完整", "建议检查兼容性配置后重新检测。"),
    "runtime_import_failed": ("运行环境加载失败", "建议重新安装或检查运行环境。"),
    "feishu_sdk_incompatible": (
        "飞书连接 SDK 不兼容",
        "请重新运行 setup/install，并重启 Hermes Gateway。",
    ),
    "streaming_disabled": ("流式更新未启用", "建议启用流式更新配置。"),
    "streaming_not_detected": ("未检测到流式配置", "建议检查流式更新配置。"),
    "install_state_clean": ("尚未安装卡片钩子", "如需使用卡片，请完成安装。"),
    "install_state_installed": ("安装状态正常", "当前安装状态已确认。"),
    "install_state_changed": ("安装状态需要检查", "建议查看安装状态后再操作。"),
    "install_state_incomplete": ("安装状态不完整", "建议先执行安全修复或重新检测。"),
    "profile_identity_missing": ("未指定配置档案", "建议选择明确的配置档案。"),
    "profile_unknown": ("配置档案不可用", "建议检查所选配置档案。"),
    "profile_credentials_missing": ("配置档案凭据不完整", "建议补全对应配置后重新检测。"),
    "event_endpoint_mismatch": ("事件地址不一致", "建议检查事件地址配置。"),
    "bot_unknown": ("机器人配置不可用", "建议检查机器人绑定配置。"),
    "route_fallback": ("当前使用默认路由", "建议检查路由绑定以获得稳定投递。"),
    "operations_diagnosis_failed": ("诊断暂时不可用", "请稍后重新检测。"),
    "config_load_failed": ("配置读取失败", "建议检查配置后重新检测。"),
    "hermes_check_skipped": ("尚未完成 Hermes 检查", "建议完成环境检查后重新检测。"),
    "hermes_not_checked": ("尚未完成 Hermes 检查", "建议完成环境检查后重新检测。"),
    "backup_hash_mismatch": ("备份状态需要检查", "建议核对备份状态后再操作。"),
    "backup_invalid": ("备份状态不可用", "建议检查备份后重新检测。"),
    "backup_missing": ("缺少可用备份", "建议先确认备份状态。"),
    "backup_read_error": ("备份读取失败", "建议检查备份状态后重新检测。"),
    "backup_source_mismatch": ("备份来源不一致", "建议核对备份状态后再操作。"),
    "manifest_backup_hash_invalid": ("备份记录需要检查", "建议核对安装记录与备份状态。"),
    "manifest_current_hash_invalid": ("当前记录需要检查", "建议核对安装记录与当前状态。"),
    "manifest_invalid": ("安装记录不可用", "建议检查安装状态后重新检测。"),
    "manifest_missing": ("缺少安装记录", "建议检查安装状态后重新检测。"),
    "manifest_path_mismatch": ("安装记录不一致", "建议核对安装状态后再操作。"),
    "marker_error": ("安装标记异常", "建议检查安装状态后重新检测。"),
    "reapplication_invalid": ("重复安装状态异常", "建议检查安装状态后再操作。"),
    "current_hash_mismatch": ("当前文件状态不一致", "建议检查当前安装状态。"),
    "current_patch_mismatch": ("当前文件未匹配安装状态", "建议检查当前安装状态。"),
    "current_read_error": ("当前文件读取失败", "建议检查当前安装状态。"),
    "symlink_refused": ("检测到不安全的链接状态", "建议检查安装目录后再操作。"),
    "unsupported_anchors": ("当前版本缺少兼容锚点", "建议使用受支持的版本或检查兼容性。"),
    "cron_backup_hash_mismatch": ("定时任务备份状态需要检查", "建议核对定时任务备份。"),
    "cron_backup_invalid": ("定时任务备份不可用", "建议检查定时任务备份。"),
    "cron_backup_read_error": ("定时任务备份读取失败", "建议检查定时任务备份。"),
    "cron_backup_source_mismatch": ("定时任务备份来源不一致", "建议核对定时任务备份。"),
    "cron_current_hash_mismatch": ("定时任务当前状态不一致", "建议检查定时任务安装状态。"),
    "cron_current_patch_mismatch": ("定时任务当前文件未匹配安装状态", "建议检查定时任务安装状态。"),
    "cron_current_read_error": ("定时任务当前文件读取失败", "建议检查定时任务安装状态。"),
    "cron_manifest_backup_hash_invalid": ("定时任务备份记录需要检查", "建议核对定时任务安装记录。"),
    "cron_manifest_current_hash_invalid": ("定时任务当前记录需要检查", "建议核对定时任务安装记录。"),
    "cron_manifest_missing": ("缺少定时任务安装记录", "建议检查定时任务安装状态。"),
    "cron_manifest_path_mismatch": ("定时任务安装记录不一致", "建议核对定时任务安装状态。"),
    "cron_marker_error": ("定时任务安装标记异常", "建议检查定时任务安装状态。"),
    "cron_reapplication_invalid": ("定时任务重复安装状态异常", "建议检查定时任务安装状态。"),
    "cron_source_missing": ("缺少定时任务来源文件", "建议检查定时任务安装状态。"),
    "cron_symlink_refused": ("定时任务链接状态不安全", "建议检查定时任务安装目录。"),
    "cron_unsupported_anchors": ("定时任务缺少兼容锚点", "建议检查定时任务兼容性。"),
}
_UNKNOWN_FINDING_COPY = ("检测到需要检查的项目", "建议重新检测后再决定下一步。")


class OperationRejected(ValueError):
    pass


@dataclass(frozen=True)
class OperationClaims:
    operation_id: str
    action: str
    report_fingerprint: str
    expires_at: int


@dataclass
class OperationRecord:
    operation_id: str
    transport_lineage_id: str
    chat_id: str
    profile_id: str
    report_fingerprint: str
    recovery_fingerprint: str
    group: bool
    owner_open_id: str
    state: str
    expires_at: float
    result: dict[str, object] | None = None
    successor_operation_id: str = ""
    report: DiagnosticReport | None = None


def _next_operation_state(state: str, action: str) -> str:
    transitions = {
        ("diagnosed", "details"): "diagnosed",
        ("diagnosed", "recheck"): "diagnosed",
        ("repaired", "recheck"): "repaired",
        ("failed", "recheck"): "failed",
        ("expired", "recheck"): "expired",
        ("restarted", "recheck"): "restarted",
        ("restart_failed", "recheck"): "restart_failed",
        ("diagnosed", "repair"): "confirm_repair",
        ("confirm_repair", "cancel"): "diagnosed",
        ("confirm_repair", "confirm_repair"): "executing",
        ("repaired", "restart"): "confirm_restart",
        ("confirm_restart", "cancel"): "repaired",
        ("confirm_restart", "confirm_restart"): "restarting",
        ("diagnosed", "dismiss"): "dismissed",
        ("repaired", "dismiss"): "dismissed",
    }
    try:
        return transitions[(state, action)]
    except KeyError as exc:
        raise OperationRejected("invalid operation transition") from exc


class OperationStore:
    def __init__(
        self,
        *,
        secret: bytes,
        now: Callable[[], float] = time.time,
        max_records: int = 200,
    ):
        if not isinstance(secret, bytes) or not secret:
            raise ValueError("operation secret is required")
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self._secret = secret
        self._now = now
        self._max_records = max_records
        self._records: dict[str, OperationRecord] = {}
        self._recheck_predecessors: dict[str, OperationRecord] = {}
        self._transport_secrets: dict[str, bytes] = {}
        self._idempotency: dict[str, tuple[str, float]] = {}
        self._lock = threading.RLock()

    def prepare(
        self,
        *,
        chat_id: str,
        profile_id: str,
        group: bool,
        initiator_open_id: str,
        operation_id: str,
        transport_secret: bytes,
        idempotency_key: str,
    ) -> tuple[OperationRecord, bool]:
        if not isinstance(transport_secret, bytes) or len(transport_secret) < 16:
            raise ValueError("operation transport secret is invalid")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("operation idempotency key is required")
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("operation id is required")
        with self._lock:
            self._prune_locked()
            existing = self._idempotency.get(idempotency_key)
            if existing is not None:
                existing_operation_id, expires_at = existing
                record = self._records.get(existing_operation_id)
                if record is not None and expires_at > self._now():
                    return record, False
                self._idempotency.pop(idempotency_key, None)
            if (
                operation_id in self._records
                or operation_id in self._recheck_predecessors
            ):
                raise OperationRejected("operation id collision")
            self._reserve_capacity_locked()
            record = OperationRecord(
                operation_id=operation_id,
                transport_lineage_id=operation_id,
                chat_id=chat_id,
                profile_id=profile_id,
                report_fingerprint="",
                recovery_fingerprint="",
                group=group,
                owner_open_id=initiator_open_id if group else "",
                state="preparing",
                expires_at=self._now() + 120.0,
            )
            self._records[operation_id] = record
            self._transport_secrets[operation_id] = transport_secret
            self._idempotency[idempotency_key] = (operation_id, record.expires_at)
            return record, True

    def diagnose(
        self,
        operation_id: str,
        *,
        report: DiagnosticReport | None = None,
        report_fingerprint: str | None = None,
        recovery_fingerprint: str | None = None,
    ) -> OperationRecord:
        if report is not None:
            report_fingerprint = report.fingerprint
            recovery_fingerprint = report.recovery_fingerprint
        if report_fingerprint is None or recovery_fingerprint is None:
            raise ValueError("diagnostic report is required")
        with self._lock:
            record = self._records.get(operation_id)
            if record is None or record.state != "preparing":
                raise OperationRejected("operation state changed")
            record.report_fingerprint = report_fingerprint
            record.recovery_fingerprint = recovery_fingerprint
            record.report = report
            record.state = "diagnosed"
            record.expires_at = self._now() + 120.0
            for key, (candidate, _expires_at) in self._idempotency.items():
                if candidate == operation_id:
                    self._idempotency[key] = (operation_id, record.expires_at)
            return record

    def create(
        self,
        *,
        chat_id: str,
        profile_id: str,
        report_fingerprint: str,
        recovery_fingerprint: str,
        group: bool,
        initiator_open_id: str = "",
        transport_secret: bytes | None = None,
        transport_source_operation_id: str = "",
    ) -> OperationRecord:
        if transport_secret is not None and transport_source_operation_id:
            raise ValueError("operation transport binding is ambiguous")
        if transport_secret is not None and (
            not isinstance(transport_secret, bytes) or len(transport_secret) < 16
        ):
            raise ValueError("operation transport secret is invalid")
        with self._lock:
            transport_lineage_id = ""
            if transport_source_operation_id:
                transport_secret = self._transport_secrets.get(
                    transport_source_operation_id
                )
                if transport_secret is None:
                    raise OperationRejected("operation transport binding expired")
                source = self._records.get(transport_source_operation_id) or self._recheck_predecessors.get(transport_source_operation_id)
                transport_lineage_id = (
                    source.transport_lineage_id if source is not None else transport_source_operation_id
                )
            self._prune_locked()
            self._reserve_capacity_locked()
            operation_id = secrets.token_urlsafe(18)
            record = OperationRecord(
                operation_id=operation_id,
                transport_lineage_id=transport_lineage_id or operation_id,
                chat_id=chat_id,
                profile_id=profile_id,
                report_fingerprint=report_fingerprint,
                recovery_fingerprint=recovery_fingerprint,
                group=group,
                owner_open_id=initiator_open_id if group else "",
                state="diagnosed",
                expires_at=self._now() + 120.0,
            )
            self._records[operation_id] = record
            if transport_secret is not None:
                self._transport_secrets[operation_id] = transport_secret
            return record

    def create_successor(
        self,
        previous_operation_id: str,
        *,
        report: DiagnosticReport,
    ) -> OperationRecord:
        with self._lock:
            previous = self._records.get(previous_operation_id) or self._recheck_predecessors.get(
                previous_operation_id
            )
            if previous is None:
                raise OperationRejected("operation expired")
            if previous.successor_operation_id:
                successor = self.current_successor(previous.operation_id)
                if successor is not None:
                    return successor
                raise OperationRejected("operation successor unavailable")
            transport_secret = self._transport_secrets.get(previous.operation_id)
            if transport_secret is None:
                raise OperationRejected("operation transport binding expired")
            self._prune_locked()
            if previous.operation_id in self._records:
                self._records.pop(previous.operation_id, None)
                self._recheck_predecessors[previous.operation_id] = previous
            self._reserve_capacity_locked()
            successor = OperationRecord(
                operation_id=secrets.token_urlsafe(18),
                transport_lineage_id=previous.transport_lineage_id or previous.operation_id,
                chat_id=previous.chat_id,
                profile_id=previous.profile_id,
                report_fingerprint=report.fingerprint,
                recovery_fingerprint=report.recovery_fingerprint,
                group=previous.group,
                owner_open_id=previous.owner_open_id,
                state="diagnosed",
                expires_at=self._now() + 120.0,
                report=report,
            )
            previous.successor_operation_id = successor.operation_id
            self._records[successor.operation_id] = successor
            self._transport_secrets[successor.operation_id] = transport_secret
            return successor

    def current_successor(self, operation_id: str) -> OperationRecord | None:
        with self._lock:
            record = self._records.get(operation_id) or self._recheck_predecessors.get(
                operation_id
            )
            seen: set[str] = set()
            while record is not None and record.successor_operation_id:
                if record.operation_id in seen:
                    return None
                seen.add(record.operation_id)
                record = self._records.get(
                    record.successor_operation_id
                ) or self._recheck_predecessors.get(record.successor_operation_id)
            return record

    def is_inflight(self, operation_id: str) -> bool:
        with self._lock:
            record = self._records.get(operation_id)
            return record is not None and record.state in _INFLIGHT_STATES

    def is_preparing(self, operation_id: str) -> bool:
        with self._lock:
            self._prune_locked()
            record = self._records.get(operation_id)
            return record is not None and record.state == "preparing"

    def bind_transport_secret(self, operation_id: str, secret: bytes) -> None:
        if not isinstance(secret, bytes) or len(secret) < 16:
            raise ValueError("operation transport secret is invalid")
        with self._lock:
            if operation_id not in self._records:
                raise OperationRejected("operation expired")
            existing = self._transport_secrets.get(operation_id)
            if existing is not None and not hmac.compare_digest(existing, secret):
                raise OperationRejected("operation transport already bound")
            self._transport_secrets[operation_id] = secret

    def _prune_locked(self) -> None:
        now = self._now()
        cutoff = now - 300.0
        for operation_id, item in list(self._records.items()):
            if item.state == "preparing" and item.expires_at <= now:
                self._remove_locked(operation_id)
            elif item.expires_at < cutoff and item.state not in _INFLIGHT_STATES:
                self._remove_locked(operation_id)
        for operation_id, item in list(self._recheck_predecessors.items()):
            if item.expires_at < cutoff:
                self._recheck_predecessors.pop(operation_id, None)
                self._transport_secrets.pop(operation_id, None)

    def _reserve_capacity_locked(
        self, protected_operation_ids: frozenset[str] = frozenset()
    ) -> None:
        while len(self._records) >= self._max_records:
            candidate = next(
                (
                    operation_id
                    for operation_id, record in self._records.items()
                    if not self._capacity_protected_locked(record)
                    and operation_id not in protected_operation_ids
                ),
                None,
            )
            if candidate is None:
                raise OperationRejected(
                    "operation store overloaded: capacity exhausted"
                )
            self._remove_locked(candidate)

    def _capacity_protected_locked(self, record: OperationRecord) -> bool:
        return record.state in {"executing", "restarting"} or (
            record.state == "preparing" and record.expires_at > self._now()
        )

    def begin_recheck(
        self,
        token: str,
        *,
        callback_chat_id: str,
        callback_profile_id: str,
        callback_profile_scope: str,
        callback_report_fingerprint: str,
        callback_recovery_fingerprint: str,
    ) -> tuple[OperationRecord, bool]:
        with self._lock:
            claims, record = self._verify_token_locked(
                token, allow_recheck_predecessor=True
            )
            self._verify_callback_locked(
                claims,
                record,
                callback_chat_id=callback_chat_id,
                callback_profile_id=callback_profile_id,
                callback_profile_scope=callback_profile_scope,
                callback_report_fingerprint=callback_report_fingerprint,
                callback_recovery_fingerprint=callback_recovery_fingerprint,
            )
            if claims.action != "recheck":
                raise OperationRejected("operation action mismatch")
            if record.state == "preparing":
                return record, False
            if record.successor_operation_id:
                successor = self._records.get(record.successor_operation_id)
                if successor is not None:
                    return successor, False
            _next_operation_state(record.state, "recheck")
            if record.report is None:
                raise OperationRejected("operation report snapshot unavailable")
            transport_secret = self._transport_secrets.get(record.operation_id)
            if transport_secret is None:
                raise OperationRejected("operation transport binding expired")
            self._prune_locked()
            successor = OperationRecord(
                operation_id=secrets.token_urlsafe(18),
                transport_lineage_id=record.transport_lineage_id or record.operation_id,
                chat_id=record.chat_id,
                profile_id=record.profile_id,
                report_fingerprint=record.report_fingerprint,
                recovery_fingerprint=record.recovery_fingerprint,
                group=record.group,
                owner_open_id=record.owner_open_id,
                state="preparing",
                expires_at=self._now() + 120.0,
                report=record.report,
            )
            record.successor_operation_id = successor.operation_id
            self._records.pop(record.operation_id, None)
            self._recheck_predecessors[record.operation_id] = record
            for key, (candidate, _expires_at) in list(self._idempotency.items()):
                if candidate == record.operation_id:
                    self._idempotency.pop(key, None)
            self._records[successor.operation_id] = successor
            self._transport_secrets[successor.operation_id] = transport_secret
            return successor, True

    def recheck_successor(
        self,
        token: str,
        *,
        callback_chat_id: str,
        callback_profile_id: str,
        callback_profile_scope: str,
        callback_report_fingerprint: str,
        callback_recovery_fingerprint: str,
        successor_report_fingerprint: str,
        successor_recovery_fingerprint: str,
    ) -> tuple[OperationRecord, bool]:
        with self._lock:
            claims, record = self._verify_token_locked(
                token, allow_recheck_predecessor=True
            )
            self._verify_callback_locked(
                claims,
                record,
                callback_chat_id=callback_chat_id,
                callback_profile_id=callback_profile_id,
                callback_profile_scope=callback_profile_scope,
                callback_report_fingerprint=callback_report_fingerprint,
                callback_recovery_fingerprint=callback_recovery_fingerprint,
            )
            if claims.action != "recheck":
                raise OperationRejected("operation action mismatch")
            _next_operation_state(record.state, "recheck")
            if record.successor_operation_id:
                successor = self._records.get(record.successor_operation_id)
                if successor is not None:
                    return successor, False
            transport_secret = self._transport_secrets.get(record.operation_id)
            if transport_secret is None:
                raise OperationRejected("operation transport binding expired")
            self._prune_locked()
            replace_predecessor = len(self._records) >= self._max_records
            if not replace_predecessor:
                self._reserve_capacity_locked(frozenset({record.operation_id}))
            successor = OperationRecord(
                operation_id=secrets.token_urlsafe(18),
                transport_lineage_id=record.transport_lineage_id or record.operation_id,
                chat_id=record.chat_id,
                profile_id=record.profile_id,
                report_fingerprint=successor_report_fingerprint,
                recovery_fingerprint=successor_recovery_fingerprint,
                group=record.group,
                owner_open_id=record.owner_open_id,
                state="diagnosed",
                expires_at=self._now() + 120.0,
            )
            record.successor_operation_id = successor.operation_id
            if replace_predecessor:
                self._remove_locked(record.operation_id)
                self._recheck_predecessors[record.operation_id] = record
                self._transport_secrets[record.operation_id] = transport_secret
            self._records[successor.operation_id] = successor
            self._transport_secrets[successor.operation_id] = transport_secret
            return successor, True

    def _remove_locked(self, operation_id: str) -> None:
        self._records.pop(operation_id, None)
        self._transport_secrets.pop(operation_id, None)
        self._recheck_predecessors.pop(operation_id, None)
        for key, (candidate, _expires_at) in list(self._idempotency.items()):
            if candidate == operation_id:
                self._idempotency.pop(key, None)
        for predecessor_id, predecessor in list(self._recheck_predecessors.items()):
            if predecessor.successor_operation_id == operation_id:
                self._recheck_predecessors.pop(predecessor_id, None)
                self._transport_secrets.pop(predecessor_id, None)

    def token(self, record: OperationRecord, action: str) -> str:
        payload = json.dumps(
            {
                "operation_id": record.operation_id,
                "action": action,
                "report_fingerprint": record.report_fingerprint,
                "expires_at": int(record.expires_at),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        signature = self._signature(encoded, record)
        token = f"{encoded}.{signature}"
        if len(token) > _TOKEN_MAX_CHARS:
            raise OperationRejected("invalid operation token")
        return token

    def scope_fingerprint(self, record: OperationRecord) -> str:
        return hmac.new(
            self._secret,
            self._scope_input(record, prefix="callback"),
            sha256,
        ).hexdigest()

    def verify_transport_proof(
        self,
        *,
        proof: str,
        token: str,
        action: str,
        callback_chat_id: str,
        callback_profile_id: str,
        callback_profile_scope: str,
        operator_open_id: str,
        timestamp: int,
    ) -> OperationRecord:
        with self._lock:
            if isinstance(timestamp, bool) or not isinstance(timestamp, int):
                raise OperationRejected("invalid transport proof")
            if abs(self._now() - timestamp) > _TRANSPORT_PROOF_MAX_AGE_SECONDS:
                raise OperationRejected("transport proof expired")
            operation_id = self._operation_id_from_token_locked(token)
            record = self._records.get(operation_id) or self._recheck_predecessors.get(
                operation_id
            )
            if record is None:
                raise OperationRejected("invalid transport proof")
            secret = self._transport_secrets.get(record.operation_id)
            if secret is None:
                raise OperationRejected("invalid transport proof")
            expected = sign_transport_proof(
                secret,
                token=token,
                action=action,
                callback_chat_id=callback_chat_id,
                callback_profile_id=record.profile_id,
                callback_profile_scope=callback_profile_scope,
                operator_open_id=operator_open_id,
                timestamp=timestamp,
            )
            if not isinstance(proof, str) or not hmac.compare_digest(proof, expected):
                raise OperationRejected("invalid transport proof")
            return record

    @staticmethod
    def _operation_id_from_token_locked(token: str) -> str:
        try:
            if not isinstance(token, str) or not token or len(token) > _TOKEN_MAX_CHARS:
                raise ValueError
            encoded, _signature = token.rsplit(".", 1)
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.b64decode(
                encoded + padding,
                altchars=b"-_",
                validate=True,
            )
            if len(decoded) > 1024:
                raise ValueError
            payload = json.loads(decoded.decode("utf-8"))
            operation_id = payload.get("operation_id") if isinstance(payload, dict) else None
            if not isinstance(operation_id, str) or not operation_id:
                raise ValueError
            return operation_id
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OperationRejected("invalid transport proof") from exc

    def inspect(
        self,
        token: str,
        *,
        callback_chat_id: str,
        callback_profile_id: str = "",
        callback_profile_scope: str = "",
        callback_report_fingerprint: str = "",
        callback_recovery_fingerprint: str = "",
        allow_expired: bool = False,
        allow_recheck_predecessor: bool = False,
        allow_successor_predecessor: bool = False,
    ) -> tuple[OperationClaims, OperationRecord]:
        with self._lock:
            claims, record = self._verify_token_locked(
                token,
                allow_recheck_predecessor=allow_recheck_predecessor,
                allow_successor_predecessor=allow_successor_predecessor,
            )
            self._verify_callback_locked(
                claims,
                record,
                callback_chat_id=callback_chat_id,
                callback_profile_id=callback_profile_id,
                callback_profile_scope=callback_profile_scope,
                callback_report_fingerprint=callback_report_fingerprint,
                callback_recovery_fingerprint=callback_recovery_fingerprint,
                verify_expiry=not allow_expired,
            )
            return claims, record

    def transition(
        self,
        token: str,
        *,
        action: str,
        operator_open_id: str,
        callback_chat_id: str,
        callback_profile_id: str,
        callback_report_fingerprint: str = "",
        callback_recovery_fingerprint: str = "",
    ) -> OperationRecord:
        with self._lock:
            claims, record = self._verify_token_locked(token)
            self._verify_callback_locked(
                claims,
                record,
                callback_chat_id=callback_chat_id,
                callback_profile_id=callback_profile_id,
                callback_report_fingerprint=callback_report_fingerprint,
                callback_recovery_fingerprint=callback_recovery_fingerprint,
            )
            if claims.action != action:
                raise OperationRejected("operation action mismatch")
            if action in _MUTATION_ACTIONS and record.group:
                if not operator_open_id:
                    raise OperationRejected("operator identity required")
                if not record.owner_open_id and action in {"repair", "restart"}:
                    record.owner_open_id = operator_open_id
                elif operator_open_id != record.owner_open_id:
                    raise OperationRejected("different operator")
            record.state = _next_operation_state(record.state, action)
            return record

    def complete(
        self,
        operation_id: str,
        *,
        expected_state: str,
        state: str,
        result: dict[str, object],
    ) -> OperationRecord:
        with self._lock:
            record = self._records.get(operation_id)
            if record is None or record.state != expected_state:
                raise OperationRejected("operation state changed")
            record.state = state
            record.result = dict(result)
            return record

    def _verify_token_locked(
        self,
        token: str,
        *,
        allow_recheck_predecessor: bool = False,
        allow_successor_predecessor: bool = False,
    ) -> tuple[OperationClaims, OperationRecord]:
        try:
            if not isinstance(token, str) or not token or len(token) > _TOKEN_MAX_CHARS:
                raise ValueError
            encoded, supplied = token.rsplit(".", 1)
            if not encoded or len(supplied) != 64:
                raise ValueError
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.b64decode(
                encoded + padding,
                altchars=b"-_",
                validate=True,
            )
            if len(decoded) > 1024:
                raise ValueError
            payload = json.loads(decoded.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "operation_id",
                "action",
                "report_fingerprint",
                "expires_at",
            }:
                raise ValueError
            if isinstance(payload["expires_at"], bool):
                raise ValueError
            claims = OperationClaims(
                operation_id=str(payload["operation_id"]),
                action=str(payload["action"]),
                report_fingerprint=str(payload["report_fingerprint"]),
                expires_at=int(payload["expires_at"]),
            )
            if not claims.operation_id or not claims.action:
                raise ValueError
            record = self._records.get(claims.operation_id)
            if (
                record is None
                and (
                    allow_successor_predecessor
                    or (allow_recheck_predecessor and claims.action == "recheck")
                )
            ):
                record = self._recheck_predecessors.get(claims.operation_id)
            if record is None:
                raise OperationRejected("operation expired")
            expected = self._signature(encoded, record)
            if not hmac.compare_digest(supplied, expected):
                raise ValueError
            return claims, record
        except OperationRejected:
            raise
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise OperationRejected("invalid operation token") from exc

    def _verify_callback_locked(
        self,
        claims: OperationClaims,
        record: OperationRecord,
        *,
        callback_chat_id: str,
        callback_profile_id: str,
        callback_profile_scope: str = "",
        callback_report_fingerprint: str,
        callback_recovery_fingerprint: str,
        verify_expiry: bool = True,
    ) -> None:
        profile_matches = callback_profile_id == record.profile_id
        scope_matches = True
        if callback_profile_scope:
            scope_matches = hmac.compare_digest(
                callback_profile_scope,
                self.scope_fingerprint(record),
            )
        if not callback_profile_id:
            profile_matches = bool(callback_profile_scope) and scope_matches
        if callback_chat_id != record.chat_id or not profile_matches or not scope_matches:
            raise OperationRejected("operation scope mismatch")
        if claims.report_fingerprint != record.report_fingerprint:
            raise OperationRejected("diagnosis changed")
        if (
            callback_report_fingerprint
            and callback_report_fingerprint != record.report_fingerprint
        ):
            raise OperationRejected("diagnosis changed")
        if (
            callback_recovery_fingerprint
            and callback_recovery_fingerprint != record.recovery_fingerprint
        ):
            raise OperationRejected("recovery changed")
        if verify_expiry:
            now = self._now()
            if claims.expires_at <= now or record.expires_at <= now:
                raise OperationRejected("operation expired")

    def _signature(self, encoded: str, record: OperationRecord) -> str:
        signing_input = encoded.encode("ascii") + b"." + self._scope_input(record)
        return hmac.new(self._secret, signing_input, sha256).hexdigest()

    @staticmethod
    def _scope_input(record: OperationRecord, prefix: str = "token") -> bytes:
        return (
            f"{prefix}\0{record.chat_id}\0{record.profile_id}"
        ).encode("utf-8")


def sign_transport_proof(
    secret: bytes,
    *,
    token: str,
    action: str,
    callback_chat_id: str,
    callback_profile_id: str,
    callback_profile_scope: str,
    operator_open_id: str,
    timestamp: int,
) -> str:
    if not isinstance(secret, bytes) or not secret:
        raise ValueError("transport secret is required")
    canonical = json.dumps(
        [
            "hfc-operation-transport-v1",
            token,
            action,
            callback_chat_id,
            callback_profile_id,
            callback_profile_scope,
            operator_open_id,
            timestamp,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(secret, canonical, sha256).hexdigest()


def render_operations_card(
    report: DiagnosticReport,
    operation: OperationRecord,
    footer: str,
    *,
    store: OperationStore | None = None,
) -> dict[str, object]:
    safe = report.to_dict(card_safe=True)
    status = str(safe.get("status") or "warning")
    template = {"ok": "green", "warning": "orange", "error": "red"}.get(
        status, "orange"
    )
    elements: list[dict[str, object]] = [
        {
            "tag": "markdown",
            "element_id": "operations_summary",
            "content": _operations_summary(report, operation),
        }
    ]
    buttons = _operation_buttons(report, operation, store)
    if buttons:
        elements.extend(_operation_button_rows(buttons))
    elements.extend(
        [
            {"tag": "hr", "element_id": "operations_divider"},
            {
                "tag": "markdown",
                "element_id": "operations_footer",
                "content": str(footer or ""),
                "text_size": "x-small",
            },
        ]
    )
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": "运行诊断"},
        },
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": "Hermes Agent"},
            "subtitle": {"tag": "plain_text", "content": "运行诊断"},
        },
        "body": {"elements": elements},
    }


def _operations_summary(report: DiagnosticReport, operation: OperationRecord) -> str:
    state_messages = {
        "preparing": "**正在重新检测**\n\n已锁定本次操作，请稍候。",
        "confirm_repair": "**确认安全修复**\n\n将重新校验当前安装证据，通过后执行安全修复。",
        "executing": "**正在安全修复**\n\n已锁定本次操作，请稍候。",
        "repaired": "**安全修复已完成**",
        "confirm_restart": "**确认重启 Gateway**\n\n重启会中断当前 Hermes 工作。",
        "restarting": "**正在重启 Gateway**\n\n回调已返回，重启正在后台执行。",
        "restarted": "**修复与重启已完成**",
        "restart_failed": "**修复完成，重启失败**",
        "failed": "**本次安全修复未执行**",
        "expired": "**诊断已过期**\n\n请重新检测后再操作。",
        "dismissed": "**已暂不处理**",
    }
    if operation.state in state_messages:
        content = state_messages[operation.state]
        message = str((operation.result or {}).get("message") or "").strip()
        return f"{content}\n\n{message}" if message else content

    findings = report.to_dict(card_safe=True).get("findings")
    show_details = bool((operation.result or {}).get("show_details"))
    lines = [
        "**诊断详情**" if show_details else "**诊断摘要**",
        f"\n- 状态：{_status_label(report.status)}",
    ]
    if isinstance(findings, list):
        for finding in findings[:8]:
            if not isinstance(finding, dict):
                continue
            code = str(finding.get("code") or "")
            summary, detail = _FINDING_COPY.get(code, _UNKNOWN_FINDING_COPY)
            lines.append(f"- {summary}")
            if show_details:
                lines.append(f"  - 建议：{detail}")
    if len(lines) == 2:
        lines.append("- 未发现需要处理的问题。")
    return "\n".join(lines)


def _operation_buttons(
    report: DiagnosticReport,
    operation: OperationRecord,
    store: OperationStore | None,
) -> list[dict[str, object]]:
    actions: list[tuple[str, str, str]]
    if operation.state == "diagnosed":
        actions = [("recheck", "重新检测", "default")]
        if not bool((operation.result or {}).get("show_details")):
            actions.insert(0, ("details", "查看诊断", "default"))
        if bool(report.install_state.get("recovery_executable")):
            actions.append(("repair", "安全修复", "default"))
        actions.append(("dismiss", "暂不处理", "default"))
    elif operation.state == "confirm_repair":
        actions = [
            ("confirm_repair", "确认修复", "primary"),
            ("cancel", "取消", "default"),
        ]
    elif operation.state == "repaired":
        actions = [("recheck", "重新检测", "default")]
        if bool((operation.result or {}).get("restart_available")):
            actions.append(("restart", "重启 Gateway", "default"))
        actions.append(("dismiss", "暂不处理", "default"))
    elif operation.state == "confirm_restart":
        actions = [
            ("confirm_restart", "确认重启", "primary"),
            ("cancel", "取消", "default"),
        ]
    elif operation.state in {"preparing", "executing", "restarting"}:
        actions = [("recheck", "重新检测", "default")]
    elif operation.state in {"expired", "failed", "restart_failed", "restarted"}:
        actions = [("recheck", "重新检测", "default")]
    else:
        actions = []
    return [
        _operation_button(operation, action, label, style, store)
        for action, label, style in actions
    ]


def _operation_button_rows(
    buttons: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row_index in range(0, len(buttons), 2):
        pair = buttons[row_index : row_index + 2]
        rows.append(
            {
                "tag": "column_set",
                "element_id": f"operations_row_{row_index // 2}",
                "flex_mode": "none",
                "horizontal_spacing": "8px",
                "columns": [
                    {
                        "tag": "column",
                        "width": "auto",
                        "vertical_align": "top",
                        "elements": [button],
                    }
                    for button in pair
                ],
            }
        )
    return rows


def _operation_button(
    operation: OperationRecord,
    action: str,
    label: str,
    style: str,
    store: OperationStore | None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "hfc_action": "operations.select",
        "operation_action": action,
    }
    if store is not None:
        value["token"] = store.token(operation, action)
        value["profile_scope"] = store.scope_fingerprint(operation)
        if operation.transport_lineage_id:
            value["transport_lineage_id"] = operation.transport_lineage_id
    return {
        "tag": "button",
        "element_id": f"operations_{action}",
        "type": style,
        "size": "medium",
        "width": "default",
        "text": {"tag": "plain_text", "content": label},
        "behaviors": [{"type": "callback", "value": value}],
    }


def _status_label(status: str) -> str:
    return {"ok": "正常", "warning": "需要关注", "error": "异常"}.get(
        status, "需要关注"
    )
