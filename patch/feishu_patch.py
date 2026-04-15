"""
feishu_patch.py
===============
Injects (or verifies) the Feishu streaming card feature into
hermes-agent/gateway/platforms/feishu.py.

Version-aware: works on both Hermes versions.
If streaming card is already installed, verifies and skips.
Otherwise, injects into the correct locations based on detected code structure.

What it patches:
  1. __init__: adds _streaming_card, _streaming_card_locks, _streaming_pending
  2. Adds methods: _get_card_lock, _get_tenant_access_token, _build_streaming_card,
                   send_streaming_card, _update_card_element, set_streaming_greeting,
                   set_streaming_pending, is_streaming_pending, clear_streaming_pending,
                   clear_streaming_card
  3. Modifies send(): prepends the 4-branch streaming card routing at the start
  4. Modifies edit_message(): routes ALL content to streaming card

Config (from config.yaml):
  feishu_streaming_card.greeting         — card header title
  feishu_streaming_card.enabled           — enable/disable
  feishu_streaming_card.pending_timeout   — send_progress timeout
"""

from __future__ import annotations

import re

logger = __import__("logging").getLogger(__name__)


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
# Code to inject: streaming card state attrs in __init__
# Injected after the last "self._xxx =" assignment in __init__
# ─────────────────────────────────────────────────────────────────

STREAMING_STATE_INIT = '''
        # ── Feishu Streaming Card state ────────────────────────────────────
        # chat_id → {"card_id", "message_id", "sequence", "tenant_token", ...}
        self._streaming_card: dict = {}
        # Per-chat lock to serialize card updates (prevents code=300317 race)
        self._streaming_card_locks: dict = {}
        # Pending flag: signals send_progress_messages to wait for card creation
        self._streaming_pending: dict = {}
'''

# ─────────────────────────────────────────────────────────────────
# Code to inject: streaming card methods
# Injected after the __init__ closing, before the next method
# ─────────────────────────────────────────────────────────────────

