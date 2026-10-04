"""Credential-free, network-free previews using the actual card renderer."""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from .card_limits import inspect_card_limits
from .events import SidecarEvent
from .render import render_card_result
from .render_options import card_render_options
from .session import CardSession, InteractionOption, InteractionState


def sample_sessions() -> list[tuple[str, str, CardSession]]:
    now = time.time()
    def sample(name: str, **values: Any) -> CardSession:
        session = CardSession(conversation_id="preview-conversation", message_id=f"preview-{name}", chat_id="preview-chat")
        session.created_at = now - 84
        session.updated_at = now - 3
        for key, value in values.items():
            setattr(session, key, value)
        return session

    running = sample("running", thinking_text="先核对资料，再整理本次修改。")
    for index, status in enumerate(("completed", "running")):
        running.apply(SidecarEvent.from_dict({
            "schema_version": "1", "event": "tool.updated", "sequence": index,
            "platform": "feishu", "conversation_id": running.conversation_id,
            "message_id": running.message_id, "chat_id": running.chat_id, "created_at": now - 3,
            "data": {"tool_id": f"preview-tool-{index}", "name": "terminal",
                     "status": status, "detail": "检查项目测试" if index else "读取项目说明",
                     "arguments": {"command": "python -m pytest tests/unit -q"}},
        }))
    running.updated_at = now - 3
    waiting = sample("waiting", updated_at=now - 130)
    approval = sample("approval")
    approval.active_interaction = InteractionState(
        interaction_id="preview-approval", kind="approval", prompt="允许更新示例项目配置吗？",
        description="将在示例项目中更新一项阅读设置。原配置先备份，不修改模型、账号或权限。\n\n请核对完整操作范围后选择。",
        options=[InteractionOption("允许本次操作", "once"), InteractionOption("拒绝", "deny")],
        callback_token="preview-only-not-a-live-token", requested_at=now,
    )
    clarify = sample("clarify")
    clarify.active_interaction = InteractionState(
        interaction_id="preview-clarify", kind="clarify", prompt="这份报告重点展示哪些内容？",
        options=[InteractionOption("结果与结论", "result"), InteractionOption("执行过程", "process"),
                 InteractionOption("数据与限制", "evidence")],
        multi_select=True, allow_custom_input=True,
        callback_token="preview-only-not-a-live-token", requested_at=now,
    )
    completed = sample(
        "completed", status="completed", duration=84, model="deepseek/deepseek-v4-pro",
        answer_text="## 项目检查完成\n\n配置与文档保持一致，可以继续下一步验证。\n\n"
                    "- 已核对阅读设置和覆盖顺序\n- 已保留原始配置\n- 真实客户端效果仍需单独验收\n\n"
                    "| 检查项 | 结果 |\n| --- | --- |\n| 配置解析 | 通过 |\n| 文档链接 | 通过 |\n\n"
                    "使用示例命令检查配置：\n\n```bash\nhermes-feishu-card card-config --config example.yaml\n```",
        tokens={"input_tokens": 4200, "output_tokens": 680},
        # Deliberately stale sample: the renderer must hide another provider's quota.
        subscription_usage="5h 26% · weekly 89%",
    )
    stopped = sample("stopped", status="failed", answer_text="已保留前半部分结果。\n\n> 请求未完成，请检查连接后重新发起任务。", duration=32)
    restored = sample("restored", status="failed", presentation_state="reconnecting", answer_text="此前已经收到的内容会继续保留。")
    notice = sample("notice", status="completed", delivery_kind="notice", notice_title="配置检查", answer_text="配置文件可以正常读取。下次启动时加载新设置。")
    short = sample("short", status="completed", answer_text="已收到，稍后见。")
    return [
        ("running", "执行过程", running), ("waiting", "长时间无更新", waiting),
        ("approval", "等待审批", approval), ("clarify", "多选与输入", clarify),
        ("completed", "结果与长内容", completed), ("short", "简短回复", short),
        ("stopped", "失败与中断", stopped), ("restored", "连接恢复", restored),
        ("notice", "独立通知", notice),
    ]


def build_preview(report: dict[str, Any]) -> dict[str, Any]:
    values = dict(report["values"])
    config = {**values, "_hide_successful_tool_activity": values["terminal_tool_activity"] == "failed_only"}
    config["text_sizes"] = {
        role: value for role, value in values["text_sizes"].items()
        if report["text_size_sources"][role].endswith("(explicit)")
    }
    options = card_render_options(config)
    mode = values.get("interaction_mode")
    mode = "text" if mode in {"text", "markdown", "reply"} else "callback"
    scenarios = []
    for key, label, session in sample_sessions():
        variants = {}
        for layout in ("classic", "task"):
            result = render_card_result(
                session, **{**options, "presentation": layout},
                title="示例任务", footer_fields=values["footer_fields"],
                interaction_mode=mode, mentions_enabled=False,
            )
            if result.disposition != "card" or not inspect_card_limits(result.card).safe:
                raise ValueError("Preview configuration exceeds card limits")
            variants[layout] = result.card
        scenarios.append({"id": key, "label": label, "cards": variants})
    return {
        "schema_version": 1,
        "notice": "离线结构预览，全部为示例数据。与运行时共用 renderer；不代表真实飞书/Lark 客户端效果。按钮不会执行操作。",
        "configuration": report, "scenarios": scenarios,
    }


def write_preview(directory: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    """Create new artifacts exclusively; never overwrite existing user files."""
    data = build_preview(report)
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    embedded = serialized.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    # Use literal escapes in the script body, not executable HTML from JSON strings.
    embedded = embedded.replace(chr(0x2028), "\\u2028").replace(chr(0x2029), "\\u2029")
    template = Path(__file__).with_name("preview_template.html").read_text(encoding="utf-8")
    html = template.replace("__HFC_PREVIEW_DATA__", embedded)
    directory.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for name, contents in (("cards.json", serialized + "\n"), ("index.html", html)):
            path = directory / name
            with path.open("x", encoding="utf-8") as output:
                created.append(path)
                output.write(contents)
    except OSError:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return directory / "index.html", directory / "cards.json"
