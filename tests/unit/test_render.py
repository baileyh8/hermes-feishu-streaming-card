from hermes_feishu_card.card_limits import inspect_card_limits
from hermes_feishu_card.render import (
    _SPINNER_FRAMES,
    _colored_model_label,
    render_card,
    render_card_result,
    render_legacy_interaction_callback_card,
)
from hermes_feishu_card.session import CardSession, InteractionOption, InteractionState, ToolState
from hermes_feishu_card.status import StatusConfig
import pytest
import time


def test_render_thinking_card_keeps_runtime_status_only_in_footer():
    from hermes_feishu_card.events import SidecarEvent
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "正在分析。"
    event = SidecarEvent(
        schema_version="1", event="tool.updated",
        conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc",
        platform="feishu", sequence=0, created_at=0.0,
        data={"tool_id": "t1", "name": "search", "status": "running"},
    )
    session.apply(event)
    card = render_card(session)
    assert card["schema"] == "2.0"
    # Maintainer note (contract change): this used to assert "⏳ 执行中 · Hermes Agent" — the
    # header carried the state word and nothing else. The user asked the title to also show the
    # running time and tool count ("⏳ Sales Bot · 工具 #3 · 1m12s · 正在读取文件"), so the bare state
    # word is replaced by metrics when there are any. What this test is actually about is
    # unchanged: the runtime status stays out of the thinking text's way, and the header never
    # leaks "正在思考"/"思考中".
    assert card["header"]["title"]["content"] == "⏳ Hermes Agent · 工具 #1"
    assert "subtitle" not in card["header"]
    content = str(card)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    assert main["content"] == "正在分析。"
    assert "正在思考" not in content
    assert "思考中" not in str(card["header"])
    assert "执行中" in content
    assert "生成中" not in content
    assert "正在分析。" in content
    assert "思考过程" in content


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [("fallback", "model-1", "fallback/model-1"), ("fallback", "fallback/model-1", "fallback/model-1"), ("fallback", "", "Unknown"), ("", "model-1", "model-1"), ("<unsafe>", "model-1", "&lt;unsafe&gt;/model-1")],
)
def test_footer_uses_reported_provider_without_duplicate_prefix(provider, model, expected):
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.status = "completed"
    # A real metric is required for the metrics row to render at all: a card with NO metric now
    # shows the state pill alone (the user asked for the "0s · Unknown · ↑0 · ↓0" line to go). This
    # test is about the model/provider prefix rule, so it must supply a turn that reported something.
    session.duration = 1
    session.provider = provider
    session.model = model
    card = render_card(session, footer_fields=["model"])
    footer = next(item for item in card["body"]["elements"] if item.get("element_id") == "footer")
    assert expected in footer["content"]
    assert "fallback/fallback/" not in footer["content"]
    assert "<unsafe>" not in footer["content"]


def test_render_card_accepts_custom_header_title():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "⏳ 执行中 · 研发助手"


def test_render_initial_running_card_shows_context_loading_without_empty_timeline():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    card = render_card(session)

    main = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "main_content"
    )
    assert any(frame in main["content"] for frame in _SPINNER_FRAMES)
    assert "正在加载上下文…" in main["content"]
    assert card["header"]["title"]["content"] == "⏳ 执行中 · Hermes Agent"
    assert "subtitle" not in card["header"]
    assert {"auxiliary_timeline", "tool_summary"}.isdisjoint({
        item.get("element_id") for item in card["body"]["elements"]
    })


def test_render_completed_card_omits_zero_tool_timeline():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.status = "completed"
    session.answer_text = "最终答案"
    session.thinking_text = "不会公开的 raw thinking"

    card = render_card(session)
    assert "不会公开的 raw thinking" not in str(card)
    assert {"auxiliary_timeline", "tool_summary"}.isdisjoint({
        item.get("element_id") for item in card["body"]["elements"]
    })


def test_tool_activity_window_shows_the_current_step_and_the_one_before_it():
    """Two rows, oldest first: the current step and the one it replaced.

    Maintainer note (contract): the content area used to render a single row, so the moment a tool
    was replaced the reader lost the previous step entirely — the user's report was that on a
    changeover they could not tell what the previous command had been
    (「如果更换的时候 不知道上一条执行的是什么」). Running tools are never dropped to make room; the
    window is backfilled from the most recent finished ones instead.
    """
    from hermes_feishu_card.events import SidecarEvent
    from hermes_feishu_card.render import (
        _TOOL_ACTIVITY_WINDOW,
        _render_tool_activity_elements,
    )

    def build(spec):
        session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
        for sequence, (tool_id, status, created_at) in enumerate(spec, start=1):
            session.apply(
                SidecarEvent(
                    schema_version="1",
                    event="tool.updated",
                    conversation_id="chat-1",
                    message_id="msg-1",
                    chat_id="oc_abc",
                    platform="feishu",
                    sequence=sequence,
                    created_at=created_at,
                    data={
                        "tool_id": tool_id,
                        "name": "terminal",
                        "status": status,
                        "detail": "pytest -q",
                    },
                )
            )
        return session

    assert _TOOL_ACTIVITY_WINDOW == 2

    rows = _render_tool_activity_elements(
        build(
            (
                ("terminal-1", "completed", 1.0),
                ("terminal-2", "completed", 2.0),
                ("terminal-3", "running", 3.0),
            )
        ),
        display_status="running",
    )

    assert [row["element_id"] for row in rows] == ["tool_activity_0", "tool_activity_1"]
    # Oldest first, newest last — and the newest is the running one.
    assert "#2" in rows[0]["content"]
    assert "执行中" not in rows[0]["content"]
    assert "#3" in rows[1]["content"]
    assert "执行中" in rows[1]["content"]

    # The window is a ceiling, not a quota: one tool still renders one row.
    assert len(
        _render_tool_activity_elements(
            build((("terminal-1", "completed", 1.0),)), display_status="completed"
        )
    ) == 1


def test_running_tool_without_model_text_removes_loading_placeholder_from_body():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=10.0,
            data={
                "tool_id": "terminal-1",
                "name": "terminal",
                "status": "running",
                "detail": "pytest -q",
            },
        )
    )

    card = render_card(session)

    row = next(
        item for item in card["body"]["elements"]
        if item.get("element_id") == "tool_activity_0"
    )
    assert not any(
        str(item.get("element_id", "")).startswith("main_content")
        for item in card["body"]["elements"]
    )
    assert "正在加载上下文…" not in str(card)


def test_v4_running_card_uses_state_title_and_public_interim_body():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.thinking_text = "我先检查天气客户端。"
    session.latest_tool_preview = "读取文件：weather_client.py"

    card = render_card(session, title="Hermes Agent")
    main = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "main_content"
    )
    footer = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "footer"
    )

    assert card["header"]["title"]["content"] == "⏳ 执行中 · Hermes Agent"
    assert "subtitle" not in card["header"]
    assert not any(
        element.get("element_id") == "runtime_summary"
        for element in card["body"]["elements"]
    )
    assert main["content"] == "我先检查天气客户端。"
    assert "gpt-" not in footer["content"]
    assert "ctx " not in footer["content"]


def test_compaction_phase_replaces_header_title_and_hides_stale_tool_summary():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.thinking_text = "已保留的公开阶段说明"
    session.latest_tool_preview = "读取文件：weather_client.py"
    session.runtime_phase_text = "正在压缩上下文"

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "⏳ 执行中 · 研发助手"
    assert card["header"]["subtitle"]["content"] == "正在压缩上下文"
    assert "读取文件：weather_client.py" not in str(card["header"])


def test_pending_interaction_has_priority_over_compaction_phase():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.runtime_phase_text = "正在压缩上下文"
    session.active_interaction = InteractionState(
        interaction_id="approval-compaction",
        kind="approval",
        prompt="允许继续执行吗？",
    )

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "待审批：允许继续执行吗？"
    assert "正在压缩上下文" not in str(card["header"])


def test_an_interaction_with_its_own_card_is_not_rendered_into_the_turn_card():
    """One approval must not appear as two cards.

    Maintainer note (contract change): with a pending approval in callback mode, render_card used to
    return the APPROVAL card shape for the session's own card as well — while the standalone approval
    card had already been sent and recorded on the interaction as `feishu_message_id`. Two cards asked
    the same question and only the standalone one accepted the click; the user reported that
    double-track ("双轨审批卡"). Now the session card keeps its streaming shape, keeps the interaction
    rows out of its body, and the header still announces the pending decision.
    """
    def _session(*, feishu_message_id: str) -> CardSession:
        session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
        session.active_interaction = InteractionState(
            interaction_id="approval-1",
            kind="approval",
            prompt="允许继续执行吗？",
            description="rm -rf /tmp/build",
            options=[
                InteractionOption(label="允许", value="allow"),
                InteractionOption(label="拒绝", value="deny"),
            ],
            feishu_message_id=feishu_message_id,
        )
        return session

    def _elements(card) -> list[dict]:
        return card.get("elements") or (card.get("body") or {}).get("elements") or []

    def _has_approval_rail(card) -> bool:
        # The approval rail's own tells: the authorisation hint, the button row, or its footer.
        rendered = str(card)
        return (
            "请核对下方完整操作后" in rendered
            or any(element.get("tag") == "action" for element in _elements(card))
            or "等待选择…" in rendered
        )

    # Without a card of its own the approval rides on this card — the existing contract.
    assert _has_approval_rail(render_card(_session(feishu_message_id=""), title="研发助手"))

    # With its own card, this one stays a streaming card: no second question, no inert buttons.
    card = render_card(_session(feishu_message_id="om_approval_card"), title="研发助手")
    assert not _has_approval_rail(card)
    assert [
        element
        for element in _elements(card)
        if str(element.get("element_id", "")).startswith("interaction")
    ] == []
    # ...and the pending approval is still announced.
    assert card["header"]["title"]["content"] == "待审批：允许继续执行吗？"


