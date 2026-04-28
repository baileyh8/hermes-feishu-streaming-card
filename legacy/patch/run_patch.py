"""
run_patch.py
============
Injects Feishu streaming card pre-creation and finalize into run.py.
Version-aware: works on Hermes versions.

What it patches in run.py:
  1. _handle_message_with_agent: pre-creates streaming card on message receipt
  2. After agent:end hook: calls finalize_streaming_card to deliver result
  3. send_progress_messages: increased timeout (10s → 30s) for Feishu pending wait

Config (from config.yaml):
  feishu_streaming_card.greeting           — card header title
  feishu_streaming_card.enabled             — enable/disable
  feishu_streaming_card.pending_timeout     — send_progress timeout (default: 30)
"""

from __future__ import annotations

import re

logger = __import__("logging").getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# FEISHU_PRECREATE: injected right after logger.info(...) block
# in _handle_message_with_agent
# Indentation: 8 spaces for top-level body, 12 for 'if', 16 for 'try'
# ─────────────────────────────────────────────────────────────────

FEISHU_PRECREATE = (
    # ── Feishu Streaming Card: pre-create on message receipt ─────────────
    "        feishu_adapter = self.adapters.get(Platform.FEISHU)\n"
    "        if source.platform.value == \"feishu\" and feishu_adapter is not None:\n"
    "            try:\n"
    "                _cfg_path = (self._hermes_dir + \"/config.yaml\"\n"
    "                             if hasattr(self, \"_hermes_dir\") else \"\")\n"
    "                _greeting = \"主人，苏菲为您服务！\"\n"
    "                if _cfg_path:\n"
    "                    try:\n"
    "                        import yaml\n"
    "                        with open(_cfg_path) as _f:\n"
    "                            _cfg = yaml.safe_load(_f) or {}\n"
    "                        _greeting = (_cfg.get(\"feishu_streaming_card\", {})\n"
    "                                     .get(\"greeting\", \"主人，苏菲为您服务！\"))\n"
    "                    except Exception:\n"
    "                        pass\n"
    "                _model_name = (\n"
    "                    getattr(self, \"_gateway_model\", None)\n"
    "                    or (self.config.model if hasattr(self, \"config\") else None)\n"
    "                    or \"\"\n"
    "                )\n"
    "                feishu_adapter.set_streaming_greeting(_greeting)\n"
    "                feishu_adapter.set_streaming_model(str(_model_name))\n"
    "                try:\n"
    "                    _state = await feishu_adapter.send_streaming_card(\n"
    "                        chat_id=source.chat_id,\n"
    "                        greeting=_greeting,\n"
    "                        subtitle=str(_model_name),\n"
    "                        metadata=None,\n"
    "                    )\n"
    "                    if _state:\n"
    "                        _state[\"_tool_count\"] = 0\n"
    "                        _state[\"_tool_lines\"] = []\n"
    "                        _state[\"_model\"] = str(_model_name)\n"
    "                        _state[\"_greeting\"] = _greeting\n"
    "                        feishu_adapter._streaming_card[source.chat_id] = _state\n"
    "                        feishu_adapter.clear_streaming_pending(source.chat_id)\n"
    "                        __import__(\"logging\").getLogger(\"run\").info(\n"
    "                            \"[Feishu] Pre-created streaming card for chat_id=%s\",\n"
    "                            source.chat_id)\n"
    "                except Exception as _e:\n"
    "                    __import__(\"logging\").getLogger(\"run\").warning(\n"
    "                        \"[Feishu] Pre-create streaming card failed: %s\", _e)\n"
    "                    feishu_adapter.set_streaming_pending(source.chat_id)\n"
    "            except Exception:\n"
    "                pass\n"
)


# ─────────────────────────────────────────────────────────────────
# FEISHU_FINALIZE: injected right after "await self.hooks.emit("agent:end", {...})"
# Indentation: 12 spaces (inside if block)
# ─────────────────────────────────────────────────────────────────

FEISHU_FINALIZE = (
    # ── Feishu Streaming Card: finalize on agent completion ───────────
    "            feishu_adapter = self.adapters.get(Platform.FEISHU)\n"
    "            if source.platform.value == \"feishu\" and feishu_adapter is not None:\n"
    "                try:\n"
    "                    _elapsed = __import__(\"time\").time() - _msg_start_time\n"
    "                    _model_name = (\n"
    "                        getattr(self, \"_gateway_model\", None)\n"
    "                        or (self.config.model if hasattr(self, \"config\") else None)\n"
    "                        or \"\"\n"
    "                    )\n"
    "                    _raw = (response or \"\")\n"
    "                    _clean = re.sub(r\"\\n苏菲\\s*·.*$\", \"\", _raw)\n"
    "                    _clean = re.sub(r\"\\nHermes\\s*·.*$\", \"\", _clean)\n"
    "                    _result_summary = _clean[:800].strip()\n"
    "                    await feishu_adapter.finalize_streaming_card(\n"
    "                        chat_id=source.chat_id,\n"
    "                        model=str(_model_name),\n"
    "                        elapsed=_elapsed,\n"
    "                        in_t=agent_result.get(\"input_tokens\", 0),\n"
    "                        out_t=agent_result.get(\"output_tokens\", 0),\n"
    "                        cache_t=agent_result.get(\"cache_read_tokens\", 0),\n"
    "                        ctx_used=agent_result.get(\"last_prompt_tokens\", 0),\n"
    "                        ctx_limit=200_000,\n"
    "                        result_summary=_result_summary,\n"
    "                    )\n"
    "                    agent_result[\"already_sent\"] = True\n"
    "                except Exception as _e:\n"
    "                    __import__(\"logging\").getLogger(\"run\").warning(\n"
    "                        \"[Feishu] finalize_streaming_card error: %s\", _e)\n"
)