STREAMING_METHODS = '''
    # ══════════════════════════════════════════════════════════════════════
    # Feishu Streaming Card (CardKit v1) — typewriter card per chat
    # ══════════════════════════════════════════════════════════════════════

    def _get_card_lock(self, chat_id: str):
        """Get or create a per-chat asyncio.Lock for serializing card updates."""
        if chat_id not in self._streaming_card_locks:
            self._streaming_card_locks[chat_id] = asyncio.Lock()
        return self._streaming_card_locks[chat_id]

    def _get_tenant_access_token(self) -> str | None:
        """Get a fresh tenant_access_token via lark-cli."""
        try:
            import subprocess as _subprocess
            from pathlib import Path
            _lark_cli = Path.home() / ".npm-global" / "bin" / "lark-cli"
            if not _lark_cli.exists():
                _lark_cli = Path("/usr/local/bin/lark-cli")
            _r = _subprocess.run(
                [_lark_cli, "api", "POST",
                 "/open-apis/auth/v3/tenant_access_token/internal",
                 "--data", __import__("json").dumps({
                     "app_id": self._app_id,
                     "app_secret": self._app_secret,
                 })],
                capture_output=True, text=True, timeout=10,
            )
            if _r.returncode == 0:
                _data = __import__("json").loads(_r.stdout.strip())
                return _data.get("tenant_access_token")
        except Exception as _e:
            __import__("logging").getLogger("feishu").debug(
                "[Feishu] Failed to get tenant token: %s", _e)
        return None

    def _build_streaming_card(self, greeting: str, subtitle: str) -> dict:
        """Vertical layout streaming card. All element_ids are at body root level."""
        return {
            "schema": "2.0",
            "config": {
                "streaming_mode": True,
                "update_multi": True,
                "summary": {"content": "处理中..."},
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
                    {"tag": "markdown", "element_id": "thinking_content",
                     "content": "⏳ 执行中，等待结果...",
                     "text_size": "normal", "text_align": "left",
                     "margin": "0px 0px 6px 0px"},
                    {"tag": "markdown", "element_id": "status_label",
                     "content": "🤔思考中", "text_size": "small",
                     "text_color": "grey", "margin": "0px 0px 2px 0px"},
                    {"tag": "markdown", "element_id": "tools_label",
                     "content": "🔧 工具调用 (0次)", "text_size": "small",
                     "text_color": "grey", "margin": "0px 0px 2px 0px"},
                    {"tag": "markdown", "element_id": "tools_body",
                     "content": "⏳ 等待开始...", "text_size": "x-small",
                     "text_color": "grey", "margin": "0px 0px 6px 0px"},
                    {"tag": "markdown", "element_id": "footer",
                     "content": "⏳ 执行中...", "text_size": "x-small",
                     "text_align": "left", "margin": "0px 0px 0px 0px"},
                ],
            },
        }

    async def send_streaming_card(
        self, chat_id: str, greeting: str, subtitle: str, metadata: dict | None = None
    ) -> dict | None:
        """Create and send a Feishu streaming card. Returns card state on success."""
        try:
            import urllib.request
            import urllib.error
            card_payload = self._build_streaming_card(greeting, subtitle)
            token = self._get_tenant_access_token() or ""
            create_url = "https://open.feishu.cn/open-apis/cardkit/v1/cards"
            req = urllib.request.Request(
                create_url,
                data=__import__("json").dumps(card_payload).encode(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                create_data = __import__("json").loads(resp.read())
                if create_data.get("code") != 0:
                    __import__("logging").getLogger("feishu").warning(
                        "[Feishu] CardKit create failed: %s", create_data)
                    return None
                card_id = create_data["data"]["card"]["card_id"]

            send_url = (
                "https://open.feishu.cn/open-apis/im/v1/messages"
                "?receive_id_type=chat_id"
            )
            msg_payload = __import__("json").dumps({
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": __import__("json").dumps(card_payload),
            }).encode()
            send_req = urllib.request.Request(
                send_url, data=msg_payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(send_req, timeout=10) as send_resp:
                send_data = __import__("json").loads(send_resp.read())
                if send_data.get("code") != 0:
                    __import__("logging").getLogger("feishu").warning(
                        "[Feishu] Card send failed: %s", send_data)
                    return None
                message_id = send_data["data"]["message_id"]

            initial_sequence = create_data["data"]["card"].get("sequence", 1)
            __import__("logging").getLogger("feishu").info(
                "[Feishu] send_streaming_card: card_id=%s message_id=%s seq=%s",
                card_id, message_id, initial_sequence)
            return {
                "card_id": card_id,
                "message_id": message_id,
                "sequence": initial_sequence,
                "tenant_token": token,
            }
        except Exception as e:
            __import__("logging").getLogger("feishu").error(
                "[Feishu] send_streaming_card error: %s", e, exc_info=True)
            return None

    def _update_card_element(
        self, card_id: str, element_id: str, content: str,
        sequence: int, token: str,
    ) -> tuple:
        """Update a card element via CardKit API. Returns (success, next_sequence)."""
        try:
            import urllib.request
            import urllib.error
            fresh_token = self._get_tenant_access_token() or token
            payload = __import__("json").dumps({"content": content}).encode()
            url = (
                f"https://open.feishu.cn/open-apis/cardkit/v1/cards/"
                f"{card_id}/elements/{element_id}/content"
            )
            req = urllib.request.Request(
                url, data=payload,
                headers={
                    "Authorization": f"Bearer {fresh_token}",
                    "Content-Type": "application/json",
                },
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = __import__("json").loads(resp.read())
                code = data.get("code", -1)
                if code == 0:
                    next_seq = data.get("data", {}).get("sequence", sequence + 1)
                    return True, next_seq
                __import__("logging").getLogger("feishu").warning(
                    "[Feishu] _update_card_element element=%s ok=False code=%s msg=%s",
                    element_id, code, data.get("msg", ""))
                return False, -1
        except Exception as e:
            __import__("logging").getLogger("feishu").warning(
                "[Feishu] _update_card_element element=%s error=%s", element_id, e)
        return False, -1

    def set_streaming_greeting(self, greeting: str) -> None:
        """Set the greeting to display in the streaming card header."""
        self._pending_greeting = greeting

    def set_streaming_model(self, model: str) -> None:
        """Set the model name to display in the streaming card subtitle."""
        self._pending_subtitle = model

    def set_streaming_pending(self, chat_id: str) -> None:
        if not hasattr(self, "_streaming_pending"):
            self._streaming_pending = {}
        self._streaming_pending[chat_id] = True

    def is_streaming_pending(self, chat_id: str) -> bool:
        return getattr(self, "_streaming_pending", {}).get(chat_id, False)

    def clear_streaming_pending(self, chat_id: str) -> None:
        if hasattr(self, "_streaming_pending"):
            self._streaming_pending.pop(chat_id, None)

    def clear_streaming_card(self, chat_id: str) -> None:
        self._streaming_card.pop(chat_id, None)
        self.clear_streaming_pending(chat_id)

    def format_token(self, n: int) -> str:
        if n >= 1_000_000:
            s = f"{n / 1_000_000:.1f}M"
            return s.replace(".0M", "M")
        elif n >= 1_000:
            s = f"{n / 1_000:.1f}K"
            return s.replace(".0K", "K")
        return str(n)

    def _build_footer(
        self, model: str, elapsed: float,
        in_t: int, out_t: int, cache_t: int,
        ctx_used: int, ctx_limit: int,
    ) -> str:
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
            f"上下文 {self.format_token(ctx_used)}/{self.format_token(ctx_limit)}"
            f" ({ctx_pct:.1f}%)"
        )
        return "  ·  ".join(parts)

    async def finalize_streaming_card(
        self, chat_id: str, model: str, elapsed: float,
        in_t: int, out_t: int, cache_t: int,
        ctx_used: int, ctx_limit: int,
        result_summary: str = "",
    ) -> None:
        """Update streaming card to 'completed' state. Caller holds the card lock."""
        state = self._streaming_card.get(chat_id)
        if not state:
            return
        greeting = state.get("_greeting", "主人，苏菲为您服务！")
        tool_count = state.get("_tool_count", 0)
        model = state.get("_model", model)
        loop = asyncio.get_event_loop()

        # ① status_label → "✅已完成"
        _ok, _next_seq = await loop.run_in_executor(
            None, lambda: self._update_card_element(
                state["card_id"], "status_label", "✅已完成",
                state["sequence"], state["tenant_token"]))
        if _ok:
            state["sequence"] = _next_seq

        # ② thinking_content → result_summary (strip XML tags + Agent footer)
        summary = result_summary if result_summary else "主人，任务已完成！"
        summary = re.sub(r"<think>.*?</think>", "", summary, flags=re.DOTALL)
        summary = re.sub(r"<think>.*", "", summary, flags=re.DOTALL)
        summary = re.sub(r"(\n)?\s*Agent\s*·.*", "", summary)
        summary = summary.strip()
        _ok, next_seq = await loop.run_in_executor(
            None, lambda: self._update_card_element(
                state["card_id"], "thinking_content",
                summary[:800] if summary else "主人，任务已完成！",
                state["sequence"], state["tenant_token"]))
        if _ok:
            state["sequence"] = next_seq

        # ③ tools_label → completion
        _ok, _next_seq = await loop.run_in_executor(
            None, lambda: self._update_card_element(
                state["card_id"], "tools_label",
                f"🔧 工具调用 ({tool_count}次)  ✅完成",
                state["sequence"], state["tenant_token"]))
        if _ok:
            state["sequence"] = _next_seq

        # ④ footer → token stats
        footer = self._build_footer(model, elapsed, in_t, out_t, cache_t, ctx_used, ctx_limit)
        _ok, _next_seq = await loop.run_in_executor(
            None, lambda: self._update_card_element(
                state["card_id"], "footer", footer,
                state["sequence"], state["tenant_token"]))
        if _ok:
            state["sequence"] = _next_seq

        state["finalized"] = True
        state["finalize_ts"] = __import__("time").time()
        if not hasattr(self, "_finalized_chats"):
            self._finalized_chats = {}
        self._finalized_chats[chat_id] = {
            "ts": state["finalize_ts"],
            "card_id": state["card_id"],
            "message_id": state["message_id"],
            "tenant_token": state["tenant_token"],
            "sequence": state["sequence"],
        }
'''


