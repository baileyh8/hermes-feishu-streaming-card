"""Exact known notice templates; no locale loading, route selection or I/O.

Busy en/zh wording comes from Hermes e06df9c2eae6901f4c88f464e0ae6e753c0428b8
gateway/run_busy.py and locales/{en,zh}.yaml. Unknown translations stay visible.
"""
from __future__ import annotations

import re


# Compatibility names remain exported by hook_runtime for installed callers.
BUSY_REDIRECT_ACK_PREFIX = "↪ Redirected current run"
BUSY_REDIRECT_ACK_RECALL_SECONDS = 15.0
BUSY_INTERRUPT_ACK_PREFIX = "⚡ Interrupting current task"
BUSY_INTERRUPT_ACK_RECALL_SECONDS = 15.0
BUSY_STEER_ACK_PREFIX = "⏩ Steered into current run"
BUSY_STEER_ACK_RECALL_SECONDS = 15.0
STATUS_NOTICE_PREFIX = "⏳"
STATUS_NOTICE_RECALL_SECONDS = 15.0

# Full head/detail/tail matches exclude queue demotions and onboarding suffixes.
# Details are native single-line activity summaries, not arbitrary trailing text.
_BUSY_TEMPLATES = (
    (BUSY_REDIRECT_ACK_PREFIX, ". I'll adjust using your correction.", "en"),
    (BUSY_INTERRUPT_ACK_PREFIX, ". I'll respond to your message shortly.", "en"),
    (BUSY_STEER_ACK_PREFIX, ". Your message arrives after the next tool call.", "en"),
    ("⏩ Steered into current run and its active subagent(s)",
     ". Your message arrives after their next tool call.", "en"),
    ("↪ 已重定向当前运行", "。我会根据你的修正进行调整。", "zh"),
    ("⚡ 正在中断当前任务", "。我很快就会回复你的消息。", "zh"),
    ("⏩ 已引导至当前运行", "。你的消息将在下一次工具调用后送达。", "zh"),
    ("⏩ 已引导至当前运行及其活跃的子代理", "。你的消息将在它们下一次工具调用后送达。", "zh"),
)
_BUSY_PATTERNS = tuple(re.compile(
    re.escape(head) + (r"(?: \([^\r\n]*\))?" if language == "en" else r"(?: （[^\r\n]*）)?")
    + re.escape(tail)
) for head, tail, language in _BUSY_TEMPLATES)

# Keep the previous English one-shot status shapes. Only the known simplified
# Chinese compression head is added; no guessed translations or emoji wildcard.
_STATUS_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"⏳ (?:Compressing context|Waiting for approval|tool execution timed out; retrying)(?:\.\.\.)?",
    r"⏳ 正在压缩上下文(?:\.\.\.)?",
    r"⏳ Retrying in \d+(?:\.\d+)?s \(attempt \d+/\d+\)(?:\.\.\.)?",
    r"⏳ loading [^\r\n]+ into memory — \d+(?:\.\d+)?%[^\r\n]*",
    r"⏳ waiting on [^\r\n]+ — (?:no stream output for \d+s[^\r\n]*|retrying in \d+s \(attempt \d+/\d+\))",
))


# Hermes e06df9c conversation_compression.py:106/78. Feishu has no
# send_or_update_status: each confirmed PRE/DONE send is a separate message.
# Native {:,} nonnegative integers use ASCII digits and groups of three; reject
# guessed punctuation, translations, progress heartbeats and appended guidance.
_NATIVE_GROUPED_INTEGER = r"(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})*)"
_COMPRESSION_NOTICE_PATTERNS = (
    re.compile(
        r"📦 Preflight compression: ~" + _NATIVE_GROUPED_INTEGER
        + r" tokens >= " + _NATIVE_GROUPED_INTEGER
        + r" threshold\. This may take a moment\."
    ),
    re.compile(re.escape("✓ Context compaction complete — continuing turn...")),
)


def is_transient_busy_ack(content: object) -> bool:
    """Recognize a complete receipt; prefix similarity never authorizes recall."""
    return isinstance(content, str) and any(pattern.fullmatch(content) for pattern in _BUSY_PATTERNS)


def transient_notice_recall_seconds(content: object) -> float | None:
    """Known one-shot receipts/status expire after 15s; everything else stays.

    Working heartbeats keep their native edit target and cleanup. Subagent and
    compression busy demotions contain important queue instructions and also stay.
    """
    if is_transient_busy_ack(content):
        return BUSY_REDIRECT_ACK_RECALL_SECONDS
    if isinstance(content, str) and any(
        pattern.fullmatch(content) for pattern in (*_STATUS_PATTERNS, *_COMPRESSION_NOTICE_PATTERNS)
    ):
        return STATUS_NOTICE_RECALL_SECONDS
    return None