def test_tool_activity_clears_compaction_and_restores_tool_subtitle():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.runtime_phase_text = "正在压缩上下文"
    assert session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="c",
            message_id="m",
            chat_id="oc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "tool_id": "tool-1",
                "name": "terminal",
                "status": "running",
                "detail": "pytest",
            },
        )
    )

    card = render_card(session, title="研发助手")

    assert session.runtime_phase_text == ""
    # The header's second row now names the CONCRETE work; the title stays an identity line.
    # Maintainer note (contract change): the phrase used to lead the title ("⏳ 正在执行终端 ·
    # 研发助手"), then sat LAST on the title line, then moved to the row beneath it. The user then
    # asked for the concrete action there ("标题行这边的第二个行像工具行那样看到具体的工具动作"), so
    # the sub-title carries phrase + target now, same source as the content-area tool row.
    assert card["header"]["title"]["content"] == "⏳ 研发助手 · 工具 #1"
    assert card["header"]["subtitle"]["content"] == "执行命令：pytest"
    row = next(
        item for item in card["body"]["elements"]
        if item.get("element_id") == "tool_activity_0"
    )
    assert "pytest" in row["content"]


def test_tool_row_names_the_work_when_only_the_arguments_are_known():
    """A tool whose preview line is missing must still say what the agent is doing.

    The stored detail is multi-line ("preview\\n参数: …\\n耗时: …" — see
    session._tool_detail_from_event_data). When the preview line is absent the arguments used to be
    discarded as "no target", so the row rendered as a bare "执行中 · terminal · #2" and the reader
    could not tell what the running agent was doing. The arguments now carry the row instead.
    """
    from hermes_feishu_card.render import _tool_activity_row

    tool = ToolState(
        tool_id="t1",
        name="terminal",
        status="running",
        detail='参数: {"command": "git status --short"}\n耗时: 12s',
        ordinal=2,
        started_at=0.0,
    )

    row = _tool_activity_row(tool, index=0, now=12.0)

    assert "执行命令：git status --short" in row["content"]
    # The arguments blob is an implementation detail of the event, not something to read.
    assert '{"command"' not in row["content"]
    # Meta lines have their own slot in the row (the elapsed time); they are not the target.
    assert "耗时" not in row["content"]


def test_tool_row_is_three_rows_status_then_action_then_parameters():
    """Status / action / parameters get a row each — cramming them together reads as noise.

    The user's report was that one joined line ("执行中 · terminal · #2 · 执行命令：pytest -q 参数: …")
    is confusing ("不然都挤在一行 很混乱"). Row 1 is the status, row 2 the action, row 3 the parameters;
    the parameter row only exists when the arguments carry something the action does not.
    """
    from hermes_feishu_card.render import _tool_activity_row

    tool = ToolState(
        tool_id="t1",
        name="terminal",
        status="running",
        detail='参数: {"command": "pytest -q", "timeout": 120}',
        ordinal=4,
        # Epoch-sized: the duration is only added while a running tool has a start time, and a small
        # value like 0.0 (or `now - 12` with a small `now`) is falsy and silently drops it.
        started_at=1_000_000.0,
    )

    rows = _tool_activity_row(tool, index=0, now=1_000_012.0)["content"].splitlines()

    assert len(rows) == 3
    # The ordinal precedes the duration, matching the header ("工具 #N · 1m12s"): the count names the
    # tool, the time qualifies it. Asserted as an ORDER, not just presence — presence alone would
    # pass with the two swapped, which is exactly what the user reported ("时间调整到编号后面").
    assert "terminal" in rows[0] and "#4" in rows[0] and "12s" in rows[0]
    assert rows[0].index("#4") < rows[0].index("12s")
    assert rows[0].endswith("12s")
    assert "执行命令" not in rows[0]  # the action must not share the status row
    assert rows[1] == "执行命令：pytest -q"
    # The command named the work already, so only its siblings count as parameters.
    assert rows[2] == "参数: timeout=120"


def test_tool_row_drops_the_parameter_row_when_it_would_only_repeat_the_action():
    """No fourth copy of the same value: a lone argument that named the work leaves no parameter row."""
    from hermes_feishu_card.render import _tool_activity_row

    tool = ToolState(
        tool_id="t1",
        name="read_file",
        status="completed",
        detail='参数: {"path": "/Users/mac/render.py"}',
        ordinal=1,
    )

    rows = _tool_activity_row(tool, index=0, now=0.0)["content"].splitlines()

    assert rows == [
        "<text_tag color='green'>已完成</text_tag> · <text_tag color='neutral'>read_file</text_tag> · #1",
        "读取文件：render.py",
    ]


def test_tool_row_reads_its_first_detail_line_only_and_never_repeats_the_phrase():
    """One line, phrased once: a multi-line detail must not be flattened or phrased twice."""
    from hermes_feishu_card.render import _tool_activity_row, _tool_activity_text

    preview_and_meta = ToolState(
        tool_id="t1",
        name="terminal",
        status="completed",
        detail='pytest -q\n参数: {"command": "pytest -q"}\n耗时: 12s',
    )
    assert _tool_activity_text(preview_and_meta) == "执行命令：pytest -q"

    already_phrased = ToolState(
        tool_id="t2", name="terminal", status="completed", detail="执行命令：pytest -q"
    )
    assert _tool_activity_text(already_phrased) == "执行命令：pytest -q"

    # A detail that is ONLY meta says nothing the row does not already say.
    meta_only = ToolState(tool_id="t3", name="terminal", status="completed", detail="耗时: 12s")
    assert _tool_activity_text(meta_only) == ""
    row = _tool_activity_row(meta_only, index=0, now=0.0)
    assert "耗时" not in row["content"]


def test_unrecognised_tool_row_still_names_its_task():
    """A plugin/MCP tool falls back to "使用 <tool>" — which must also carry the target.

    "使用 delegate task" on its own is the same content-free row the targeted form exists to avoid;
    the header is unaffected because it reads only the phrase before "：".
    """
    from hermes_feishu_card.render import _tool_activity_text

    known = ToolState(
        tool_id="t1", name="delegate_task", status="running", detail='参数: {"goal": "分析日志"}'
    )
    assert _tool_activity_text(known) == "分派子任务：分析日志"

    unknown = ToolState(
        tool_id="t2", name="mystery_tool", status="running", detail="参数: some raw text"
    )
    assert _tool_activity_text(unknown) == "使用 mystery tool：some raw text"


def test_completed_card_never_renders_stale_compaction_phase():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "最终答案"
    session.runtime_phase_text = "正在压缩上下文"

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "✅ 研发助手"
    assert "正在压缩上下文" not in str(card)


def test_v4_answer_delta_remains_primary_over_public_interim_text():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.thinking_text = "公开阶段说明"
    session.answer_text = "主回答已经开始"

    card = render_card(session)
    main = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "main_content"
    )

    assert main["content"] == "主回答已经开始"
    assert "公开阶段说明" not in str(card)


def test_v4_waiting_prompt_moves_to_header_without_body_duplication():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.active_interaction = InteractionState(
        interaction_id="approval-1",
        kind="approval",
        prompt="允许覆盖文件吗？",
        description="目标文件：report.html",
        options=[],
    )

    card = render_card(session)
    waiting = next(
        item
        for item in reversed(card["elements"])
        if item.get("tag") == "markdown"
    )

    assert card["header"]["title"]["content"] == "待审批：允许覆盖文件吗？"
    assert "subtitle" not in card["header"]
    assert str(card).count("允许覆盖文件吗？") == 1
    assert "目标文件：report.html" in str(card)
    assert "等待" in waiting["content"]
    assert "ctx " not in waiting["content"]


def test_multi_select_form_binds_submit_action_to_callback_token():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.active_interaction = InteractionState(
        interaction_id="approval-1",
        kind="clarify",
        prompt="请选择要继续的项目",
        options=[],
        callback_token="callback-secret",
        multi_select=True,
    )

    card = render_card(session)

    form = next(
        item
        for item in card["elements"]
        if item.get("tag") == "form"
    )
    submit = next(
        item
        for item in form["elements"]
        if item.get("tag") == "button"
    )
    assert submit["action_type"] == "form_submit"
    assert submit["value"] == {"profile_id": "default"}
    assert submit["name"] == "hfc_confirm_callback-secret"
    assert "approval-1" not in submit["name"]


def test_v4_completed_restores_configured_title_and_metrics():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.latest_tool_preview = "执行命令：pytest"
    session.status = "completed"
    session.answer_text = "最终答案"
    session.duration = 2.0
    session.model = "gpt-5.5"

    card = render_card(session, title="研发助手")

    # Maintainer note (contract change): the completed title now also carries the metrics the user
    # asked to see there ("✅ 研发助手 · 2s"), instead of the bare "✅ 研发助手". The point of this
    # test is untouched: the CONFIGURED title is restored (the stale tool preview never comes back
    # into the header), and duration/model stay visible somewhere ("2s"/"gpt-5.5" below).
    assert card["header"]["title"]["content"] == "✅ 研发助手 · 2s"
    assert "执行命令：pytest" not in str(card["header"])
    assert "2s" in str(card)
    assert "gpt-5.5" in str(card)


def test_v4_completed_reply_card_uses_only_native_feishu_quote_header():
    session = CardSession(conversation_id="c", message_id="om_user", chat_id="oc")
    session.status = "completed"
    session.answer_text = "最终答案"
    session.reply_to_message_id = "om_user"

    card = render_card(session, title="Hermes Agent")

    assert "header" not in card
    assert "最终答案" in str(card)
    assert card["config"]["summary"]["content"] == "最终答案"
    footer = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "footer"
    )
    # Maintainer note (contract change): this used to pin the completion note INSIDE the footer
    # (first ahead of the state pill, then behind it). The user asked for the footer to stop
    # repeating it ("footer 区域不显示本轮回复结束"), so the footer now carries the state and the
    # metrics only. What this test protects is that the state survives on its own, and that the
    # note does NOT creep back into the footer on this rail — the header is dropped here, so the
    # note's other homes (the header sub-title, the native completion message) are out of scope.
    content = footer["content"]
    assert "已完成" in content
    assert "本轮回复结束" not in content


