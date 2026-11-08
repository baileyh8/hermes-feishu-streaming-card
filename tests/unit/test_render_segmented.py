"""Opt-in segmented terminal rendering for over-limit answers (issue #511)."""
from hermes_feishu_card.card_limits import inspect_card_limits
from hermes_feishu_card.render import render_card_result
from hermes_feishu_card.session import CardSession


def _oversize_session(status: str = "completed") -> CardSession:
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    session.answer_text = (
        "# 调研报告\n\n" + "长结论内容，包含要点与分析。\n\n" * 2_000
    )
    session.status = status
    return session


def test_terminal_oversize_renders_segmented_when_opted_in():
    session = _oversize_session()

    result = render_card_result(session, over_limit="segmented")

    assert result.disposition == "segmented"
    assert result.limit_reason == "json_bytes"
    assert inspect_card_limits(result.card).safe is True
    assert result.segments
    total = len(result.segments)
    for index, card in enumerate(result.segments, start=1):
        assert inspect_card_limits(card).safe is True
        assert card["header"]["title"]["content"].endswith(f"（续 {index}/{total}）")
        assert card["schema"] == "2.0"
    # The main card announces the continuation cards and keeps the lead only.
    main_text = str(result.card)
    assert f"{total} 张续卡" in main_text
    # The full answer never fits one card; the tail only travels on cards.
    assert result.primary_text.startswith("# 调研报告")


def test_streaming_oversize_never_segments_during_live_updates():
    session = _oversize_session(status="thinking")

    result = render_card_result(session, over_limit="segmented")

    assert result.disposition == "deferred_native"
    assert result.segments == ()
    assert inspect_card_limits(result.card).safe is True


def test_default_over_limit_keeps_the_native_handoff():
    session = _oversize_session()

    result = render_card_result(session)

    assert result.disposition == "native"
    assert result.segments == ()
    assert "完整内容已切换为 Hermes 原生消息发送" in str(result.card)


def test_unbounded_content_falls_back_to_native_handoff():
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    # Dense three-byte characters easily exceed the segment-card cap.
    session.answer_text = "密" * 280_000
    session.status = "completed"

    result = render_card_result(session, over_limit="segmented")

    assert result.disposition == "native"
    assert result.segments == ()
    assert "完整内容已切换为 Hermes 原生消息发送" in str(result.card)


def test_main_card_note_only_still_falls_back_when_panels_overflow():
    session = _oversize_session()
    # Auxiliary panels alone (timeline of huge reasoning entries) push the
    # main card past the budget no matter how small the lead is.
    for _ in range(60):
        session.timeline.record_reasoning("推理" * 240)

    result = render_card_result(
        session,
        over_limit="segmented",
        show_reasoning=True,
        timeline_expanded=True,
        max_timeline_items=60,
        max_reasoning_chars=4_000,
    )

    if result.disposition == "segmented":
        # The planner only proceeds when every card was verified in limit.
        assert inspect_card_limits(result.card).safe is True
        assert all(inspect_card_limits(card).safe for card in result.segments)
    else:
        assert result.disposition == "native"
        assert result.segments == ()
