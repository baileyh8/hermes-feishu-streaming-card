# 飞书流式卡片 Sidecar-only 第一阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标:** 交付一个可本地运行、可测试、可安全安装/恢复的 Hermes 飞书流式卡片 sidecar-only 插件第一版。

**架构:** 新代码放在 `hermes_feishu_card/`，旧 `adapter/`、`sidecar/`、`patch/`、历史安装器不进入新运行路径。Hermes Gateway 只负责最小事件转发，sidecar 负责事件协议、会话状态、文本归一化、卡片渲染、飞书 API 和诊断。

**技术栈:** Python 3.9+、aiohttp、PyYAML、argparse、pytest、pytest-asyncio、标准库 dataclasses/pathlib/subprocess/hashlib。

---

## 文件结构

- 新建 `pyproject.toml`: 项目元数据、依赖、pytest 配置、console script。
- 新建 `hermes_feishu_card/__init__.py`: 包版本和公开入口。
- 新建 `hermes_feishu_card/cli.py`: `doctor/install/start/stop/status/restore/uninstall` 命令入口。
- 新建 `hermes_feishu_card/config.py`: 配置加载、默认值、环境变量覆盖。
- 新建 `hermes_feishu_card/events.py`: 事件 schema、校验、序列化。
- 新建 `hermes_feishu_card/text.py`: `<think>` 标签过滤、句子/段落 flush 判断。
- 新建 `hermes_feishu_card/session.py`: `CardSession` 状态机、幂等、乱序保护、flush 决策。
- 新建 `hermes_feishu_card/render.py`: 飞书卡片 JSON v2 渲染。
- 新建 `hermes_feishu_card/feishu_client.py`: 飞书 token、发送、更新客户端。
- 新建 `hermes_feishu_card/server.py`: aiohttp sidecar HTTP 服务。
- 新建 `hermes_feishu_card/install/detect.py`: Hermes 版本与结构检测。
- 新建 `hermes_feishu_card/install/manifest.py`: 备份 manifest 数据结构与 hash。
- 新建 `hermes_feishu_card/install/patcher.py`: 最小 Hook 补丁计划、应用、恢复。
- 新建 `tests/unit/`: 纯单元测试。
- 新建 `tests/integration/`: sidecar HTTP 和安装器 fixture 测试。
- 新建 `tests/fixtures/hermes_v2026_4_23/`: 最小 Hermes fixture。
- 修改 `README.md`: 第一阶段中文安装和恢复说明。
- 新建 `docs/architecture.md`、`docs/event-protocol.md`、`docs/installer-safety.md`、`docs/testing.md`。

## 任务 1：建立干净包结构和测试入口

**文件:**
- 创建: `pyproject.toml`
- 创建: `hermes_feishu_card/__init__.py`
- 创建: `tests/unit/test_package_metadata.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/unit/test_package_metadata.py
from hermes_feishu_card import __version__


def test_package_has_version():
    assert __version__ == "0.1.0"
```

- [ ] **步骤 2：运行测试确认失败**

运行: `python3 -m pytest tests/unit/test_package_metadata.py -q`

预期: 失败，错误包含 `ModuleNotFoundError: No module named 'hermes_feishu_card'`。

- [ ] **步骤 3：写最小实现**

```toml
# pyproject.toml
[project]
name = "hermes-feishu-streaming-card"
version = "0.1.0"
description = "Hermes Gateway Feishu streaming card sidecar plugin"
requires-python = ">=3.9"
dependencies = [
  "aiohttp>=3.9",
  "PyYAML>=6.0",
]

[project.optional-dependencies]
test = [
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
]

[project.scripts]
hermes-feishu-card = "hermes_feishu_card.cli:main"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]
```

```python
# hermes_feishu_card/__init__.py
__version__ = "0.1.0"
```

- [ ] **步骤 4：运行测试确认通过**

运行: `python3 -m pytest tests/unit/test_package_metadata.py -q`

预期: `1 passed`。

- [ ] **步骤 5：提交**

```bash
git add pyproject.toml hermes_feishu_card/__init__.py tests/unit/test_package_metadata.py
git commit -m "chore: scaffold clean sidecar package"
```

## 任务 2：实现文本归一化和句子/段落感知 flush

**文件:**
- 创建: `hermes_feishu_card/text.py`
- 创建: `tests/unit/test_text.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/unit/test_text.py
from hermes_feishu_card.text import normalize_stream_text, should_flush_text


def test_normalize_removes_think_tags():
    raw = "<think>我在分析</think>\n最终不会出现标签"
    assert normalize_stream_text(raw) == "我在分析\n最终不会出现标签"


def test_flushes_on_chinese_sentence_end():
    assert should_flush_text("我先分析这个问题。", elapsed_ms=50, max_wait_ms=800, max_chars=200)


def test_flushes_on_newline_boundary():
    assert should_flush_text("第一段\n", elapsed_ms=50, max_wait_ms=800, max_chars=200)


def test_flushes_on_wait_threshold():
    assert should_flush_text("半句话", elapsed_ms=801, max_wait_ms=800, max_chars=200)


def test_does_not_flush_tiny_fragment_too_early():
    assert not should_flush_text("半句话", elapsed_ms=100, max_wait_ms=800, max_chars=200)
```

