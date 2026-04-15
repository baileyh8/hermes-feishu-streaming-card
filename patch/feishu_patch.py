"""
feishu_patch.py
===============
Injects the Feishu streaming card feature into hermes-agent/gateway/platforms/feishu.py.

What it patches:
  1. __init__: adds _streaming_card, _streaming_pending, _streaming_card_locks,
               _pending_greeting, _pending_subtitle
  2. Adds methods: _get_card_lock, format_token, build_footer,
                   send_streaming_card, _update_card_element,
                   _update_card_subtitle, finalize_streaming_card
  3. Modifies send(): prepends the 4-branch streaming card routing
  4. Modifies edit_message(): routes ALL content to streaming card (not just emoji)

Config (from config.yaml):
  feishu_streaming_card.greeting  — card header title  (default: 主人，苏菲为您服务！)
  feishu_streaming_card.enabled  — enable/disable      (default: true)
  feishu_streaming_card.pending_timeout — send_progress timeout (default: 30)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import hashlib
import re

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _get_default_greeting() -> str:
    return "主人，苏菲为您服务！"


def _read_config(hermes_dir: str) -> dict:
    """Read hermes config.yaml and return the feishu_streaming_card section."""
    import yaml
    cfg_path = f"{hermes_dir}/config.yaml"
    try:
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        cfg = {}
    return cfg.get("feishu_streaming_card", {})


# ─────────────────────────────────────────────────────────────────
# Code to inject: streaming card state attrs
# ─────────────────────────────────────────────────────────────────

STREAMING_STATE_INIT = '''
        # ── Feishu Streaming Card state ────────────────────────────────────
        # Streaming card state: chat_id → {"card_id": str, "message_id": str,
        #                                   "sequence": int, "tenant_token": str, ...}
        self._streaming_card: dict = {{}}
        # Per-chat lock to serialize card updates and prevent sequence race (code=300317)
        self._streaming_card_locks: dict = {{}}
        # Pending flag: signals send_progress_messages to wait for card creation
        self._streaming_pending: dict = {{}}
        # Greeting / model name set before agent starts (read by send() on first emoji tool)
        self._pending_greeting = "主人，苏妃为您服务！"
        self._pending_subtitle = ""
'''


# ─────────────────────────────────────────────────────────────────
# Code to inject: streaming card methods
# ─────────────────────────────────────────────────────────────────

STREAMING_METHODS = '''
    # ══════════════════════════════════════════════════════════════════════
    # Feishu Streaming Card — per-chat typewriter card for task execution
    # ══════════════════════════════════════════════════════════════════════

    def _get_card_lock(self, chat_id: str):
        """Get or create a per-chat asyncio.Lock for serializing card updates."""
        if chat_id not in self._streaming_card_locks:
            self._streaming_card_locks[chat_id] = asyncio.Lock()
        return self._streaming_card_locks[chat_id]

    def set_streaming_greeting(self, greeting: str) -> None:
        """Set the greeting to display in the streaming card header title."""
        self._pending_greeting = greeting

    def set_streaming_model(self, model: str) -> None:
        """Set the model name to display in the streaming card subtitle."""
        self._pending_subtitle = model

    def format_token(self, n: int) -> str:
        """Format token count with K/M suffix."""
        if n >= 1_000_000:
            s = f"{n / 1_000_000:.1f}M"
            return s.replace(".0M", "M")
        elif n >= 1_000:
            s = f"{n / 1_000:.1f}K"
            return s.replace(".0K", "K")
        return str(n)

    def build_footer(
        self,
        model: str,
        elapsed: float,
        in_t: int,
        out_t: int,
        cache_t: int,
        ctx_used: int,
        ctx_limit: int,
    ) -> str:
        """Build the standard Hermes footer string.

        Format: Xs/XmXs  ·  Xin↑ / Xout↓  [·  缓存 X.XK (x.x%)]  ·  上下文 X.XK / XXXK (x.x%)
        - 用时: Xs (<60s) or XmXs (≥60s)
        - Token: K/M unit + direction arrow
        - Cache: K unit + percentage (only shown when cache_t > 0)
        - Context: K/M unit + ctx_used / ctx_limit + percentage
        - Model name NOT in footer (already in header subtitle)
        """
        if elapsed >= 60:
            m = int(elapsed // 60)
            s = int(elapsed % 60)
            elapsed_str = f"{m}m{s}s"
        else:
            elapsed_str = f"{elapsed:.1f}s"

        ctx_pct = ctx_used / ctx_limit * 100 if ctx_limit else 0
        parts = [
            f"{elapsed_str}  ·  {self.format_token(in_t)}↑ / {self.format_token(out_t)}↓",
        ]
        if cache_t > 0:
            cache_pct = cache_t / in_t * 100 if in_t else 0
            parts.append(f"缓存 {self.format_token(cache_t)} ({cache_pct:.1f}%)")
        parts.append(
            f"上下文 {self.format_token(ctx_used)}/{self.format_token(ctx_limit)} ({ctx_pct:.1f}%)"
        )
        return "  ·  ".join(parts)

    def set_streaming_pending(self, chat_id: str) -> None:
        """Signal that a streaming card is about to be created for this chat.

        send_progress_messages waits for this to be cleared (card created) or
        timeout (fallback to normal messages). Prevents the race where
        send_progress_messages sends a normal message before the streaming
        card is ready.
        """
        if not hasattr(self, "_streaming_pending"):
            self._streaming_pending = {}
        self._streaming_pending[chat_id] = True

    def is_streaming_pending(self, chat_id: str) -> bool:
        """Check if a streaming card is pending creation for this chat."""
        return getattr(self, "_streaming_pending", {}).get(chat_id, False)

    def clear_streaming_pending(self, chat_id: str) -> None:
        """Clear the streaming pending flag after card is created."""
        if hasattr(self, "_streaming_pending"):
            self._streaming_pending.pop(chat_id, None)

    def clear_streaming_card(self, chat_id: str) -> None:
        """Remove the streaming card state for a chat (does not close the card)."""
        self._streaming_card.pop(chat_id, None)
        self.clear_streaming_pending(chat_id)
        self._pending_greeting = "主人，苏菲为您服务！"
        self._pending_subtitle = ""

    def _update_card_element(
        self, card_id: str, element_id: str, content: str, sequence: int, token: str
    ) -> tuple:
        """Update a card element's content via CardKit API.

        Returns (success: bool, next_sequence: int).
        next_sequence is -1 if failed.
        """
        try:
            fresh_token = self._get_tenant_access_token() or token
            payload = json.dumps({"content": content}).encode()
            url = (
                f"https://open.feishu.cn/open-apis/cardkit/v1/cards/"
                f"{card_id}/elements/{element_id}/content"
            )
            import urllib.request
            req = urllib.request.Request(
                url, data=payload,
                headers={
                    "Authorization": f"Bearer {fresh_token}",
                    "Content-Type": "application/json",
                },
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                code = data.get("code", -1)
                if code == 0:
                    # CardKit returns the updated sequence in the response
                    next_seq = data.get("data", {}).get("sequence", sequence + 1)
                    logger.info(
                        "[Feishu] _update_card_element element=%s ok=True code=0",
                        element_id,
                    )
                    return True, next_seq
                logger.warning(
                    "[Feishu] _update_card_element element=%s ok=False code=%s msg=%s",
                    element_id, code, data.get("msg", ""),
                )
                return False, -1
        except Exception as e:
            logger.warning("[Feishu] _update_card_element element=%s error=%s", element_id, e)
        return False, -1

    def _update_card_subtitle(self, card_id: str, subtitle_text: str, token: str) -> bool:
        """Update only the header subtitle (title stays unchanged)."""
        try:
            import urllib.request
            fresh_token = self._get_tenant_access_token() or token
            payload = json.dumps({
                "subtitle": {"content": subtitle_text, "tag": "plain_text"},
            }).encode()
            url = f"https://open.feishu.cn/open-apis/cardkit/v1/cards/{card_id}/header"
            req = urllib.request.Request(
                url, data=payload,
                headers={
                    "Authorization": f"Bearer {fresh_token}",
                    "Content-Type": "application/json",
                },
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read()).get("code") == 0
        except Exception as e:
            logger.debug("[Feishu] _update_card_subtitle failed: %s", e)
        return False

    def _build_streaming_card(self, greeting: str, subtitle: str) -> dict:
        """Vertical layout streaming card. All element_ids are at body root level."""
        return {
            "schema": "2.0",
            "config": {
                "streaming_mode": True,
                "update_multi": True,
                "summary": {"content": "苏菲为您服务中..."},
                "streaming_config": {
                    "print_frequency_ms": {"default": 60, "android": 60, "ios": 60, "pc": 60},
                    "print_step": {"default": 2, "android": 2, "ios": 2, "pc": 2},
                    "print_strategy": "fast",
                },
            },
            "header": {
                "template": "indigo",
                "title": {"content": greeting, "tag": "plain_text"},
                "subtitle": {"content": f"{subtitle}  🤔思考中", "tag": "plain_text"},
            },
            "body": {
                "direction": "vertical",
                "padding": "10px 16px 10px 16px",
                "vertical_spacing": "6px",
                "elements": [
                    # ① thinking_content — AI thinking / result display
                    {
                        "tag": "markdown",
                        "element_id": "thinking_content",
                        "content": "⏳ 执行中，等待结果...",
                        "text_size": "normal",
                        "text_align": "left",
                        "margin": "0px 0px 6px 0px",
                    },
                    # ② status_label — 🤔/⚡/✅ status
                    {
                        "tag": "markdown",
                        "element_id": "status_label",
                        "content": "🤔思考中",
                        "text_size": "small",
                        "text_color": "grey",
                        "margin": "0px 0px 2px 0px",
                    },
                    # ③ tools_label — tool call count
                    {
                        "tag": "markdown",
                        "element_id": "tools_label",
                        "content": "🔧 工具调用 (0次)",
                        "text_size": "small",
                        "text_color": "grey",
                        "margin": "0px 0px 2px 0px",
                    },
                    # ④ tools_body — tool call log
                    {
                        "tag": "markdown",
                        "element_id": "tools_body",
                        "content": "⏳ 等待开始...",
                        "text_size": "x-small",
                        "text_color": "grey",
                        "margin": "0px 0px 6px 0px",
                    },
                    # ⑤ footer — token stats
                    {
                        "tag": "markdown",
                        "element_id": "footer",
                        "content": "⏳ 执行中...",
                        "text_size": "x-small",
                        "text_align": "left",
                        "margin": "0px 0px 0px 0px",
                    },
                ],
            },
        }

    async def send_streaming_card(
        self,
        chat_id: str,
        greeting: str,
        subtitle: str,
        metadata: dict | None,
    ) -> dict | None:
        """Create and send a Feishu streaming card for the given chat.

        Returns the card state dict on success, None on failure.
        """
        try:
            card_payload = self._build_streaming_card(greeting, subtitle)
            token = self._get_tenant_access_token() or ""
            create_url = "https://open.feishu.cn/open-apis/cardkit/v1/cards"
            import urllib.request
            req = urllib.request.Request(
                create_url,
                data=json.dumps(card_payload).encode(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                create_data = json.loads(resp.read())
                if create_data.get("code") != 0:
                    logger.warning("[Feishu] CardKit create failed: %s", create_data)
                    return None
                card_id = create_data["data"]["card"]["card_id"]

            # Send the card as a Feishu interactive message
            send_url = (
                "https://open.feishu.cn/open-apis/im/v1/messages"
                "?receive_id_type=chat_id"
            )
            msg_payload = json.dumps({
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card_payload),
            }).encode()
            send_req = urllib.request.Request(
                send_url,
                data=msg_payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(send_req, timeout=10) as send_resp:
                send_data = json.loads(send_resp.read())
                if send_data.get("code") != 0:
                    logger.warning("[Feishu] Card send failed: %s", send_data)
                    return None
                message_id = send_data["data"]["message_id"]

            initial_sequence = create_data["data"]["card"].get("sequence", 1)
            logger.info(
                "[Feishu] send_streaming_card created: card_id=%s message_id=%s seq=%s",
                card_id, message_id, initial_sequence,
            )
            return {
                "card_id": card_id,
                "message_id": message_id,
                "sequence": initial_sequence,
                "tenant_token": token,
            }
        except Exception as e:
            logger.error("[Feishu] send_streaming_card error: %s", e, exc_info=True)
            return None

    async def finalize_streaming_card(
        self,
        chat_id: str,
        model: str,
        elapsed: float,
        in_t: int,
        out_t: int,
        cache_t: int,
        ctx_used: int,
        ctx_limit: int,
        result_summary: str = "",
    ) -> None:
        """Update the streaming card to 'completed' state.

        All card updates are serialized via per-chat Lock (caller holds the lock).
        """
        state = self._streaming_card.get(chat_id)
        if not state:
            return

        greeting = state.get("_greeting", "主人，苏菲为您服务！")
        tool_count = state.get("_tool_count", 0)
        tool_lines = state.get("_tool_lines", [])
        model = state.get("_model", model)
        loop = asyncio.get_event_loop()

        # ── ① Update status_label → "✅已完成" ───────────────────────
        _sub_ok, _next_seq = await loop.run_in_executor(
            None, lambda: self._update_card_element(
                state["card_id"], "status_label",
                "✅已完成",
                state["sequence"], state["tenant_token"],
            )
        )
        logger.info("[Feishu] finalize: status_label → ✅已完成 ok=%s", _sub_ok)
        if _sub_ok:
            state["sequence"] = _next_seq

        # ── ② Update thinking_content with result summary ──────────────
        summary = result_summary if result_summary else "主人，任务已完成！"
        # Strip XML thinking tags and Agent footer that may be in the text
        summary = re.sub(r"<think>.*?</think>", "", summary, flags=re.DOTALL)
        summary = re.sub(r"<think>.*", "", summary, flags=re.DOTALL)
        summary = re.sub(r"(\n)?\s*Agent\s*·.*", "", summary)
        summary = summary.strip()
        _ok, next_seq = await loop.run_in_executor(
            None, lambda: self._update_card_element(
                state["card_id"], "thinking_content",
                summary[:800] if summary else "主人，任务已完成！",
                state["sequence"], state["tenant_token"],
            )
        )
        if _ok:
            state["sequence"] = next_seq

        # ── ③ Update tools_label to show completion ──────────────────
        _ok, _next_seq = await loop.run_in_executor(
            None, lambda: self._update_card_element(
                state["card_id"], "tools_label",
                f"🔧 工具调用 ({tool_count}次)  ✅完成",
                state["sequence"], state["tenant_token"],
            )
        )
        if _ok:
            state["sequence"] = _next_seq

        # ── ④ Update footer with token stats ─────────────────────────
        footer = self.build_footer(
            model, elapsed, in_t, out_t, cache_t, ctx_used, ctx_limit
        )
        _ok, _next_seq = await loop.run_in_executor(
            None, lambda: self._update_card_element(
                state["card_id"], "footer", footer,
                state["sequence"], state["tenant_token"],
            )
        )
        if _ok:
            state["sequence"] = _next_seq

        # ── Mark finalized ────────────────────────────────────────────
        state["finalized"] = True
        state["finalize_ts"] = time.time()

        if not hasattr(self, "_finalized_chats"):
            self._finalized_chats = {}
        self._finalized_chats[chat_id] = {
            "ts": time.time(),
            "card_id": state["card_id"],
            "message_id": state["message_id"],
            "tenant_token": state["tenant_token"],
            "sequence": state["sequence"],
        }
'''


# ─────────────────────────────────────────────────────────────────
# Code to inject at START of send() method — streaming routing
# (before the "try" block, replacing the early-return check)
# ─────────────────────────────────────────────────────────────────

SEND_STREAMING_PRELUDE = '''
        # ════════════════════════════════════════════════════════════════════
        # Feishu Streaming Card — routing
        # ALL card updates are serialized via per-chat Lock to prevent
        # sequence race conditions (code=300317) between concurrent calls.
        # ════════════════════════════════════════════════════════════════════

        # Emoji detection: first char of first line is emoji → tool progress
        try:
            import regex as _regex
            _EMOJI_RE = _regex.compile(r"^[\p{Emoji_Pressentation}\p{Extended_Pictographic}]")
            _first_line = content.split("\\n")[0].strip() if content else ""
            _match = _EMOJI_RE.match(_first_line) if _first_line else None
            is_tool_progress = bool(_match and len(_first_line) < 200)
        except Exception:
            is_tool_progress = False

        _has_card = chat_id in self._streaming_card

        async with self._get_card_lock(chat_id):
            # Re-check after acquiring lock
            _has_card = chat_id in self._streaming_card

            # ── ① Has card + finalized → grace-period result write ───────
            if _has_card:
                state = self._streaming_card[chat_id]
                if state.get("finalized"):
                    import time as _time
                    finalized_info = getattr(self, "_finalized_chats", {}).get(chat_id, {})
                    grace_start = finalized_info.get("ts") or state.get("finalize_ts", 0)
                    if _time.time() - grace_start < 60:
                        loop = asyncio.get_event_loop()
                        _ok, _next_seq = await loop.run_in_executor(
                            None, lambda: self._update_card_element(
                                state["card_id"], "thinking_content",
                                content[:800],
                                state["sequence"], state["tenant_token"],
                            )
                        )
                        if _ok:
                            state["sequence"] = _next_seq
                            self._streaming_card.pop(chat_id, None)
                            self._finalized_chats.pop(chat_id, None)
                    else:
                        self._streaming_card.pop(chat_id, None)
                        self._finalized_chats.pop(chat_id, None)
                    return SendResult(success=True, message_id=state["message_id"], card_id=state["card_id"])

            # ── ② Has card + non-emoji → update thinking_content ──────────
            # (overwrite mode — write latest chunk, do NOT accumulate)
            if _has_card and not is_tool_progress:
                state = self._streaming_card[chat_id]
                if not state.get("finalized"):
                    _clean = content
                    _clean = re.sub(r"<think>", "", _clean)
                    _clean = re.sub(r"</think>", "", _clean)
                    _clean = re.sub(r"(\\n)?\\s*Agent\\s*·.*", "", _clean)
                    _clean = _clean.strip()
                    if _clean:
                        loop = asyncio.get_event_loop()
                        _ok, _next_seq = await loop.run_in_executor(
                            None, lambda: self._update_card_element(
                                state["card_id"], "thinking_content",
                                _clean[:2000],
                                state["sequence"], state["tenant_token"],
                            )
                        )
                        if _ok:
                            state["sequence"] = _next_seq
                return SendResult(success=True, message_id=state["message_id"], card_id=state["card_id"])

            # ── ③ First emoji tool → create streaming card ────────────────
            if is_tool_progress and not _has_card:
                self._streaming_card.pop(chat_id, None)
                self.set_streaming_pending(chat_id)

                greeting = (
                    metadata.get("greeting")
                    if metadata and metadata.get("greeting")
                    else getattr(self, "_pending_greeting", "主人，苏菲为您服务！")
                )
                model = (
                    metadata.get("model")
                    if metadata and metadata.get("model")
                    else getattr(self, "_pending_subtitle", "")
                )

                logger.info("[Feishu] Creating streaming card greeting=%r model=%r", greeting, model)
                state = await self.send_streaming_card(
                    chat_id=chat_id,
                    greeting=greeting,
                    subtitle=model,
                    metadata=metadata,
                )
                if state:
                    state["_tool_count"] = 1
                    state["_tool_lines"] = [_first_line]
                    state["_model"] = model
                    state["_greeting"] = greeting
                    self._streaming_card[chat_id] = state
                    self.clear_streaming_pending(chat_id)

                    loop = asyncio.get_event_loop()

                    _label_ok, _next_seq = await loop.run_in_executor(
                        None, lambda: self._update_card_element(
                            state["card_id"], "status_label",
                            "⚡执行中",
                            state["sequence"], state["tenant_token"],
                        )
                    )
                    if _label_ok:
                        state["sequence"] = _next_seq
                    logger.info(
                        "[Feishu] status_label → ⚡执行中 ok=%s seq=%s",
                        _label_ok, state["sequence"],
                    )

                    _ok, _next_seq = await loop.run_in_executor(
                        None, lambda: self._update_card_element(
                            state["card_id"], "tools_body",
                            f"⚙️ `{_first_line}`",
                            state["sequence"], state["tenant_token"],
                        )
                    )
                    if _ok:
                        state["sequence"] = _next_seq
                    return SendResult(success=True, message_id=state["message_id"], card_id=state["card_id"])

                logger.warning("[Feishu] Streaming card creation failed, sending as normal message")

            # ── ④ Has card + emoji → tool progress update ────────────────
            if _has_card and is_tool_progress:
                state = self._streaming_card[chat_id]
                tool_count = state.get("_tool_count", 0) + 1
                tool_lines = state.get("_tool_lines", [])
                # Deduplicate: skip if same as last tool line
                if not tool_lines or tool_lines[-1] != _first_line:
                    tool_lines.append(_first_line)
                else:
                    tool_count = state["_tool_count"]
                state["_tool_count"] = tool_count
                state["_tool_lines"] = tool_lines
                loop = asyncio.get_event_loop()

                _ok, _next_seq = await loop.run_in_executor(
                    None, lambda: self._update_card_element(
                        state["card_id"], "tools_label",
                        f"🔧 工具调用 ({tool_count}次)",
                        state["sequence"], state["tenant_token"],
                    )
                )
                if _ok:
                    state["sequence"] = _next_seq

                display_lines, seen = [], set()
                for line in tool_lines:
                    if line not in seen:
                        display_lines.append(line)
                        seen.add(line)
                display_lines = display_lines[-8:]
                tool_text = "\\n".join([f"⚙️ `{l}`" for l in display_lines])
                _ok, _next_seq = await loop.run_in_executor(
                    None, lambda: self._update_card_element(
                        state["card_id"], "tools_body", tool_text,
                        state["sequence"], state["tenant_token"],
                    )
                )
                if _ok:
                    state["sequence"] = _next_seq

                return SendResult(success=True, message_id=state["message_id"], card_id=state["card_id"])

        # ── Normal message send (no streaming card for this chat) ─────────
'''


# ─────────────────────────────────────────────────────────────────
# Code to inject: edit_message streaming card routing
# ─────────────────────────────────────────────────────────────────

EDIT_MESSAGE_STREAMING_ROUTING = '''
        # If streaming card is active for this chat, route ALL content to the card
        # (not just emoji tool progress). This ensures thinking content from
        # stream_consumer._send_or_edit() also updates the card.
        if chat_id in self._streaming_card:
            state = self._streaming_card[chat_id]
            if not state.get("finalized"):
                return await self.send(chat_id, content, reply_to=None, metadata=None)

        # Normal Feishu message edit (no streaming card for this chat)
'''


# ─────────────────────────────────────────────────────────────────
# The actual patch function
# ─────────────────────────────────────────────────────────────────

def apply_patch(feishu_py_path: str, hermes_dir: str) -> list:
    """
    Apply all streaming card patches to feishu.py.

    Returns list of (status, message) tuples.
    """
    results = []

    with open(feishu_py_path) as f:
        original = f.read()

    patched = original
    changes = []

    # ── 1. Inject streaming state into __init__ ──────────────────────
    # Find the last "# ──" block separator before the first "async def" or "def" in __init__
    # We look for "self._initialized = True" or similar init completion marker.
    init_marker = "self._streaming_card"
    if init_marker not in patched:
        # Find where to inject: after the last attribute assignment in __init__
        # Strategy: find the last "self.xxx = " line before the first "async def send"
        import re as _re

        # Find the __init__ method bounds
        class_match = _re.search(r"class \w+Adapter.*?:", patched)
        class_start = class_match.start() if class_match else 0

        first_send_match = _re.search(r"\n    async def send\(", patched)
        first_send_start = first_send_match.start() if first_send_match else 0

        # Find the end of __init__ — last assignment before first_send
        init_section = patched[class_start:first_send_start]
        # Find last "self." assignment line
        assignments = list(_re.finditer(r"^\s+self\.[^=]+=", init_section, _re.MULTILINE))
        if assignments:
            last_assign_end = assignments[-1].end()
            inject_pos = class_start + last_assign_end
            # Find the newline after this assignment
            nl_match = _re.search(r"\n", patched[inject_pos:])
            if nl_match:
                inject_pos += nl_match.end()
            patched = patched[:inject_pos] + STREAMING_STATE_INIT + patched[inject_pos:]
            changes.append("  ✓ Added streaming card state to __init__")
        else:
            results.append(("FAIL", "Could not find __init__ injection point"))
            return results
    else:
        changes.append("  ℹ Already has streaming state (skip)")

    # ── 2. Inject streaming methods before send() ───────────────────
    send_marker = "\n    async def send("
    if "def _get_card_lock" not in patched:
        inj = patched.find(send_marker)
        if inj != -1:
            patched = patched[:inj] + STREAMING_METHODS + "\n" + patched[inj:]
            changes.append("  ✓ Injected streaming card methods")
        else:
            results.append(("FAIL", "Could not find send() insertion point"))
            return results
    else:
        changes.append("  ℹ Already has streaming methods (skip)")

    # ── 3. Patch send() — add streaming routing at start ────────────
    if "SEND_STREAMING_PRELUDE" not in patched and "Feishu Streaming Card — routing" not in patched:
        # Find the start of send() method body — the "try:" line
        import re as _re
        send_try_match = _re.search(
            r"(\n    async def send\(.*?\):)\s*\n        try:",
            patched,
            _re.DOTALL,
        )
        if send_try_match:
            # Replace the "try:" with our streaming routing + "try:"
            old = send_try_match.group(0)
            new = send_try_match.group(1) + "\n" + SEND_STREAMING_PRELUDE.strip() + "\n        try:"
            patched = patched.replace(old, new, 1)
            changes.append("  ✓ Patched send() with streaming routing")
        else:
            results.append(("FAIL", "Could not find send() try block"))
            return results
    else:
        changes.append("  ℹ send() already patched (skip)")

    # ── 4. Patch edit_message — add streaming card routing ───────────
    if "If streaming card is active for this chat" not in patched:
        import re as _re
        edit_method_match = _re.search(
            r"(\n    async def edit_message\(\n        self,\n        chat_id: str,\n        message_id: str,\n        content: str,\n    \) -> SendResult:)",
            patched,
        )
        if edit_method_match:
            old = edit_method_match.group(1) + "\n        \"\"\""
            new = edit_method_match.group(1) + EDIT_MESSAGE_STREAMING_ROUTING + '        """'
            patched = patched.replace(old, new, 1)
            changes.append("  ✓ Patched edit_message() with streaming routing")
        else:
            # Try a simpler pattern
            edit_marker = "\n    async def edit_message("
            em_pos = patched.find(edit_marker)
            if em_pos != -1:
                # Find the docstring end
                ds_start = patched.find('"""', em_pos + len(edit_marker))
                if ds_start != -1:
                    ds_end = patched.find('"""', ds_start + 3)
                    if ds_end != -1:
                        inj = ds_end + 3
                        patched = patched[:inj] + "\n" + EDIT_MESSAGE_STREAMING_ROUTING + patched[inj:]
                        changes.append("  ✓ Patched edit_message() with streaming routing")
    else:
        changes.append("  ℹ edit_message() already patched (skip)")

    # ── Write patched file ───────────────────────────────────────────
    backup_path = feishu_py_path + ".bak"
    with open(backup_path, "w") as f:
        f.write(original)

    with open(feishu_py_path, "w") as f:
        f.write(patched)

    results.append(("OK", f"Patched {feishu_py_path}"))
    for c in changes:
        results.append(("OK", c))
    results.append(("OK", f"  Backup: {backup_path}"))

    return results