def test_v4_plain_completed_reply_card_footer_claims_no_completion_note():
    """A completed turn must not claim 本轮回复结束 in its footer.

    This rail never had the note (the native-quote rail did), and since the user asked for the
    footer to stop showing it at all, the assertion now guards both rails: the footer carries the
    state and the metrics, never a completion line — a completed card that was never replied to
    saying "回复结束" would claim a reply that never happened.
    """

    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "最终答案"

    card = render_card(session, title="Hermes Agent")
    footer = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "footer"
    )

    assert "本轮回复结束" not in footer["content"]


def test_v4_failed_retains_preview_and_status_only_footer():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.latest_tool_preview = "读取文件：演示天气数据"
    session.status = "failed"
    session.answer_text = "数据源暂时不可用。"
    # A stopped run that reported metrics keeps its whole line — that is the contract this test
    # protects. Metrics are supplied explicitly because a card with NONE now shows the pill alone:
    # the "0s · Unknown · ↑0 · ↓0 · ctx 0/0 0%" row was the zero-noise the user asked to drop.
    # Deliberately tokens + context rather than a duration: the HEADER also renders a duration, and
    # this test pins the header to "⛔ Hermes Agent" exactly.
    session.tokens = {"input_tokens": 100, "output_tokens": 20}
    session.context = {"used_tokens": 1000, "max_tokens": 128000}

    card = render_card(session, title="Hermes Agent")
    footer = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "footer"
    )

    assert card["header"]["title"]["content"] == "⛔ Hermes Agent"
    assert "subtitle" not in card["header"]
    # Maintainer note (contract change): this used to assert the footer was ONLY the 已停止 pill
    # ("ctx " explicitly absent). The user asked a stopped card to keep its status information —
    # that is how a reader sees WHERE the run stopped — so the pill now sits on top of the same
    # fields a completed card shows.
    assert footer["content"].startswith("<text_tag color='red'>已停止</text_tag>")
    assert "ctx " in footer["content"]
    assert "↑100" in footer["content"]


def test_v4_missing_preview_keeps_configured_title():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.thinking_text = "正在处理。"

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "⏳ 执行中 · 研发助手"


@pytest.mark.parametrize(
    "preview",
    [
        "```bash\nexport APP_SECRET=unsafe\n```",
        "curl https://example.test?a=1&token=unsafe",
        "deploy --password unsafe --api-key=unsafe",
    ],
)
def test_v4_runtime_header_is_single_line_bounded_and_redacted(preview):
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.latest_tool_preview = preview + ("x" * 300)
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="c",
            message_id="m",
            chat_id="oc",
            platform="feishu",
            sequence=1,
            created_at=10.0,
            data={
                "tool_id": "terminal-1",
                "name": "terminal",
                "status": "running",
                "detail": preview + ("x" * 300),
            },
        )
    )

    card = render_card(session)
    rendered = str(card)

    assert "unsafe" not in rendered
    assert "[REDACTED]" in rendered


def test_render_completed_card_replaces_thinking():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "不会展示"
    session.answer_text = "最终答案"
    session.status = "completed"
    card = render_card(session)
    content = str(card)
    assert card["header"]["subtitle"]["content"] == "本轮回复结束"
    assert "最终答案" in content
    assert "不会展示" not in content


def test_v3818_normal_completed_card_keeps_element_order_and_configured_footer():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.status = "completed"
    session.answer_text = "最终答案"
    session.duration = 3.0
    session.model = "MiniMax M2.7"
    session.tokens = {"output_tokens": 34}

    card = render_card(session, footer_fields=["model", "duration", "output_tokens"])

    assert [element["element_id"] for element in card["body"]["elements"]] == [
        "main_content",
        "main_divider",
        "footer",
    ]
    assert card["body"]["elements"][-1] == {
        "tag": "markdown",
        "element_id": "footer",
        "content": "<text_tag color='green'>已完成</text_tag> · MiniMax M2.7 · 3s · ↓34",
        "text_size": "x-small",
    }


def test_v3818_normal_failed_card_keeps_element_order_and_footer():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.status = "failed"
    session.answer_text = "处理失败"

    card = render_card(session, footer_fields=["model", "duration"])

    assert [element["element_id"] for element in card["body"]["elements"]] == [
        "main_content",
        "main_divider",
        "footer",
    ]
    # Maintainer note (contract change): the footer used to collapse to the bare 已停止 pill; the
    # user asked a stopped card to keep its status fields (see the note above). Element order is
    # what this test is about, and it is unchanged.
    assert card["body"]["elements"][-1]["content"].startswith(
        "<text_tag color='red'>已停止</text_tag>"
    )


@pytest.mark.parametrize(
    ("model", "color"),
    [
        ("GPT-5.5", "blue"),
        ("claude-opus-4", "orange"),
        ("DeepSeek-V4", "indigo"),
        ("KIMI-K2", "purple"),
        ("GLM-5", "green"),
        ("Tencent/hunyuan", "teal"),
    ],
)
def test_model_footer_uses_sanitized_semantic_color(model, color):
    assert _colored_model_label(model) == f'<font color="{color}">{model}</font>'


def test_model_footer_escapes_unknown_and_malicious_model_names():
    assert _colored_model_label("MiniMax M2.7") == "MiniMax M2.7"
    escaped = _colored_model_label('<script>alert("x")</script>')
    assert escaped == "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"
    assert "<script>" not in escaped


def test_model_footer_color_preserves_layout_order_and_configured_fields():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.status = "completed"
    session.answer_text = "最终答案"
    session.duration = 3.0
    session.model = "gpt-5.5"
    session.tokens = {"output_tokens": 34}

    card = render_card(session, footer_fields=["model", "duration", "output_tokens"])

    assert [element["element_id"] for element in card["body"]["elements"]] == [
        "main_content",
        "main_divider",
        "footer",
    ]
    assert card["body"]["elements"][-1] == {
        "tag": "markdown",
        "element_id": "footer",
        "content": "<text_tag color='green'>已完成</text_tag> · <font color=\"blue\">gpt-5.5</font> · 3s · ↓34",
        "text_size": "x-small",
    }


def test_model_footer_color_applies_only_where_fields_render():
    """The coloured model label belongs to the field set, not to every state.

    Maintainer note (contract change): this was `..._does_not_change_non_completed_or_empty_fields`
    and asserted a stopped card shows NO fields at all (that is how the old footer behaved — it
    collapsed to the bare 已停止 pill). The user asked a stopped card to keep its status
    information so others can see where the run stopped, so the stopped footer now renders the same
    fields as a completed one, model colour included. What survives from the original intent: a
    RUNNING footer shows no field set (so no coloured model there).

    Second change: an empty field list with no reported metric now yields the pill ALONE. It used
    to append a duration fallback, which printed "已完成 · 0s" for a card that simply never reported
    a duration — the same zero-noise the user rejected ("为什么是 unknown。0。 如果这样的话感觉
    不需要展示这一行"). "Hide every field" and "show one field anyway" cannot both be true.
    """
    thinking = CardSession(conversation_id="c", message_id="m1", chat_id="oc")
    thinking.model = "gpt-5.5"
    running_footer = render_card(thinking)["body"]["elements"][-1]["content"]
    assert "执行中" in running_footer
    assert "gpt-5.5" not in running_footer

    failed = CardSession(conversation_id="c", message_id="m2", chat_id="oc")
    failed.status = "failed"
    failed.model = "gpt-5.5"
    failed_footer = render_card(failed)["body"]["elements"][-1]["content"]
    assert failed_footer.startswith("<text_tag color='red'>已停止</text_tag>")
    assert '<font color="blue">gpt-5.5</font>' in failed_footer

    completed = CardSession(conversation_id="c", message_id="m3", chat_id="oc")
    completed.status = "completed"
    completed.model = "gpt-5.5"
    assert render_card(completed, footer_fields=[])["body"]["elements"][-1]["content"] == "<text_tag color='green'>已完成</text_tag>"

    # A reported duration must still render when its field IS configured — the guard drops empty
    # metrics, not real ones.
    measured = CardSession(conversation_id="c", message_id="m4", chat_id="oc")
    measured.status = "completed"
    measured.duration = 92
    assert "1m32s" in render_card(measured, footer_fields=["duration"])["body"]["elements"][-1]["content"]


def test_progress_handoff_changes_only_header_status_from_completed_card():
    answer = "数据收集中，数据到位后我会继续生成报告。"
    completed = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    completed.status = "completed"
    completed.display_status = "completed"
    completed.answer_text = answer
    inferred = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    inferred.status = "completed"
    inferred.answer_text = answer

    completed_card = render_card(completed)
    inferred_card = render_card(inferred)

    assert completed_card["header"]["template"] == "green"
    assert completed_card["header"]["subtitle"]["content"] == "本轮回复结束"
    assert inferred_card["header"]["template"] == "blue"
    assert "subtitle" not in inferred_card["header"]
    assert inferred_card["config"]["summary"]["content"] == "生成中"
    assert inferred_card["body"] == completed_card["body"]


def test_render_status_uses_custom_conservative_marker_pairs():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.status = "completed"
    session.answer_text = "queued now; resume later"
    config = StatusConfig(active_markers=("queued",), future_markers=("resume later",))

    card = render_card(session, status_config=config)

    assert card["header"]["template"] == "blue"
    assert card["config"]["summary"]["content"] == "生成中"


