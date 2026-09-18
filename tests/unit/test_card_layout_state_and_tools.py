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


def test_header_action_row_names_the_work_and_stays_within_the_cap():
    """The second row names the CONCRETE work ("执行命令：pytest -q") — capped, never on the title.

    Maintainer note (contract change): the target used to be banned from the header entirely ("the
    header only answers 'still working?'"). The user then asked to see the concrete action on the
    second row exactly as the tool row shows it ("标题行这边的第二个行像工具行那样看到具体的工具
    动作"), so the target DOES reach the sub-title now. What still holds: the title stays an identity
    line, and the 100-char cap keeps a long command from taking over the header.
    """
    session = _session()
    long_command = "pytest -q " + ("x" * 200)
    session.apply(_tool_event(tool_id="t1", name="terminal", detail=long_command))

    card = render_card(session, title="研发助手")
    title = card["header"]["title"]["content"]
    subtitle = card["header"]["subtitle"]["content"]

    assert title == "⏳ 研发助手 · 工具 #1"
    assert subtitle.startswith("执行命令：pytest -q")
    assert len(subtitle) <= 100
    assert long_command not in subtitle  # the 200-char command is never carried whole
    assert "x" * 20 not in title
    # ...and the target is not lost either: the content-area row carries the same line.
    row = _elements(card, "tool_activity_0")[0]
    assert "pytest" in row["content"]


def test_content_tool_row_carries_a_long_command_whole_even_though_the_header_caps_it():
    """The row the user reads is NOT capped at the header's length — it shares the panel's budget.

    Maintainer note (contract change): the header sub-title and the content-area tool row used to
    share one 100-char cap, and that made the content row the LEAST complete surface in the card: a
    long command was cut with an ellipsis there while the 思考过程 panel below carried the same
    command up to its own (larger) budget. The user reported exactly that ("正文里面的工具行的执行
    命令和参数没有像 timeline 里面那样子比较全"). The two still share one source and one sanitizer —
    the header keeps a short cap because it is a one-line identity strip; the content row takes the
    card's tool-detail budget so it agrees with the panel instead of disagreeing with it.
    """
    session = _session()
    long_command = "grep -aE " + ("x" * 250) + " /var/log/gateway.log"
    session.apply(_tool_event(tool_id="t1", name="terminal", detail=long_command))

    card = render_card(session, title="研发助手")
    row = _elements(card, "tool_activity_0")[0]["content"]
    subtitle = card["header"]["subtitle"]["content"]

    # The command reaches the row whole — no ellipsis, same as the panel below.
    assert long_command in row
    assert "…" not in row
    # ...while the header stays a one-line strip.
    assert len(subtitle) <= 100
    assert long_command not in subtitle


def test_completed_header_subtitle_keeps_the_completion_note():
    """A finished turn owns the second row: "本轮回复结束" is not displaced by a tool phrase.

    The sub-title now prefers the action phrase (see
    test_header_keeps_the_action_phrase_but_never_the_target). A completed turn normally has no
    running tool, but a turn CAN end with a tool still marked running (interrupted mid-call). The
    completion note must win in that case too — it is the only line that says the reply is over, and
    losing it to a stale phrase would make a finished card read as still working.
    """
    session = _session()
    session.apply(_tool_event(tool_id="t1", name="terminal", detail="pytest -q"))
    session.status = "completed"
    session.duration = 152.0
    session.answer_text = "完成"

    card = render_card(session, title="Sales Bot")

    assert card["header"]["title"]["content"] == "✅ Sales Bot · 工具 #1 · 2m32s"
    assert card["header"]["subtitle"]["content"] == "本轮回复结束"


def test_header_carries_elapsed_time_and_tool_count(monkeypatch):
    """User's spec: the title itself shows how long it has run and how many tools were used."""
    from hermes_feishu_card import render as render_module

    session = _session()
    session.apply(
        _tool_event(tool_id="t1", name="terminal", detail="pytest -q", sequence=1, created_at=10.0)
    )
    session.apply(
        _tool_event(
            tool_id="t2", name="read_file", detail="session.py", sequence=2, created_at=12.0
        )
    )
    monkeypatch.setattr(
        render_module._time, "time", lambda: session.created_at + 72.0
    )

    title = render_card(session, title="Sales Bot")["header"]["title"]["content"]
    subtitle = render_card(session, title="Sales Bot")["header"]["subtitle"]["content"]
    # Order: name → count (#N) → elapsed. The action left the title for the row beneath it and now
    # names the CONCRETE work (see test_header_action_row_names_the_work_and_stays_within_the_cap).
    assert title == "⏳ Sales Bot · 工具 #2 · 1m12s"
    assert subtitle == "读取文件：session.py"

    session.status = "completed"
    session.duration = 152.0
    session.answer_text = "完成"
    done = render_card(session, title="Sales Bot")["header"]["title"]["content"]
    assert done == "✅ Sales Bot · 工具 #2 · 2m32s"


def test_tool_row_shows_state_name_ordinal_and_elapsed_time():
    session = _session()
    session.apply(
        _tool_event(tool_id="t1", name="read_file", detail="/Users/mac/secret/session.py")
    )

    row = _elements(render_card(session), "tool_activity_0")[0]

    assert row["tag"] == "markdown"
    assert "<text_tag color='blue'>执行中</text_tag>" in row["content"]
    assert "<text_tag color='neutral'>read_file</text_tag>" in row["content"]
    assert "#1" in row["content"]
    assert "读取文件" in row["content"]
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
    # Both steps remain visible in start order; status updates do not renumber them.
    rows = _elements(render_card(session), "tool_activity_")
    assert len(rows) == 2
    assert "#1" in rows[0]["content"] and "已完成" in rows[0]["content"]
    assert "#2" in rows[1]["content"] and "执行中" in rows[1]["content"]


