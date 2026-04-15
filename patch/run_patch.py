"""
run_patch.py
============
Injects Feishu streaming card pre-creation and finalize into run.py.
Version-aware: works on both Hermes versions (old + latest NousResearch).

What it patches in run.py:
  1. _handle_message_with_agent: pre-creates streaming card on message receipt
  2. After agent:end hook: calls finalize_streaming_card to deliver result
  3. send_progress_messages: increased timeout (10s → 30s) for Feishu pending wait

Config (from config.yaml):
  feishu_streaming_card.greeting         — card header title
  feishu_streaming_card.enabled           — enable/disable
  feishu_streaming_card.pending_timeout   — send_progress timeout (default: 30)
"""

from __future__ import annotations

import re

logger = __import__("logging").getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Code to inject: pre-create streaming card in _handle_message_with_agent
# ─────────────────────────────────────────────────────────────────

FEISHU_PRECREATE = '''
        # ── Feishu Streaming Card: pre-create on message receipt ─────────────
        feishu_adapter = self.adapters.get(Platform.FEISHU)
        if source.platform.value == "feishu" and feishu_adapter is not None:
            try:
                import re as _re
                _cfg_path = self._hermes_dir + "/config.yaml" if hasattr(self, "_hermes_dir") else ""
                _greeting = "主人，苏菲为您服务！"
                if _cfg_path:
                    try:
                        import yaml
                        with open(_cfg_path) as _f:
                            _cfg = yaml.safe_load(_f) or {}
                        _greeting = (_cfg.get("feishu_streaming_card", {})
                                     .get("greeting", "主人，苏菲为您服务！"))
                    except Exception:
                        pass
                _model_name = (
                    getattr(self, "_gateway_model", None)
                    or (self.config.model if hasattr(self, "config") else None)
                    or ""
                )
                feishu_adapter.set_streaming_greeting(_greeting)
                feishu_adapter.set_streaming_model(str(_model_name))
                try:
                    import asyncio as _aio
                    _state = await feishu_adapter.send_streaming_card(
                        chat_id=source.chat_id,
                        greeting=_greeting,
                        subtitle=str(_model_name),
                        metadata=None,
                    )
                    if _state:
                        _state["_tool_count"] = 0
                        _state["_tool_lines"] = []
                        _state["_model"] = str(_model_name)
                        _state["_greeting"] = _greeting
                        feishu_adapter._streaming_card[source.chat_id] = _state
                        feishu_adapter.clear_streaming_pending(source.chat_id)
                        __import__("logging").getLogger("run").info(
                            "[Feishu] Pre-created streaming card for chat_id=%s", source.chat_id)
                except Exception as _e:
                    __import__("logging").getLogger("run").warning(
                        "[Feishu] Pre-create streaming card failed: %s (will create on first tool)", _e)
                    feishu_adapter.set_streaming_pending(source.chat_id)
            except Exception:
                pass
'''

# ─────────────────────────────────────────────────────────────────
# Code to inject: finalize streaming card after agent:end hook
# ─────────────────────────────────────────────────────────────────

FEISHU_FINALIZE = '''
            # ── Feishu Streaming Card: finalize on agent completion ───────────
            feishu_adapter = self.adapters.get(Platform.FEISHU)
            if source.platform.value == "feishu" and feishu_adapter is not None:
                try:
                    _elapsed = time.time() - _msg_start_time
                    _model_name = (
                        getattr(self, "_gateway_model", None)
                        or (self.config.model if hasattr(self, "config") else None)
                        or ""
                    )
                    _raw = (response or "")
                    _clean = re.sub(r"\\n苏菲\\s*·.*$", "", _raw)
                    _clean = re.sub(r"\\nHermes\\s*·.*$", "", _clean)
                    _result_summary = _clean[:800].strip()
                    await feishu_adapter.finalize_streaming_card(
                        chat_id=source.chat_id,
                        model=str(_model_name),
                        elapsed=_elapsed,
                        in_t=agent_result.get("input_tokens", 0),
                        out_t=agent_result.get("output_tokens", 0),
                        cache_t=agent_result.get("cache_read_tokens", 0),
                        ctx_used=agent_result.get("last_prompt_tokens", 0),
                        ctx_limit=200_000,
                        result_summary=_result_summary,
                    )
                    agent_result["already_sent"] = True
                except Exception as _e:
                    __import__("logging").getLogger("run").warning(
                        "[Feishu] finalize_streaming_card error: %s", _e)
'''