def test_render_pending_interaction_as_buttons():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="interaction.requested",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "interaction_id": "approval-1",
                "kind": "approval",
                "prompt": "允许执行命令吗？",
                "description": "rm -rf /tmp/demo",
                "allow_custom_input": False,
                "options": [
                    {"label": "允许一次", "value": "once", "style": "primary"},
                    {"label": "拒绝", "value": "deny", "style": "danger"},
                ],
            },
        )
    )

    card = render_card(session, interaction_profile_id="work")

    assert "schema" not in card
    assert "body" not in card
    assert card["config"] == {"wide_screen_mode": True, "update_multi": True}
    action = next(
        element
        for element in card["elements"]
        if element.get("tag") == "action"
    )
    buttons = action["actions"]
    assert [item["text"]["content"] for item in buttons] == ["1", "2"]
    assert "behaviors" not in buttons[0]
    assert buttons[0]["value"]["hfc_action"] == "interaction.select"
    assert buttons[0]["value"]["interaction_id"] == "approval-1"
    assert buttons[0]["value"]["choice"] == "once"
    assert buttons[0]["value"]["token"]
    assert buttons[0]["value"]["profile_id"] == "work"
    assert "interaction_actions" not in str(card)
    assert "rm -rf /tmp/demo" in str(card)
    assert "hfc_other" not in str(card)
    assert "自定义" not in str(card)


def test_render_clarify_custom_input_only_when_capability_is_enabled():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="interaction.requested",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "interaction_id": "clarify-1",
                "kind": "clarify",
                "prompt": "请选择处理方式",
                "allow_custom_input": True,
                "options": [
                    {"label": "继续", "value": "continue"},
                    {"label": "取消", "value": "cancel"},
                ],
            },
        )
    )

    card = render_card(session)

    assert "hfc_other" in str(card)
    assert "输入自定义内容" in str(card)


def test_render_pending_interaction_as_text_choices_for_localhost_mode():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="interaction.requested",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "interaction_id": "clarify-1",
                "kind": "clarify",
                "prompt": "请选择处理方式",
                "options": [
                    {"label": "删除空文件", "value": "delete"},
                    {"label": "保留并补索引", "value": "keep"},
                ],
            },
        )
    )

    card = render_card(session, interaction_mode="text")

    content = str(card)
    assert not any(element.get("tag") == "button" for element in card["body"]["elements"])
    assert "1. 删除空文件" in content
    assert "2. 保留并补索引" in content
    assert "Reply with the number" in content


def test_render_completed_interaction_replaces_buttons_with_choice():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="interaction.requested",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "interaction_id": "approval-1",
                "kind": "approval",
                "prompt": "允许执行命令吗？",
                "description": "敏感命令详情不应保留在完成态 tooltip",
                "options": [{"label": "允许一次", "value": "once"}],
            },
        )
    )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="interaction.completed",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=2,
            created_at=0.0,
            data={
                "interaction_id": "approval-1",
                "choice": "once",
                "choice_label": "允许一次",
                "user_name": "Bailey",
            },
        )
    )

    card = render_card(session)

    assert not any(
        element.get("tag") == "button" and element.get("behaviors")
        for element in card["body"]["elements"]
    )
    assert "已选择：允许一次" in str(card)
    assert "Bailey" in str(card)
    visible = "\n".join(
        element.get("content", "") for element in card["body"]["elements"]
        if element.get("tag") == "markdown"
    )
    assert "允许执行命令吗？" in visible
    assert "1. 允许一次" in visible
    assert "已选择：允许一次 by Bailey" in visible
    # Contract change: the operation scope (the exact command) is RETAINED after the decision.
    # Dropping it left the card unauditable — neither the approver nor anyone reviewing the chat
    # afterwards could see what had actually been approved. The command is already visible while
    # the approval is pending, so keeping it exposes nothing new to the chat.
    assert "敏感命令详情" in visible
    assert not any(e.get("hover_tips") for e in card["body"]["elements"])


def test_render_completed_legacy_callback_card_removes_controls_and_credentials():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.active_interaction = InteractionState(
        interaction_id="clarify-1",
        kind="clarify",
        prompt="请选择",
        status="completed",
        callback_token="secret-token",
        choice="alpha",
        choice_label="Alpha",
        options=[InteractionOption(label="Alpha", value="alpha")],
    )

    card = render_legacy_interaction_callback_card(
        session,
        title="Hermes Agent",
    )

    assert "schema" not in card and "body" not in card
    assert "已选择：Alpha" in str(card)
    assert "请选择" in str(card["elements"])
    assert "1. Alpha" in str(card["elements"])
    assert "secret-token" not in str(card)
    assert not any(
        item.get("tag") in {"action", "form"} for item in card["elements"]
    )


def test_render_failed_legacy_callback_card_is_noninteractive():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.active_interaction = InteractionState(
        interaction_id="clarify-1",
        kind="clarify",
        prompt="请选择",
        status="failed",
        error="交互已过期",
    )

    card = render_legacy_interaction_callback_card(
        session,
        title="Hermes Agent",
    )

    assert "schema" not in card and "body" not in card
    assert "交互已过期" in str(card)
    assert "action" not in {item.get("tag") for item in card["elements"]}


def test_render_completed_card_shows_attachment_summary():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"
    session.attachments = [{"kind": "file", "name": "report.pdf", "summary": "report.pdf"}]

    card = render_card(session)

    assert "附件：report.pdf" in str(card)


def test_render_completed_card_places_attachment_summary_before_tools():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"
    session.attachments = [{"kind": "file", "name": "report.pdf", "summary": "report.pdf"}]

    card = render_card(session)

    element_ids = [element.get("element_id") for element in card["body"]["elements"]]
    assert element_ids.index("attachment_summary") < element_ids.index("main_divider")


def test_render_completed_card_shows_at_most_eight_attachments():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"
    session.attachments = [
        {"kind": "file", "name": f"file-{index}.txt", "summary": f"file-{index}.txt"}
        for index in range(10)
    ]

    card = render_card(session)

    attachment_element = next(
        element
        for element in card["body"]["elements"]
        if element.get("element_id") == "attachment_summary"
    )
    assert "file-7.txt" in attachment_element["content"]
    assert "file-8.txt" not in attachment_element["content"]
    assert "file-9.txt" not in attachment_element["content"]


def test_render_completed_card_without_attachments_has_no_attachment_summary():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"

    card = render_card(session)

    element_ids = [element.get("element_id") for element in card["body"]["elements"]]
    assert "attachment_summary" not in element_ids


def test_render_long_main_content_splits_markdown_elements_without_truncating():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "甲" * 2600 + "乙" * 2600
    session.status = "completed"

    card = render_card(session)

    main_elements = [
        item
        for item in card["body"]["elements"]
        if str(item.get("element_id", "")).startswith("main_content")
    ]
    assert len(main_elements) == 3
    assert all(len(item["content"]) <= 2400 for item in main_elements)
    assert "".join(item["content"] for item in main_elements) == session.answer_text


def test_render_card_default_json_has_no_style_or_explicit_body_text_size():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"

    card = render_card(session)
    main = next(
        item for item in card["body"]["elements"] if item["element_id"] == "main_content"
    )
    footer = next(
        item for item in card["body"]["elements"] if item["element_id"] == "footer"
    )

    assert "style" not in card["config"]
    assert "text_size" not in main
    assert footer["text_size"] == "x-small"


def test_render_scalar_text_sizes_apply_to_each_role_and_body_chunks():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    base = {
        "schema_version": "1",
        "conversation_id": "c",
        "message_id": "m",
        "chat_id": "oc",
        "platform": "feishu",
        "created_at": 0.0,
    }
    session.apply(
        SidecarEvent(
            event="answer.delta",
            sequence=1,
            data={"text": "先分析实现。"},
            **base,
        )
    )
    session.apply(
        SidecarEvent(
            event="tool.updated",
            sequence=2,
            data={
                "tool_id": "terminal",
                "name": "terminal",
                "status": "completed",
                "detail": "pytest",
            },
            **base,
        )
    )
    session.apply(
        SidecarEvent(
            event="system.notice",
            sequence=3,
            data={"title": "提示", "content": "已切换上下文"},
            **base,
        )
    )
    session.apply(
        SidecarEvent(
            event="message.completed",
            sequence=4,
            data={"answer": "甲" * 2600},
            **base,
        )
    )
    session.attachments = [{"name": "report.txt"}]

    card = render_card(
        session,
        timeline_expanded=True,
        text_sizes={
            "body": "large",
            "reasoning": "medium",
            "tool": "small",
            "notice": "notation",
            "footer": "normal",
        },
    )

    main = [
        item
        for item in card["body"]["elements"]
        if str(item.get("element_id", "")).startswith("main_content")
    ]
    timeline = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    reasoning = next(item for item in timeline["elements"] if "思考" in item["content"])
    tool = next(item for item in timeline["elements"] if "terminal" in item["content"])
    notice = next(item for item in timeline["elements"] if "提示" in item["content"])
    attachment = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "attachment_summary"
    )
    footer = next(
        item for item in card["body"]["elements"] if item.get("element_id") == "footer"
    )

    assert len(main) > 1
    assert all(item["text_size"] == "large" for item in main)
    assert reasoning["text_size"] == "medium"
    assert tool["text_size"] == "small"
    assert notice["text_size"] == "notation"
    assert footer["text_size"] == "normal"
    assert "text_size" not in attachment


def test_render_independent_notice_uses_notice_text_size():
    session = CardSession(conversation_id="c", message_id="n", chat_id="oc")
    session.delivery_kind = "notice"
    session.notice_title = "通知"
    session.answer_text = "通知正文"
    session.status = "completed"

    card = render_card(session, text_sizes={"body": "large", "notice": "small"})
    main = next(
        item for item in card["body"]["elements"] if item["element_id"] == "main_content"
    )

    assert main["text_size"] == "small"


def test_render_device_text_size_emits_footer_alias():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"

    card = render_card(
        session,
        text_sizes={
            "footer": {
                "default": "x-small",
                "pc": "x-small",
                "mobile": "notation",
            }
        },
    )
    footer = next(
        item for item in card["body"]["elements"] if item["element_id"] == "footer"
    )

    assert card["config"]["style"]["text_size"] == {
        "hfc_footer": {
            "default": "x-small",
            "pc": "x-small",
            "mobile": "notation",
        }
    }
    assert footer["text_size"] == "hfc_footer"