def test_a_late_running_event_does_not_renumber_a_finished_tool():
    """A finished tool that gets another `running` tick keeps its number — so `#N` has no holes.

    Regression, measured in the field: re-numbering a tool that had already gone terminal consumed a
    number while its ONE row moved to the new one, so the old number stayed vacant forever. Every
    hole found on disk had a running tool immediately after it — a checkpoint held ordinals [1…9, 11,
    12] with 10 vacant and the running read_file on #11, which is exactly what the reader asked about
    (「为什么工具 11 在执行，但是显示了前面的却是工具 9？那工具 10 怎么不见了」). All 11 ids in that
    checkpoint were unique, so the second event was a replay/late tick for the SAME call: a call that
    has already finished cannot also be the fresh start of a call.

    The title's 工具 #N tally is the same counter, so a hole there also made the count disagree with
    the rows the card shows.
    """
    session = _session()
    session.apply(_tool_event(tool_id="t1", name="terminal", sequence=1, created_at=1.0))
    session.apply(
        _tool_event(tool_id="t1", name="terminal", status="completed", sequence=2, created_at=2.0)
    )
    # A late tick brings the finished tool back to running — the very shape that used to renumber it.
    session.apply(
        _tool_event(tool_id="t1", name="terminal", status="running", sequence=3, created_at=3.0)
    )
    session.apply(_tool_event(tool_id="t2", name="read_file", sequence=4, created_at=4.0))

    ordinals = sorted(tool.ordinal for tool in session.tools.values())
    assert ordinals == [1, 2]
    # The tally and the rows agree, which is what the reader checks against the card.
    assert session.tool_count == len(session.tools)


def test_a_reused_tool_id_is_still_counted_as_a_new_call():
    """The upstream contract this fix must NOT break: a repeated `completed` for one id counts again.

    Pinned by `test_timeline_preserves_repeated_completed_tool_calls_with_same_id` in test_session.py;
    repeated here as the counterweight, so a future "make ordinals stable" change cannot quietly drop
    a genuinely new execution from the tally. Only a terminal→running tick is treated as a replay.
    """
    session = _session()
    for index in range(3):
        session.apply(
            _tool_event(
                tool_id="execute_code",
                name="execute_code",
                status="completed",
                sequence=index + 1,
                created_at=float(index + 1),
            )
        )

    assert session.tool_count == 3


def test_finished_card_keeps_one_tool_row_as_evidence_of_what_ran():
    """With the rows switched ON, a finished card keeps one row as evidence of what ran.

    ``hide_completed_tool_activity`` (default true) now drops the rows on a completed turn, so this
    keeps the row-format contract pinned for a deployment that sets it false — otherwise the switch
    would have silently removed the only test of what a finished card's row looks like.
    """
    session = _session()
    session.apply(
        _tool_event(tool_id="t1", name="terminal", status="completed", detail="pytest -q")
    )
    session.status = "completed"
    session.answer_text = "完成"

    rows = _elements(render_card(session, hide_completed_tool_activity=False), "tool_activity_")

    assert len(rows) == 1
    assert "<text_tag color='green'>已完成</text_tag>" in rows[0]["content"]

    # The default hides them instead — the same switch, the other way round.
    assert _elements(render_card(session), "tool_activity_") == []


def test_verb_only_tool_line_is_dropped_from_the_row():
    """User-reported noise on a finished card: "已完成 · terminal · #4 · 正在执行终端".

    A verb-only line (no target) only repeats what the tool-name pill already says, so the row
    keeps status + name + ordinal and drops the phrase. The targeted form must still render —
    that is the pair this contract protects (see the assertion at the end).
    """
    session = _session()
    session.apply(
        _tool_event(tool_id="t1", name="terminal", status="completed", detail="正在执行终端")
    )
    session.status = "completed"
    session.answer_text = "完成"

    row = _elements(
        render_card(session, hide_completed_tool_activity=False), "tool_activity_0"
    )[0]["content"]

    assert row == (
        "<text_tag color='green'>已完成</text_tag> · "
        "<text_tag color='neutral'>terminal</text_tag> · #1"
    )
    assert "执行命令" not in row

    targeted = _session()
    targeted.apply(
        _tool_event(tool_id="t1", name="terminal", status="completed", detail="pytest -q")
    )
    targeted.status = "completed"
    targeted.answer_text = "完成"
    # The same phrase WITH a target still earns its line.
    assert "执行命令：pytest -q" in _elements(
        render_card(targeted, hide_completed_tool_activity=False), "tool_activity_0"
    )[0][
        "content"
    ]


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
    assert "工具 #1" in running

    session.status = "completed"
    session.duration = 8.0
    session.answer_text = "答案"
    completed = render_card(session)["body"]["elements"][-1]["content"]
    # Maintainer note (contract change): the count now leads the elapsed time and carries a hash,
    # per the user's request (was "已完成 · 8s · … · 工具 1").
    assert completed.startswith("<text_tag color='green'>已完成</text_tag> · 工具 #1 · 8s")
    assert "工具 #1" in completed


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
