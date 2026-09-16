"""Card layout contract: header state, the content-area tool block, and a live footer.

The user's spec, in order: the title must show whether the agent is still working (and at what)
without hiding the session name; the tool running right now belongs in the CONTENT area where
there is room for it, tagged with which call it is (#N) and how long it has run; the footer
carries completion state, elapsed time and the tool count.
"""
from __future__ import annotations

from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession


def _session() -> CardSession:
    return CardSession(conversation_id="c", message_id="m", chat_id="oc")


def _tool_event(
    *,
    tool_id: str,
    name: str,
    status: str = "running",
    detail: str = "",
    sequence: int = 1,
    created_at: float = 10.0,
) -> SidecarEvent:
    return SidecarEvent(
        schema_version="1",
        event="tool.updated",
        conversation_id="c",
        message_id="m",
        chat_id="oc",
        platform="feishu",
        sequence=sequence,
        created_at=created_at,
        data={"tool_id": tool_id, "name": name, "status": status, "detail": detail},
    )


def _elements(card, element_id: str) -> list[dict]:
    return [
        item
        for item in card["body"]["elements"]
        if str(item.get("element_id", "")).startswith(element_id)
    ]


def test_header_leads_with_the_state_and_keeps_the_session_name():
    session = _session()
    card = render_card(session, title="研发助手")
    assert card["header"]["title"]["content"] == "⏳ 执行中 · 研发助手"

    session.status = "completed"
    session.answer_text = "答案"
    assert render_card(session, title="研发助手")["header"]["title"]["content"] == "✅ 研发助手"

    failed = _session()
    failed.status = "failed"
    assert render_card(failed, title="研发助手")["header"]["title"]["content"] == "⛔ 研发助手"


def test_header_keeps_the_action_phrase_but_never_the_target():
    """The phrase ("正在执行终端") stays; the long command it used to append does not."""
    session = _session()
    long_command = "pytest -q " + ("x" * 200)
    session.apply(_tool_event(tool_id="t1", name="terminal", detail=long_command))

    card = render_card(session, title="研发助手")
    title = card["header"]["title"]["content"]

    assert title == "⏳ 正在执行终端 · 研发助手"
    assert "x" * 20 not in title
    # ...and the target is not lost: it renders in the content-area row instead.
    row = _elements(card, "tool_activity_0")[0]
    assert "pytest" in row["content"]


def test_tool_row_shows_state_name_ordinal_and_elapsed_time():
    session = _session()
    session.apply(
        _tool_event(tool_id="t1", name="read_file", detail="/Users/mac/secret/session.py")
    )

    row = _elements(render_card(session), "tool_activity_0")[0]

    assert row["tag"] == "markdown"
    assert "<text_tag color='blue'>运行中</text_tag>" in row["content"]
    assert "<text_tag color='neutral'>read_file</text_tag>" in row["content"]
    assert "#1" in row["content"]
    assert "正在读取" in row["content"]
    assert "session.py" in row["content"]


def test_tool_ordinal_counts_each_call_and_survives_status_updates():
    session = _session()
    session.apply(_tool_event(tool_id="t1", name="terminal", sequence=1, created_at=1.0))
    session.apply(_tool_event(tool_id="t2", name="read_file", sequence=2, created_at=2.0))
    # A terminal update for the FIRST tool must not look like a new call.
    session.apply(
        _tool_event(
            tool_id="t1", name="terminal", status="completed", sequence=3, created_at=3.0
        )
    )

    assert session.tool_count == 2
    # Numbered by CALL, not by row position or by update count.
    assert session.tools["t1"].ordinal == 1
    assert session.tools["t2"].ordinal == 2
    # t1 has finished, so the live row is t2 — and it keeps its call number.
    row = _elements(render_card(session), "tool_activity_0")[0]
    assert "#2" in row["content"]


def test_finished_card_keeps_one_tool_row_as_evidence_of_what_ran():
    session = _session()
    session.apply(
        _tool_event(tool_id="t1", name="terminal", status="completed", detail="pytest -q")
    )
    session.status = "completed"
    session.answer_text = "完成"

    rows = _elements(render_card(session), "tool_activity_")

    assert len(rows) == 1
    assert "<text_tag color='green'>已完成</text_tag>" in rows[0]["content"]


def test_failed_tool_is_tagged_red():
    session = _session()
    session.apply(_tool_event(tool_id="t1", name="terminal", status="failed"))

    row = _elements(render_card(session), "tool_activity_0")[0]

    assert "<text_tag color='red'>失败</text_tag>" in row["content"]


def test_pending_approval_hides_tool_rows():
    """During an approval the card is about the decision — tool rows would push it down."""
    from hermes_feishu_card.session import InteractionOption, InteractionState

    session = _session()
    session.apply(_tool_event(tool_id="t1", name="terminal"))
    session.active_interaction = InteractionState(
        interaction_id="i1",
        kind="approval",
        prompt="允许继续吗？",
        options=[InteractionOption(label="允许", value="allow")],
        status="pending",
        requested_at=0.0,
        timeout_seconds=9_999_999.0,
    )

    assert "tool_activity_" not in str(render_card(session))


def test_footer_reports_state_elapsed_time_and_tool_count():
    session = _session()
    session.apply(_tool_event(tool_id="t1", name="terminal"))
    running = render_card(session)["body"]["elements"][-1]["content"]
    assert "<text_tag color='blue'>执行中</text_tag>" in running
    assert "工具 1" in running

    session.status = "completed"
    session.duration = 8.0
    session.answer_text = "答案"
    completed = render_card(session)["body"]["elements"][-1]["content"]
    assert completed.startswith("<text_tag color='green'>已完成</text_tag> · 8s")
    assert "工具 1" in completed


def test_footer_elapsed_time_grows_with_the_session(monkeypatch):
    """The clock is live: the same session renders a larger elapsed time later."""
    from hermes_feishu_card import render as render_module

    session = _session()
    monkeypatch.setattr(render_module._time, "time", lambda: session.created_at + 65.0)
    later = render_module._render_footer(session)
    monkeypatch.setattr(render_module._time, "time", lambda: session.created_at + 185.0)
    much_later = render_module._render_footer(session)

    assert "1m5s" in later
    assert "3m5s" in much_later