- [ ] **步骤 2：运行测试确认失败**

运行: `python3 -m pytest tests/unit/test_text.py -q`

预期: 失败，错误包含 `ModuleNotFoundError` 或 `ImportError`。

- [ ] **步骤 3：写最小实现**

```python
# hermes_feishu_card/text.py
from __future__ import annotations

import re

THINK_TAG_RE = re.compile(r"</?think>", re.IGNORECASE)
SENTENCE_END_RE = re.compile(r"[。！？!?\.]$")


def normalize_stream_text(text: str) -> str:
    """移除模型 thinking 标签，保留用户可读内容。"""
    return THINK_TAG_RE.sub("", text or "")


def should_flush_text(
    buffer: str,
    *,
    elapsed_ms: int,
    max_wait_ms: int,
    max_chars: int,
    force: bool = False,
) -> bool:
    if force:
        return True
    if not buffer:
        return False
    if len(buffer) >= max_chars:
        return True
    if elapsed_ms >= max_wait_ms:
        return True
    if buffer.endswith(("\n", "\r\n")):
        return True
    return bool(SENTENCE_END_RE.search(buffer.rstrip()))
```

- [ ] **步骤 4：运行测试确认通过**

运行: `python3 -m pytest tests/unit/test_text.py -q`

预期: `5 passed`。

- [ ] **步骤 5：提交**

```bash
git add hermes_feishu_card/text.py tests/unit/test_text.py
git commit -m "feat: add streaming text normalization"
```

## 任务 3：定义事件协议和校验

**文件:**
- 创建: `hermes_feishu_card/events.py`
- 创建: `tests/unit/test_events.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/unit/test_events.py
import pytest

from hermes_feishu_card.events import EventValidationError, SidecarEvent


def valid_payload(event="thinking.delta", sequence=2):
    return {
        "schema_version": "1",
        "event": event,
        "conversation_id": "chat-1",
        "message_id": "msg-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "sequence": sequence,
        "created_at": 1777017600.0,
        "data": {"text": "我在分析。"},
    }


def test_parses_valid_event():
    event = SidecarEvent.from_dict(valid_payload())
    assert event.event == "thinking.delta"
    assert event.sequence == 2


def test_rejects_unknown_event_name():
    with pytest.raises(EventValidationError, match="unknown event"):
        SidecarEvent.from_dict(valid_payload(event="bad.event"))


def test_rejects_missing_chat_id():
    payload = valid_payload()
    del payload["chat_id"]
    with pytest.raises(EventValidationError, match="chat_id"):
        SidecarEvent.from_dict(payload)


def test_rejects_non_feishu_platform():
    payload = valid_payload()
    payload["platform"] = "slack"
    with pytest.raises(EventValidationError, match="platform"):
        SidecarEvent.from_dict(payload)
```

- [ ] **步骤 2：运行测试确认失败**

运行: `python3 -m pytest tests/unit/test_events.py -q`

预期: 失败，错误包含 `No module named 'hermes_feishu_card.events'`。

- [ ] **步骤 3：写最小实现**

```python
# hermes_feishu_card/events.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

SUPPORTED_EVENTS = {
    "message.started",
    "thinking.delta",
    "tool.updated",
    "answer.delta",
    "message.completed",
    "message.failed",
}


class EventValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SidecarEvent:
    schema_version: str
    event: str
    conversation_id: str
    message_id: str
    chat_id: str
    platform: str
    sequence: int
    created_at: float
    data: Dict[str, Any]

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SidecarEvent":
        required = (
            "schema_version",
            "event",
            "conversation_id",
            "message_id",
            "chat_id",
            "platform",
            "sequence",
            "created_at",
            "data",
        )
        for key in required:
            if key not in payload:
                raise EventValidationError(f"missing required field: {key}")
        if payload["schema_version"] != "1":
            raise EventValidationError("unsupported schema_version")
        if payload["event"] not in SUPPORTED_EVENTS:
            raise EventValidationError(f"unknown event: {payload['event']}")
        if payload["platform"] != "feishu":
            raise EventValidationError("platform must be feishu")
        if not isinstance(payload["sequence"], int) or payload["sequence"] < 0:
            raise EventValidationError("sequence must be a non-negative integer")
        data = payload["data"]
        if not isinstance(data, dict):
            raise EventValidationError("data must be an object")
        return cls(
            schema_version=payload["schema_version"],
            event=payload["event"],
            conversation_id=str(payload["conversation_id"]),
            message_id=str(payload["message_id"]),
            chat_id=str(payload["chat_id"]),
            platform=payload["platform"],
            sequence=payload["sequence"],
            created_at=float(payload["created_at"]),
            data=data,
        )
```

