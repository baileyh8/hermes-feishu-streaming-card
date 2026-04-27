from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import sys
import textwrap
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="docs/assets")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cards = build_preview_cards()
    (output_dir / "e2e-card-preview.json").write_text(
        json.dumps(cards, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "e2e-card-preview.svg").write_text(
        render_preview_svg(cards),
        encoding="utf-8",
    )
    print(f"wrote {output_dir / 'e2e-card-preview.svg'}")
    print(f"wrote {output_dir / 'e2e-card-preview.json'}")
    return 0


def build_preview_cards() -> dict[str, dict[str, Any]]:
    thinking = CardSession(
        conversation_id="preview-conversation",
        message_id="preview-message",
        chat_id="oc_preview",
    )
    for event in (
        _event("thinking.delta", 0, {"text": "<think>先检查输入参数，确认 Hermes hook 已经发出事件。"}),
        _event("thinking.delta", 1, {"text": "\n再读取资料并整理回答结构。</think>"}),
        _event(
            "tool.updated",
            2,
            {
                "tool_id": "read-docs",
                "name": "读取资料",
                "status": "已完成",
                "detail": "docs",
            },
        ),
        _event(
            "tool.updated",
            3,
            {
                "tool_id": "compose",
                "name": "生成答案",
                "status": "运行中",
                "detail": "answer",
            },
        ),
    ):
        thinking.apply(event)

    completed = CardSession(
        conversation_id="preview-conversation",
        message_id="preview-message",
        chat_id="oc_preview",
    )
    for event in (
        _event(
            "tool.updated",
            0,
            {"tool_id": "read-docs", "name": "读取资料", "status": "已完成"},
        ),
        _event(
            "tool.updated",
            1,
            {"tool_id": "compose", "name": "生成答案", "status": "已完成"},
        ),
        _event(
            "message.completed",
            2,
            {
                "answer": "这是流式卡片的最终回答。思考内容已经被结果覆盖，工具调用次数保留。",
                "duration": 8.4,
                "tokens": {"input_tokens": 128, "output_tokens": 256},
            },
        ),
    ):
        completed.apply(event)

    return {
        "thinking": render_card(thinking),
        "completed": render_card(completed),
    }


def _event(event: str, sequence: int, data: dict[str, Any]) -> SidecarEvent:
    return SidecarEvent(
        schema_version="1",
        event=event,
        conversation_id="preview-conversation",
        message_id="preview-message",
        chat_id="oc_preview",
        platform="feishu",
        sequence=sequence,
        created_at=1777017600.0 + sequence,
        data=data,
    )


def render_preview_svg(cards: dict[str, dict[str, Any]]) -> str:
    thinking = _card_parts(cards["thinking"])
    completed = _card_parts(cards["completed"])
    return "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="720" viewBox="0 0 1120 720">',
            '<rect width="1120" height="720" fill="#f5f7fb"/>',
            '<text x="56" y="54" font-family="Arial, sans-serif" font-size="24" font-weight="700" fill="#1f2937">Hermes Feishu Streaming Card E2E Preview</text>',
            _render_card_panel(56, 96, "思考流更新", thinking, "#4f46e5"),
            _render_card_panel(580, 96, "完成态更新", completed, "#16a34a"),
            "</svg>",
            "",
        ]
    )


def _card_parts(card: dict[str, Any]) -> dict[str, str]:
    elements = card["body"]["elements"]
    return {
        "title": card["header"]["title"]["content"],
        "subtitle": card["header"]["subtitle"]["content"],
        "main": elements[0]["content"],
        "tools": elements[2]["content"],
        "footer": elements[3]["content"],
    }


def _render_card_panel(
    x: int,
    y: int,
    label: str,
    parts: dict[str, str],
    accent: str,
) -> str:
    lines = [
        f'<text x="{x}" y="{y - 22}" font-family="Arial, sans-serif" font-size="16" font-weight="700" fill="#374151">{escape(label)}</text>',
        f'<rect x="{x}" y="{y}" width="484" height="560" rx="12" fill="#ffffff" stroke="#d6dbe6"/>',
        f'<rect x="{x}" y="{y}" width="484" height="72" rx="12" fill="{accent}"/>',
        f'<text x="{x + 28}" y="{y + 33}" font-family="Arial, sans-serif" font-size="20" font-weight="700" fill="#ffffff">{escape(parts["title"])}</text>',
        f'<text x="{x + 28}" y="{y + 56}" font-family="Arial, sans-serif" font-size="14" fill="#eef2ff">{escape(parts["subtitle"])}</text>',
    ]
    cursor = y + 116
    lines.extend(_text_block(x + 28, cursor, parts["main"], 22, 14, "#111827"))
    cursor += 184
    lines.append(f'<line x1="{x + 28}" y1="{cursor}" x2="{x + 456}" y2="{cursor}" stroke="#e5e7eb"/>')
    cursor += 38
    lines.extend(_text_block(x + 28, cursor, parts["tools"], 22, 14, "#374151"))
    cursor = y + 520
    lines.append(f'<text x="{x + 28}" y="{cursor}" font-family="Arial, sans-serif" font-size="13" fill="#6b7280">{escape(parts["footer"])}</text>')
    return "\n".join(lines)


def _text_block(
    x: int,
    y: int,
    text: str,
    line_height: int,
    font_size: int,
    color: str,
) -> list[str]:
    output = []
    cursor = y
    for raw_line in text.splitlines() or [""]:
        for line in textwrap.wrap(raw_line, width=30, replace_whitespace=False) or [""]:
            output.append(
                f'<text x="{x}" y="{cursor}" font-family="Arial, sans-serif" font-size="{font_size}" fill="{color}">{escape(line)}</text>'
            )
            cursor += line_height
    return output


if __name__ == "__main__":
    raise SystemExit(main())