# ─────────────────────────────────────────────────────────────────
# Streaming routing code to prepend at the start of send()
# This is version-aware and adapts to the Hermes structure
# ─────────────────────────────────────────────────────────────────

# The routing code to inject at the START of send() (after the docstring)
# It wraps the entire original send() body in a conditional
SEND_STREAMING_PRELUDE = '''
        # ════════════════════════════════════════════════════════════════════
        # Feishu Streaming Card routing (4 branches, per-chat Lock serialized)
        # ════════════════════════════════════════════════════════════════════

        # Emoji detection: first char of first line is emoji → tool progress
        try:
            import regex as _regex
            _EMOJI_RE = _regex.compile(r"^[\\p{Emoji_Presentation}\\p{Extended_Pictographic}]")
            _first_line = content.split("\\n")[0].strip() if content else ""
            _match = _EMOJI_RE.match(_first_line) if _first_line else None
            is_tool_progress = bool(_match and len(_first_line) < 200)
        except Exception:
            is_tool_progress = False

        _has_card = chat_id in self._streaming_card

        async with self._get_card_lock(chat_id):
            _has_card = chat_id in self._streaming_card

            # ── ① Has card + finalized → grace-period result write ─────────
            if _has_card:
                _state = self._streaming_card[chat_id]
                if _state.get("finalized"):
                    import time as _time
                    _finfo = getattr(self, "_finalized_chats", {}).get(chat_id, {})
                    _grace_start = _finfo.get("ts") or _state.get("finalize_ts", 0)
                    if _time.time() - _grace_start < 60:
                        _loop = asyncio.get_event_loop()
                        _ok, _ns = await _loop.run_in_executor(
                            None, lambda: self._update_card_element(
                                _state["card_id"], "thinking_content",
                                content[:800], _state["sequence"], _state["tenant_token"]))
                        if _ok:
                            _state["sequence"] = _ns
                            self._streaming_card.pop(chat_id, None)
                            self._finalized_chats.pop(chat_id, None)
                    else:
                        self._streaming_card.pop(chat_id, None)
                        self._finalized_chats.pop(chat_id, None)
                    return SendResult(success=True, message_id=_state["message_id"], card_id=_state["card_id"])

            # ── ② Has card + non-emoji → update thinking_content (overwrite) ──
            if _has_card and not is_tool_progress:
                _st = self._streaming_card[chat_id]
                if not _st.get("finalized"):
                    _clean = content
                    _clean = re.sub(r"<think>", "", _clean)
                    _clean = re.sub(r"</think>", "", _clean)
                    _clean = re.sub(r"(\\n)?\\s*Agent\\s*·.*", "", _clean)
                    _clean = _clean.strip()
                    if _clean:
                        _loop = asyncio.get_event_loop()
                        _ok, _ns = await _loop.run_in_executor(
                            None, lambda: self._update_card_element(
                                _st["card_id"], "thinking_content",
                                _clean[:2000], _st["sequence"], _st["tenant_token"]))
                        if _ok:
                            _st["sequence"] = _ns
                return SendResult(success=True, message_id=_st["message_id"], card_id=_st["card_id"])

            # ── ③ First emoji tool → create streaming card ───────────────────
            if is_tool_progress and not _has_card:
                self._streaming_card.pop(chat_id, None)
                self.set_streaming_pending(chat_id)

                _greeting = (metadata.get("greeting") if metadata and metadata.get("greeting")
                             else getattr(self, "_pending_greeting", _get_default_greeting()))
                _model_name = (metadata.get("model") if metadata and metadata.get("model")
                                else getattr(self, "_pending_subtitle", ""))

                _state = await self.send_streaming_card(
                    chat_id=chat_id, greeting=_greeting, subtitle=_model_name, metadata=metadata)
                if _state:
                    _state["_tool_count"] = 1
                    _state["_tool_lines"] = [_first_line]
                    _state["_model"] = _model_name
                    _state["_greeting"] = _greeting
                    self._streaming_card[chat_id] = _state
                    self.clear_streaming_pending(chat_id)
                    _loop = asyncio.get_event_loop()

                    _lok, _lns = await _loop.run_in_executor(
                        None, lambda: self._update_card_element(
                            _state["card_id"], "status_label", "⚡执行中",
                            _state["sequence"], _state["tenant_token"]))
                    if _lok:
                        _state["sequence"] = _lns

                    _ok, _ns = await _loop.run_in_executor(
                        None, lambda: self._update_card_element(
                            _state["card_id"], "tools_body", f"⚙️ `{_first_line}`",
                            _state["sequence"], _state["tenant_token"]))
                    if _ok:
                        _state["sequence"] = _ns
                    return SendResult(success=True, message_id=_state["message_id"], card_id=_state["card_id"])

                __import__("logging").getLogger("feishu").warning(
                    "[Feishu] Streaming card creation failed, sending as normal message")

            # ── ④ Has card + emoji → tool progress update ────────────────────
            if _has_card and is_tool_progress:
                _st = self._streaming_card[chat_id]
                _tc = _st.get("_tool_count", 0) + 1
                _tl = _st.get("_tool_lines", [])
                if not _tl or _tl[-1] != _first_line:
                    _tl.append(_first_line)
                else:
                    _tc = _st["_tool_count"]
                _st["_tool_count"] = _tc
                _st["_tool_lines"] = _tl
                _loop = asyncio.get_event_loop()

                _ok, _ns = await _loop.run_in_executor(
                    None, lambda: self._update_card_element(
                        _st["card_id"], "tools_label", f"🔧 工具调用 ({_tc}次)",
                        _st["sequence"], _st["tenant_token"]))
                if _ok:
                    _st["sequence"] = _ns

                _display, _seen = [], set()
                for _l in _tl:
                    if _l not in _seen:
                        _display.append(_l)
                        _seen.add(_l)
                _display = _display[-8:]
                _ok, _ns = await _loop.run_in_executor(
                    None, lambda: self._update_card_element(
                        _st["card_id"], "tools_body",
                        "\\n".join([f"⚙️ `{l}`" for l in _display]),
                        _st["sequence"], _st["tenant_token"]))
                if _ok:
                    _st["sequence"] = _ns
                return SendResult(success=True, message_id=_st["message_id"], card_id=_st["card_id"])

        # ── Normal send (no streaming card for this chat) ──────────────────
        # Falls through to the original send() body below.
        pass
'''