- [ ] **步骤 4：运行测试确认通过**

运行: `python3 -m pytest tests/unit/test_events.py -q`

预期: `4 passed`。

- [ ] **步骤 5：提交**

```bash
git add hermes_feishu_card/events.py tests/unit/test_events.py
git commit -m "feat: define sidecar event protocol"
```

## 任务 4：实现 CardSession 状态机

**文件:**
- 创建: `hermes_feishu_card/session.py`
- 创建: `tests/unit/test_session.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/unit/test_session.py
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.session import CardSession


def event(name, sequence, data):
    return SidecarEvent.from_dict({
        "schema_version": "1",
        "event": name,
        "conversation_id": "chat-1",
        "message_id": "msg-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "sequence": sequence,
        "created_at": 1777017600.0 + sequence,
        "data": data,
    })


def test_thinking_accumulates_and_strips_tags():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("thinking.delta", 1, {"text": "<think>先分析"}))
    assert session.apply(event("thinking.delta", 2, {"text": "</think>结束。"}))
    assert session.thinking_text == "先分析结束。"


def test_rejects_duplicate_and_stale_sequence():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("thinking.delta", 2, {"text": "新"}))
    assert not session.apply(event("thinking.delta", 2, {"text": "重复"}))
    assert not session.apply(event("thinking.delta", 1, {"text": "旧"}))
    assert session.thinking_text == "新"


def test_tool_updates_count_unique_events():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(event("tool.updated", 1, {"tool_id": "t1", "name": "search", "status": "running"}))
    session.apply(event("tool.updated", 2, {"tool_id": "t1", "name": "search", "status": "completed"}))
    session.apply(event("tool.updated", 3, {"tool_id": "t2", "name": "fetch", "status": "completed"}))
    assert session.tool_count == 2
    assert session.tools["t1"].status == "completed"


def test_completion_replaces_thinking_with_answer():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(event("thinking.delta", 1, {"text": "思考内容。"}))
    session.apply(event("message.completed", 2, {"answer": "最终答案", "tokens": {"input_tokens": 10}, "duration": 3.5}))
    assert session.status == "completed"
    assert session.visible_main_text == "最终答案"
```

- [ ] **步骤 2：运行测试确认失败**

运行: `python3 -m pytest tests/unit/test_session.py -q`

预期: 失败，错误包含 `No module named 'hermes_feishu_card.session'`。

- [ ] **步骤 3：写最小实现**

```python
# hermes_feishu_card/session.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from .events import SidecarEvent
from .text import normalize_stream_text


@dataclass
class ToolState:
    tool_id: str
    name: str
    status: str
    detail: str = ""


@dataclass
class CardSession:
    conversation_id: str
    message_id: str
    chat_id: str
    status: str = "thinking"
    last_sequence: int = -1
    thinking_text: str = ""
    answer_text: str = ""
    tools: Dict[str, ToolState] = field(default_factory=dict)
    tokens: Dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0

    @property
    def tool_count(self) -> int:
        return len(self.tools)

    @property
    def visible_main_text(self) -> str:
        if self.status == "completed":
            return self.answer_text
        return self.thinking_text

    def apply(self, event: SidecarEvent) -> bool:
        if event.message_id != self.message_id:
            return False
        if event.sequence <= self.last_sequence:
            return False
        self.last_sequence = event.sequence

        if event.event == "thinking.delta":
            self.thinking_text += normalize_stream_text(str(event.data.get("text", "")))
        elif event.event == "answer.delta":
            self.answer_text += normalize_stream_text(str(event.data.get("text", "")))
        elif event.event == "tool.updated":
            tool_id = str(event.data.get("tool_id") or event.data.get("name") or f"tool-{self.tool_count + 1}")
            self.tools[tool_id] = ToolState(
                tool_id=tool_id,
                name=str(event.data.get("name", tool_id)),
                status=str(event.data.get("status", "running")),
                detail=str(event.data.get("detail", "")),
            )
        elif event.event == "message.completed":
            self.status = "completed"
            self.answer_text = normalize_stream_text(str(event.data.get("answer") or self.answer_text))
            self.tokens = dict(event.data.get("tokens", {}))
            self.duration = float(event.data.get("duration", 0.0))
        elif event.event == "message.failed":
            self.status = "failed"
            self.answer_text = str(event.data.get("error", "消息处理失败"))
        return True
```

- [ ] **步骤 4：运行测试确认通过**

运行: `python3 -m pytest tests/unit/test_session.py -q`

预期: `4 passed`。

- [ ] **步骤 5：提交**

```bash
git add hermes_feishu_card/session.py tests/unit/test_session.py
git commit -m "feat: add card session state machine"
```

## 任务 5：实现飞书卡片渲染器