def test_render_text_size_aliases_use_deterministic_role_order():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"
    mapping = {"default": "normal", "pc": "large", "mobile": "small"}

    card = render_card(
        session,
        text_sizes={
            "footer": mapping,
            "body": mapping,
            "reasoning": mapping,
        },
    )

    assert list(card["config"]["style"]["text_size"]) == [
        "hfc_body",
        "hfc_footer",
    ]
    assert "hfc_reasoning" not in card["config"]["style"]["text_size"]


def test_render_scalar_only_text_sizes_do_not_emit_style_aliases():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"

    card = render_card(session, text_sizes={"body": "large", "footer": "small"})

    assert "style" not in card["config"]


def test_render_long_table_chunks_keep_markdown_table_shape():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    rows = "\n".join(f"| {index} | {'甲' * 80} |" for index in range(80))
    session.answer_text = f"| id | content |\n| --- | --- |\n{rows}\n"
    session.status = "completed"

    card = render_card(session)

    main_elements = [
        item
        for item in card["body"]["elements"]
        if str(item.get("element_id", "")).startswith("main_content")
    ]
    assert len(main_elements) > 1
    assert all(len(item["content"]) <= 2400 for item in main_elements)
    assert all("| id | content |" in item["content"] for item in main_elements)
    assert all("| --- | --- |" in item["content"] for item in main_elements)


def test_render_long_code_block_chunks_remain_fenced():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    code = "\n".join(f"print({index!r})  # {'x' * 80}" for index in range(90))
    session.answer_text = f"```python\n{code}\n```"
    session.status = "completed"

    card = render_card(session)

    main_elements = [
        item
        for item in card["body"]["elements"]
        if str(item.get("element_id", "")).startswith("main_content")
    ]
    assert len(main_elements) > 1
    assert all(len(item["content"]) <= 2400 for item in main_elements)
    assert all(item["content"].startswith("```python\n") for item in main_elements)
    assert all(item["content"].rstrip().endswith("```") for item in main_elements)


def test_render_timeline_limits_reasoning_without_truncating_answer():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "思考" * 200},
        )
    )
    sequence = 2
    for index in range(6):
        session.apply(
            SidecarEvent(
                schema_version="1",
                event="tool.updated",
                conversation_id="chat-1",
                message_id="msg-1",
                chat_id="oc_abc",
                platform="feishu",
                sequence=sequence,
                created_at=0.0,
                data={
                    "tool_id": f"tool-{index}",
                    "name": "search",
                    "status": "completed",
                    "detail": "结果" * 200,
                },
            )
        )
        sequence += 1
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=sequence,
            created_at=0.0,
            data={"text": "最终回答完整保留" * 20},
        )
    )

    card = render_card(
        session,
        max_reasoning_chars=80,
        max_tool_result_chars=80,
        max_timeline_items=3,
    )

    content = str(card)
    assert "最终回答完整保留" in content
    assert "思考内容过长，已截断" in content
    assert "工具详情过长，已截断" in content
    assert "内容已折叠" not in content
    timeline = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    timeline_content = "".join(item["content"] for item in timeline["elements"])
    assert "已折叠" in timeline_content
    assert len(timeline_content) < 1000


def test_render_failed_card_shows_error_without_thinking():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "不会展示"
    session.answer_text = "处理出错"
    session.status = "failed"
    card = render_card(session)
    content = str(card)
    assert card["config"]["summary"]["content"] == "处理失败"
    assert card["header"]["template"] == "red"
    assert "subtitle" not in card["header"]
    assert card["config"]["summary"]["content"] == "处理失败"
    assert "处理出错" in content
    assert "不会展示" not in content
    assert "已停止" in content


def test_render_card_filters_think_tags_at_render_boundary():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "<think>hidden</think>可见内容"
    card = render_card(session)
    content = str(card)
    assert "<think>" not in content
    assert "</think>" not in content
    assert "hidden可见内容" in content


def test_render_completed_card_handles_empty_tokens_and_non_numeric_duration():
    """No metrics reported → the state pill alone, never faked zeros.

    Maintainer note (contract change): this footer used to render "0s · Unknown · ↑0 · ↓0 ·
    ctx 0/0 0%" whenever a card held no metrics — which is every notice-only card (the gateway
    restart notices) and every turn cut short before it reported. The user read that line as broken
    data and asked for it to go ("为什么是 unknown。0。 如果这样的话感觉不需要展示这一行"), so empty
    metrics are now omitted instead of drawn as zeros. The card must still survive the junk input
    below without raising.
    """
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "最终答案"
    session.status = "completed"
    session.duration = "bad"
    card = render_card(session)
    footer = next(item for item in card["body"]["elements"] if item.get("element_id") == "footer")
    assert "已完成" in footer["content"]
    assert "Unknown" not in footer["content"]
    assert "↑0" not in footer["content"]
    assert "↓0" not in footer["content"]
    assert "ctx 0/0 0%" not in footer["content"]


def test_render_completed_card_handles_missing_token_stats():
    """`tokens=None` must not fabricate a zeroed metrics row either."""
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "最终答案"
    session.status = "completed"
    session.tokens = None
    card = render_card(session)
    footer = next(item for item in card["body"]["elements"] if item.get("element_id") == "footer")
    assert "↑0" not in footer["content"]
    assert "↓0" not in footer["content"]


def test_subscription_usage_alone_keeps_the_footer_line():
    """A quota-only card must not lose its usage line.

    Regression guard for the empty-metrics guard above: the plan-quota field IS real data, so a card
    reporting ONLY `subscription_usage` (no duration, model, tokens or tool count) still renders it.
    Dropping it would hide the one number the footer was configured for.
    """
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "最终答案"
    session.status = "completed"
    session.subscription_usage = "5h 26% · weekly 89%"
    card = render_card(session, footer_fields=["duration", "subscription_usage"])
    footer = next(item for item in card["body"]["elements"] if item.get("element_id") == "footer")
    assert "5h 26% · weekly 89%" in footer["content"]


def test_body_reasoning_reads_chronologically_while_the_panel_reads_newest_first():
    """正文的思考按发生顺序读；折叠面板仍最新在前。

    Maintainer note (contract change): the newest-first flip was applied to EVERY timeline entry,
    so the reasoning blocks rendered into the CARD BODY came out bottom-up ("思考 3" above "思考 1")
    while the panel below them read top-down. The user asked for the body to be chronological
    ("正文的思考应该正序") and for the panel to stay newest-first ("Timeline 最好倒序一下"). One pass
    now yields both orders: reasoning keeps its recorded order, panel-only entries are reversed.
    """
    from hermes_feishu_card.card_timeline import TimelineEntry

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    for number in (1, 2, 3):
        session.timeline._entries.append(
            TimelineEntry(
                kind="reasoning",
                title=f"思考 {number}",
                status="completed",
                content=f"第{number}段",
            )
        )
    for tool_id, name in (("call-1", "terminal"), ("call-2", "web_search")):
        session.timeline._entries.append(
            TimelineEntry(
                kind="tool", title=name, status="completed", detail="命令", tool_id=tool_id
            )
        )

    elements = render_card(session, reasoning_format="code")["body"]["elements"]
    body = " ".join(
        item.get("content", "")
        for item in elements
        if "reasoningentry" in item.get("element_id", "")
    )
    assert body.index("第1段") < body.index("第2段") < body.index("第3段")

    panel = next(item for item in elements if item.get("tag") == "collapsible_panel")
    panel_text = " ".join(item.get("content", "") for item in panel["elements"])
    assert "第1段" not in panel_text  # body thinking stays out of the collapsed panel
    assert panel_text.index("web_search") < panel_text.index("terminal")


def test_render_completed_card_footer_uses_compact_metrics_format():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "最终答案"
    session.status = "completed"
    session.duration = 92
    session.model = "MiniMax M2.7"
    session.tokens = {"input_tokens": 1_100_000, "output_tokens": 2_200}
    session.context = {"used_tokens": 182_000, "max_tokens": 204_000}

    card = render_card(session)

    assert "1m32s · MiniMax M2.7 · ↑1.1m · ↓2.2k · ctx 182k/204k 89%" in str(card)


def test_render_completed_card_footer_respects_configured_fields_and_order():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "最终答案"
    session.status = "completed"
    session.duration = 92
    session.model = "MiniMax M2.7"
    session.tokens = {"input_tokens": 1_100_000, "output_tokens": 2_200}
    session.context = {"used_tokens": 182_000, "max_tokens": 204_000}

    card = render_card(session, footer_fields=["model", "duration", "context"])

    content = str(card)
    assert "MiniMax M2.7 · 1m32s · ctx 182k/204k 89%" in content
    assert "↑1.1m" not in content
    assert "↓2.2k" not in content


def test_render_completed_card_footer_adds_configured_subscription_usage_only():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "最终答案"
    session.status = "completed"
    session.duration = 3
    session.subscription_usage = "5h 26% · weekly 89%"

    configured = render_card(
        session, footer_fields=["duration", "subscription_usage"]
    )
    default = render_card(session, footer_fields=["duration"])

    assert configured["body"]["elements"][-1]["content"] == (
        "<text_tag color='green'>已完成</text_tag> · 3s · 5h 26% · weekly 89%"
    )
    assert default["body"]["elements"][-1]["content"] == "<text_tag color='green'>已完成</text_tag> · 3s"


def test_spinner_text_changes_over_time():
    from hermes_feishu_card.render import _spinner_text
    frames = set()
    for _ in range(20):
        frames.add(_spinner_text("生成中"))
        time.sleep(0.05)
    assert len(frames) >= 2  # 至少两个不同帧


def test_spinner_text_contains_label():
    from hermes_feishu_card.render import _spinner_text
    assert "处理中" in _spinner_text("处理中")


