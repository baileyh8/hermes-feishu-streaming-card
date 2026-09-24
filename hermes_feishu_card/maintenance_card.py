from __future__ import annotations

import logging
from typing import Any, Mapping

from .config import card_width_config
from .feishu_client import FeishuAPIError
from .maintenance_store import UpdateJob
from .maintenance_update import UpdateInspection


logger = logging.getLogger(__name__)

_PHASE_COPY = {
    "locking": ("正在准备更新", "正在锁定本次维护任务。", "blue"),
    "draining": (
        "正在等待任务结束",
        "新任务已暂停接入，正在等待当前工作安全结束。",
        "blue",
    ),
    "restoring_hooks": (
        "正在准备 Hermes",
        "正在安全移除本版本 HFC 管理的钩子。",
        "blue",
    ),
    "updating_hermes": (
        "正在更新 Hermes",
        "正在执行官方 Hermes 更新流程。",
        "blue",
    ),
    "reinstalling_hfc": (
        "正在恢复卡片功能",
        "正在重新安装同一 HFC 版本并生成新钩子。",
        "blue",
    ),
    "starting_services": (
        "正在启动服务",
        "正在启动 sidecar 并重启 Gateway。",
        "blue",
    ),
    "verifying": (
        "正在完成验证",
        "正在检查版本、导入来源、运行时认证与健康状态。",
        "blue",
    ),
    "succeeded": (
        "更新完成",
        "Hermes 已更新，HFC 版本保持不变且服务已就绪。",
        "green",
    ),
    "failed": (
        "更新未完成",
        "已停在安全边界，请按下方恢复建议处理。",
        "red",
    ),
    "cancelled": ("已取消更新", "未执行 Hermes 更新。", "grey"),
}

_INSPECTION_REASON_COPY = {
    "artifact_version_mismatch": "维护包与当前 HFC 版本不一致。",
    "artifact_hash_invalid": "维护包完整性证据无效。",
    "hermes_detection_failed": "无法确认当前 Hermes 安装。",
    "hermes_not_fully_supported": "当前 Hermes 与 HFC 自动更新流程不完全兼容。",
    "git_operation_incomplete": "Hermes 仓库存在未完成的 Git 操作。",
    "hook_evidence_unavailable": "无法验证当前 HFC 钩子状态。",
    "hook_state_unverified": "当前 HFC 钩子状态不适合自动更新。",
    "git_head_unavailable": "无法确认当前 Hermes 提交。",
    "git_status_unavailable": "无法确认 Hermes 工作树状态。",
    "unrelated_tracked_changes": "Hermes 工作树存在不属于 HFC 的已跟踪改动。",
    "update_check_timeout": "Hermes 更新检查超时。",
    "update_check_failed": "Hermes 更新检查失败。",
    "update_target_unavailable": "无法绑定 Hermes 更新目标提交。",
    "update_target_diverged": "当前 Hermes 与 origin/main 已分叉，拒绝自动更新。",
    "no_update_available": "当前已是 origin/main 最新版本。",
    "maintenance_runtime_unavailable": "独立维护环境尚未就绪。",
    "gateway_runtime_unavailable": "Gateway 运行状态尚未提供可验证的任务计数。",
}

_RECOVERY_COMMAND = "hermes-feishu-card maintenance status"


def render_update_inspection_card(
    inspection: UpdateInspection,
    confirm_value: Mapping[str, object],
    cancel_value: Mapping[str, object],
    *,
    title: str = "Hermes Agent",
    width_mode: str = "default",
) -> dict[str, object]:
    if not inspection.ready:
        reason = _INSPECTION_REASON_COPY.get(
            inspection.reason_code,
            "当前环境不满足自动更新条件。",
        )
        content = (
            f"**自动更新暂不可用**\n\n"
            f"- 原因：{reason}\n"
            f"- 本机检查：`{_RECOVERY_COMMAND}`\n"
            "- 未执行 Hermes 更新。"
        )
        return _base_card(title, "自动更新暂不可用", "red", content, width_mode=width_mode)

    drain_line = (
        f"- 当前有 {inspection.active_sessions} 个任务；确认后将等待其安全结束。"
        if inspection.requires_drain
        else "- 当前没有需要等待的运行任务。"
    )
    target = inspection.target_summary or "当前更新快照已确认"
    content = "\n".join(
        [
            "**确认更新 Hermes**",
            "",
            f"- Hermes：`{inspection.current_version or 'unknown'}`",
            f"- 当前更新快照：{target}",
            f"- HFC：`{inspection.hfc_version}`（保持不变）",
            "- 钩子与维护包：已验证",
            drain_line,
            "",
            "确认会授权官方 updater 在执行时重新获取最新 `origin/main`；",
            "若远端在此期间变化，流程会先恢复服务，再把目标变化明确报告为失败。",
        ]
    )
    card = _base_card(title, "确认更新 Hermes", "blue", content, width_mode=width_mode)
    card["body"]["elements"].append(
        {
            "tag": "column_set",
            "element_id": "hfc_update_confirmation",
            "flex_mode": "none",
            "horizontal_spacing": "8px",
            "columns": [
                _button_column("确认更新", "primary", confirm_value, "confirm"),
                _button_column("取消", "default", cancel_value, "cancel"),
            ],
        }
    )
    return card