**文件:**
- 创建: `hermes_feishu_card/render.py`
- 创建: `tests/unit/test_render.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/unit/test_render.py
from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession, ToolState


def test_render_thinking_card_has_two_state_label_and_tools():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "正在分析。"
    session.tools["t1"] = ToolState(tool_id="t1", name="search", status="running")
    card = render_card(session)
    assert card["schema"] == "2.0"
    assert card["header"]["subtitle"]["content"] == "思考中"
    content = str(card)
    assert "正在分析。" in content
    assert "工具调用 1 次" in content


def test_render_completed_card_replaces_thinking():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "不会展示"
    session.answer_text = "最终答案"
    session.status = "completed"
    card = render_card(session)
    content = str(card)
    assert card["header"]["subtitle"]["content"] == "已完成"
    assert "最终答案" in content
    assert "不会展示" not in content
```

- [ ] **步骤 2：运行测试确认失败**

运行: `python3 -m pytest tests/unit/test_render.py -q`

预期: 失败，错误包含 `No module named 'hermes_feishu_card.render'`。

- [ ] **步骤 3：写最小实现**

```python
# hermes_feishu_card/render.py
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
```

- [ ] **步骤 4：运行测试确认通过**

运行: `python3 -m pytest tests/unit/test_render.py -q`

预期: `2 passed`。

- [ ] **步骤 5：提交**

```bash
git add hermes_feishu_card/render.py tests/unit/test_render.py
git commit -m "feat: render two-state Feishu cards"
```

## 任务 6：实现 FeishuClient 与 fake client 边界

**文件:**
- 创建: `hermes_feishu_card/feishu_client.py`
- 创建: `tests/unit/test_feishu_client.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/unit/test_feishu_client.py
import pytest

from hermes_feishu_card.feishu_client import FeishuClientConfig, FeishuClient


def test_config_requires_credentials_for_real_client():
    with pytest.raises(ValueError, match="app_id"):
        FeishuClientConfig(app_id="", app_secret="secret")


def test_build_message_payload_serializes_card():
    cfg = FeishuClientConfig(app_id="cli_a", app_secret="sec")
    client = FeishuClient(cfg)
    payload = client.build_message_payload("oc_abc", {"schema": "2.0"})
    assert payload["receive_id"] == "oc_abc"
    assert payload["msg_type"] == "interactive"
    assert '"schema": "2.0"' in payload["content"]
```

- [ ] **步骤 2：运行测试确认失败**

运行: `python3 -m pytest tests/unit/test_feishu_client.py -q`

预期: 失败，错误包含 `No module named 'hermes_feishu_card.feishu_client'`。

- [ ] **步骤 3：写最小实现**

```python
# hermes_feishu_card/feishu_client.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class FeishuClientConfig:
    app_id: str
    app_secret: str
    base_url: str = "https://open.feishu.cn/open-apis"
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if not self.app_id:
            raise ValueError("app_id is required")
        if not self.app_secret:
            raise ValueError("app_secret is required")


class FeishuClient:
    def __init__(self, config: FeishuClientConfig):
        self.config = config

    def build_message_payload(self, chat_id: str, card: Dict[str, Any]) -> Dict[str, str]:
        return {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }

    async def send_card(self, chat_id: str, card: Dict[str, Any]) -> str:
        raise NotImplementedError("send_card will be implemented with aiohttp in the integration task")

    async def update_card_message(self, message_id: str, card: Dict[str, Any]) -> None:
        raise NotImplementedError("update_card_message will be implemented with aiohttp in the integration task")
```

- [ ] **步骤 4：运行测试确认通过**

运行: `python3 -m pytest tests/unit/test_feishu_client.py -q`

预期: `2 passed`。

- [ ] **步骤 5：提交**

```bash
git add hermes_feishu_card/feishu_client.py tests/unit/test_feishu_client.py
git commit -m "feat: define Feishu client boundary"
```

## 任务 7：实现 sidecar HTTP 服务闭环

**文件:**
- 创建: `hermes_feishu_card/server.py`
- 创建: `tests/integration/test_server.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/integration/test_server.py
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card.server import create_app


class FakeFeishuClient:
    def __init__(self):
        self.sent = []
        self.updated = []

    async def send_card(self, chat_id, card):
        self.sent.append((chat_id, card))
        return "om_fake"

    async def update_card_message(self, message_id, card):
        self.updated.append((message_id, card))


async def test_health_endpoint():
    fake = FakeFeishuClient()
    app = create_app(fake)
    client = TestClient(TestServer(app))
    await client.start_server()
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "healthy"
    await client.close()


async def test_event_lifecycle_creates_and_completes_card():
    fake = FakeFeishuClient()
    app = create_app(fake)
    client = TestClient(TestServer(app))
    await client.start_server()
    base = {
        "schema_version": "1",
        "conversation_id": "chat-1",
        "message_id": "msg-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "created_at": 1777017600.0,
    }
    started = dict(base, event="message.started", sequence=0, data={})
    thinking = dict(base, event="thinking.delta", sequence=1, data={"text": "我在分析。"})
    done = dict(base, event="message.completed", sequence=2, data={"answer": "最终答案"})
    assert (await client.post("/events", json=started)).status == 200
    assert (await client.post("/events", json=thinking)).status == 200
    assert (await client.post("/events", json=done)).status == 200
    assert fake.sent[0][0] == "oc_abc"
    assert fake.updated
    assert "最终答案" in str(fake.updated[-1][1])
    await client.close()
```