# ─────────────────────────────────────────────────────────────────
# Code to inject: send_progress_messages streaming card wait
# ─────────────────────────────────────────────────────────────────

FEISHU_PROGRESS_WAIT = '''
        # ── Feishu Streaming Card: wait for card creation ─────────────────
        feishu_adapter = getattr(adapter, "feishu_adapter", None)
        _feishu_pending = (
            feishu_adapter.is_streaming_pending(source.chat_id)
            if feishu_adapter and hasattr(feishu_adapter, "is_streaming_pending")
            else False
        )
        if _feishu_pending:
            _wait_start = _pm_time.time()
            _timeout = 30  # configurable via feishu_streaming_card.pending_timeout
            while _pm_time.time() - _wait_start < _timeout:
                await _pm_asyncio.sleep(0.5)
                _card = (
                    feishu_adapter._streaming_card.get(source.chat_id)
                    if feishu_adapter and hasattr(feishu_adapter, "_streaming_card")
                    else None
                )
                if _card and _card.get("message_id"):
                    _pm_logger.debug(
                        "[Feishu] Streaming card created (waited %.1fs)", _pm_time.time() - _wait_start)
                    break
            else:
                __import__("logging").getLogger("run").info(
                    "[Feishu] Streaming card timeout — using normal progress messages")
        # ── End Feishu streaming card wait ───────────────────────────────
'''

# ─────────────────────────────────────────────────────────────────
# Code to inject: pending fallback
# ─────────────────────────────────────────────────────────────────

FEISHU_PENDING_FALLBACK = '''
        # Feishu: streaming card was already pre-created in _handle_message_with_agent
        feishu_adapter = self.adapters.get(Platform.FEISHU)
        if (
            feishu_adapter is not None
            and hasattr(feishu_adapter, "_streaming_card")
            and source.chat_id not in feishu_adapter._streaming_card
        ):
            feishu_adapter.set_streaming_pending(source.chat_id)
'''


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

    # Detect send_progress_messages timeout value
    has_30s_timeout = "wait_start < 30" in content

    return {
        "status": "ok",
        "has_streaming_card": has_streaming,
        "has_precreate": has_precreate,
        "has_finalize": has_finalize,
        "has_30s_timeout": has_30s_timeout,
        "line_count": content.count("\n"),
    }