def test_footer_shows_spinner_not_static_for_thinking():
    from hermes_feishu_card.session import CardSession
    from hermes_feishu_card.render import _render_footer
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    session.status = "thinking"
    footer = _render_footer(session)
    assert footer != "生成中"  # 不再是静态文本
    assert "执行中" in footer  # label 仍然包含


def test_footer_still_static_for_failed():
    """A stopped card's footer is static — no spinner — but keeps its status fields.

    Maintainer note (contract change): this used to equal the bare 已停止 pill. The user asked the
    state to survive a stop so a reader can see where the run ended, so the fields stay; what this
    test guards (nothing animates) is asserted by the absence of a spinner frame.
    """
    from hermes_feishu_card.session import CardSession
    from hermes_feishu_card.render import _SPINNER_FRAMES, _render_footer
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    session.status = "failed"

    footer = _render_footer(session)

    assert footer.startswith("<text_tag color='red'>已停止</text_tag>")
    assert not any(frame in footer for frame in _SPINNER_FRAMES)


def test_footer_treats_explicit_completed_snapshot_as_terminal():
    from hermes_feishu_card.render import _render_footer

    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    session.status = "running"
    session.duration = 1.25

    footer = _render_footer(session, display_status="completed")

    assert "生成中" not in footer
    assert footer.startswith("<text_tag color='green'>已完成</text_tag> · 1s · ")


def test_waiting_footer_uses_absolute_remaining_deadline(monkeypatch):
    from hermes_feishu_card import render as render_module
    from hermes_feishu_card.render import _render_footer

    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    session.active_interaction = InteractionState(
        interaction_id="approval-1",
        kind="approval",
        prompt="允许吗？",
        timeout_seconds=120.0,
        requested_at=100.0,
    )
    monkeypatch.setattr(render_module._time, "time", lambda: 160.0)

    footer = _render_footer(session, display_status="waiting")

    # Maintainer note (contract change): this used to pin the WHOLE line. The user asked for the
    # consumption line to survive every state, so the tool count and elapsed time now lead the
    # waiting footer too (see _render_footer). What this test protects is the deadline itself —
    # an absolute remaining time, not a per-render re-computed one — so it anchors the tail.
    assert footer.endswith("等待选择 · ⏳ 1 分钟后过期")
    assert "工具 #" not in footer  # nothing ran, so no phantom tool count


def test_render_card_compacts_tables_over_limit_by_default_without_losing_prose():
    from hermes_feishu_card.session import CardSession
    from hermes_feishu_card.render import render_card
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    session.answer_text = "\n\n".join(
        [f"Table {i}\n| col |\n| --- |\n| {i} |" for i in range(7)]
    ) + "\n\nTAIL MUST LIVE"
    session.status = "completed"
    card = render_card(session)
    body_text = "".join(
        el.get("content", "") for el in card["body"]["elements"]
        if el.get("tag") == "markdown"
    )
    assert "已转换为紧凑字段列表" in body_text
    assert "**Table 6 · Row 1**" in body_text
    assert "- col: 5" in body_text
    assert "**Table 7 · Row 1**" in body_text
    assert "- col: 6" in body_text
    assert "TAIL MUST LIVE" in body_text
    assert inspect_card_limits(card).table_count == 5


def test_render_card_explicit_truncate_removes_only_overflow_tables():
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    session.answer_text = "\n\n".join(
        [f"Table {i}\n| col |\n| --- |\n| {i} |" for i in range(7)]
    ) + "\n\nTAIL MUST LIVE"
    session.status = "completed"

    card = render_card(session, table_overflow_mode="truncate")
    body_text = "".join(
        element.get("content", "")
        for element in card["body"]["elements"]
        if element.get("tag") == "markdown"
    )

    assert "超出部分已省略" in body_text
    assert "| 4 |" in body_text
    assert "| 5 |" not in body_text
    assert "| 6 |" not in body_text
    assert "TAIL MUST LIVE" in body_text


def test_nonterminal_oversize_returns_small_deferred_native_waiting_card():
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    sensitive = "SENSITIVE-NONTERMINAL-" + ("密" * 40_000)
    session.answer_text = sensitive
    session.status = "streaming"

    result = render_card_result(session)
    rendered = str(result.card)

    assert result.disposition == "deferred_native"
    assert result.limit_reason == "json_bytes"
    assert "SENSITIVE-NONTERMINAL" not in rendered
    assert "内容较长，完成后将由 Hermes 原生消息发送" in rendered
    assert inspect_card_limits(result.card).safe is True


def test_terminal_oversize_returns_small_native_handoff_without_full_answer():
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    sensitive = "SENSITIVE-TERMINAL-" + ("密" * 40_000)
    session.answer_text = sensitive
    session.status = "completed"

    result = render_card_result(session)
    rendered = str(result.card)

    assert result.disposition == "native"
    assert result.limit_reason == "json_bytes"
    assert "SENSITIVE-TERMINAL" not in rendered
    assert "完整内容已切换为 Hermes 原生消息发送" in rendered
    assert inspect_card_limits(result.card).safe is True


def test_limit_handoff_remains_small_with_pathological_configured_title():
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    session.answer_text = "密" * 40_000
    session.status = "completed"

    result = render_card_result(session, title="T" * 40_000)

    assert result.disposition == "native"
    assert inspect_card_limits(result.card).safe is True
    assert len(result.card["header"]["title"]["content"]) <= 80


def test_rendered_table_splitting_is_checked_after_final_card_shape():
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    session.answer_text = (
        "| key | value |\n| --- | --- |\n"
        + "".join(f"| row-{index} | {'值' * 100} |\n" for index in range(180))
    )
    session.status = "completed"

    result = render_card_result(session)

    assert result.disposition == "native"
    assert "tables" in result.inspection.violations
    assert inspect_card_limits(result.card).safe is True


def test_render_answer_stays_primary_over_public_interim_text():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="thinking.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "先分析约束。"},
        )
    )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=2,
            created_at=0.0,
            data={"text": "这是主回答。"},
        )
    )

    card = render_card(session)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")

    assert main["content"] == "这是主回答。"
    assert "auxiliary_timeline" not in str(card)
    assert "工具调用 0 次" not in str(card)
    assert "先分析约束。" not in str(card)


def test_render_in_progress_answer_status_is_generating():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "正在生成主回答。"},
        )
    )

    card = render_card(session)

    assert "subtitle" not in card["header"]
    assert card["config"]["summary"]["content"] == "生成中"
    assert "生成中" in str(card)


def test_render_public_interim_text_in_main_content_before_answer():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "这是公开的阶段性说明。"

    card = render_card(session)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")

    assert main["content"] == "这是公开的阶段性说明。"
    assert "正在思考" not in str(card)
    assert "这是公开的阶段性说明。" in str(card)


def test_render_keeps_pre_tool_answer_in_main_while_tool_runs():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "好的，我先做分析再动手。"},
        )
    )

    card = render_card(session, timeline_expanded=True)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    assert main["content"] == "好的，我先做分析再动手。"
    assert "auxiliary_timeline" not in str(card)
    assert "工具调用 0 次" not in str(card)

    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=2,
            created_at=0.0,
            data={
                "tool_id": "terminal",
                "name": "terminal",
                "status": "running",
                "detail": "gh release view",
            },
        )
    )

    card = render_card(session, timeline_expanded=True)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")

    assert main["content"] == "好的，我先做分析再动手。"
    assert "好的，我先做分析再动手。" not in str(timeline)
    assert "terminal" in str(timeline)


