from __future__ import annotations

from typing import Any, Dict

from .session import CardSession


def render_card(session: CardSession) -> Dict[str, Any]:
    is_done = session.status == "completed"
    subtitle = "已完成" if is_done else "思考中"
    template = "green" if is_done else "indigo"
    main_text = session.visible_main_text or ("正在思考..." if not is_done else "")
    tool_summary = _render_tool_summary(session)
    footer = _render_footer(session)
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": subtitle},
        },
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": "Hermes Agent"},
            "subtitle": {"tag": "plain_text", "content": subtitle},
        },
        "body": {
            "elements": [
                {"tag": "markdown", "element_id": "main_content", "content": main_text},
                {"tag": "hr", "element_id": "main_divider"},
                {"tag": "markdown", "element_id": "tool_summary", "content": tool_summary},
                {"tag": "markdown", "element_id": "footer", "content": footer, "text_size": "x-small"},
            ]
        },
    }


def _render_tool_summary(session: CardSession) -> str:
    if not session.tools:
        return "工具调用 0 次"
    lines = [f"工具调用 {session.tool_count} 次"]
    for tool in session.tools.values():
        lines.append(f"- `{tool.name}`: {tool.status}")
    return "\n".join(lines)


def _render_footer(session: CardSession) -> str:
    if session.status != "completed":
        return "生成中"
    input_tokens = session.tokens.get("input_tokens", 0)
    output_tokens = session.tokens.get("output_tokens", 0)
    return f"耗时 {session.duration:.1f}s · 输入 {input_tokens} · 输出 {output_tokens}"