# ─────────────────────────────────────────────────────────────────
# Code to inject at the START of edit_message()
# ─────────────────────────────────────────────────────────────────

EDIT_MESSAGE_STREAMING_ROUTING = '''
        # If streaming card is active for this chat, route ALL content to the card
        if chat_id in self._streaming_card:
            _st = self._streaming_card[chat_id]
            if not _st.get("finalized"):
                return await self.send(chat_id, content, reply_to=None, metadata=None)
'''


# ─────────────────────────────────────────────────────────────────
# Detect Hermes version / streaming card installation status
# ─────────────────────────────────────────────────────────────────

def detect_hermes_version(feishu_py_path: str) -> dict:
    """Detect Hermes version and whether streaming card is already installed."""
    try:
        with open(feishu_py_path) as f:
            content = f.read()
    except Exception:
        return {"status": "error", "message": f"Cannot read {feishu_py_path}"}

    has_streaming = (
        "def send_streaming_card" in content
        and "_streaming_card" in content
        and "Feishu Streaming Card" in content
    )

    # Detect send() structure variants
    has_early_return = bool(re.search(
        r"async def send\([^)]+\)[^:]*:\s*\"\"\"[^\"]*\"\"\"\s*if not self\._client:",
        content, re.DOTALL))

    has_formatted_before_try = bool(re.search(
        r"async def send\([^)]+\)[^:]*:.*?formatted = self\.format_message",
        content, re.DOTALL))

    # Line count as additional signal
    line_count = content.count("\n")

    return {
        "status": "ok",
        "has_streaming_card": has_streaming,
        "has_early_return": has_early_return,
        "has_formatted_before_try": has_formatted_before_try,
        "line_count": line_count,
    }