def test_reasoning_code_is_visible_outside_collapsed_tool_panel():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    base = dict(schema_version="1", conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc", platform="feishu", created_at=0.0)
    reasoning = "Check literal ```code``` before running."
    session.apply(SidecarEvent(event="answer.delta", sequence=1, data={"text": reasoning}, **base))
    session.apply(SidecarEvent(event="tool.updated", sequence=2, data={"tool_id": "t", "name": "terminal", "status": "completed"}, **base))
    session.apply(SidecarEvent(event="message.completed", sequence=3, data={"answer": "done"}, **base))
    result = render_card_result(session, reasoning_format="code")
    assert result.disposition == "card"
    elements = result.card["body"]["elements"]
    direct_reasoning = next(item for item in elements if "reasoningentry" in item.get("element_id", ""))
    assert f"````text\n{reasoning}\n````" in direct_reasoning["content"]
    panel = next(item for item in elements if item.get("tag") == "collapsible_panel")
    assert panel["expanded"] is False
    assert reasoning not in str(panel)
    assert "terminal" in str(panel)
    assert not any("reasoningentry" in item.get("element_id", "") for item in render_card(session)["body"]["elements"])
    assert reasoning not in str(render_card(session, show_reasoning=False, reasoning_format="code"))


@pytest.mark.parametrize("interaction_mode", ["callback", "text"])
def test_pending_approval_keeps_complete_scope_and_choices_without_old_output(interaction_mode):
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "old answer " * 4000
    session.active_interaction = InteractionState(
        interaction_id="approval-1", kind="approval", prompt="允许执行？",
        description="complete command " + "x" * 3500 + " LAST_ARGUMENT",
    )
    result = render_card_result(session, interaction_mode=interaction_mode)
    assert result.disposition == "card"
    elements = result.card.get("body", result.card)["elements"]
    description = next(item for item in elements if item.get("content", "").startswith("complete command"))
    assert description["content"].endswith("LAST_ARGUMENT")
    assert "old answer" not in str(result.card)
    assert not any(item.get("tag") == "collapsible_panel" for item in elements)


def test_reasoning_code_preserves_card_limit_handoff():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    base = dict(schema_version="1", conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc", platform="feishu", created_at=0.0)
    session.apply(SidecarEvent(event="answer.delta", sequence=1, data={"text": "思考" * 9000}, **base))
    session.apply(SidecarEvent(event="tool.updated", sequence=2, data={"tool_id": "t", "name": "terminal", "status": "completed"}, **base))
    session.apply(SidecarEvent(event="message.completed", sequence=3, data={"answer": "done"}, **base))
    result = render_card_result(session, reasoning_format="code", max_reasoning_chars=30000)
    assert result.disposition == "native"
    assert inspect_card_limits(result.card).safe


def test_render_timeline_styles_reasoning_and_tools_with_compact_hierarchy():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    base = {
        "schema_version": "1",
        "conversation_id": "chat-1",
        "message_id": "msg-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "created_at": 0.0,
    }
    session.apply(SidecarEvent(event="answer.delta", sequence=1, data={"text": "先确认 release。"}, **base))
    session.apply(
        SidecarEvent(
            event="tool.updated",
            sequence=2,
            data={
                "tool_id": "terminal",
                "name": "terminal",
                "status": "completed",
                "detail": "gh release list --limit 3",
            },
            **base,
        )
    )
    session.apply(SidecarEvent(event="message.completed", sequence=3, data={"answer": "最终总结。"}, **base))

    timeline = next(
        item
        for item in render_card(session, timeline_expanded=True)["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    reasoning = next(item for item in timeline["elements"] if "思考 1" in item["content"])
    tool = next(item for item in timeline["elements"] if "terminal" in item["content"])

    assert reasoning["text_size"] == "small"
    assert tool["text_size"] == "x-small"
    assert tool["content"].startswith('<font color="green">✓ **terminal** · #1</font>')
    assert "gh release list" in tool["content"]


def test_render_tool_timeline_uses_compact_semantic_event_rows():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    base = {
        "schema_version": "1",
        "conversation_id": "chat-1",
        "message_id": "msg-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "created_at": 0.0,
    }
    session.apply(
        SidecarEvent(
            event="tool.updated",
            sequence=1,
            data={
                "tool_id": "terminal",
                "name": "terminal",
                "status": "completed",
                "arguments": {"command": "gh issue list"},
                "duration_ms": 250,
            },
            **base,
        )
    )
    session.apply(
        SidecarEvent(
            event="tool.updated",
            sequence=2,
            data={
                "tool_id": "editor",
                "name": "edit_file",
                "status": "running",
                "detail": "优化工具事件层级",
            },
            **base,
        )
    )
    session.apply(
        SidecarEvent(
            event="tool.updated",
            sequence=3,
            data={
                "tool_id": "fetch",
                "name": "fetch",
                "status": "failed",
                "duration_ms": 4000,
                "error": "exit 1",
            },
            **base,
        )
    )

    timeline = next(
        item
        for item in render_card(session, timeline_expanded=True)["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    rows = [
        item
        for item in timeline["elements"]
        if str(item.get("element_id", "")).startswith("auxiliary_timeline_toolentry_")
    ]
    # Newest first (see test_render_timeline_reads_newest_first): the row for the LAST event leads, so
    # the unpack order is the reverse of the order the events were applied in.
    failed, running, completed = (row["content"] for row in rows)

    assert completed.startswith('<font color="green">✓ **terminal** · #1 · 250ms</font>')
    assert '<font color="grey">　参数: ' in completed
    assert "耗时:" not in completed
    assert running.startswith('<font color="blue">')
    assert any(frame in running for frame in _SPINNER_FRAMES)
    assert "**edit_file** · #2 · 执行中" in running
    assert failed.startswith('<font color="red">✕ **fetch** · #3 · 4s · 失败</font>')
    assert '<font color="grey">　失败: exit 1</font>' in failed
    assert all(not row["content"].startswith("> ") for row in rows)
    assert timeline["border"]["corner_radius"] == "8px"


def test_render_subagent_uses_dedicated_escaped_semantic_row():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession("chat-1", "msg-1", "oc_abc")
    assert session.apply(
        SidecarEvent(
            schema_version="1",
            event="subagent.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=1.0,
            data={
                "child_id": "child-1",
                "role": "研究 <Lead>",
                "status": "running",
                "goal_preview": "检查 <script>alert(1)</script>",
            },
        )
    )

    card = render_card(session, timeline_expanded=True)
    timeline = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    subagent = next(
        item
        for item in timeline["elements"]
        if str(item.get("element_id", "")).startswith(
            "auxiliary_timeline_subagententry_"
        )
    )
    content = subagent["content"]
    assert "子代理：研究 &lt;Lead&gt;" in content
    assert "执行中" in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content
    assert "<script>" not in content
    assert session.tool_count == 0


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("queued", "等待中"),
        ("completed", "已完成"),
        ("failed", "失败"),
        ("cancelled", "已取消"),
        ("interrupted", "已中断"),
    ],
)
def test_render_subagent_distinguishes_lifecycle_statuses(status, expected):
    from hermes_feishu_card.card_timeline import TimelineEntry

    session = CardSession("chat-1", "msg-1", "oc_abc")
    session.timeline._entries.append(
        TimelineEntry(
            kind="subagent",
            title="research",
            status=status,
            subagent_id="child-1",
        )
    )

    card = render_card(session, timeline_expanded=True)
    timeline = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    subagent = next(
        item
        for item in timeline["elements"]
        if str(item.get("element_id", "")).startswith(
            "auxiliary_timeline_subagententry_"
        )
    )

    assert expected in subagent["content"]
    assert "子代理：research" in subagent["content"]
    if status == "interrupted":
        assert "执行中" not in subagent["content"]


def test_render_tool_timeline_removes_all_duration_lines_from_detail():
    from hermes_feishu_card.card_timeline import TimelineEntry

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.timeline._entries.append(
        TimelineEntry(
            kind="tool",
            title="web_search",
            status="completed",
            detail="搜索参数\n耗时: 2.47s\n耗时: 2.12s",
            tool_id="call-1",
        )
    )
    session._tool_call_count = 1

    card = render_card(session, timeline_expanded=True)
    timeline = next(
        item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline"
    )
    content = str(timeline)

    assert "web_search** · #1 · 2.47s" in content
    assert "搜索参数" in content
    assert "耗时:" not in content


def test_render_tool_timeline_recognizes_localized_terminal_status():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "tool_id": "read-docs",
                "name": "读取资料",
                "status": "已完成",
                "detail": "docs/wiki/README.md",
            },
        )
    )

    timeline = next(
        item
        for item in render_card(session, timeline_expanded=True)["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    row = next(
        item
        for item in timeline["elements"]
        if str(item.get("element_id", "")).startswith("auxiliary_timeline_toolentry_")
    )

    assert row["content"].startswith(
        '<font color="green">✓ **读取资料** · #1</font>'
    )


def test_render_timeline_styles_system_notices_as_compact_status_lines():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    base = {
        "schema_version": "1",
        "conversation_id": "chat-1",
        "message_id": "msg-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "created_at": 0.0,
    }
    session.apply(
        SidecarEvent(
            event="system.notice",
            sequence=1,
            data={
                "title": "上下文窗口提示",
                "content": "Codex gpt-5.5 caps context at 272K.",
                "notice_id": "context-cap",
            },
            **base,
        )
    )

    # Contract change: a timeline holding ONLY notices no longer wraps them in the 思考与工具 panel
    # — that produced a panel headed "思考与工具 · 0 次工具调用" over unrelated content. The notice
    # keeps its compact quoted styling; it is a top-level element now. Notice styling inside the
    # panel (when real think/tool work exists) stays covered by
    # tests/unit/test_timeline_notice_panel.py.
    elements = render_card(session, timeline_expanded=True)["body"]["elements"]
    assert not any(item.get("tag") == "collapsible_panel" for item in elements)
    notice = next(item for item in elements if "上下文窗口提示" in item.get("content", ""))

    assert notice["text_size"] == "x-small"
    assert notice["content"].startswith("> ")
    assert "Codex gpt-5.5 caps context" in notice["content"]


def test_render_independent_notice_card_uses_notice_title_and_status():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="notice-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="system.notice",
            conversation_id="chat-1",
            message_id="notice-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "title": "技能加载",
                "content": "Reading skill hermes-agent",
                "notice_scope": "independent",
                "level": "info",
            },
        )
    )

    card = render_card(session)

    assert card["header"]["title"]["content"] == "技能加载"
    assert card["header"]["template"] == "blue"
    assert card["header"]["subtitle"]["content"] == "已完成"
    assert "Reading skill hermes-agent" in str(card)
    assert "生成中" not in str(card)


def test_render_rotates_each_preface_from_main_to_timeline():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    base = {
        "schema_version": "1",
        "conversation_id": "chat-1",
        "message_id": "msg-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "created_at": 0.0,
    }

    session.apply(SidecarEvent(event="answer.delta", sequence=1, data={"text": "第一段验证计划。"}, **base))
    session.apply(
        SidecarEvent(
            event="tool.updated",
            sequence=2,
            data={"tool_id": "release", "name": "terminal", "status": "completed"},
            **base,
        )
    )
    session.apply(SidecarEvent(event="answer.delta", sequence=3, data={"text": "第二段补充检查。"}, **base))

    card = render_card(session, timeline_expanded=True)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    assert main["content"] == "第二段补充检查。"
    assert "第一段验证计划。" in str(timeline)
    assert "第二段补充检查。" not in str(timeline)

    session.apply(
        SidecarEvent(
            event="tool.updated",
            sequence=4,
            data={"tool_id": "readme", "name": "read_file", "status": "completed"},
            **base,
        )
    )
    card = render_card(session, timeline_expanded=True)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    assert main["content"] == "第二段补充检查。"
    assert "第二段补充检查。" not in str(timeline)
    assert "read_file" in str(timeline)

    session.apply(
        SidecarEvent(
            event="message.completed",
            sequence=5,
            data={"answer": "最终总结。"},
            **base,
        )
    )

    card = render_card(session, timeline_expanded=True)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    assert main["content"] == "最终总结。"
    assert "第一段验证计划。" in str(timeline)
    assert "第二段补充检查。" in str(timeline)
    assert "思考 1" in str(timeline)
    assert "思考 2" in str(timeline)


