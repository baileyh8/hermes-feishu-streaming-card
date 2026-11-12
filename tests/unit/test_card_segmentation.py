"""Terminal over-limit segmentation planning (issue #511)."""
from hermes_feishu_card.card_limits import FEISHU_MAX_TABLES, SAFE_CARD_JSON_BYTES
from hermes_feishu_card.card_segmentation import (
    MAX_SEGMENT_CARDS,
    SEGMENT_CHROME_BYTES,
    plan_terminal_segments,
)
from hermes_feishu_card.text import count_markdown_tables


def _paragraphs(count: int, *, marker: str = "段") -> str:
    return "\n\n".join(
        f"第 {i} {marker}：调研结论要点，包含背景、证据与分析推演。" for i in range(count)
    )


def test_split_is_lossless_on_block_boundaries():
    text = _paragraphs(400)

    plan = plan_terminal_segments(text)

    assert plan is not None
    assert plan.lead_text + "".join(plan.segments) == text


def test_every_card_respects_the_byte_budget():
    text = _paragraphs(400)

    plan = plan_terminal_segments(text)

    assert plan is not None
    budget = SAFE_CARD_JSON_BYTES - SEGMENT_CHROME_BYTES
    assert len(plan.lead_text.encode("utf-8")) <= 8_000
    assert all(
        len(segment.encode("utf-8")) <= budget for segment in plan.segments
    )


def test_segmented_table_pieces_repeat_their_headers():
    header = "| 指标 | 数值 | 说明 |"
    separator = "| --- | --- | --- |"
    rows = "\n".join(f"| 项目{i} | {i} | 长说明{'内容' * 40} |" for i in range(150))
    text = f"# 表格报告\n\n{header}\n{separator}\n{rows}"

    plan = plan_terminal_segments(text)

    assert plan is not None
    for segment in plan.segments:
        assert count_markdown_tables(segment) <= FEISHU_MAX_TABLES
        # Every table piece keeps a real header row, never a bare data row.
        assert "| 指标 | 数值 | 说明 |" in segment or "项目" not in segment


def test_table_count_never_exceeds_the_per_card_limit():
    header = "| a | b |"
    tables = "\n\n".join(
        f"{header}\n| --- | --- |\n| {i} | {i} |" for i in range(12)
    )

    plan = plan_terminal_segments(tables, lead_bytes=0)

    assert plan is not None
    assert all(
        count_markdown_tables(segment) <= FEISHU_MAX_TABLES
        for segment in plan.segments
    )


def test_giant_fence_pieces_stay_complete_fences():
    code = "\n".join(f"print({i})  # {'x' * 60}" for i in range(600))
    text = f"```python\n{code}\n```"

    plan = plan_terminal_segments(text)

    assert plan is not None
    assert len(plan.segments) >= 2
    for piece in (plan.lead_text, *plan.segments):
        assert piece.lstrip().startswith("```")
        assert piece.rstrip().endswith("```")


def test_zero_lead_budget_moves_the_opener_to_continuations():
    text = _paragraphs(40)

    plan = plan_terminal_segments(text, lead_bytes=0)

    assert plan is not None
    assert plan.lead_text == ""
    assert "".join(plan.segments) == text


def test_sections_stay_whole_when_cards_have_room():
    def section(index: int) -> str:
        return f"## 第{index}节\n\n" + "小节内容段落，保持相对完整。\n\n" * 10

    text = "".join(section(index) for index in range(1, 6))

    plan = plan_terminal_segments(text, lead_bytes=1_000, segment_bytes=1_000)

    assert plan is not None
    # With section-sized content well under the card budget, every card
    # opens on a heading instead of cutting the previous section mid-way.
    assert plan.lead_text.startswith("## 第1节")
    for segment in plan.segments:
        assert segment.startswith("## ")
    assert plan.lead_text + "".join(plan.segments) == text


def test_card_count_cap_falls_back_to_none():
    text = "密" * ((SAFE_CARD_JSON_BYTES - SEGMENT_CHROME_BYTES) * MAX_SEGMENT_CARDS)

    plan = plan_terminal_segments(text)

    assert plan is None


def test_empty_text_is_not_segmentable():
    assert plan_terminal_segments("") is None
    assert plan_terminal_segments("\n\n") is None