# ─────────────────────────────────────────────────────────────────
# Apply patch to run.py
# Returns list of (status, message) tuples
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

    # ── 0. Version detection ─────────────────────────────────────────
    version_info = detect_run_version(run_py_path)
    if version_info.get("status") != "ok":
        results.append(("FAIL", version_info.get("message", "Unknown error")))
        return results

    if version_info["has_streaming_card"]:
        results.append(("OK", "Streaming card is already installed in run.py — skipping"))
        return results

    # ── 1. Inject pre-create card in _handle_message_with_agent ───────
    if "Pre-created streaming card for chat_id" not in patched:
        # Find the end of the logger.info for inbound message in _handle_message_with_agent
        # Pattern: after "logger.info( ... inbound message: ...)" block
        # Look for: "        )" after logger.info for inbound message
        precreate_pattern = re.search(
            r'([\s]+logger\.info\(\s*\n\s*"inbound message:.*?)\n(\s*\)\s*\n\n\s*# Get or create session)',
            patched, re.DOTALL)
        if precreate_pattern:
            inj = precreate_pattern.start(2)
            patched = patched[:inj] + FEISHU_PRECREATE.strip() + "\n" + patched[inj:]
            changes.append("  ✓ Injected pre-create streaming card in _handle_message_with_agent")
        else:
            # Try a simpler pattern: after logger.info("inbound message")
            precreate_pattern2 = re.search(
                r'([\s]+logger\.info\(\s*\n\s*"inbound message:.*?)(\n\s+# Get or create session)',
                patched, re.DOTALL)
            if precreate_pattern2:
                inj = precreate_pattern2.start(2)
                patched = patched[:inj] + FEISHU_PRECREATE.strip() + "\n" + patched[inj:]
                changes.append("  ✓ Injected pre-create streaming card (pattern 2)")
            else:
                results.append(("FAIL", "Could not find _handle_message_with_agent logger.info injection point"))
                return results
    else:
        changes.append("  ℹ Pre-create already present (skip)")

    # ── 2. Inject finalize_streaming_card after agent:end hook ───────
    if "finalize_streaming_card" not in patched:
        # Find: after "await self.hooks.emit("agent:end", {"
        finalize_pattern = re.search(
            r'(# Emit agent:end hook\s+await self\.hooks\.emit\("agent:end",\s*\{.*?\}\)\s*\n)\s*\n(\s+# Check for pending process watchers)',
            patched, re.DOTALL)
        if finalize_pattern:
            inj = finalize_pattern.start(2)
            patched = patched[:inj] + FEISHU_FINALIZE.strip() + "\n" + patched[inj:]
            changes.append("  ✓ Injected finalize_streaming_card after agent:end hook")
        else:
            # Try simpler pattern
            finalize_pattern2 = re.search(
                r'(# Emit agent:end hook\s+await self\.hooks\.emit\("agent:end",.*?\)\s*\n)\s*\n(\s+# Check for pending process watchers)',
                patched, re.DOTALL)
            if finalize_pattern2:
                inj = finalize_pattern2.start(2)
                patched = patched[:inj] + FEISHU_FINALIZE.strip() + "\n" + patched[inj:]
                changes.append("  ✓ Injected finalize_streaming_card (pattern 2)")
            else:
                results.append(("FAIL", "Could not find agent:end injection point"))
                return results
    else:
        changes.append("  ℹ finalize already present (skip)")

    # ── 3. Inject send_progress_messages wait for Feishu streaming card ──
    if "[Feishu] Streaming card timeout" not in patched:
        # Find: inside send_progress_messages, after the "else: logger.info(failed)"
        # Pattern: "else: logger.warning(failed to send progress)" → inject wait after it
        progress_wait_pattern = re.search(
            r'(logger\.warning\(f?["\'].*?(?:send progress|failed to send).*?"\s*%.*?\)\s*\n\s*else:\s*\n\s*logger\.(?:warning|error)\()',
            patched, re.DOTALL)
        if progress_wait_pattern:
            inj = progress_wait_pattern.start(2)
            patched = patched[:inj] + "\n" + FEISHU_PROGRESS_WAIT.strip() + patched[inj:]
            changes.append("  ✓ Injected streaming card wait in send_progress_messages")
        else:
            changes.append("  ⚠ Could not find send_progress_messages wait injection point (non-critical)")
    else:
        changes.append("  ℹ Progress wait already present (skip)")

    # ── 4. Increase send_progress_messages timeout (10s → 30s) ───────
    if "wait_start < 30" not in patched:
        timeout_match = re.search(r"while _pm_time\.time\(\) - _wait_start < 10:", patched)
        if timeout_match:
            patched = patched.replace(
                "while _pm_time.time() - _wait_start < 10:",
                "while _pm_time.time() - _wait_start < 30:",
                1,
            )
            changes.append("  ✓ Increased pending timeout to 30s")
        else:
            changes.append("  ℹ Timeout already set (skip)")
    else:
        changes.append("  ℹ Timeout already 30s (skip)")

    # ── Write patched file ────────────────────────────────────────────
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