def test_render_timeline_keeps_latest_reasoning_when_tool_burst_fills_recent_items():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "关键思考内容应该保留在展开区。"},
        )
    )
    for index in range(8):
        session.apply(
            SidecarEvent(
                schema_version="1",
                event="tool.updated",
                conversation_id="chat-1",
                message_id="msg-1",
                chat_id="oc_abc",
                platform="feishu",
                sequence=index + 2,
                created_at=0.0,
                data={
                    "tool_id": f"tool-{index}",
                    "name": f"tool_{index}",
                    "status": "completed",
                },
            )
        )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="message.completed",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=20,
            created_at=0.0,
            data={"answer": "最终回答。"},
        )
    )

    card = render_card(session, max_timeline_items=4, timeline_expanded=True)
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    timeline_content = str(timeline)

    assert "关键思考内容应该保留在展开区。" in timeline_content
    assert "tool_7" in timeline_content
    assert "tool_0" not in timeline_content


def test_render_expanded_timeline_splits_without_global_truncation():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    marker = "TIMELINE_TAIL_MARKER"
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "思考" * 180 + marker},
        )
    )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=2,
            created_at=0.0,
            data={"tool_id": "tool-1", "name": "search", "status": "completed"},
        )
    )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="message.completed",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=3,
            created_at=0.0,
            # Keep this fixture focused on timeline splitting: a substantive
            # authoritative completion should archive the earlier stream,
            # unlike the short postscript preservation covered by issue #188.
            data={"answer": "最终回答。" * 50},
        )
    )

    card = render_card(session, timeline_expanded=True, max_reasoning_chars=1000)
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    timeline_content = "".join(item["content"] for item in timeline["elements"])

    assert marker in timeline_content
    assert "内容已折叠" not in timeline_content


def test_render_omits_redundant_tool_summary_when_timeline_is_visible():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="thinking.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "先分析。"},
        )
    )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=2,
            created_at=0.0,
            data={"tool_id": "tool-1", "name": "search", "status": "completed"},
        )
    )

    card = render_card(session)
    element_ids = [item.get("element_id") for item in card["body"]["elements"]]

    assert "auxiliary_timeline" in element_ids
    assert "tool_summary" not in element_ids
    assert "思考过程" in str(card)


def test_render_timeline_reads_newest_first():
    """The 思考过程 panel is ordered newest → oldest, so the latest work is read first.

    Maintainer note (contract change): the panel used to be chronological (oldest first). Because the
    panel sits at the BOTTOM of a card that is read downward, the entry the reader most wants ("what
    is happening now?") was the furthest from their eye — the user asked for reverse order
    ("Timeline 最好倒序一下 阅读上能够看最近的比较方便"). Selection is unchanged: the same entries are
    shown, only the order they are written in flips.
    """
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    for index in range(3):
        session.apply(
            SidecarEvent(
                schema_version="1",
                event="tool.updated",
                conversation_id="chat-1",
                message_id="msg-1",
                chat_id="oc_abc",
                platform="feishu",
                sequence=index + 1,
                created_at=0.0,
                data={
                    "tool_id": f"tool-{index}",
                    "name": f"step_{index}",
                    "status": "completed",
                },
            )
        )

    card = render_card(session, max_timeline_items=10)
    timeline = next(
        item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline"
    )
    content = "".join(item["content"] for item in timeline["elements"])

    newest = content.index("step_2")
    middle = content.index("step_1")
    oldest = content.index("step_0")
    assert newest < middle < oldest


def test_render_timeline_folds_old_entries_before_answer():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    for index in range(8):
        session.apply(
            SidecarEvent(
                schema_version="1",
                event="thinking.delta",
                conversation_id="chat-1",
                message_id="msg-1",
                chat_id="oc_abc",
                platform="feishu",
                sequence=index * 2 + 1,
                created_at=0.0,
                data={"text": f"思考{index}"},
            )
        )
        session.apply(
            SidecarEvent(
                schema_version="1",
                event="tool.updated",
                conversation_id="chat-1",
                message_id="msg-1",
                chat_id="oc_abc",
                platform="feishu",
                sequence=index * 2 + 2,
                created_at=0.0,
                data={"tool_id": f"tool-{index}", "name": f"tool_{index}", "status": "completed"},
            )
        )
    session.answer_text = "最终回答不能被折叠"

    card = render_card(session, max_timeline_items=4)
    content = str(card)

    assert "最终回答不能被折叠" in content
    assert "已折叠 4 条早期思考/工具记录" in content
    assert "tool_7" in content
    assert "tool_0" not in content


def test_render_timeline_redacts_sensitive_tool_detail():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "tool_id": "tool-1",
                "name": "lark_send",
                "status": "completed",
                "detail": (
                    "FEISHU_APP_SECRET=abc123 "
                    "token=secret-token "
                    "chat_id=oc_secret"
                ),
            },
        )
    )

    card = render_card(session)
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    content = str(timeline)

    assert "FEISHU_APP_SECRET=abc123" not in content
    assert "token=secret-token" not in content
    assert "chat_id=oc_secret" not in content
    assert "lark_send" in content


def test_render_timeline_shows_compact_tool_arguments_duration_and_error():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "tool_id": "tool-1",
                "name": "terminal",
                "status": "failed",
                "arguments": {"command": "gh issue list", "token": "secret-token"},
                "duration_ms": 250,
                "error": "exit 1",
            },
        )
    )

    card = render_card(session)
    timeline = next(
        item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline"
    )
    content = str(timeline)

    assert "参数:" in content
    assert "gh issue list" in content
    assert "250ms" in content
    assert "耗时:" not in content
    assert "失败: exit 1" in content
    assert "secret-token" not in content


def test_render_timeline_redacts_sensitive_tool_detail_in_json_and_repr():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "tool_id": "tool-1",
                "name": "timeline-json",
                "status": "completed",
                "detail": '{"chat_id":"oc_secret","tenant_access_token":"token-123"}',
            },
        )
    )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=2,
            created_at=0.0,
            data={
                "tool_id": "tool-2",
                "name": "timeline-repr",
                "status": "completed",
                "detail": "{'app_secret': 'super-secret', 'open_id': 'ou_secret'}",
            },
        )
    )

    card = render_card(session)
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    content = str(timeline)

    assert "oc_secret" not in content
    assert "token-123" not in content
    assert "super-secret" not in content
    assert "ou_secret" not in content
    assert "[REDACTED]" in content


def test_render_thinking_without_answer_uses_public_interim_main_content():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="thinking.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "这是公开的阶段性输出。"},
        )
    )

    card = render_card(session)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")

    assert main["content"] == "这是公开的阶段性输出。"
    assert "正在思考" not in str(card)
    assert "这是公开的阶段性输出。" in str(card)
    assert "auxiliary_timeline" not in str(card)
    assert "工具调用 0 次" not in str(card)


def test_render_tool_activity_keeps_tool_names_when_reasoning_hidden():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="thinking.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "隐藏的思考"},
        )
    )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=2,
            created_at=0.0,
            data={"tool_id": "tool-1", "name": "search", "status": "running"},
        )
    )

    card = render_card(session, show_reasoning=False)
    row = next(
        item for item in card["body"]["elements"]
        if item.get("element_id") == "tool_activity_0"
    )

    assert "search" in str(row)
    assert "#1" in str(row)
    assert "执行中" in str(row)
    assert "auxiliary_timeline" not in str(card)


def test_render_can_hide_reasoning_timeline_when_configured():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="thinking.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "隐藏的思考"},
        )
    )
    session.answer_text = "主回答"

    card = render_card(session, show_reasoning=False)

    content = str(card)
    assert "主回答" in content
    assert "隐藏的思考" not in content
    assert "auxiliary_timeline" not in content


@pytest.mark.parametrize('kind', ['approval', 'clarify'])
def test_mobile_interaction_preserves_full_prompt_in_body(kind):
    prompt = '需要完整显示的问题内容，' * 25 + '不要丢失末尾条件。'
    session = CardSession('c', 'm', 'oc')
    session.active_interaction = InteractionState(
        interaction_id='mobile-question', kind=kind, prompt=prompt,
        description='完整授权范围', options=[InteractionOption(label='允许一次', value='once')],
    )
    card = render_legacy_interaction_callback_card(session)
    contents = [item.get('content') for item in card['elements'] if item.get('tag') == 'markdown']
    assert prompt in contents
    assert '完整授权范围' in contents


def test_answer_mentions_resolve_only_known_requester_and_preserve_code():
    from hermes_feishu_card.render import _render_known_requester_mentions
    session = CardSession('c', 'm', 'oc', sender_open_id='ou_requester', sender_name='牟勇')
    text = '@牟勇，请查看。**@牟勇** @陌生人 @牟勇其他 `@牟勇`\n```text\n@牟勇\n```\n[链接](https://example.com/@牟勇)'
    result = _render_known_requester_mentions(text, session)
    assert result.count('<at id="ou_requester"></at>') == 2
    for literal in ('@陌生人', '@牟勇其他', '`@牟勇`', '```text\n@牟勇\n```', 'https://example.com/@牟勇'):
        assert literal in result


def test_answer_mentions_obey_disable_switch_and_do_not_guess_missing_identity():
    session = CardSession('c', 'm', 'oc', sender_open_id='ou_requester', sender_name='牟勇')
    session.answer_text = '@牟勇，请查看。'
    session.status = 'completed'
    assert '<at id="ou_requester">' in str(render_card(session))
    assert '<at id="ou_requester">' not in str(render_card(session, mentions_enabled=False))
    session.sender_open_id = ''
    assert '<at id=' not in str(render_card(session))


def test_requester_mentions_preserve_existing_at_markup_and_plain_urls():
    from hermes_feishu_card.render import _render_known_requester_mentions
    session = CardSession('c', 'm', 'oc', sender_open_id='ou_requester', sender_name='牟勇')
    original = '<at id="ou_requester">@牟勇</at> https://example.com/@牟勇 `@牟勇'
    assert _render_known_requester_mentions(original, session) == original
