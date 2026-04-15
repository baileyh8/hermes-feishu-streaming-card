"""
run_patch.py
============
Injects the Feishu streaming card pre-creation and finalize calls into
hermes-agent/gateway/run.py.

What it patches:
  1. _handle_message: pre-creates streaming card before agent starts
  2. run_agent finalize: calls finalize_streaming_card after agent completes
  3. send_progress_messages: increased pending timeout (30s)

Config (from config.yaml):
  feishu_streaming_card.greeting  — card header title
  feishu_streaming_card.enabled  — enable/disable
  feishu_streaming_card.pending_timeout — send_progress timeout (default: 30)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Code to inject: feishu streaming card pre-creation in _handle_message
# ─────────────────────────────────────────────────────────────────

FEISHU_PRECREATE_CARD = '''
        # ── Feishu Streaming Card: pre-create on message receipt ─────────────
        feishu_adapter = self.adapters.get(Platform.FEISHU)
        if source.platform.value == "feishu" and feishu_adapter is not None:
            from gateway.platforms.feishu_patch import _read_config, _get_default_greeting

            hermes_dir = self._hermes_dir or ""
            cfg = _read_config(hermes_dir)
            enabled = cfg.get("enabled", True)
            greeting = cfg.get("greeting", _get_default_greeting())

            if enabled:
                _model_name = _resolve_gateway_model()
                _user_display = getattr(source, "user_name", None) or ""
                feishu_adapter.set_streaming_greeting(greeting)
                feishu_adapter.set_streaming_model(_model_name)
                # Create streaming card IMMEDIATELY upon receiving user message.
                # This ensures thinking content goes into the card (not as a normal
                # message), even before the first tool call arrives.
                try:
                    import asyncio as _aio
                    state = await feishu_adapter.send_streaming_card(
                        chat_id=source.chat_id,
                        greeting=greeting,
                        subtitle=_model_name,
                        metadata=None,
                    )
                    if state:
                        state["_tool_count"] = 0
                        state["_tool_lines"] = []
                        state["_model"] = _model_name
                        state["_greeting"] = greeting
                        feishu_adapter._streaming_card[source.chat_id] = state
                        feishu_adapter.clear_streaming_pending(source.chat_id)
                        logger.info("[Feishu] Pre-created streaming card for chat_id=%s", source.chat_id)
                except Exception as _e:
                    logger.warning("[Feishu] Pre-create streaming card failed: %s (will create on first tool)", _e)
                    feishu_adapter.set_streaming_pending(source.chat_id)
'''


# ─────────────────────────────────────────────────────────────────
# Code to replace: finalize streaming card call block
# ─────────────────────────────────────────────────────────────────

FEISHU_FINALIZE_BLOCK = '''
            # Finalize streaming card: thinking_label→✅, collapse tools, write footer
            _elapsed = time.time() - _msg_start_time
            feishu_adapter = self.adapters.get(Platform.FEISHU)
            if source.platform.value == "feishu" and feishu_adapter is not None:
                try:
                    # Acquire the lock before finalizing to avoid race with concurrent send()
                    async with feishu_adapter._get_card_lock(source.chat_id):
                        # Extract result summary for thinking_content (strip footer/tags)
                        import re as _re
                        _raw = (response or "")
                        _clean = _re.sub(r"<think>.*?</think>", "", _raw, flags=_re.DOTALL)
                        _clean = _re.sub(r"<think>.*", "", _clean, flags=_re.DOTALL)
                        _clean = _re.sub(r"\\n苏菲\\s*·.*$", "", _clean)
                        _clean = _re.sub(r"\\nHermes\\s*·.*$", "", _clean)
                        _result_summary = _clean[:800].strip()
                        await feishu_adapter.finalize_streaming_card(
                            chat_id=source.chat_id,
                            model=_model_name,
                            elapsed=_elapsed,
                            in_t=agent_result.get("usage", {}).get("input_tokens", 0),
                            out_t=agent_result.get("usage", {}).get("output_tokens", 0),
                            cache_t=agent_result.get("usage", {}).get("cache_read_tokens", 0),
                            ctx_used=agent_result.get("usage", {}).get("input_tokens", 0),
                            ctx_limit=200_000,
                            result_summary=_result_summary,
                        )
                        logger.info("[Feishu] finalize_streaming_card done for chat_id=%s", source.chat_id)
                except Exception as _e:
                    logger.warning("[Feishu] finalize_streaming_card error: %s", _e)
'''


# ─────────────────────────────────────────────────────────────────
# Code to replace: pending set (fallback)
# ─────────────────────────────────────────────────────────────────

FEISHU_PENDING_FALLBACK = '''
        # For feishu: the streaming card was already pre-created in _handle_message.
        # No need to set streaming_pending here (only used as fallback).
        feishu_adapter = self.adapters.get(Platform.FEISHU)
        if feishu_adapter is not None and source.chat_id not in feishu_adapter._streaming_card:
            feishu_adapter.set_streaming_pending(source.chat_id)
'''


# ─────────────────────────────────────────────────────────────────
# Code to patch: send_progress_messages timeout (10s → configurable)
# ─────────────────────────────────────────────────────────────────

def patch_run_py(run_py_path: str, hermes_dir: str) -> list:
    """
    Apply all run.py patches for Feishu streaming card support.

    Returns list of (status, message) tuples.
    """
    results = []

    with open(run_py_path) as f:
        original = f.read()

    patched = original
    changes = []

    import re

    # ── 1. Inject pre-create card in _handle_message ─────────────────
    # Find the "Set Feishu streaming greeting & model" block and replace
    if "Feishu Streaming Card: pre-create on message receipt" not in patched:
        old_block = re.search(
            r"# Set Feishu streaming greeting & model before agent starts\s+"
            r"feishu_adapter = self\.adapters\.get\(Platform\.FEISHU\)\s+"
            r"if source\.platform\.value == .feishu. and feishu_adapter is not None:.*?"
            r"(?=# Get or create session)",
            patched,
            re.DOTALL,
        )
        if old_block:
            patched = patched.replace(old_block.group(0), FEISHU_PRECREATE_CARD.strip(), 1)
            changes.append("  ✓ Patched _handle_message with pre-create card")
        else:
            results.append(("FAIL", "Could not find feishu adapter setup block in _handle_message"))
            return results
    else:
        changes.append("  ℹ _handle_message already has pre-create card (skip)")

    # ── 2. Patch finalize_streaming_card call ────────────────────────
    if "Feishu Streaming Card: thinking_label→✅" not in patched:
        finalize_old = re.search(
            r"# Finalize streaming card:.*?"
            r"await feishu_adapter\.finalize_streaming_card\("
            r".*?"
            r"(?=\n            # (?:Save the full|if agent_failed_early))",
            patched,
            re.DOTALL,
        )
        if finalize_old:
            patched = patched.replace(finalize_old.group(0), FEISHU_FINALIZE_BLOCK.strip(), 1)
            changes.append("  ✓ Patched finalize_streaming_card call")
        else:
            results.append(("FAIL", "Could not find finalize_streaming_card call block"))
            return results
    else:
        changes.append("  ℹ finalize already patched (skip)")

    # ── 3. Patch pending fallback ────────────────────────────────────
    if "was already pre-created in _handle_message" not in patched:
        pending_old = re.search(
            r"# For feishu: signal that a streaming card is about to be created.*?"
            r"feishu_adapter\.set_streaming_pending\(source\.chat_id\)",
            patched,
            re.DOTALL,
        )
        if pending_old:
            patched = patched.replace(pending_old.group(0), FEISHU_PENDING_FALLBACK.strip(), 1)
            changes.append("  ✓ Patched pending fallback")
        else:
            results.append(("FAIL", "Could not find pending set block"))
            return results
    else:
        changes.append("  ℹ pending fallback already patched (skip)")

    # ── 4. Increase send_progress_messages timeout (10s → 30s) ───────
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
