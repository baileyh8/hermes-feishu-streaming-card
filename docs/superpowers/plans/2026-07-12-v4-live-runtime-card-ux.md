# V4.0.0 Live Runtime Card UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release V4.0.0 with a live Hermes tool-preview header, independently streamed public interim-assistant content, natural waiting/failed states, refreshed real-Feishu screenshots, and verified release assets.

**Architecture:** `CardSession` combines the latest non-empty `tool.updated.detail` with the tool name into a deterministic action summary and stores it separately from `thinking_text`. `render.py` keeps the user-configured Header title, places the action summary in the subtitle for non-completed tool states, and preserves status-only non-completed footers.

**Tech Stack:** Python 3.9+, dataclasses, aiohttp, Feishu/Lark Card JSON 2.0, Hermes Gateway runtime hooks, pytest, GitHub Actions, GitHub CLI.

## Global Constraints

- Active runtime is `hermes_feishu_card/`; do not edit `legacy/`.
- Do not manually edit an installed Hermes `gateway/run.py`; the existing patcher already forwards `preview` into `tool.updated.detail`.
- Header content comes from a deterministic tool-action adapter over Hermes tool name + `preview`, or a pending Hermes interaction prompt. HFC adds only action labels, reduces unsafe machine detail, and does not call an LLM or infer results.
- `thinking.delta` is the public interim-assistant stream; hidden reasoning or chain-of-thought is never added to the event contract or rendered.
- Empty previews preserve the previous non-empty preview until a new preview, interaction, failure, or completion.
- Running, waiting, and failed footers contain status only. Completed native-reply cards show `已完成` followed by duration, model, tokens, and context statistics.
- Normal-chat completion uses Feishu's native reply quote as the only header; HFC removes the Card JSON Header and does not add a duplicate quote inside the card. Paths without a valid reply anchor retain the configured-title fallback.
- Existing timeline, interaction security, button grouping, attachment order, update queue, retry behavior, topic routing, and native gray-message suppression remain unchanged.
- Preview content is single-line, bounded to 120 characters, and redacted before entering Card JSON.
- Public screenshots must come from real Feishu, use deliberately presentable task copy, and contain no secret, real chat id, open id, unrelated conversation, or private desktop content.
- Release only after the full test suite, private/group Feishu acceptance, screenshot review, tag workflow, and four release assets pass.

---

### Task 1: Store Independent Runtime Header State

**Files:**
- Modify: `hermes_feishu_card/session.py:54-222`
- Modify: `tests/unit/test_session.py`

**Interfaces:**
- Consumes: `SidecarEvent(event="tool.updated", data={"detail": ...})`, `InteractionState`, and the existing terminal-state guard.
- Produces: `CardSession.latest_tool_preview: str` and `CardSession.runtime_header_text: str`.

- [ ] **Step 1: Write failing state tests**

Append to `tests/unit/test_session.py`:

```python
def test_tool_preview_replaces_header_without_touching_thinking_text():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("thinking.delta", 1, {"text": "先分析接口。", "mode": "append_block"}))
    assert session.apply(event("tool.updated", 2, {
        "tool_id": "read-1", "name": "read_file", "status": "running",
        "detail": "读取 weather_client.py",
    }))
    assert session.latest_tool_preview == "读取 weather_client.py"
    assert session.runtime_header_text == "读取 weather_client.py"
    assert session.thinking_text == "先分析接口。"


def test_empty_tool_preview_preserves_previous_header():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("tool.updated", 1, {
        "tool_id": "read-1", "name": "read_file", "status": "running",
        "detail": "读取 weather_client.py",
    }))
    assert session.apply(event("tool.updated", 2, {
        "tool_id": "read-1", "name": "read_file", "status": "completed", "detail": " \n ",
    }))
    assert session.latest_tool_preview == "读取 weather_client.py"


def test_interaction_temporarily_overrides_then_restores_preview():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("tool.updated", 1, {
        "tool_id": "terminal", "name": "terminal", "status": "running", "detail": "执行 pytest",
    }))
    assert session.apply(event("interaction.requested", 2, {
        "interaction_id": "approval-1", "kind": "approval", "prompt": "允许继续执行测试吗？",
        "options": [{"label": "允许", "value": "yes"}],
    }))
    assert session.runtime_header_text == "允许继续执行测试吗？"
    assert session.apply(event("interaction.completed", 3, {
        "interaction_id": "approval-1", "choice": "yes", "choice_label": "允许",
    }))
    assert session.runtime_header_text == "执行 pytest"


def test_completed_clears_header_but_failed_retains_preview():
    completed = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert completed.apply(event("tool.updated", 1, {
        "tool_id": "read", "name": "read_file", "status": "running", "detail": "读取 config.py",
    }))
    assert completed.apply(event("message.completed", 2, {"answer": "完成"}))
    assert completed.runtime_header_text == ""

    failed = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert failed.apply(event("tool.updated", 1, {
        "tool_id": "terminal", "name": "terminal", "status": "running", "detail": "执行 pytest",
    }))
    assert failed.apply(event("message.failed", 2, {"error": "测试失败"}))
    assert failed.runtime_header_text == "执行 pytest"
    assert not failed.apply(event("tool.updated", 3, {
        "tool_id": "late", "name": "read_file", "status": "running", "detail": "迟到更新",
    }))
    assert failed.runtime_header_text == "执行 pytest"
```

