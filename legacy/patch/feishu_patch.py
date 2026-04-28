"""
feishu_patch.py
===============
Injects (or verifies) the Feishu streaming card feature into
hermes-agent/gateway/platforms/feishu.py.

Version-aware: works on Hermes versions with different send() structures.
If streaming card is already installed, verifies and skips.
Otherwise, injects into the correct locations based on detected code structure.

What it patches:
  1. __init__: adds _streaming_card, _streaming_card_locks, _streaming_pending
  2. Adds methods: _get_card_lock, _get_tenant_access_token, _build_streaming_card,
                   send_streaming_card, _update_card_element, set_streaming_greeting,
                   set_streaming_pending, is_streaming_pending, clear_streaming_pending,
                   clear_streaming_card, format_token, _build_footer, finalize_streaming_card
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


# ─────────────────────────────────────────────────────────────────
# Code to inject: streaming card state attrs in __init__
# Injected after the last "self._xxx =" assignment in __init__
# Indentation: 8 spaces (one level inside __init__)
# ─────────────────────────────────────────────────────────────────

STREAMING_STATE_INIT = (
    "        # ── Feishu Streaming Card state ────────────────────────────────────\n"
    "        # chat_id → card state (card_id, message_id, sequence, ...)\n"
    "        self._streaming_card: dict = {}\n"
    "        # Per-chat asyncio.Lock to serialize card updates\n"
    "        self._streaming_card_locks: dict = {}\n"
    "        # Pending flag: signals send_progress_messages to wait for card creation\n"
    "        self._streaming_pending: dict = {}\n"
)


# ─────────────────────────────────────────────────────────────────
# Code to inject: streaming card methods
# Injected right before "async def send("
# Indentation: 12 spaces (inside class)
# ─────────────────────────────────────────────────────────────────

STREAMING_METHODS = (
    "    # ══════════════════════════════════════════════════════════════════════\n"
    "    # Feishu Streaming Card (CardKit v1) — typewriter card per chat\n"
    "    # ══════════════════════════════════════════════════════════════════════\n"
    "\n"
    "    def _get_card_lock(self, chat_id: str):\n"
    "        if chat_id not in self._streaming_card_locks:\n"
    "            self._streaming_card_locks[chat_id] = asyncio.Lock()\n"
    "        return self._streaming_card_locks[chat_id]\n"
    "\n"
    "    def _get_tenant_access_token(self) -> str | None:\n"
    '        """Get a fresh tenant_access_token via lark-cli."""\n'
    "        try:\n"
    "            import subprocess as _subprocess\n"
    "            from pathlib import Path\n"
    "            _lark_cli = Path.home() / \".npm-global\" / \"bin\" / \"lark-cli\"\n"
    "            if not _lark_cli.exists():\n"
    "                _lark_cli = Path(\"/usr/local/bin/lark-cli\")\n"
    "            _r = _subprocess.run(\n"
    "                [_lark_cli, \"api\", \"POST\",\n"
    '                 "/open-apis/auth/v3/tenant_access_token/internal",\n'
    "                 \"--data\", __import__(\"json\").dumps({\n"
    "                     \"app_id\": self._app_id,\n"
    "                     \"app_secret\": self._app_secret,\n"
    "                 })],\n"
    "                capture_output=True, text=True, timeout=10,\n"
    "            )\n"
    "            if _r.returncode == 0:\n"
    "                _data = __import__(\"json\").loads(_r.stdout.strip())\n"
    "                return _data.get(\"tenant_access_token\")\n"
    "        except Exception as _e:\n"
    "            __import__(\"logging\").getLogger(\"feishu\").debug(\n"
    '                "[Feishu] Failed to get tenant token: %s", _e)\n'
    "        return None\n"
    "\n"
    "    def _build_streaming_card(self, greeting: str, subtitle: str) -> dict:\n"
    '        """Vertical layout streaming card. All element_ids are at body root level."""\n'
    "        return {\n"
    '            "schema": "2.0",\n'
    "            \"config\": {\n"
    '                "streaming_mode": True,\n'
    '                "update_multi": True,\n'
    '                "summary": {"content": "处理中..."},\n'
    '                "streaming_config": {\n'
    '                    "print_frequency_ms": {"default": 60, "android": 60, "ios": 60, "pc": 60},\n'
    '                    "print_step": {"default": 2, "android": 2, "ios": 2, "pc": 2},\n'
    '                    "print_strategy": "fast",\n'
    "                },\n"
    "            },\n"
    "            \"header\": {\n"
    '                "template": "indigo",\n'
    '                "title": {"content": greeting, "tag": "plain_text"},\n'
    '                "subtitle": {"content": f"{subtitle}  🤔思考中", "tag": "plain_text"},\n'
    "            },\n"
    "            \"body\": {\n"
    '                "direction": "vertical",\n'
    '                "padding": "10px 16px 10px 16px",\n'
    '                "vertical_spacing": "6px",\n'
    '                "elements": [\n'
    '                    {"tag": "markdown", "element_id": "thinking_content",\n'
    '                     "content": "⏳ 执行中，等待结果...",\n'
    '                     "text_size": "normal", "text_align": "left",\n'
    '                     "margin": "0px 0px 6px 0px"},\n'
    '                    {"tag": "markdown", "element_id": "status_label",\n'
    '                     "content": "🤔思考中", "text_size": "small",\n'
    '                     "text_color": "grey", "margin": "0px 0px 2px 0px"},\n'
    '                    {"tag": "markdown", "element_id": "tools_label",\n'
    '                     "content": "🔧 工具调用 (0次)", "text_size": "small",\n'
    '                     "text_color": "grey", "margin": "0px 0px 2px 0px"},\n'
    '                    {"tag": "markdown", "element_id": "tools_body",\n'
    '                     "content": "⏳ 等待开始...", "text_size": "x-small",\n'
    '                     "text_color": "grey", "margin": "0px 0px 6px 0px"},\n'
    '                    {"tag": "markdown", "element_id": "footer",\n'
    '                     "content": "⏳ 执行中...", "text_size": "x-small",\n'
    '                     "text_align": "left", "margin": "0px 0px 0px 0px"},\n'
    "                ],\n"
    "            },\n"
    "        }\n"
    "\n"
    "    async def send_streaming_card(\n"
    "        self, chat_id: str, greeting: str, subtitle: str, metadata: dict | None = None\n"
    "    ) -> dict | None:\n"
    '        """Create and send a Feishu streaming card. Returns card state on success."""\n'
    "        try:\n"
    "            import urllib.request\n"
    "            import urllib.error\n"
    "            card_payload = self._build_streaming_card(greeting, subtitle)\n"
    "            token = self._get_tenant_access_token() or \"\"\n"
    '            create_url = "https://open.feishu.cn/open-apis/cardkit/v1/cards"\n'
    "            req = urllib.request.Request(\n"
    "                create_url,\n"
    "                data=__import__(\"json\").dumps(card_payload).encode(),\n"
    "                headers={\n"
    '                    "Authorization": f"Bearer {token}",\n'
    '                    "Content-Type": "application/json",\n'
    "                },\n"
    "                method=\"POST\",\n"
    "            )\n"
    "            with urllib.request.urlopen(req, timeout=10) as resp:\n"
    "                create_data = __import__(\"json\").loads(resp.read())\n"
    "                if create_data.get(\"code\") != 0:\n"
    "                    __import__(\"logging\").getLogger(\"feishu\").warning(\n"
    '                        "[Feishu] CardKit create failed: %s", create_data)\n'
    "                    return None\n"
    "                card_id = create_data[\"data\"][\"card\"][\"card_id\"]\n"
    "\n"
    '            send_url = (\n'
    '                "https://open.feishu.cn/open-apis/im/v1/messages"\n'
    '                "?receive_id_type=chat_id"\n'
    "            )\n"
    "            msg_payload = __import__(\"json\").dumps({\n"
    '                "receive_id": chat_id,\n'
    '                "msg_type": "interactive",\n'
    '                "content": __import__("json").dumps(card_payload),\n'
    "            }).encode()\n"
    "            send_req = urllib.request.Request(\n"
    "                send_url, data=msg_payload,\n"
    "                headers={\n"
    '                    "Authorization": f"Bearer {token}",\n'
    '                    "Content-Type": "application/json",\n'
    "                },\n"
    "                method=\"POST\",\n"
    "            )\n"
    "            with urllib.request.urlopen(send_req, timeout=10) as send_resp:\n"
    "                send_data = __import__(\"json\").loads(send_resp.read())\n"
    "                if send_data.get(\"code\") != 0:\n"
    "                    __import__(\"logging\").getLogger(\"feishu\").warning(\n"
    '                        "[Feishu] Card send failed: %s", send_data)\n'
    "                    return None\n"
    "                message_id = send_data[\"data\"][\"message_id\"]\n"
    "\n"
    "            initial_sequence = create_data[\"data\"][\"card\"].get(\"sequence\", 1)\n"
    "            __import__(\"logging\").getLogger(\"feishu\").info(\n"
    '                "[Feishu] send_streaming_card: card_id=%s message_id=%s seq=%s",\n'
    "                card_id, message_id, initial_sequence)\n"
    "            return {\n"
    '                "card_id": card_id,\n'
    '                "message_id": message_id,\n'
    '                "sequence": initial_sequence,\n'
    '                "tenant_token": token,\n'
    "            }\n"
    "        except Exception as e:\n"
    "            __import__(\"logging\").getLogger(\"feishu\").error(\n"
    '                "[Feishu] send_streaming_card error: %s", e, exc_info=True)\n'
    "            return None\n"
    "\n"
    "    def _update_card_element(\n"
    "        self, card_id: str, element_id: str, content: str,\n"
    "        sequence: int, token: str,\n"
    "    ) -> tuple:\n"
    '        """Update a card element via CardKit API. Returns (success, next_sequence)."""\n'
    "        try:\n"
    "            import urllib.request\n"
    "            import urllib.error\n"
    "            fresh_token = self._get_tenant_access_token() or token\n"
    "            payload = __import__(\"json\").dumps({\"content\": content}).encode()\n"
    "            url = (\n"
    '                f"https://open.feishu.cn/open-apis/cardkit/v1/cards/"\n'
    '                f"{card_id}/elements/{element_id}/content"\n'
    "            )\n"
    "            req = urllib.request.Request(\n"
    "                url, data=payload,\n"
    "                headers={\n"
    '                    "Authorization": f"Bearer {fresh_token}",\n'
    '                    "Content-Type": "application/json",\n'
    "                },\n"
    "                method=\"PUT\",\n"
    "            )\n"
    "            with urllib.request.urlopen(req, timeout=10) as resp:\n"
    "                data = __import__(\"json\").loads(resp.read())\n"
    "                code = data.get(\"code\", -1)\n"
    "                if code == 0:\n"
    "                    next_seq = data.get(\"data\", {}).get(\"sequence\", sequence + 1)\n"
    "                    return True, next_seq\n"
    "                __import__(\"logging\").getLogger(\"feishu\").warning(\n"
    '                    "[Feishu] _update_card_element element=%s ok=False code=%s msg=%s",\n'
    "                    element_id, code, data.get(\"msg\", \"\"))\n"
    "                return False, -1\n"
    "        except Exception as e:\n"
    "            __import__(\"logging\").getLogger(\"feishu\").warning(\n"
    '                "[Feishu] _update_card_element element=%s error=%s", element_id, e)\n'
    "        return False, -1\n"
    "\n"
    "    def set_streaming_greeting(self, greeting: str) -> None:\n"
    '        """Set the greeting to display in the streaming card header."""\n'
    "        self._pending_greeting = greeting\n"
    "\n"
    "    def set_streaming_model(self, model: str) -> None:\n"
    '        """Set the model name to display in the streaming card subtitle."""\n'
    "        self._pending_subtitle = model\n"
    "\n"
    "    def set_streaming_pending(self, chat_id: str) -> None:\n"
    "        if not hasattr(self, \"_streaming_pending\"):\n"
    "            self._streaming_pending = {}\n"
    "        self._streaming_pending[chat_id] = True\n"
    "\n"
    "    def is_streaming_pending(self, chat_id: str) -> bool:\n"
    "        return getattr(self, \"_streaming_pending\", {}).get(chat_id, False)\n"
    "\n"
    "    def clear_streaming_pending(self, chat_id: str) -> None:\n"
    "        if hasattr(self, \"_streaming_pending\"):\n"
    "            self._streaming_pending.pop(chat_id, None)\n"
    "\n"
    "    def clear_streaming_card(self, chat_id: str) -> None:\n"
    "        self._streaming_card.pop(chat_id, None)\n"
    "        self.clear_streaming_pending(chat_id)\n"
    "\n"
    "    def format_token(self, n: int) -> str:\n"
    "        if n >= 1_000_000:\n"
    "            s = f\"{n / 1_000_000:.1f}M\"\n"
    "            return s.replace(\".0M\", \"M\")\n"
    "        elif n >= 1_000:\n"
    "            s = f\"{n / 1_000:.1f}K\"\n"
    "            return s.replace(\".0K\", \"K\")\n"
    "        return str(n)\n"
    "\n"
    "    def _build_footer(\n"
    "        self, model: str, elapsed: float,\n"
    "        in_t: int, out_t: int, cache_t: int,\n"
    "        ctx_used: int, ctx_limit: int,\n"
    "    ) -> str:\n"
    "        if elapsed >= 60:\n"
    "            m = int(elapsed // 60)\n"
    "            s = int(elapsed % 60)\n"
    "            elapsed_str = f\"{m}m{s}s\"\n"
    "        else:\n"
    "            elapsed_str = f\"{elapsed:.1f}s\"\n"
    "        ctx_pct = ctx_used / ctx_limit * 100 if ctx_limit else 0\n"
    "        parts = [\n"
    "            f\"{elapsed_str}  ·  {self.format_token(in_t)}↑ / {self.format_token(out_t)}↓\",\n"
    "        ]\n"
    "        if cache_t > 0:\n"
    "            cache_pct = cache_t / in_t * 100 if in_t else 0\n"
    "            parts.append(f\"缓存 {self.format_token(cache_t)} ({cache_pct:.1f}%)\")\n"
    "        parts.append(\n"
    "            f\"上下文 {self.format_token(ctx_used)}/{self.format_token(ctx_limit)}\"\n"
    "            f\" ({ctx_pct:.1f}%)\"\n"
    "        )\n"
    "        return \"  ·  \".join(parts)\n"
    "\n"
    "    async def finalize_streaming_card(\n"
    "        self, chat_id: str, model: str, elapsed: float,\n"
    "        in_t: int, out_t: int, cache_t: int,\n"
    "        ctx_used: int, ctx_limit: int,\n"
    "        result_summary: str = \"\",\n"
    "    ) -> None:\n"
    '        """Update streaming card to completed state. Caller holds the card lock."""\n'
    "        state = self._streaming_card.get(chat_id)\n"
    "        if not state:\n"
    "            return\n"
    "        greeting = state.get(\"_greeting\", \"主人，苏菲为您服务！\")\n"
    "        tool_count = state.get(\"_tool_count\", 0)\n"
    "        model = state.get(\"_model\", model)\n"
    "        loop = asyncio.get_event_loop()\n"
    "\n"
    "        # ① status_label → completed\n"
    "        _ok, _next_seq = await loop.run_in_executor(\n"
    "            None, lambda: self._update_card_element(\n"
    '                state["card_id"], "status_label", "✅已完成",\n'
    "                state[\"sequence\"], state[\"tenant_token\"]))\n"
    "        if _ok:\n"
    "            state[\"sequence\"] = _next_seq\n"
    "\n"
    "        # ② thinking_content → result_summary (strip XML tags + Agent footer)\n"
    "        summary = result_summary if result_summary else \"主人，任务已完成！\"\n"
    "        summary = re.sub(r\"<think>.*?</think>\", \"\", summary, flags=re.DOTALL)\n"
    '        summary = re.sub(r"<think>.*", "", summary, flags=re.DOTALL)\n'
    "        summary = re.sub(r\"\\n?\\s*Agent\\s*·.*\", \"\", summary)\n"
    "        summary = summary.strip()\n"
    "        _ok, next_seq = await loop.run_in_executor(\n"
    "            None, lambda: self._update_card_element(\n"
    '                state["card_id"], "thinking_content",\n'
    "                summary[:800] if summary else \"主人，任务已完成！\",\n"
    "                state[\"sequence\"], state[\"tenant_token\"]))\n"
    "        if _ok:\n"
    "            state[\"sequence\"] = next_seq\n"
    "\n"
    "        # ③ tools_label → completion\n"
    "        _ok, _next_seq = await loop.run_in_executor(\n"
    "            None, lambda: self._update_card_element(\n"
    '                state["card_id"], "tools_label",\n'
    "                f\"🔧 工具调用 ({tool_count}次)  ✅完成\",\n"
    "                state[\"sequence\"], state[\"tenant_token\"]))\n"
    "        if _ok:\n"
    "            state[\"sequence\"] = _next_seq\n"
    "\n"
    "        # ④ footer → token stats\n"
    "        footer = self._build_footer(model, elapsed, in_t, out_t, cache_t, ctx_used, ctx_limit)\n"
    "        _ok, _next_seq = await loop.run_in_executor(\n"
    "            None, lambda: self._update_card_element(\n"
    '                state["card_id"], "footer", footer,\n'
    "                state[\"sequence\"], state[\"tenant_token\"]))\n"
    "        if _ok:\n"
    "            state[\"sequence\"] = _next_seq\n"
    "\n"
    "        state[\"finalized\"] = True\n"
    "        state[\"finalize_ts\"] = __import__(\"time\").time()\n"
    "        if not hasattr(self, \"_finalized_chats\"):\n"
    "            self._finalized_chats = {}\n"
    "        self._finalized_chats[chat_id] = {\n"
    '            "ts": state["finalize_ts"],\n'
    '            "card_id": state["card_id"],\n'
    '            "message_id": state["message_id"],\n'
    '            "tenant_token": state["tenant_token"],\n'
    '            "sequence": state["sequence"],\n'
    "        }\n"
)


# ─────────────────────────────────────────────────────────────────
# Streaming routing code to prepend at the start of send()
# All lines are 16 spaces (proper function body level)
# ─────────────────────────────────────────────────────────────────

SEND_STREAMING_PRELUDE = (
    "        # ════════════════════════════════════════════════════════════════════\n"
    "        # Feishu Streaming Card routing (4 branches, per-chat Lock serialized)\n"
    "        # ════════════════════════════════════════════════════════════════════\n"
    "\n"
    "        # Emoji detection: first char of first line is emoji → tool progress\n"
    "        try:\n"
    "            import regex as _regex\n"
    "            _EMOJI_RE = _regex.compile(r\"^[\\p{Emoji_Presentation}\\p{Extended_Pictographic}]\")\n"
    "            _first_line = content.split(\"\\n\")[0].strip() if content else \"\"\n"
    "            _match = _EMOJI_RE.match(_first_line) if _first_line else None\n"
    "            is_tool_progress = bool(_match and len(_first_line) < 200)\n"
    "        except Exception:\n"
    "            is_tool_progress = False\n"
    "\n"
    "        _has_card = chat_id in self._streaming_card\n"
    "\n"
    "        async with self._get_card_lock(chat_id):\n"
    "            _has_card = chat_id in self._streaming_card\n"
    "\n"
    "            # ── ① Has card + finalized → grace-period result write ─────────\n"
    "            if _has_card:\n"
    "                _state = self._streaming_card[chat_id]\n"
    "                if _state.get(\"finalized\"):\n"
    "                    import time as _time\n"
    "                    _finfo = getattr(self, \"_finalized_chats\", {}).get(chat_id, {})\n"
    "                    _grace_start = _finfo.get(\"ts\") or _state.get(\"finalize_ts\", 0)\n"
    "                    if _time.time() - _grace_start < 60:\n"
    "                        _loop = asyncio.get_event_loop()\n"
    "                        _ok, _ns = await _loop.run_in_executor(\n"
    "                            None, lambda: self._update_card_element(\n"
    '                                    _state["card_id"], "thinking_content",\n'
    "                                content[:800], _state[\"sequence\"], _state[\"tenant_token\"]))\n"
    "                        if _ok:\n"
    "                            _state[\"sequence\"] = _ns\n"
    "                            self._streaming_card.pop(chat_id, None)\n"
    "                            self._finalized_chats.pop(chat_id, None)\n"
    "                    else:\n"
    "                        self._streaming_card.pop(chat_id, None)\n"
    "                        self._finalized_chats.pop(chat_id, None)\n"
    "                    return SendResult(success=True,\n"
    '                                          message_id=_state["message_id"],\n'
    '                                          card_id=_state["card_id"])\n'
    "\n"
    "            # ── ② Has card + non-emoji → update thinking_content (overwrite) ──\n"
    "            if _has_card and not is_tool_progress:\n"
    "                _st = self._streaming_card[chat_id]\n"
    "                if not _st.get(\"finalized\"):\n"
    "                    _clean = content\n"
    "                    _clean = re.sub(r\"<think>\", \"\", _clean)\n"
    "                    _clean = re.sub(r\"</think>\", \"\", _clean)\n"
    "                    _clean = re.sub(r\"\\n?\\s*Agent\\s*·.*\", \"\", _clean)\n"
    "                    _clean = _clean.strip()\n"
    "                    if _clean:\n"
    "                        _loop = asyncio.get_event_loop()\n"
    "                        _ok, _ns = await _loop.run_in_executor(\n"
    "                            None, lambda: self._update_card_element(\n"
    '                                    _st["card_id"], "thinking_content",\n'
    "                                _clean[:2000], _st[\"sequence\"], _st[\"tenant_token\"]))\n"
    "                        if _ok:\n"
    "                            _st[\"sequence\"] = _ns\n"
    "                return SendResult(success=True,\n"
    '                                      message_id=_st["message_id"],\n'
    '                                      card_id=_st["card_id"])\n'
    "\n"
    "            # ── ③ First emoji tool → create streaming card ───────────────────\n"
    "            if is_tool_progress and not _has_card:\n"
    "                self._streaming_card.pop(chat_id, None)\n"
    "                self.set_streaming_pending(chat_id)\n"
    "\n"
    "                _greeting = (metadata.get(\"greeting\") if metadata and metadata.get(\"greeting\")\n"
    "                             else getattr(self, \"_pending_greeting\", _get_default_greeting()))\n"
    "                _model_name = (metadata.get(\"model\") if metadata and metadata.get(\"model\")\n"
    "                              else getattr(self, \"_pending_subtitle\", \"\"))\n"
    "\n"
    "                _state = await self.send_streaming_card(\n"
    "                    chat_id=chat_id, greeting=_greeting, subtitle=_model_name, metadata=metadata)\n"
    "                if _state:\n"
    "                    _state[\"_tool_count\"] = 1\n"
    "                    _state[\"_tool_lines\"] = [_first_line]\n"
    "                    _state[\"_model\"] = _model_name\n"
    "                    _state[\"_greeting\"] = _greeting\n"
    "                    self._streaming_card[chat_id] = _state\n"
    "                    self.clear_streaming_pending(chat_id)\n"
    "                    _loop = asyncio.get_event_loop()\n"
    "\n"
    "                    _lok, _lns = await _loop.run_in_executor(\n"
    "                        None, lambda: self._update_card_element(\n"
    '                                _state["card_id"], "status_label", "⚡执行中",\n'
    "                            _state[\"sequence\"], _state[\"tenant_token\"]))\n"
    "                    if _lok:\n"
    "                        _state[\"sequence\"] = _lns\n"
    "\n"
    "                    _ok, _ns = await _loop.run_in_executor(\n"
    "                        None, lambda: self._update_card_element(\n"
    '                                _state["card_id"], "tools_body",\n'
    '                                f"⚙️ `{_first_line}`",\n'
    "                            _state[\"sequence\"], _state[\"tenant_token\"]))\n"
    "                    if _ok:\n"
    "                        _state[\"sequence\"] = _ns\n"
    "                    return SendResult(success=True,\n"
    '                                          message_id=_state["message_id"],\n'
    '                                          card_id=_state["card_id"])\n'
    "\n"
    "                __import__(\"logging\").getLogger(\"feishu\").warning(\n"
    '                        "[Feishu] Streaming card creation failed, sending as normal message")\n'
    "\n"
    "            # ── ④ Has card + emoji → tool progress update ────────────────────\n"
    "            if _has_card and is_tool_progress:\n"
    "                _st = self._streaming_card[chat_id]\n"
    "                _tc = _st.get(\"_tool_count\", 0) + 1\n"
    "                _tl = _st.get(\"_tool_lines\", [])\n"
    "                if not _tl or _tl[-1] != _first_line:\n"
    "                    _tl.append(_first_line)\n"
    "                else:\n"
    "                    _tc = _st[\"_tool_count\"]\n"
    "                _st[\"_tool_count\"] = _tc\n"
    "                _st[\"_tool_lines\"] = _tl\n"
    "                _loop = asyncio.get_event_loop()\n"
    "\n"
    "                _ok, _ns = await _loop.run_in_executor(\n"
    "                    None, lambda: self._update_card_element(\n"
    '                            _st["card_id"], "tools_label",\n'
    "                        f\"🔧 工具调用 ({_tc}次)\",\n"
    "                        _st[\"sequence\"], _st[\"tenant_token\"]))\n"
    "                if _ok:\n"
    "                    _st[\"sequence\"] = _ns\n"
    "\n"
    "                _display, _seen = [], set()\n"
    "                for _l in _tl:\n"
    "                    if _l not in _seen:\n"
    "                        _display.append(_l)\n"
    "                        _seen.add(_l)\n"
    "                _display = _display[-8:]\n"
    "                _ok, _ns = await _loop.run_in_executor(\n"
    "                    None, lambda: self._update_card_element(\n"
    '                            _st["card_id"], "tools_body",\n'
    '                            "\\n".join([f"⚙️ `{l}`" for l in _display]),\n'
    "                        _st[\"sequence\"], _st[\"tenant_token\"]))\n"
    "                if _ok:\n"
    "                    _st[\"sequence\"] = _ns\n"
    "                return SendResult(success=True,\n"
    '                                      message_id=_st["message_id"],\n'
    '                                      card_id=_st["card_id"])\n'
    "\n"
    "        # ── Normal send (no streaming card for this chat) ──────────────────\n"
    "        # Falls through to the original send() body below.\n"
    "        pass\n"
)


# ─────────────────────────────────────────────────────────────────
# Code to inject at the START of edit_message()
# Indentation: 12 spaces
# ─────────────────────────────────────────────────────────────────

EDIT_MESSAGE_STREAMING_ROUTING = (
    "        # If streaming card is active for this chat, route ALL content to the card\n"
    "        if chat_id in self._streaming_card:\n"
    "            _st = self._streaming_card[chat_id]\n"
    "            if not _st.get(\"finalized\"):\n"
    "                return await self.send(chat_id, content, reply_to=None, metadata=None)\n"
)


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

    return {
        "status": "ok",
        "has_streaming_card": has_streaming,
        "has_early_return": has_early_return,
        "has_formatted_before_try": has_formatted_before_try,
        "line_count": content.count("\n"),
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
    init_end_pattern = r"(self\._load_seen_message_ids\(\))\n(\n    @)"
    match = re.search(init_end_pattern, patched)
    if match:
        inj = match.start(2)
        patched = patched[:inj] + "\n" + STREAMING_STATE_INIT + "\n" + patched[inj:]
        changes.append("  ✓ Injected streaming card state into __init__")
    else:
        approval_match = re.search(
            r"(self\._approval_counter = itertools\.count\(\d+\))\n(\n    @staticmethod)",
            patched)
        if approval_match:
            inj = approval_match.start(2)
            patched = patched[:inj] + "\n" + STREAMING_STATE_INIT + "\n" + patched[inj:]
            changes.append("  ✓ Injected streaming card state into __init__ (fallback)")
        else:
            results.append(("FAIL", "Could not find __init__ injection point"))
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

    # ── 3. Patch send() — inject streaming routing after docstring ──────────
    if "Feishu Streaming Card routing" not in patched:
        # Find the line that STARTS the send() method (the "async def send(" line)
        send_def_match = re.search(r"\n    async def send\(\n        self,", patched)
        if not send_def_match:
            results.append(("FAIL", "Could not find 'async def send(self,' in feishu.py"))
            return results

        send_def_start = send_def_match.start()

        # Find the end of the send() signature: the line that has "-> SendResult:" or just "):"
        # and the next line starts the body (docstring or first statement)
        # We look for the first line after the signature that starts at 16 spaces
        # (which is the first body statement, like "if not self._client:" or the docstring)
        rest = patched[send_def_start:]
        lines = rest.split("\n")
        # lines[0] = "    async def send(...)", lines[1+] = signature lines
        # Find the closing of the signature:
        sig_close_idx = None
        for i, line in enumerate(lines[1:], start=1):
            stripped = line.strip()
            if stripped.startswith("-> ") or stripped == "):":
                sig_close_idx = i
                break
            if stripped.startswith("metadata:") or "metadata" in stripped:
                # metadata arg line
                continue
            if not line.strip() or line.strip().startswith("self,"):
                continue
            # Check if this is a arg line (starts with spaces, has type annotation)
            if line.startswith("            ") and ":" in line:
                continue
            # If we see a line that's not an arg, it might be the closing
            if "->" in line or line.strip() == "):": 
                sig_close_idx = i
                break
            # Also check if the closing is on the same line as the last arg
            if line.strip().endswith("):") or ("metadata" in line and "= None" in line):
                sig_close_idx = i
                break

        # More robust: find the line containing ")" that closes the signature
        # followed by the return type annotation or just ":"
        sig_pattern = re.search(
            r"(\n    async def send\([\s\S]*?\)(?:\s*->\s*\w+:\s*))",
            patched)
        if not sig_pattern:
            results.append(("FAIL", "Could not find send() signature end"))
            return results

        if send_def_match:
            sig_end_pos = sig_pattern.end()

            after_sig = patched[sig_end_pos:]

            # Find the end of the first line in after_sig
            # This handles both single-line docstrings (no indent) and body lines (8 spaces)
            # We look for the next newline that ends the first content line
            first_newline = after_sig.find('\n')
            if first_newline == -1:
                results.append(("FAIL", "Could not find body after send() signature"))
                return results
            first_line_end = sig_end_pos + first_newline
            first_line_text = patched[sig_end_pos:first_line_end].strip()
            stripped = first_line_text.strip()

            if stripped.startswith('"""') and stripped.endswith('"""'):
                # Single-line docstring — inject after its newline
                patched = (
                    patched[:first_line_end]
                    + "\n"
                    + SEND_STREAMING_PRELUDE
                    + patched[first_line_end:]
                )
                changes.append("  ✓ Patched send() with streaming routing (single-line docstring)")
            elif stripped.startswith('"""'):
                # Multi-line docstring start — find its end
                end_triple = patched.find('"""', first_line_end)
                if end_triple == -1:
                    results.append(("FAIL", "Could not find docstring end"))
                    return results
                # Find the newline after the closing """
                doc_end = patched.find('\n', end_triple)
                if doc_end == -1:
                    results.append(("FAIL", "Could not find docstring end newline"))
                    return results
                patched = (
                    patched[:doc_end + 1]
                    + "\n"
                    + SEND_STREAMING_PRELUDE
                    + patched[doc_end + 1:]
                )
                changes.append("  ✓ Patched send() with streaming routing (multi-line docstring)")
            else:
                # No docstring — inject before the first body line
                patched = (
                    patched[:first_line_end]
                    + "\n"
                    + SEND_STREAMING_PRELUDE
                    + patched[first_line_end:]
                )
                changes.append("  ✓ Patched send() with streaming routing (no docstring)")
        else:
            results.append(("FAIL", "Could not find send() signature pattern"))
            return results
    # ── 4. Patch edit_message() ──────────────────────────────────────
    if "If streaming card is active for this chat" not in patched:
        edit_match = re.search(
            r"(\n    async def edit_message\(\n        self,\n        chat_id: str,\n        message_id: str,\n        content: str,\n    \) -> SendResult:\n        \"\"\"[^\"]*\"\"\")",
            patched, re.DOTALL)
        if edit_match:
            inj = edit_match.end()
            patched = patched[:inj] + "\n" + EDIT_MESSAGE_STREAMING_ROUTING + patched[inj:]
            changes.append("  ✓ Patched edit_message() with streaming routing")
        else:
            edit_match2 = re.search(
                r"(\n    async def edit_message\([^)]+\)[^:]*:\n        \"\"\"[^\"]*\"\"\")",
                patched, re.DOTALL)
            if edit_match2:
                inj = edit_match2.end()
                patched = patched[:inj] + "\n" + EDIT_MESSAGE_STREAMING_ROUTING + patched[inj:]
                changes.append("  ✓ Patched edit_message() (relaxed pattern)")
            else:
                changes.append("  ⚠ edit_message() not patched (non-critical)")
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
