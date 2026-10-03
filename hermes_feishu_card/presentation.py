"""Read-only task presentation; execution and authorization remain in CardSession."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import math
import re
import time
from typing import Any


TASK_TEXT_SIZE_DEFAULTS = {
    "body": "normal", "reasoning": "small", "tool": "small",
    "notice": "normal", "footer": "notation",
}
TASK_OBSERVATION_SECONDS = 60

# Documented Feishu Markdown selectors, not guessed aliases such as text/none.
# https://open.feishu.cn/document/feishu-cards/card-json-v2-components/content-components/rich-text
_TASK_CODE_LANGUAGES = frozenset("""
abap ada apache apex assembly bash c_sharp cpp c cmake cobol css coffee_script d
dart delphi diff django docker_file erlang fortran gherkin go graphql groovy html
htmlbars http haskell json java javascript julia kotlin latex lisp lua matlab
makefile markdown nginx objective_c opengl_shading_language php perl powershell
prolog properties protobuf python r ruby rust sas scss sql scala scheme shell
solidity swift toml thrift typescript vbscript visual_basic xml yaml
""".split())
_TASK_CODE_OPENING = re.compile(
    r"(?P<marker>`{3,}|~{3,})(?P<before>[ \t]*)(?P<language>[A-Za-z_]+)"
    r"(?P<after>[ \t]*)(?P<newline>\r?\n)"
)


def task_answer_code_projection(text: str, *, max_block_size: int | None = None) -> str:
    """Use neutral native code blocks only in a disposable task answer view.

    Keep the original selector as a literal label outside the copyable body.
    The complete opening line is required, but a streaming block need not have
    closed yet: no body character or closing fence is inserted or rewritten.
    """
    from .text import _is_fence_closing, scan_markdown_blocks

    # The shared scanner also recognizes non-Markdown Unicode line separators.
    # Raw HTML blocks have container rules it does not model. Leave both alone.
    if re.search(r"\r(?!\n)|[\v\f\x1c-\x1e\x85\u2028\u2029]", text):
        return text
    blocks = scan_markdown_blocks(text)
    if any(block.kind == "plain" and re.search(r"(?m)^ {0,3}<[A-Za-z!/?]", block.text)
           for block in blocks):
        return text
    projected = []
    for block in blocks:
        opening_end = block.text.find("\n") + 1
        opening = _TASK_CODE_OPENING.fullmatch(block.text[:opening_end]) if block.kind == "fence" else None
        if opening is None or opening["language"].lower() not in _TASK_CODE_LANGUAGES:
            projected.append(block.text)
            continue
        newline = opening["newline"]
        replacement = f"{opening['marker']}{opening['before']}plain_text{opening['after']}{newline}"
        fence = replacement + block.text[opening_end:]
        if max_block_size is not None and len(fence) > max_block_size:
            lines = block.text.splitlines(keepends=True)
            marker = opening["marker"]
            closed = len(lines) > 1 and _is_fence_closing(lines[-1], marker[0], len(marker))
            closing = lines[-1] if closed else marker + "\n"
            body_lines = lines[1:-1] if closed else lines[1:]
            body_limit = max_block_size - len(replacement) - len(closing)
            # The shared splitter adds a newline when wrapping a cut long line
            # or an unfinished final line. Never newly cause either rewrite.
            if (body_limit <= 0 or any(len(line) > body_limit for line in body_lines)
                    or body_lines and not body_lines[-1].endswith("\n")):
                projected.append(block.text)
                continue
        # Official selectors contain underscores; keep their labels literal too.
        label = opening["language"].replace("_", "&#95;")
        projected.append(
            f"语言：{label}{newline}{newline}" + fence
        )
    return "".join(projected)


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


def task_interaction_prompt(interaction: Any) -> str:
    from .text import normalize_stream_text
    prompt = normalize_stream_text(interaction.prompt).strip()
    if prompt and interaction.kind == "approval" and interaction.status == "failed":
        return f"**原审批请求**\n\n{prompt}"
    return prompt


def task_button_label(label: str) -> str:
    """Return the complete label only when a task button can carry it."""
    from .text import normalize_stream_text
    label = normalize_stream_text(label).strip()
    return label if 0 < len(label) <= 16 and "\n" not in label else ""


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
    return TaskPresentation(label, "grey" if observation else "blue", observation)


def apply_task_presentation(
    card: dict[str, Any], session: Any, *, title: str, action: str = "",
    has_primary_content: bool = True, now: float | None = None,
    legacy_fallback_card: dict[str, Any] | None = None,
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
        # Feishu's grey header uses a filled surface with light text. Its
        # default template is the neutral theme-aware surface for unknown state.
        result["header"]["template"] = "default" if view.color == "grey" else view.color
        result["header"]["title"] = {"tag": "plain_text", "content": label}
        result["header"].pop("subtitle", None)

    if is_legacy:
        # A compact title must never hide a short question previously shown only
        # in the header. Keep the full question before the existing scope/controls.
        if interaction is not None:
            from .text import normalize_stream_text
            prompt = task_interaction_prompt(interaction)
            if prompt and not any(item.get("tag") == "markdown" and item.get("content") == prompt
                                  for item in elements):
                elements.insert(0, {"tag": "markdown", "content": prompt})
            if interaction.status == "completed":
                result["header"]["title"]["content"] = f"{title} · 选择已记录"
                result["header"]["template"] = "green"
                receipt = {"tag": "markdown", "content": "后续结果会继续显示在回复卡中。"}
                if receipt not in elements:
                    elements.append(receipt)
            elif interaction.status == "failed":
                result["header"]["title"]["content"] = f"{title} · 此次选择已失效"
                result["header"]["template"] = "red"
            if interaction.kind in {"approval", "clarify"} and interaction.status == "pending":
                # Short choices can name their action on the button.
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
                            label = task_button_label(labels.get(value.get("choice"), ""))
                            if label:
                                text["content"] = label
                        if node.get("tag") == "multi_select_static":
                            for option in node.get("options", []):
                                label = labels.get(option.get("value"), "")
                                text = option.get("text", {})
                                number = str(text.get("content", ""))
                                if label and number.isdigit():
                                    preview = " ".join(label.split())
                                    if len(preview) > 44:
                                        preview = preview[:43].rstrip() + "…"
                                    text["content"] = f"{number}. {preview}"
                        for child in node.values():
                            label_buttons(child)
                    elif isinstance(node, list):
                        for child in node:
                            label_buttons(child)
                label_buttons(elements)
                # The header owns pending state. Remove only the renderer's
                # known trailing hint, never a matching question/body string.
                if (len(elements) >= 2 and elements[-2] == {"tag": "hr"}
                        and elements[-1] == {"tag": "markdown", "content": "等待选择…"}):
                    del elements[-2:]
        # Legacy callback cards intentionally receive no schema-2 size/element IDs.
        # The callback response also uses this path directly, outside
        # render_card_result. Never let decoration make an accepted scope exceed
        # its original budget; preserve the full classic card when it cannot fit.
        from .card_limits import inspect_card_limits
        fallback = legacy_fallback_card if legacy_fallback_card is not None else card
        return result if inspect_card_limits(result).safe else copy.deepcopy(fallback)

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