- [ ] **Step 2: Run the focused tests and verify failure**

```bash
.venv/bin/python -m pytest tests/unit/test_session.py -k 'tool_preview or interaction_temporarily or completed_clears_header' -q
```

Expected: failures report missing `latest_tool_preview` or `runtime_header_text`.

- [ ] **Step 3: Implement the minimal state model**

Add to `CardSession`:

```python
latest_tool_preview: str = ""

@property
def runtime_header_text(self) -> str:
    interaction = self.active_interaction
    if interaction is not None and interaction.status == "pending":
        return normalize_stream_text(interaction.prompt).strip()
    if self.status == "completed":
        return ""
    return self.latest_tool_preview
```

At the start of the `tool.updated` branch, retain only a non-empty Hermes `detail`, before `_tool_detail_from_event_data()` expands arguments and duration:

```python
raw_preview = event.data.get("detail")
if isinstance(raw_preview, str):
    normalized_preview = normalize_stream_text(raw_preview).strip()
    if normalized_preview:
        self.latest_tool_preview = normalized_preview
```

Clear `latest_tool_preview` in `message.completed`. Do not clear it in `message.failed`; the existing terminal guard rejects delayed events.

- [ ] **Step 4: Verify all session tests**

```bash
.venv/bin/python -m pytest tests/unit/test_session.py -q
```

Expected: all session tests pass.

- [ ] **Step 5: Commit**

```bash
git add hermes_feishu_card/session.py tests/unit/test_session.py
git commit -m "feat: track live runtime card headers"
```

### Task 2: Render V4 Header, Public Interim Body, and Footer States

**Files:**
- Modify: `hermes_feishu_card/render.py:32-266,553-602`
- Modify: `tests/unit/test_render.py`

**Interfaces:**
- Consumes: `CardSession.runtime_header_text`, `thinking_text`, `answer_text`, `resolve_display_status(...)`, and `_redact_tool_detail(...)`.
- Produces: `_sanitize_runtime_header(text: str) -> str`, `_runtime_header_title(session, configured_title) -> str`, and state-correct Card JSON 2.0.

- [ ] **Step 1: Write failing V4 renderer tests**

Add tests that assert these exact boundaries:

```python
def test_v4_running_card_uses_preview_title_and_public_interim_body():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.thinking_text = "我先检查天气客户端。"
    session.latest_tool_preview = "读取 weather_client.py"
    card = render_card(session, title="Hermes Agent")
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    footer = next(item for item in card["body"]["elements"] if item.get("element_id") == "footer")
    assert card["header"]["title"]["content"] == "读取 weather_client.py"
    assert main["content"] == "我先检查天气客户端。"
    assert "gpt-" not in footer["content"]
    assert "ctx " not in footer["content"]


def test_v4_answer_delta_remains_primary_over_public_interim_text():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.thinking_text = "公开阶段说明"
    session.answer_text = "主回答已经开始"
    card = render_card(session)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    assert main["content"] == "主回答已经开始"
    assert "公开阶段说明" not in str(card)


def test_v4_waiting_prompt_moves_to_header_without_body_duplication():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.active_interaction = InteractionState(
        interaction_id="approval-1", kind="approval", prompt="允许覆盖文件吗？",
        description="目标文件：report.html", options=[],
    )
    card = render_card(session)
    assert card["header"]["title"]["content"] == "允许覆盖文件吗？"
    assert str(card).count("允许覆盖文件吗？") == 1
    assert "目标文件：report.html" in str(card)
    footer = next(item for item in card["body"]["elements"] if item.get("element_id") == "footer")
    assert "等待" in footer["content"]
    assert "ctx " not in footer["content"]


def test_v4_completed_restores_configured_title_and_metrics():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.latest_tool_preview = "执行 pytest"
    session.status = "completed"
    session.answer_text = "最终答案"
    session.duration = 2.0
    session.model = "gpt-5.5"
    card = render_card(session, title="研发助手")
    assert card["header"]["title"]["content"] == "研发助手"
    assert "执行 pytest" not in str(card["header"])
    assert "2s" in str(card)
    assert "gpt-5.5" in str(card)


def test_v4_failed_retains_preview_and_status_only_footer():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.latest_tool_preview = "读取演示天气数据"
    session.status = "failed"
    session.answer_text = "数据源暂时不可用。"
    card = render_card(session, title="Hermes Agent")
    assert card["header"]["title"]["content"] == "读取演示天气数据"
    footer = next(item for item in card["body"]["elements"] if item.get("element_id") == "footer")
    assert footer["content"] == "已停止"
    assert "ctx " not in footer["content"]


def test_v4_missing_preview_keeps_configured_title():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.thinking_text = "正在处理。"
    card = render_card(session, title="研发助手")
    assert card["header"]["title"]["content"] == "研发助手"


@pytest.mark.parametrize("preview", [
    "```bash\nexport APP_SECRET=unsafe\n```",
    "curl https://example.test?a=1&token=unsafe",
    "deploy --password unsafe --api-key=unsafe",
])
def test_v4_runtime_header_is_single_line_bounded_and_redacted(preview):
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.latest_tool_preview = preview + ("x" * 300)
    title = render_card(session)["header"]["title"]["content"]
    assert "\n" not in title
    assert "unsafe" not in title
    assert "[REDACTED]" in title
    assert len(title) <= 120
```