def render_update_job_card(
    job: UpdateJob,
    *,
    title: str = "Hermes Agent",
    width_mode: str = "default",
) -> dict[str, object]:
    phase_title, description, template = _PHASE_COPY.get(
        job.phase,
        ("维护状态未知", "请在本机检查维护任务状态。", "red"),
    )
    lines = [
        f"**{phase_title}**",
        "",
        description,
        "",
        f"- HFC：`{job.artifact_version}`（保持不变）",
    ]
    result = job.result if isinstance(job.result, dict) else {}
    if job.phase == "succeeded":
        actual_version = _safe_result_text(
            result.get("hermes_version") or result.get("actual_version")
        )
        lines.append(
            f"- Hermes：{job.pre_update_version} → `{actual_version or '已更新'}`"
        )
        hfc_version = _safe_result_text(result.get("hfc_version"))
        if hfc_version and hfc_version != job.artifact_version:
            lines[-2] = "- HFC：版本验证异常"
        service_status = _safe_result_text(result.get("service_status"))
        lines.append(
            "- 服务：已就绪"
            if service_status in {"ready", "healthy"}
            else "- 服务：验证完成"
        )
        if _safe_result_text(result.get("import_origin")) == "site-packages":
            lines.append("- 运行包：已从 Hermes `site-packages` 验证")
    elif job.phase == "failed":
        error_code = _safe_result_text(result.get("error_code"))
        boundary = _safe_result_text(result.get("recovery_boundary"))
        if error_code:
            lines.append(f"- 错误类型：`{error_code}`")
        if boundary:
            lines.append(f"- 安全边界：`{boundary}`")
        lines.append(f"- 本机检查：`{_RECOVERY_COMMAND}`")
    return _base_card(
        title,
        phase_title,
        template,
        "\n".join(lines),
        width_mode=width_mode,
    )


def render_update_operation_card(
    inspection: UpdateInspection,
    state: str,
    *,
    title: str = "Hermes Agent",
    width_mode: str = "default",
) -> dict[str, object]:
    if state == "cancelled":
        return _base_card(
            title,
            "已取消更新",
            "grey",
            "**已取消更新**\n\n未执行 Hermes 更新。",
            width_mode=width_mode,
        )
    return _base_card(
        title,
        "正在准备更新",
        "blue",
        "\n".join(
            [
                "**正在准备更新**",
                "",
                "正在重新核对更新目标和本机安全证据。",
                f"- HFC：`{inspection.hfc_version}`（保持不变）",
                "- 尚未执行 Hermes 更新。",
            ]
        ),
        width_mode=width_mode,
    )


class FeishuJobPublisher:
    def __init__(self, client: Any, *, width_mode: str = "default"):
        self._client = client
        self._width_mode = width_mode

    async def publish(self, job: UpdateJob) -> bool:
        try:
            await self._client.update_card_message(
                job.card_message_id,
                render_update_job_card(job, width_mode=self._width_mode),
            )
            return True
        except FeishuAPIError as exc:
            logger.warning(
                "maintenance card update failed: status=%s api_code=%s",
                _safe_status_code(exc.status_code),
                _safe_api_code(exc.api_code),
            )
            return False
        except Exception as exc:
            logger.warning(
                "maintenance card update failed: error=%s",
                exc.__class__.__name__,
            )
            return False


def _base_card(
    title: str,
    status_title: str,
    template: str,
    content: str,
    *, width_mode: str = "default",
) -> dict[str, object]:
    card_title = str(title or "").strip()[:80] or "Hermes Agent"
    return {
        "schema": "2.0",
        "config": {
            **card_width_config(width_mode),
            "wide_screen_mode": True,
            "update_multi": True,
        },
        "header": {
            "title": {"tag": "plain_text", "content": card_title},
            "subtitle": {"tag": "plain_text", "content": status_title[:80]},
            "template": template,
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": content,
                }
            ]
        },
    }


def _button_column(
    label: str,
    style: str,
    value: Mapping[str, object],
    suffix: str,
) -> dict[str, object]:
    return {
        "tag": "column",
        "width": "auto",
        "vertical_align": "top",
        "elements": [
            {
                "tag": "button",
                "element_id": f"hfc_update_{suffix}",
                "type": style,
                "size": "medium",
                "width": "default",
                "text": {"tag": "plain_text", "content": label},
                "behaviors": [
                    {
                        "type": "callback",
                        "value": dict(value),
                    }
                ],
            }
        ],
    }


def _safe_result_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.strip().split())
    if not text or len(text) > 96:
        return ""
    if any(char in text for char in ("\x00", "\n", "\r", "/", "\\")):
        return ""
    return text


def _safe_status_code(value: object) -> int | str:
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
    return "unknown"


def _safe_api_code(value: object) -> int | str:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized and len(normalized) <= 32 and all(
            char.isalnum() or char in {"_", "-"} for char in normalized
        ):
            return normalized
    return "unknown"
