"""Read-only task presentation; execution and authorization remain in CardSession."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import math
import time
from typing import Any


TASK_TEXT_SIZE_DEFAULTS = {
    "body": "normal", "reasoning": "small", "tool": "small",
    "notice": "normal", "footer": "notation",
}
TASK_OBSERVATION_SECONDS = 60


def task_observation_delay(session: Any, *, now: float | None = None) -> float:
    """Time until an honest silence observation; invalid clocks never busy-loop."""
    clock = time.time() if now is None else now
    updated = session.updated_at
    if (isinstance(updated, (int, float)) and not isinstance(updated, bool)
            and math.isfinite(updated) and updated > 0 and math.isfinite(clock)):
        return max(0.0, TASK_OBSERVATION_SECONDS - max(0.0, clock - updated))
    return float(TASK_OBSERVATION_SECONDS)


@dataclass(frozen=True)
class TaskPresentation:
    label: str
    color: str
    observation: str = ""


def task_presentation(session: Any, *, now: float | None = None) -> TaskPresentation:
    """Describe observed state without inferring success or liveness from silence."""
    if getattr(session, "presentation_state", "") == "reconnecting":
        return TaskPresentation("等待状态同步", "grey", "连接已重建；执行状态尚未同步，原授权不会恢复。")
    interaction = session.active_interaction
    if interaction is not None:
        if interaction.status == "paused":
            return TaskPresentation("审批已暂停", "orange", "请重新查看操作范围；旧授权不会自动恢复。")
        if interaction.status == "pending":
            return TaskPresentation(
                "等待审批" if interaction.kind == "approval" else "等待你的选择",
                "orange",
                "请核对完整操作范围后选择。" if interaction.kind == "approval" else "提交后会在这里显示回执。",
            )
    if session.status == "failed":
        return TaskPresentation("已停止", "red")
    if session.status == "completed":
        return TaskPresentation("已完成", "green")
    if session.delivery_kind == "notice":
        return TaskPresentation("状态更新", "blue")
    clock = time.time() if now is None else now
    updated = session.updated_at
    observation = ""
    if (isinstance(updated, (int, float)) and not isinstance(updated, bool)
            and math.isfinite(updated) and updated > 0 and math.isfinite(clock)):
        age = max(0, int(clock - updated))
        if age >= TASK_OBSERVATION_SECONDS:
            observation = f"最近 {age // 60} 分钟未收到新事件；任务是否仍在执行尚待确认。"
    label = "等待新进展" if observation else "执行中" if session.tools or session.answer_text else "思考中"
    return TaskPresentation(label, "blue", observation)


def apply_task_presentation(
    card: dict[str, Any], session: Any, *, title: str, action: str = "",
    has_primary_content: bool = True, now: float | None = None,
) -> dict[str, Any]:
    """Restyle only known display fields; preserve content, controls and dialect."""
    result = copy.deepcopy(card)
    view = task_presentation(session, now=now)
    interaction = session.active_interaction
    is_legacy = result.get("schema") != "2.0"
    elements = result.get("elements") if is_legacy else result.get("body", {}).get("elements")
    if not isinstance(elements, list):
        return result

    if "header" in result:
        label = f"{title} · {view.label}"
        result["header"]["template"] = view.color
        result["header"]["title"] = {"tag": "plain_text", "content": label}
        result["header"].pop("subtitle", None)

    if is_legacy:
        # A compact title must never hide a short question previously shown only
        # in the header. Keep the full question before the existing scope/controls.
        if interaction is not None:
            from .text import normalize_stream_text
            prompt = normalize_stream_text(interaction.prompt).strip()
            if prompt and not any(item.get("tag") == "markdown" and item.get("content") == prompt
                                  for item in elements):
                elements.insert(0, {"tag": "markdown", "content": prompt})
            if interaction.status == "completed":
                result["header"]["title"]["content"] = f"{title} · 选择已记录"
                receipt = {"tag": "markdown", "content": "后续结果会继续显示在回复卡中。"}
                if receipt not in elements:
                    elements.append(receipt)
            elif interaction.status == "failed":
                result["header"]["title"]["content"] = f"{title} · 此次选择已失效"
            if interaction.kind == "approval" and interaction.status == "pending":
                # Short approval choices can name their action on the button.
                # Long choices keep the established numbered layout and full list.
                labels = {option.value: normalize_stream_text(option.label).strip()
                          for option in interaction.options}
                def label_buttons(node: Any) -> None:
                    if isinstance(node, dict):
                        value = node.get("value")
                        text = node.get("text")
                        if (node.get("tag") == "button" and isinstance(value, dict)
                                and value.get("hfc_action") == "interaction.select"
                                and isinstance(text, dict) and str(text.get("content", "")).isdigit()):
                            label = labels.get(value.get("choice"), "")
                            if 0 < len(label) <= 16 and "\n" not in label:
                                text["content"] = label
                        for child in node.values():
                            label_buttons(child)
                    elif isinstance(node, list):
                        for child in node:
                            label_buttons(child)
                label_buttons(elements)
        # Legacy callback cards intentionally receive no schema-2 size/element IDs.
        # The callback response also uses this path directly, outside
        # render_card_result. Never let decoration make an accepted scope exceed
        # its original budget; preserve the full classic card when it cannot fit.
        from .card_limits import inspect_card_limits
        return result if inspect_card_limits(result).safe else copy.deepcopy(card)

    if not has_primary_content and not (interaction and interaction.status in {"pending", "paused"}):
        elements[:] = [item for item in elements
                       if not str(item.get("element_id", "")).startswith("main_content")]
    # The body owns the current action. Non-tool phases (for example context
    # compression) still need a visible home after removing the header subtitle.
    if (action and session.status not in {"completed", "failed"}
            and not (interaction and interaction.status in {"pending", "paused"})
            and not any(str(item.get("element_id", "")).startswith("tool_activity_") for item in elements)):
        elements.insert(0, {
            "tag": "markdown", "element_id": "task_action",
            "content": f"上次动作：{action}" if view.observation else action,
            "text_size": "small",
        })
    if view.observation:
        elements[:] = [item for item in elements if item.get("element_id") != "task_observation"]
        elements.insert(0, {
            "tag": "markdown", "element_id": "task_observation",
            "content": view.observation, "text_size": "small",
        })
    for item in elements:
        if item.get("element_id") == "auxiliary_timeline":
            header = item.get("header", {})
            heading = header.get("title")
            if isinstance(heading, dict) and isinstance(heading.get("content"), str):
                heading["content"] = heading["content"].replace("思考过程", "执行过程")
    # Only a meaningful footer needs a divider. This also keeps empty/loading
    # cards compact without dropping any answer or interactive component.
    elements[:] = [item for item in elements if item.get("element_id") != "footer" or item.get("content")]
    if elements and elements[-1].get("element_id") == "main_divider":
        elements.pop()
    elif len(elements) == 2 and elements[0].get("element_id") == "main_divider":
        del elements[0]
    return result