- [ ] **Step 2: Run focused renderer tests and verify failure**

```bash
.venv/bin/python -m pytest tests/unit/test_render.py -k 'v4_' -q
```

Expected: current rendering still uses the configured title, hides `thinking_text`, repeats the interaction prompt, and lacks bounded preview sanitation.

- [ ] **Step 3: Add runtime-header sanitation and title selection**

Add near the existing tool redaction helpers:

```python
RUNTIME_HEADER_MAX_CHARS = 120
_RUNTIME_FENCE_RE = re.compile(r"```[A-Za-z0-9_-]*")
_RUNTIME_SECRET_FLAG_RE = re.compile(
    r"(?i)(--(?:token|password|secret|api-key|app-secret)(?:=|\s+))([^\s]+)"
)
_RUNTIME_URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|password|secret|api_key|api-key|app_secret)=)([^&#\s]+)"
)


def _sanitize_runtime_header(text: str) -> str:
    normalized = normalize_stream_text(str(text or ""))
    normalized = _RUNTIME_FENCE_RE.sub("", normalized)
    normalized = " ".join(normalized.split())
    normalized = _redact_tool_detail(normalized)
    normalized = _RUNTIME_SECRET_FLAG_RE.sub(r"\1[REDACTED]", normalized)
    normalized = _RUNTIME_URL_SECRET_RE.sub(r"\1[REDACTED]", normalized)
    if len(normalized) <= RUNTIME_HEADER_MAX_CHARS:
        return normalized
    return normalized[: RUNTIME_HEADER_MAX_CHARS - 1].rstrip() + "…"


def _runtime_header_title(session: CardSession, configured_title: str) -> str:
    if session.delivery_kind == "notice" and session.notice_title:
        return session.notice_title
    if session.status == "completed":
        return configured_title
    runtime_title = _sanitize_runtime_header(session.runtime_header_text)
    return runtime_title or configured_title
```

Use `_runtime_header_title()` for the existing header title. Do not add a second header element.

- [ ] **Step 4: Render public interim text until the answer starts**

Replace primary-text selection with:

```python
if session.status in {"completed", "failed"}:
    primary_text = normalize_stream_text(session.answer_text)
elif session.answer_text:
    primary_text = normalize_stream_text(session.answer_text)
elif session.thinking_text:
    primary_text = normalize_stream_text(session.thinking_text)
else:
    primary_text = _spinner_frame()
```

Do not add any provider-reasoning hook; only existing `_interim_assistant_cb` content mapped to `thinking.delta` becomes visible.

- [ ] **Step 5: Remove duplicated pending prompt and keep original description/options**

Start `_render_interaction_elements()` with this state split, then retain the existing button and text-choice loops below it:

```python
elements: list[Dict[str, Any]] = []
if interaction.status == "pending":
    if interaction.description:
        elements.append({
            "tag": "markdown",
            "element_id": "interaction_description",
            "content": interaction.description,
        })
else:
    if interaction.status == "completed":
        choice = interaction.choice_label or interaction.choice or "已完成"
        user = f" by {interaction.user_name}" if interaction.user_name else ""
        content = f"已选择：{choice}{user}"
    else:
        content = interaction.error or "交互请求失败"
    elements.append({
        "tag": "markdown",
        "element_id": "interaction_result",
        "content": content,
    })
    return elements
```

The prompt appears only in the Header while pending; description, option order, callback values, styles, and text fallback remain unchanged.

- [ ] **Step 6: Keep non-completed footers status-only**

Pass resolved display status into `_render_footer()`:

```python
def _render_footer(session, footer_fields=None, *, display_status: str = "") -> str:
    if session.status == "failed" or display_status == "failed":
        return "已停止"
    if display_status == "waiting":
        return "等待选择"
    if session.status != "completed":
        return _spinner_text("生成中")
    # Keep the existing completed-statistics code unchanged below.
```

- [ ] **Step 7: Verify renderer/status/session tests**

```bash
.venv/bin/python -m pytest tests/unit/test_render.py tests/unit/test_status.py tests/unit/test_session.py -q
```

Expected: all tests pass; completed title and footer field order remain unchanged.

- [ ] **Step 8: Commit**

```bash
git add hermes_feishu_card/render.py tests/unit/test_render.py
git commit -m "feat: render live V4 runtime cards"
```

### Task 3: Prove the Existing Update Queue and Terminal Barriers

**Files:**
- Modify: `tests/integration/test_server.py`

**Interfaces:**
- Consumes: `/events`, `event_payload(...)`, `FakeFeishuClient`, `FlushController`, and `_render_session_card_for_app(...)`.
- Produces: evidence that preview and interim streams update one delivery, interaction restores cached preview, bursts coalesce, and terminal events win.

- [ ] **Step 1: Add one-card V4 lifecycle coverage**

```python
async def test_v4_runtime_header_and_interim_body_share_one_card(client):
    test_client, feishu_client = client
    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post("/events", json=event_payload(
        "thinking.delta", 1, {"text": "我先检查天气客户端。", "mode": "append_block"},
    ))
    await test_client.post("/events", json=event_payload("tool.updated", 2, {
        "tool_id": "read", "name": "read_file", "status": "running",
        "detail": "读取 weather_client.py",
    }))
    await wait_for_card_update(feishu_client, "读取 weather_client.py")
    running = feishu_client.updated[-1][1]
    assert running["header"]["title"]["content"] == "读取 weather_client.py"
    assert "我先检查天气客户端。" in str(running)
    assert all(message_id == "feishu-message-1" for message_id, _ in feishu_client.updated)

    await test_client.post("/events", json=event_payload("message.completed", 3, {
        "answer": "广州今天有短时阵雨。", "duration": 3.0, "model": "gpt-5.5",
        "tokens": {"input_tokens": 100, "output_tokens": 20},
        "context": {"used_tokens": 120, "max_tokens": 272000},
    }))
    await wait_for_card_update(feishu_client, "广州今天有短时阵雨。")
    completed = feishu_client.updated[-1][1]
    assert completed["header"]["title"]["content"] == "Hermes Agent"
    assert "读取 weather_client.py" not in str(completed["header"])
    assert "gpt-5.5" in str(completed)
```

- [ ] **Step 2: Add interaction restoration and preview burst coverage**

```python
async def test_v4_interaction_restores_cached_preview_on_same_card(client):
    test_client, feishu_client = client
    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post("/events", json=event_payload("tool.updated", 1, {
        "tool_id": "read", "name": "read_file", "status": "running",
        "detail": "读取 weather_client.py",
    }))
    await test_client.post("/events", json=event_payload("interaction.requested", 2, {
        "interaction_id": "approval-1", "kind": "approval",
        "prompt": "允许读取精确位置吗？", "description": "仅用于本次查询。",
        "options": [{"label": "允许一次", "value": "once", "style": "primary"}],
    }))
    waiting = feishu_client.updated[-1][1]
    assert waiting["header"]["title"]["content"] == "允许读取精确位置吗？"
    button = next(item for item in waiting["body"]["elements"] if item.get("tag") == "button")
    action_value = button["behaviors"][0]["value"]
    response = await test_client.post("/card/actions", json={"event": {
        "operator": {"open_id": "ou_bailey", "name": "Bailey"},
        "context": {"open_chat_id": "oc_abc"},
        "action": {"value": action_value},
    }})
    assert response.status == 200
    assert feishu_client.updated[-1][1]["header"]["title"]["content"] == "读取 weather_client.py"


async def test_v4_preview_burst_coalesces_and_late_preview_cannot_reopen_card(client):
    test_client, feishu_client = client
    await test_client.post("/events", json=event_payload("message.started", 0))
    responses = await asyncio.gather(*[
        test_client.post("/events", json=event_payload("tool.updated", index, {
            "tool_id": f"tool-{index}", "name": "read_file", "status": "running",
            "detail": f"读取 file-{index}.py",
        }))
        for index in range(1, 16)
    ])
    assert all(response.status == 200 for response in responses)
    await wait_for_card_update(feishu_client, "读取 file-15.py")
    completed = await test_client.post(
        "/events", json=event_payload("message.completed", 16, {"answer": "完成"}),
    )
    assert await completed.json() == {"ok": True, "applied": True}
    await wait_for_card_update(feishu_client, "完成")
    updates_before_late = len(feishu_client.updated)
    late = await test_client.post("/events", json=event_payload("tool.updated", 17, {
        "tool_id": "late", "name": "terminal", "status": "running", "detail": "迟到命令",
    }))
    assert await late.json() == {"ok": True, "applied": False}
    assert len(feishu_client.updated) == updates_before_late
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["update_coalesced"] > 0
    assert metrics["update_queue_peak"] == 1
```

- [ ] **Step 3: Run focused integration tests**

```bash
.venv/bin/python -m pytest tests/integration/test_server.py -k 'v4_ or interaction_request or streaming_deltas_are_throttled or terminal_event_with_stale_sequence or interrupted_terminal_update' -q
```

Expected: all selected tests pass without changing `server.py`, proving V4 reuses the established queue and terminal barriers.

- [ ] **Step 4: Run the server/hook matrix**

```bash
.venv/bin/python -m pytest tests/integration/test_server.py tests/unit/test_hook_runtime.py tests/integration/test_hook_runtime_integration.py -q
```

Expected: all tests pass, including topic, cron, operations, retries, and native-fallback behavior.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_server.py
git commit -m "test: verify V4 runtime card lifecycle"
```

### Task 4: Prepare V4.0.0 Metadata and Documentation

**Files:**
- Modify: `pyproject.toml`
- Modify: `hermes_feishu_card/__init__.py`
- Modify: `tests/unit/test_package_metadata.py`
- Modify: `tests/unit/test_docs.py`
- Create: `docs/release-notes-v4.0.0.md`
- Create: `docs/release-notes-v4.0.0.en.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `README-install.md`
- Modify: `TODO.md`
- Modify: `docker-compose.example.yml`
- Modify: `docs/user-guide.md`
- Modify: `docs/user-guide.en.md`
- Modify: `docs/release-readiness.md`
- Modify: `docs/release-readiness.en.md`
- Modify: `docs/wiki/event-flow.md`
- Modify: `docs/wiki/feishu-acceptance.md`

**Interfaces:**
- Consumes: verified V4 runtime behavior and `docs/wiki/release-playbook.md`.
- Produces: package version `4.0.0`, bilingual release notes, current install defaults, and documented event/acceptance contracts.

- [ ] **Step 1: Make metadata and docs tests require V4.0.0**

Update the exact version assertions in `tests/unit/test_package_metadata.py` to `4.0.0`. Add this test to `tests/unit/test_docs.py`:

```python
def test_v400_release_docs_cover_live_runtime_cards():
    changelog = read_doc("CHANGELOG.md")
    notes = read_doc("docs/release-notes-v4.0.0.md")
    notes_en = read_doc("docs/release-notes-v4.0.0.en.md")
    readme = read_doc("README.md")
    compose = read_doc("docker-compose.example.yml")
    assert "## V4.0.0" in changelog
    assert "tool.updated.detail" in notes
    assert "thinking.delta" in notes
    assert "tool.updated.detail" in notes_en
    assert "thinking.delta" in notes_en
    assert "运行态 Header" in readme
    assert 'HFC_VERSION: "${HFC_VERSION:-v4.0.0}"' in compose
```

- [ ] **Step 2: Run metadata/docs tests and verify failure**

```bash
.venv/bin/python -m pytest tests/unit/test_package_metadata.py tests/unit/test_docs.py -q
```

Expected: failures reference version `3.10.0` and missing V4 release documents.

- [ ] **Step 3: Bump package and current install defaults**

Set `pyproject.toml` to `version = "4.0.0"` and `hermes_feishu_card/__init__.py` to `__version__ = "4.0.0"`. Set current defaults in `docker-compose.example.yml`, `README-install.md`, and current install examples to `v4.0.0`. Keep historical V3.10.0 release rows and notes unchanged.

- [ ] **Step 4: Write bilingual V4 release notes and guide sections**

The Chinese release notes must include these exact claims:

```markdown
## 实时双轨卡片

- 运行态 Header 将 Hermes 工具名与 `progress_callback.preview` 整理为确定性的动作摘要，由现有 `tool.updated.detail` 驱动并原位替换。
- 正文独立流式显示 Hermes 公开的 `thinking.delta` 阶段输出；`answer.delta` 开始后主回答优先。
- 等待态 Header 显示 Hermes 原始交互问题；失败态保留最后一个工具预览。
- 运行、等待和失败 Footer 只显示状态；普通聊天完成态仅保留飞书原生回复引用并显示最终统计，不再叠加 Card JSON Header。

## 兼容性与安全

- 不扩展 Hermes patch 协议；没有 `preview` 的 Hermes 版本继续使用现有卡片。
- 工具预览进入 Card JSON 前执行单行折叠、长度限制和敏感参数脱敏。
- 完成态使用飞书原生回复引用作为唯一 Header，不在卡片内生成第二份引用或 `Hermes Agent` Header。
```

Mirror the claims in English. Update `TODO.md` to mark V4 complete only after Task 5 acceptance passes.

- [ ] **Step 5: Update maintainer event flow and acceptance checklist**

Add this mapping to `docs/wiki/event-flow.md`:

```text
progress_callback.preview -> tool.updated.detail
_interim_assistant_cb -> thinking.delta -> CardSession.thinking_text -> body until answer.delta begins
tool name + preview -> deterministic action summary -> non-completed Header subtitle
message.completed -> native Feishu reply Header only + completed status and final footer statistics
```

Add private/group smoke rows for preview replacement, empty-preview retention, interaction override/restore, failed preview retention, completed native-reply Header, and preview redaction.

- [ ] **Step 6: Verify metadata and documentation**

```bash
.venv/bin/python -m pytest tests/unit/test_package_metadata.py tests/unit/test_docs.py -q
git diff --check
```

Expected: tests pass and diff check exits 0.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml hermes_feishu_card/__init__.py tests/unit/test_package_metadata.py tests/unit/test_docs.py CHANGELOG.md README.md README.en.md README-install.md TODO.md docker-compose.example.yml docs/release-notes-v4.0.0.md docs/release-notes-v4.0.0.en.md docs/user-guide.md docs/user-guide.en.md docs/release-readiness.md docs/release-readiness.en.md docs/wiki/event-flow.md docs/wiki/feishu-acceptance.md
git commit -m "docs: prepare V4.0.0 live runtime cards"
```

### Task 5: Validate in Real Feishu and Replace Public Screenshots

**Files:**
- Create: `docs/assets/feishu-v4-runtime-running.png`
- Create: `docs/assets/feishu-v4-runtime-waiting.png`
- Create: `docs/assets/feishu-v4-runtime-failed.png`
- Create: `docs/assets/feishu-v4-runtime-completed.png`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/user-guide.en.md`
- Modify: `docs/release-notes-v4.0.0.md`
- Modify: `docs/release-notes-v4.0.0.en.md`
- Modify: `docs/release-readiness.md`
- Modify: `docs/release-readiness.en.md`
- Modify: `docs/wiki/feishu-acceptance.md`
- Modify: `docs/wiki/README.md`
- Create: `docs/wiki/feishu-cli-playbook.md`
- Modify: `tests/unit/test_docs.py`

**Interfaces:**
- Consumes: a locally installed V4.0.0 candidate, authenticated local `lark-cli`, private Feishu, and a local test group whose chat id must never be committed.
- Produces: four real-Feishu state screenshots, CLI-assisted callback/topic/message evidence, a maintainer playbook, public image references, and acceptance evidence.

- [ ] **Step 1: Install and verify the exact local candidate**

Install through the repository's supported installer/CLI path and restart through supported commands. Do not hand-edit Hermes. Verify:

```bash
.venv/bin/python -c 'import hermes_feishu_card; assert hermes_feishu_card.__version__ == "4.0.0"'
.venv/bin/python -m hermes_feishu_card.cli doctor --config ~/.hermes_feishu_card/config.yaml --hermes-dir ~/.hermes/hermes-agent --explain
```

Expected: V4.0.0 imports and doctor completes without a traceback or secret output.

Run the local CLI preflight with proxy bypass:

```bash
LARK_CLI_NO_PROXY=1 lark-cli doctor
LARK_CLI_NO_PROXY=1 lark-cli auth status
LARK_CLI_NO_PROXY=1 lark-cli whoami
LARK_CLI_NO_PROXY=1 lark-cli update --check
```

Expected: user and bot identities are ready. Record only pass/fail, CLI version, and missing scope names; never copy tokens or auth payloads into the repository. A CLI-auth success proves only the CLI app identity, not the Hermes app's permissions.

Use CLI discovery to confirm the target before sending test cards:

```bash
LARK_CLI_NO_PROXY=1 lark-cli im +chat-search --query "测试流式卡片" --as user
LARK_CLI_NO_PROXY=1 lark-cli im +chat-members-list --chat-id "$HFC_TEST_CHAT_ID" --as bot
```

Keep `HFC_TEST_CHAT_ID` in the shell environment only.

- [ ] **Step 2: Capture a presentable running card from real Feishu**

Send this exact private-chat request:

```text
请查询广州未来两小时的天气变化，并给我一份简洁的通勤建议。请先核对天气数据，再整理结论。
```

Capture while a non-empty tool preview is in the Header and public interim text is streaming in the body. Capture the card region directly so the image excludes the input box, sidebar, unrelated messages, unrelated avatars, desktop wallpaper, and notifications. Save as `docs/assets/feishu-v4-runtime-running.png`.

- [ ] **Step 3: Capture a presentable waiting card**

Prepare a harmless existing demo file, then send:

```text
请把广州周末出行建议整理到演示文件中。覆盖现有演示内容前，请先让我确认。
```

Capture the pending interaction before clicking. Require a concise prompt, useful explanation, and two compact buttons. Save as `docs/assets/feishu-v4-runtime-waiting.png`. Complete the interaction with the initiating user during group acceptance.

- [ ] **Step 4: Capture a presentable failed card**

Use an isolated acceptance event or harmless controlled tool failure with this scenario copy:

```text
请读取演示天气数据并生成摘要；如果数据源不可用，请明确报告失败原因。
```

The screenshot must show a concise failure explanation, retain a readable last preview, use a status-only Footer, and contain no stack trace or private path. Save as `docs/assets/feishu-v4-runtime-failed.png`.

- [ ] **Step 5: Capture a presentable completed card**

Use this answer shape:

```text
广州未来两小时以多云为主，局部可能有短时阵雨。

通勤建议：携带折叠伞，优先选择地铁；骑行请避开降雨较集中的时段。
```

Capture the native Feishu reply Header, a presentable final answer, timeline, and final statistics without a duplicate Card JSON Header. Save as `docs/assets/feishu-v4-runtime-completed.png`.

- [ ] **Step 6: Run screenshot privacy and visual QA**

Inspect every image at original resolution. Recapture any image containing chat/open/message ids, tokens, local usernames or paths, unrelated contacts/messages, notification banners, input drafts, excessive empty space, clipped buttons, overlapping text, or unreadable scaling. Do not blur private content into a public asset; capture a clean card region instead.

- [ ] **Step 7: Update image references and add docs tests**

Use running/completed near the README's first experience section and waiting/failed in the V4 section/release notes. Add descriptive bilingual alt text. Add:

```python
def test_v400_real_feishu_screenshots_are_referenced_and_present():
    paths = [
        "docs/assets/feishu-v4-runtime-running.png",
        "docs/assets/feishu-v4-runtime-waiting.png",
        "docs/assets/feishu-v4-runtime-failed.png",
        "docs/assets/feishu-v4-runtime-completed.png",
    ]
    for path in paths:
        assert (ROOT / path).is_file()
        assert (ROOT / path).stat().st_size > 20_000
    joined = read_doc("README.md") + read_doc("docs/release-notes-v4.0.0.md")
    for path in paths:
        assert path.replace("docs/", "") in joined or path in joined
```

- [ ] **Step 8: Run private/group acceptance and record evidence**

Verify preview replacement, tool-gap retention, independent interim/body streaming, interaction override/restore, same-user group ownership, failed preview retention, completed native-reply Header without a Card JSON Header, status-only non-completed Footer, final statistics, and absence of duplicate/native gray messages.

Observe one interaction callback from outside the HFC process:

```bash
LARK_CLI_NO_PROXY=1 lark-cli event consume card.action.trigger --as bot --max-events 1 --timeout 60s
```

Confirm the event contains the expected action tag/value and operator/message/chat routing fields, then compare it with sidecar diagnostics. Do not persist the raw event because it can contain callback tokens and identifiers.

Use message/topic inspection when a card updates the wrong delivery:

```bash
LARK_CLI_NO_PROXY=1 lark-cli im +messages-mget --message-ids "$HFC_TEST_MESSAGE_ID" --as bot
LARK_CLI_NO_PROXY=1 lark-cli im +threads-messages-list --message-id "$HFC_TEST_MESSAGE_ID" --as bot
```

Before using an unfamiliar endpoint, inspect it with `lark-cli schema`; do not guess payload fields.

- [ ] **Step 9: Write the optional CLI maintainer playbook**

Create `docs/wiki/feishu-cli-playbook.md` with:

```markdown
# 飞书 CLI 验收与诊断

`lark-cli` 是 HFC 的可选验收/诊断工具，不是 sidecar 运行时依赖。所有本机命令使用 `LARK_CLI_NO_PROXY=1`。

## 前检

- `lark-cli doctor`
- `lark-cli auth status`
- `lark-cli auth check --scope 'im:message im:message:readonly im:chat:read im:chat.members:read'`

## 真实卡片

- `im +chat-search`：定位测试群。
- `im +chat-members-list`：确认 bot 在群内。
- `event consume card.action.trigger`：旁路观察按钮/下拉回调。
- `im +messages-mget`：核对卡片消息。
- `im +threads-messages-list`：核对 topic/thread 锚点。

CLI 授权属于 CLI 应用，不能证明 Hermes 应用拥有同一组 scope。禁止把 token、callback token、chat/open/message id 或原始事件输出提交到 Git。
```

Link the playbook from `docs/wiki/README.md` and add a docs assertion that both the file and link exist.

- [ ] **Step 10: Verify and commit screenshots/evidence**

```bash
.venv/bin/python -m pytest tests/unit/test_docs.py -q
git diff --check
git add docs/assets/feishu-v4-runtime-running.png docs/assets/feishu-v4-runtime-waiting.png docs/assets/feishu-v4-runtime-failed.png docs/assets/feishu-v4-runtime-completed.png README.md README.en.md docs/user-guide.md docs/user-guide.en.md docs/release-notes-v4.0.0.md docs/release-notes-v4.0.0.en.md docs/release-readiness.md docs/release-readiness.en.md docs/wiki/feishu-acceptance.md docs/wiki/README.md docs/wiki/feishu-cli-playbook.md tests/unit/test_docs.py
git commit -m "docs: showcase V4.0.0 Feishu card states"
```

### Task 6: Merge, Tag, Publish, and Verify V4.0.0

**Files:**
- Modify only if verification finds drift: `docs/release-notes-v4.0.0.md`
- Modify only if verification finds drift: `docs/release-notes-v4.0.0.en.md`

**Interfaces:**
- Consumes: clean V4 branch, automated/real-Feishu evidence, GitHub CLI authentication, and `.github/workflows/release-assets.yml`.
- Produces: merged main, annotated `v4.0.0` tag, public GitHub Release, and four verified assets.

- [ ] **Step 1: Run the final local release gate**

```bash
.venv/bin/python -m pytest -q
git diff --check
git status --short
```

Expected: full suite passes, diff check exits 0, and worktree is clean.

- [ ] **Step 2: Push the V4 branch and open a ready PR**

Create `/tmp/v4-pr-body.md` containing the two event streams, state transitions, compatibility fallback, preview redaction, final test count, real-Feishu evidence, and four screenshot paths. Credit any contributor whose issue or PR is adopted; do not invent credit.

```bash
git push -u origin codex/v4.0.0-live-runtime-card
gh pr create --repo baileyh8/hermes-feishu-streaming-card --base main --head codex/v4.0.0-live-runtime-card --title "V4.0.0 live runtime card UX" --body-file /tmp/v4-pr-body.md
```

- [ ] **Step 3: Wait for CI and merge without discarding history**

```bash
gh pr checks --watch --repo baileyh8/hermes-feishu-streaming-card
gh pr merge --merge --delete-branch --repo baileyh8/hermes-feishu-streaming-card
```

Expected: required checks pass and GitHub reports the PR merged. Use a merge commit rather than squash when contributor-authored commits are present.

- [ ] **Step 4: Create and push the annotated tag from updated main**

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git tag -a v4.0.0 -m "Release v4.0.0 live runtime card UX"
git push origin v4.0.0
```

Expected: tag push succeeds and triggers `release-assets.yml`.

- [ ] **Step 5: Wait for release assets and apply curated notes**

```bash
gh run list --repo baileyh8/hermes-feishu-streaming-card --workflow release-assets.yml --limit 3
gh run watch --repo baileyh8/hermes-feishu-streaming-card "$(gh run list --repo baileyh8/hermes-feishu-streaming-card --workflow release-assets.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh release edit v4.0.0 --repo baileyh8/hermes-feishu-streaming-card --title "v4.0.0" --notes-file docs/release-notes-v4.0.0.md
```

Expected: workflow succeeds and release notes are the curated V4 notes.

- [ ] **Step 6: Verify the public release and four assets**

```bash
gh release view v4.0.0 --repo baileyh8/hermes-feishu-streaming-card --json tagName,name,isDraft,isPrerelease,assets,url
```

Required assets:

```text
hermes-feishu-card-v4.0.0-macos.tar.gz
hermes-feishu-card-v4.0.0-linux.tar.gz
hermes-feishu-card-v4.0.0-windows.zip
hermes-feishu-card-v4.0.0-checksums.txt
```

Verify the release is public, not draft/prerelease, and every asset has non-zero size.

- [ ] **Step 7: Install from the public tag and run final Feishu smoke**

Install V4.0.0 through the documented public path, run `doctor`, and send one short private request. Verify one card updates its runtime title, completes with final statistics, and emits no native gray duplicate.

- [ ] **Step 8: Report the release**

Report the release URL, merged PR, final test count, real Feishu states, updated screenshot assets, workflow run, four packages, contributor credit, and any explicitly deferred risk. Do not claim a state passed unless its screenshot and acceptance evidence were reviewed.