- [ ] **步骤 2：运行测试确认失败**

运行: `python3 -m pytest tests/integration/test_server.py -q`

预期: 失败，错误包含 `No module named 'hermes_feishu_card.server'`。

- [ ] **步骤 3：写最小实现**

```python
# hermes_feishu_card/server.py
from __future__ import annotations

from typing import Dict

from aiohttp import web

from .events import EventValidationError, SidecarEvent
from .render import render_card
from .session import CardSession


def create_app(feishu_client) -> web.Application:
    app = web.Application()
    app["feishu_client"] = feishu_client
    app["sessions"]: Dict[str, CardSession] = {}
    app.router.add_get("/health", handle_health)
    app.router.add_post("/events", handle_events)
    return app


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "healthy", "active_sessions": len(request.app["sessions"])})


async def handle_events(request: web.Request) -> web.Response:
    try:
        event = SidecarEvent.from_dict(await request.json())
    except EventValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    sessions = request.app["sessions"]
    session = sessions.get(event.message_id)
    if session is None:
        session = CardSession(
            conversation_id=event.conversation_id,
            message_id=event.message_id,
            chat_id=event.chat_id,
        )
        sessions[event.message_id] = session
    applied = session.apply(event)
    if event.event == "message.started":
        message_id = await request.app["feishu_client"].send_card(event.chat_id, render_card(session))
        session.feishu_message_id = message_id
    elif applied:
        feishu_message_id = getattr(session, "feishu_message_id", "om_fake")
        await request.app["feishu_client"].update_card_message(feishu_message_id, render_card(session))
    return web.json_response({"ok": True, "applied": applied})
```

- [ ] **步骤 4：运行测试确认通过**

运行: `python3 -m pytest tests/integration/test_server.py -q`

预期: `2 passed`。

- [ ] **步骤 5：提交**

```bash
git add hermes_feishu_card/server.py tests/integration/test_server.py
git commit -m "feat: add sidecar HTTP event lifecycle"
```

## 任务 8：实现配置加载和 CLI doctor/status

**文件:**
- 创建: `hermes_feishu_card/config.py`
- 创建: `hermes_feishu_card/cli.py`
- 创建: `tests/unit/test_config.py`
- 创建: `tests/integration/test_cli.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/unit/test_config.py
from hermes_feishu_card.config import load_config


def test_load_config_defaults_when_file_missing(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg["server"]["host"] == "127.0.0.1"
    assert cfg["server"]["port"] == 8765
```

```python
# tests/integration/test_cli.py
import subprocess
import sys


def test_cli_doctor_runs():
    result = subprocess.run(
        [sys.executable, "-m", "hermes_feishu_card.cli", "doctor", "--skip-hermes"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "doctor" in result.stdout.lower()
```

- [ ] **步骤 2：运行测试确认失败**

运行: `python3 -m pytest tests/unit/test_config.py tests/integration/test_cli.py -q`

预期: 失败，错误包含缺少 `config` 或 `cli`。

- [ ] **步骤 3：写最小实现**

```python
# hermes_feishu_card/config.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

DEFAULT_CONFIG: Dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 8765},
    "feishu": {"app_id": "", "app_secret": ""},
    "card": {"max_wait_ms": 800, "max_chars": 240},
}


def load_config(path: str | Path) -> Dict[str, Any]:
    cfg = {k: dict(v) for k, v in DEFAULT_CONFIG.items()}
    path = Path(path).expanduser()
    if path.exists():
        user_cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for key, value in user_cfg.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg
```

```python
# hermes_feishu_card/cli.py
from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-feishu-card")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--config", default="~/.hermes/feishu-card.yaml")
    doctor.add_argument("--skip-hermes", action="store_true")
    sub.add_parser("status")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        cfg = load_config(Path(args.config))
        print(f"doctor ok: sidecar {cfg['server']['host']}:{cfg['server']['port']}")
        return 0
    if args.command == "status":
        print("status: sidecar process manager not started")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **步骤 4：运行测试确认通过**

运行: `python3 -m pytest tests/unit/test_config.py tests/integration/test_cli.py -q`

预期: `2 passed`。

- [ ] **步骤 5：提交**

```bash
git add hermes_feishu_card/config.py hermes_feishu_card/cli.py tests/unit/test_config.py tests/integration/test_cli.py
git commit -m "feat: add config loading and doctor CLI"
```

## 任务 9：实现安装器检测、manifest 和补丁计划

**文件:**
- 创建: `hermes_feishu_card/install/__init__.py`
- 创建: `hermes_feishu_card/install/detect.py`
- 创建: `hermes_feishu_card/install/manifest.py`
- 创建: `hermes_feishu_card/install/patcher.py`
- 创建: `tests/fixtures/hermes_v2026_4_23/gateway/run.py`
- 创建: `tests/fixtures/hermes_v2026_4_23/VERSION`
- 创建: `tests/unit/test_installer_detection.py`
- 创建: `tests/unit/test_manifest.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/unit/test_installer_detection.py
from pathlib import Path

