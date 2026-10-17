"""Bounded per-turn display metrics from official lifecycle observations.

No response text, credentials, execution ownership or token estimates are kept.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
import re


RATE_MIN_OUTPUT_TOKENS = 20
RATE_MIN_SECONDS = 0.3
RATE_MAX_INVOCATION_SECONDS = 3600.0
_LABEL = re.compile(r"[A-Za-z0-9_./:+-]{1,192}\Z")


def metric_count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _epoch(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    number = float(value)  # type: ignore[arg-type]
    return number if isfinite(number) and number >= 0 else None


def invocation_generation_window(values: dict[str, object]) -> tuple[float, str] | None:
    """Seconds attributable to output for one model invocation, with its basis.

    ``decode`` spans first stream chunk to response end; non-streamed calls fall
    back to the full ``request`` duration, which includes time to first token.
    """
    ended = _epoch(values.get("ended_at"))
    first = _epoch(values.get("first_chunk_at"))
    if values.get("first_chunk_at") is not None and first is None:
        return None
    if ended is not None and first is not None:
        seconds, basis = ended - first, "decode"
    else:
        duration = _epoch(values.get("api_duration"))
        if duration is None:
            return None
        seconds, basis = duration, "request"
    if not (isfinite(seconds) and 0 < seconds < RATE_MAX_INVOCATION_SECONDS):
        return None
    return seconds, basis


def metric_label(value: object) -> str:
    if type(value) is not str or not _LABEL.fullmatch(value) or "://" in value:
        return ""
    return value


@dataclass
class TurnMetrics:
    started_at: float
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    has_usage: bool = False
    usage_complete: bool = True
    context_used: int | None = None
    context_limit: int | None = None
    # Output rate covers only model generation windows; tool and wait gaps between
    # invocations are never counted. Any unknown invocation disables the rate.
    gen_seconds: float = 0.0
    rate_complete: bool = True
    rate_request_basis: bool = False
    request_ids: set[str] = field(default_factory=set, repr=False)

    def observe_api(self, values: dict[str, object]) -> None:
        request_id = values.get("api_request_id")
        if type(request_id) is not str or not 0 < len(request_id) <= 256 or not request_id.strip():
            self.usage_complete = False
            self.rate_complete = False
            self.context_used = None
            return
        if request_id in self.request_ids:
            return
        if len(self.request_ids) >= 256:
            self.usage_complete = False
            self.rate_complete = False
            self.context_used = None
            return
        self.request_ids.add(request_id)
        self.context_used = None
        model = metric_label(values.get("response_model")) or metric_label(values.get("model"))
        if model:
            self.model = model
            self.provider = metric_label(values.get("provider"))
        # MoA: usage describes only the aggregator while timing spans the advisors.
        if values.get("moa_references"):
            self.rate_complete = False
        window = invocation_generation_window(values)
        if window is None:
            self.rate_complete = False
        usage = values.get("usage")
        if type(usage) is not dict:
            self.usage_complete = False
            self.rate_complete = False
            return
        prompt = metric_count(usage.get("prompt_tokens"))
        if prompt is None:
            base = metric_count(usage.get("input_tokens"))
            read = metric_count(usage.get("cache_read_tokens", 0))
            write = metric_count(usage.get("cache_write_tokens", 0))
            if base is not None and read is not None and write is not None:
                prompt = metric_count(base + read + write)
        output = metric_count(usage.get("output_tokens"))
        if prompt is not None:
            self.context_used = prompt
        if output is None:
            self.rate_complete = False
        if prompt is None or output is None:
            self.usage_complete = False
            return
        total_input = metric_count(self.input_tokens + prompt)
        total_output = metric_count(self.output_tokens + output)
        if total_input is None or total_output is None:
            self.usage_complete = False
            return
        self.input_tokens, self.output_tokens = total_input, total_output
        self.has_usage = True
        if window is not None and self.rate_complete:
            self.gen_seconds += window[0]
            self.rate_request_basis = self.rate_request_basis or window[1] == "request"

    def output_rate(self) -> dict[str, object] | None:
        """Exact output tokens per generation second, or None when unproven."""
        if not (self.has_usage and self.usage_complete and self.rate_complete):
            return None
        seconds = self.gen_seconds
        if self.output_tokens < RATE_MIN_OUTPUT_TOKENS or not (isfinite(seconds) and seconds >= RATE_MIN_SECONDS):
            return None
        rate = self.output_tokens / seconds
        if not isfinite(rate) or rate <= 0:
            return None
        return {"output_tps": rate, "basis": "request" if self.rate_request_basis else "decode"}

    def payload(self, now: float) -> dict[str, object]:
        result: dict[str, object] = {}
        if self.model:
            result["model"] = self.model
        if self.provider:
            result["provider"] = self.provider
        duration = now - self.started_at
        if isfinite(duration) and duration >= 0:
            result["duration"] = duration
        if self.has_usage and self.usage_complete:
            result["tokens"] = {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}
        rate = self.output_rate()
        if rate is not None:
            result["rate"] = rate
        context = {}
        if self.context_used is not None:
            context["used_tokens"] = self.context_used
        if self.context_limit is not None and self.context_limit > 0:
            context["max_tokens"] = self.context_limit
        if context:
            result["context"] = context
        return result