# ─────────────────────────────────────────────────────────────────
# Detect run.py version / streaming card installation status
# ─────────────────────────────────────────────────────────────────

def detect_run_version(run_py_path: str) -> dict:
    """Detect Hermes run.py version and whether streaming card is already installed."""
    try:
        with open(run_py_path) as f:
            content = f.read()
    except Exception:
        return {"status": "error", "message": f"Cannot read {run_py_path}"}

    has_precreate = "Pre-created streaming card for chat_id" in content
    has_finalize = "finalize_streaming_card" in content and "Feishu Streaming Card" in content
    has_streaming = has_precreate and has_finalize

    return {
        "status": "ok",
        "has_streaming_card": has_streaming,
        "has_precreate": has_precreate,
        "has_finalize": has_finalize,
        "line_count": content.count("\n"),
    }


# ─────────────────────────────────────────────────────────────────
# Apply patch to run.py
# ─────────────────────────────────────────────────────────────────

def patch_run_py(run_py_path: str, hermes_dir: str) -> list:
    """Apply streaming card patch to run.py. Version-aware."""
    results = []

    try:
        with open(run_py_path) as f:
            original = f.read()
    except Exception as e:
        return [("FAIL", f"Cannot read {run_py_path}: {e}")]

    patched = original
    changes = []

    version_info = detect_run_version(run_py_path)
    if version_info.get("status") != "ok":
        results.append(("FAIL", version_info.get("message", "Unknown error")))
        return results

    if version_info["has_streaming_card"]:
        results.append(("OK", "Streaming card already installed in run.py — skipping"))
        return results

    # ── 1. Inject pre-create card in _handle_message_with_agent ───────
    if "Pre-created streaming card for chat_id" not in patched:
        # Pattern: logger.info(...) block followed by blank line + "# Get or create session"
        # The injection point is right after the closing ) of logger.info,
        # on the blank line that precedes "# Get or create session"
        pattern = (
            r'(logger\.info\(\s*\n\s*"inbound message:.*?"[^\)]*\)\s*\n\s*\)\s*\n)'
            r'(\s*# Get or create session)'
        )
        m = re.search(pattern, patched, re.DOTALL)
        if m:
            inj = m.start(2)  # inject BEFORE "# Get or create session"
            patched = patched[:inj] + FEISHU_PRECREATE + "\n" + patched[inj:]
            changes.append("  ✓ Injected pre-create in _handle_message_with_agent")
        else:
            # Fallback: simpler pattern
            pattern2 = r'(\)\s*\n)\s*\n(\s*# Get or create session)'
            m2 = re.search(pattern2, patched)
            if m2:
                inj = m2.start(2)
                patched = patched[:inj] + FEISHU_PRECREATE + "\n" + patched[inj:]
                changes.append("  ✓ Injected pre-create (fallback pattern)")
            else:
                results.append(("FAIL", "Could not find _handle_message_with_agent injection point"))
                return results
    else:
        changes.append("  ℹ Pre-create already present (skip)")

    # ── 2. Inject finalize after agent:end hook ─────────────────────
    if "finalize_streaming_card" not in patched:
        # Pattern: "await self.hooks.emit("agent:end", {...})" followed by blank + "# Check for pending"
        pattern = r'(# Emit agent:end hook\s+await self\.hooks\.emit\("agent:end",\s*\{.*?\}\)\s*\n\s*\)\s*\n)\s*\n(\s*# Check for pending process watchers)'
        m = re.search(pattern, patched, re.DOTALL)
        if m:
            inj = m.start(2)  # inject BEFORE "# Check for pending process watchers"
            patched = patched[:inj] + FEISHU_FINALIZE + "\n" + patched[inj:]
            changes.append("  ✓ Injected finalize_streaming_card after agent:end hook")
        else:
            # Try simpler pattern
            pattern2 = r'(\)\s*\n)\s*\n(\s*# Check for pending process watchers)'
            m2 = re.search(pattern2, patched)
            if m2:
                inj = m2.start(2)
                patched = patched[:inj] + FEISHU_FINALIZE + "\n" + patched[inj:]
                changes.append("  ✓ Injected finalize (fallback pattern)")
            else:
                results.append(("FAIL", "Could not find agent:end injection point"))
                return results
    else:
        changes.append("  ℹ finalize already present (skip)")

    # ── Write patched file ──────────────────────────────────────────
    backup_path = run_py_path + ".fscbak"
    with open(backup_path, "w") as f:
        f.write(original)

    with open(run_py_path, "w") as f:
        f.write(patched)

    results.append(("OK", f"Patched {run_py_path}"))
    for c in changes:
        results.append(("OK", c))
    results.append(("OK", f"  Backup: {backup_path}"))

    return results