from hermes_feishu_card.install.detect import detect_hermes


def test_detect_supported_fixture():
    root = Path("tests/fixtures/hermes_v2026_4_23")
    result = detect_hermes(root)
    assert result.supported
    assert result.version == "v2026.4.23"
    assert result.run_py.name == "run.py"
```

```python
# tests/unit/test_manifest.py
from pathlib import Path

from hermes_feishu_card.install.manifest import file_sha256


def test_file_sha256_is_stable(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("abc", encoding="utf-8")
    assert file_sha256(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
```

- [ ] **步骤 2：创建 fixture 并运行失败测试**

```python
# tests/fixtures/hermes_v2026_4_23/gateway/run.py
class Gateway:
    async def _handle_message_with_agent(self, event, source):
        await self.hooks.emit("agent:start", {})
        response = "ok"
        await self.hooks.emit("agent:end", {"response": response})
        return response
```

```text
# tests/fixtures/hermes_v2026_4_23/VERSION
v2026.4.23
```

运行: `python3 -m pytest tests/unit/test_installer_detection.py tests/unit/test_manifest.py -q`

预期: 失败，错误包含缺少 `hermes_feishu_card.install`。

- [ ] **步骤 3：写最小实现**

```python
# hermes_feishu_card/install/__init__.py
"""安装、检测和恢复工具。"""
```

```python
# hermes_feishu_card/install/detect.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HermesDetection:
    root: Path
    version: str
    run_py: Path
    supported: bool
    reason: str


def detect_hermes(root: str | Path) -> HermesDetection:
    root = Path(root)
    version_path = root / "VERSION"
    version = version_path.read_text(encoding="utf-8").strip() if version_path.exists() else "unknown"
    run_py = root / "gateway" / "run.py"
    if not run_py.exists():
        return HermesDetection(root, version, run_py, False, "gateway/run.py not found")
    content = run_py.read_text(encoding="utf-8")
    has_agent_handler = "_handle_message_with_agent" in content
    has_agent_end = 'hooks.emit("agent:end"' in content or "hooks.emit('agent:end'" in content
    supported_version = version >= "v2026.4.23"
    supported = supported_version and has_agent_handler and has_agent_end
    reason = "ok" if supported else "missing supported version or hook anchors"
    return HermesDetection(root, version, run_py, supported, reason)
```

```python
# hermes_feishu_card/install/manifest.py
from __future__ import annotations

import hashlib
from pathlib import Path


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()
```

```python
# hermes_feishu_card/install/patcher.py
from __future__ import annotations

PATCH_BEGIN = "# BEGIN hermes-feishu-card sidecar hook"
PATCH_END = "# END hermes-feishu-card sidecar hook"
```

- [ ] **步骤 4：运行测试确认通过**

运行: `python3 -m pytest tests/unit/test_installer_detection.py tests/unit/test_manifest.py -q`

预期: `2 passed`。

- [ ] **步骤 5：提交**

```bash
git add hermes_feishu_card/install tests/fixtures/hermes_v2026_4_23 tests/unit/test_installer_detection.py tests/unit/test_manifest.py
git commit -m "feat: add Hermes detection and install manifest primitives"
```

## 任务 10：实现 Hook 补丁应用和恢复

**文件:**
- 修改: `hermes_feishu_card/install/patcher.py`
- 创建: `tests/unit/test_patcher.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/unit/test_patcher.py
from hermes_feishu_card.install.patcher import PATCH_BEGIN, apply_patch, remove_patch


def test_apply_patch_is_idempotent():
    original = "async def _handle_message_with_agent(self, event, source):\n    return 'ok'\n"
    once = apply_patch(original)
    twice = apply_patch(once)
    assert once == twice
    assert PATCH_BEGIN in once


def test_remove_patch_restores_original_region():
    original = "async def _handle_message_with_agent(self, event, source):\n    return 'ok'\n"
    patched = apply_patch(original)
    restored = remove_patch(patched)
    assert PATCH_BEGIN not in restored
    assert "return 'ok'" in restored
```

- [ ] **步骤 2：运行测试确认失败**

运行: `python3 -m pytest tests/unit/test_patcher.py -q`

预期: 失败，错误包含无法导入 `apply_patch`。

- [ ] **步骤 3：写最小实现**

```python
# hermes_feishu_card/install/patcher.py
from __future__ import annotations

PATCH_BEGIN = "# BEGIN hermes-feishu-card sidecar hook"
PATCH_END = "# END hermes-feishu-card sidecar hook"

HOOK_BLOCK = f"""
    {PATCH_BEGIN}
    try:
        # 运行时实现阶段会替换为非阻塞本地 HTTP 事件发送。
        pass
    except Exception:
        pass
    {PATCH_END}
"""


def apply_patch(content: str) -> str:
    if PATCH_BEGIN in content:
        return content
    marker = "async def _handle_message_with_agent"
    idx = content.find(marker)
    if idx == -1:
        raise ValueError("missing _handle_message_with_agent")
    line_end = content.find("\n", idx)
    return content[: line_end + 1] + HOOK_BLOCK + content[line_end + 1 :]


def remove_patch(content: str) -> str:
    start = content.find(PATCH_BEGIN)
    if start == -1:
        return content
    line_start = content.rfind("\n", 0, start)
    end = content.find(PATCH_END, start)
    if end == -1:
        raise ValueError("patch end marker missing")
    line_end = content.find("\n", end)
    if line_end == -1:
        line_end = len(content)
    return content[: line_start + 1] + content[line_end + 1 :]
```

- [ ] **步骤 4：运行测试确认通过**

运行: `python3 -m pytest tests/unit/test_patcher.py -q`

预期: `2 passed`。

- [ ] **步骤 5：提交**

```bash
git add hermes_feishu_card/install/patcher.py tests/unit/test_patcher.py
git commit -m "feat: add idempotent gateway hook patcher"
```

## 任务 11：补齐 CLI install/restore/uninstall 最小闭环

**文件:**
- 修改: `hermes_feishu_card/cli.py`
- 创建: `tests/integration/test_cli_install.py`

- [ ] **步骤 1：写失败测试**

```python
# tests/integration/test_cli_install.py
import shutil
import subprocess
import sys
from pathlib import Path


def test_cli_install_and_restore_fixture(tmp_path):
    src = Path("tests/fixtures/hermes_v2026_4_23")
    hermes = tmp_path / "hermes"
    shutil.copytree(src, hermes)
    install = subprocess.run(
        [sys.executable, "-m", "hermes_feishu_card.cli", "install", "--hermes-dir", str(hermes), "--yes"],
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, install.stderr
    run_py = hermes / "gateway" / "run.py"
    assert "hermes-feishu-card sidecar hook" in run_py.read_text(encoding="utf-8")
    restore = subprocess.run(
        [sys.executable, "-m", "hermes_feishu_card.cli", "restore", "--hermes-dir", str(hermes), "--yes"],
        capture_output=True,
        text=True,
    )
    assert restore.returncode == 0, restore.stderr
    assert "hermes-feishu-card sidecar hook" not in run_py.read_text(encoding="utf-8")
```

- [ ] **步骤 2：运行测试确认失败**

运行: `python3 -m pytest tests/integration/test_cli_install.py -q`

预期: 失败，CLI 不认识 `install` 或 `restore`。

- [ ] **步骤 3：写最小实现**

```python
# 在 hermes_feishu_card/cli.py 中扩展
from .install.detect import detect_hermes
from .install.manifest import file_sha256
from .install.patcher import apply_patch, remove_patch


def _add_install_commands(sub):
    install = sub.add_parser("install")
    install.add_argument("--hermes-dir", required=True)
    install.add_argument("--yes", action="store_true")
    restore = sub.add_parser("restore")
    restore.add_argument("--hermes-dir", required=True)
    restore.add_argument("--yes", action="store_true")
    uninstall = sub.add_parser("uninstall")
    uninstall.add_argument("--hermes-dir", required=True)
    uninstall.add_argument("--yes", action="store_true")


def _install(hermes_dir: str) -> int:
    detection = detect_hermes(hermes_dir)
    if not detection.supported:
        print(f"install failed: {detection.reason}")
        return 1
    backup = detection.run_py.with_suffix(".py.hermes_feishu_card.bak")
    if not backup.exists():
        backup.write_text(detection.run_py.read_text(encoding="utf-8"), encoding="utf-8")
    patched = apply_patch(detection.run_py.read_text(encoding="utf-8"))
    detection.run_py.write_text(patched, encoding="utf-8")
    manifest = detection.root / ".hermes_feishu_card_manifest"
    manifest.write_text(file_sha256(detection.run_py), encoding="utf-8")
    print("install ok")
    return 0


def _restore(hermes_dir: str) -> int:
    detection = detect_hermes(hermes_dir)
    backup = detection.run_py.with_suffix(".py.hermes_feishu_card.bak")
    if backup.exists():
        detection.run_py.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        detection.run_py.write_text(remove_patch(detection.run_py.read_text(encoding="utf-8")), encoding="utf-8")
    print("restore ok")
    return 0
```

同时在 `main()` 分支中调用 `_install(args.hermes_dir)`、`_restore(args.hermes_dir)`，`uninstall` 第一阶段复用 `_restore`。

- [ ] **步骤 4：运行测试确认通过**

运行: `python3 -m pytest tests/integration/test_cli_install.py -q`

预期: `1 passed`。

- [ ] **步骤 5：提交**

```bash
git add hermes_feishu_card/cli.py tests/integration/test_cli_install.py
git commit -m "feat: add install and restore CLI flow"
```

## 任务 12：更新中文文档并标记旧代码为非主线

**文件:**
- 修改: `README.md`
- 创建: `docs/architecture.md`
- 创建: `docs/event-protocol.md`
- 创建: `docs/installer-safety.md`
- 创建: `docs/testing.md`
- 修改: `TODO.md`

- [ ] **步骤 1：写文档检查测试**

```python
# tests/unit/test_docs.py
from pathlib import Path


def test_readme_mentions_sidecar_only_and_supported_version():
    text = Path("README.md").read_text(encoding="utf-8")
    assert "sidecar-only" in text.lower()
    assert "v2026.4.23" in text


def test_protocol_doc_mentions_two_card_states():
    text = Path("docs/event-protocol.md").read_text(encoding="utf-8")
    assert "思考中" in text
    assert "已完成" in text
```

- [ ] **步骤 2：运行测试确认失败**

运行: `python3 -m pytest tests/unit/test_docs.py -q`

预期: 失败，README 或协议文档尚未更新。

- [ ] **步骤 3：写中文文档**

`README.md` 必须包含以下结构：

```markdown
# Hermes 飞书流式卡片

这是一个 sidecar-only Hermes 插件，为 Hermes Agent 的飞书/Lark 对话提供流式卡片回复。

## 支持范围

- 默认支持 Hermes Agent v2026.4.23 / v0.11.0 及以上。
- 安装器会同时检查版本和代码结构；检查失败时不会写入 Hermes 文件。

## 快速开始

python3 -m pip install -e ".[test]"
python3 -m hermes_feishu_card.cli doctor --skip-hermes
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes

## 恢复

python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

`docs/event-protocol.md` 必须列出 `message.started`、`thinking.delta`、`tool.updated`、`answer.delta`、`message.completed`、`message.failed`，并说明卡片正常状态只有 `思考中` 和 `已完成`。

`docs/installer-safety.md` 必须说明安装前检查、备份、manifest、restore、uninstall 和 sidecar 不可用时 Hermes 原生文本降级。

`TODO.md` 必须改成新主线任务清单，明确旧 legacy/dual 不是 active runtime。

- [ ] **步骤 4：运行测试确认通过**

运行: `python3 -m pytest tests/unit/test_docs.py -q`

预期: `2 passed`。

- [ ] **步骤 5：提交**

```bash
git add README.md TODO.md docs/architecture.md docs/event-protocol.md docs/installer-safety.md docs/testing.md tests/unit/test_docs.py
git commit -m "docs: document sidecar-only first phase"
```

## 任务 13：最终验证第一阶段闭环

**文件:**
- 修改: 无，除非验证暴露缺陷。

- [ ] **步骤 1：运行完整测试**

运行: `python3 -m pytest -q`

预期: 所有测试通过。

- [ ] **步骤 2：运行 CLI doctor**

运行: `python3 -m hermes_feishu_card.cli doctor --skip-hermes`

预期: 输出包含 `doctor ok`，退出码为 0。

- [ ] **步骤 3：运行 fixture 安装恢复手工验证**

```bash
tmpdir="$(mktemp -d)"
cp -R tests/fixtures/hermes_v2026_4_23 "$tmpdir/hermes"
python3 -m hermes_feishu_card.cli install --hermes-dir "$tmpdir/hermes" --yes
python3 -m hermes_feishu_card.cli restore --hermes-dir "$tmpdir/hermes" --yes
rg "hermes-feishu-card sidecar hook" "$tmpdir/hermes/gateway/run.py" && exit 1 || true
```

预期: install 和 restore 都成功，最终 `run.py` 不含补丁标记。

- [ ] **步骤 4：检查 git 状态**

运行: `git status --short`

预期: 无未提交修改。

- [ ] **步骤 5：提交验证修复**

如果步骤 1-3 暴露缺陷，修复后提交：

```bash
git status --short
git add hermes_feishu_card tests docs README.md TODO.md pyproject.toml
git commit -m "fix: stabilize sidecar phase one verification"
```

如果没有缺陷，不创建空提交。

## 计划自检

- Spec 覆盖：本计划覆盖 sidecar-only 运行时、两态卡片、thinking 标签过滤、工具计数、Hermes `v2026.4.23+` 检测、安装备份恢复、测试和中文文档。
- 范围控制：第一阶段不实现 pip/release 产品化，不接入真实飞书 smoke test 默认运行，不保留 legacy/dual 主线。
- 类型一致性：事件名、模块名、CLI 命令和 spec 保持一致。
- 验证策略：每个功能任务先写失败测试，再写最小实现，再运行测试并提交。