# ─────────────────────────────────────────────────────────────────
# Apply patch to feishu.py
# Returns list of (status, message) tuples
# ─────────────────────────────────────────────────────────────────

def apply_patch(feishu_py_path: str, hermes_dir: str) -> list:
    """Apply streaming card patch to feishu.py. Version-aware."""
    results = []

    try:
        with open(feishu_py_path) as f:
            original = f.read()
    except Exception as e:
        return [("FAIL", f"Cannot read {feishu_py_path}: {e}")]

    patched = original
    changes = []

    # ── 0. Check if already installed ────────────────────────────────
    version_info = detect_hermes_version(feishu_py_path)
    if version_info.get("status") != "ok":
        results.append(("FAIL", version_info.get("message", "Unknown error")))
        return results

    if version_info["has_streaming_card"]:
        results.append(("OK", "Streaming card is already installed — skipping feishu.py patch"))
        return results

    # ── 1. Inject streaming state into __init__ ──────────────────────
    # Find the end of __init__ — last assignment + last method call
    # Pattern: after "self._load_seen_message_ids()" or the last "self._xxx =" in __init__
    init_end_pattern = r"(self\._load_seen_message_ids\(\))\n(\n    @)"
    match = re.search(init_end_pattern, patched)
    if match:
        inj = match.start(2)
        patched = patched[:inj] + "\n" + STREAMING_STATE_INIT + "\n" + patched[inj:]
        changes.append("  ✓ Injected streaming card state into __init__")
    else:
        # Fallback: after the last "self._approval_counter" line in __init__
        approval_match = re.search(
            r"(self\._approval_counter = itertools\.count\(\d+\))\n(\n    @staticmethod)",
            patched)
        if approval_match:
            inj = approval_match.start(2)
            patched = patched[:inj] + "\n" + STREAMING_STATE_INIT + "\n" + patched[inj:]
            changes.append("  ✓ Injected streaming card state into __init__ (fallback)")
        else:
            results.append(("FAIL", "Could not find __init__ injection point (tried _load_seen_message_ids and _approval_counter)"))
            return results

    # ── 2. Inject streaming methods before send() ───────────────────
    send_marker = "\n    async def send("
    if "def _get_card_lock" not in patched:
        inj = patched.find(send_marker)
        if inj != -1:
            patched = patched[:inj] + STREAMING_METHODS + "\n" + patched[inj:]
            changes.append("  ✓ Injected streaming card methods")
        else:
            results.append(("FAIL", "Could not find send() method"))
            return results
    else:
        changes.append("  ℹ _get_card_lock already exists (skip)")

    # ── 3. Patch send() — inject streaming routing at start ──────────
    # Strategy: find "async def send(...):" and its docstring, then inject
    # AFTER the docstring and any pre-processing (if not self._client, formatted = ...)
    # The streaming routing should wrap the original body.
    #
    # We replace the entire body of send() by:
    # 1. Finding the method signature + docstring
    # 2. Finding the first "real" statement (if not self._client: or formatted = ...)
    # 3. Replacing from there to the end of the method with: our routing + original body
    #
    # Approach: find the send() method, then inject routing code after docstring,
    # and wrap original body in an else branch.

    if "Feishu Streaming Card routing" not in patched:
        # Find send() method start
        send_sig_match = re.search(
            r"(\n    async def send\(\n        self,\n        chat_id: str,\n        content: str,\n        reply_to:[^)]+\)\s*(?:-> SendResult:)?\n)",
            patched, re.DOTALL)

        if not send_sig_match:
            # Try a more relaxed pattern
            send_sig_match = re.search(
                r"(\n    async def send\(\n        self,[^)]+\)\s*(?:-> SendResult:)?\n)",
                patched, re.DOTALL)

        if send_sig_match:
            sig_end = send_sig_match.end()
            # Find what comes immediately after the signature (docstring or first statement)
            after_sig = patched[sig_end:]

            # Check for docstring
            docstring_match = re.match(r'(\s*"""[^"]*"""\n)', after_sig, re.DOTALL)
            if docstring_match:
                docstring_end = sig_end + docstring_match.end()
                routing_code = SEND_STREAMING_PRELUDE.strip() + "\n        "
                patched = (
                    patched[:docstring_end]
                    + "\n"
                    + routing_code
                    + patched[docstring_end:]
                )
                changes.append("  ✓ Patched send() with streaming routing (docstring variant)")
            else:
                # No docstring, inject right after signature
                # Find the first non-empty line after signature
                first_line_match = re.match(r"(\s*\S.*?\n)", after_sig)
                if first_line_match:
                    first_line_end = sig_end + first_line_match.end()
                    routing_code = SEND_STREAMING_PRELUDE.strip() + "\n        "
                    patched = (
                        patched[:first_line_end]
                        + "\n"
                        + routing_code
                        + patched[first_line_end:]
                    )
                    changes.append("  ✓ Patched send() with streaming routing (no-docstring variant)")
                else:
                    results.append(("FAIL", "Could not determine send() body structure"))
                    return results
        else:
            results.append(("FAIL", "Could not find send() signature pattern"))
            return results
    else:
        changes.append("  ℹ send() already has streaming routing (skip)")

    # ── 4. Patch edit_message() ──────────────────────────────────────
    if "If streaming card is active for this chat" not in patched:
        # Find the docstring end of edit_message()
        edit_match = re.search(
            r"(\n    async def edit_message\(\n        self,\n        chat_id: str,\n        message_id: str,\n        content: str,\n    \) -> SendResult:\n        \"\"\"[^\"]*\"\"\")",
            patched, re.DOTALL)
        if edit_match:
            inj = edit_match.end()
            patched = patched[:inj] + "\n" + EDIT_MESSAGE_STREAMING_ROUTING + patched[inj:]
            changes.append("  ✓ Patched edit_message() with streaming routing")
        else:
            # Try simpler pattern
            edit_match2 = re.search(
                r"(\n    async def edit_message\([^)]+\)[^:]*:\n        \"\"\"[^\"]*\"\"\")",
                patched, re.DOTALL)
            if edit_match2:
                inj = edit_match2.end()
                patched = patched[:inj] + "\n" + EDIT_MESSAGE_STREAMING_ROUTING + patched[inj:]
                changes.append("  ✓ Patched edit_message() with streaming routing (relaxed pattern)")
            else:
                changes.append("  ⚠ Could not find edit_message() docstring — skipping (non-critical)")
    else:
        changes.append("  ℹ edit_message() already has streaming routing (skip)")

    # ── Write patched file ───────────────────────────────────────────
    backup_path = feishu_py_path + ".fscbak"
    with open(backup_path, "w") as f:
        f.write(original)

    with open(feishu_py_path, "w") as f:
        f.write(patched)

    results.append(("OK", f"Patched {feishu_py_path}"))
    for c in changes:
        results.append(("OK", c))
    results.append(("OK", f"  Backup: {backup_path}"))

    return results
