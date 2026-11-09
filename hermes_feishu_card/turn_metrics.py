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
    try:
        number = float(value)  # type: ignore[arg-type]
    except OverflowError:
        return None
    return number if isfinite(number) and number >= 0 else None


def invocation_generation_window(values: dict[str, object]) -> tuple[float, str] | None:
    """Seconds attributable to output for one model invocation, with its basis.

    ``decode`` is the historical wire name for an observed stream window, from
    the first accepted chunk/event (possibly role/reasoning/lifecycle) to end.
    ``request`` uses the full request duration, including first-byte and retry waits.
    """
    started = _epoch(values.get("started_at"))
    ended = _epoch(values.get("ended_at"))
    first = _epoch(values.get("first_chunk_at"))
    for name, parsed in (("started_at", started), ("ended_at", ended), ("first_chunk_at", first)):
        if values.get(name) is not None and parsed is None:
            return None
    if first is not None and ended is None:
        return None
    if started is not None and (
        ended is not None and ended < started or first is not None and first < started
    ):
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


def completion_output_rate(value: object) -> tuple[float, str] | None:
    """Validate live and checkpoint display rates at every presentation boundary."""
    if type(value) is not dict:
        return None
    rate = _epoch(value.get("output_tps"))
    basis = value.get("basis")
    if rate is None or not 0 < rate < 1_000_000 or basis not in ("decode", "request"):
        return None
    return rate, basis


def retained_output_rate(tokens: object) -> tuple[float, str] | None:
    if type(tokens) is not dict:
        return None
    count = metric_count(tokens.get("output_tokens"))
    if count is None or count < RATE_MIN_OUTPUT_TOKENS:
        return None
    return completion_output_rate({"output_tps": tokens.get("output_tps"), "basis": tokens.get("rate_basis")})


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
    rate_route: tuple[str, str] | None = None
    # Sticky per-turn display attribution only. Never persisted in CardSession.
    route_mixed: bool = False
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
        route = (model, metric_label(values.get("provider")))
        if self.rate_route is not None and route != self.rate_route:
            self.rate_complete = False
            self.route_mixed = True
        else:
            # Keep the first observed route so mixed-route turns cannot be
            # attributed to whichever provider happened to answer last.
            self.rate_route = route
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
        """Observed-window rate using exact provider tokens, or None when unproven."""
        if not (self.has_usage and self.usage_complete and self.rate_complete):
            return None
        seconds = self.gen_seconds
        if self.output_tokens < RATE_MIN_OUTPUT_TOKENS or not (isfinite(seconds) and seconds >= RATE_MIN_SECONDS):
            return None
        rate = self.output_tokens / seconds
        if completion_output_rate({"output_tps": rate, "basis": "decode"}) is None:
            return None
        return {"output_tps": rate, "basis": "request" if self.rate_request_basis else "decode"}

    def payload(self, now: float) -> dict[str, object]:
        result: dict[str, object] = {}
        route = (self.model, self.provider)
        route_consistent = (
            not self.route_mixed
            and (self.rate_route is None or route == self.rate_route)
        )
        observed_api_route = self.rate_route is not None
        if self.model:
            if not observed_api_route and "/" in self.model:
                # A pre-LLM initial model with zero API observations is only a
                # display label; its provider prefix cannot authorize quota.
                result["model"] = self.model.split("/", 1)[-1].strip() or "Unknown"
            else:
                # A mixed-route turn must not leave a qualified label or a last
                # provider behind as if the whole turn used that account.
                result["model"] = (
                    self.model if route_consistent
                    else self.model.split("/", 1)[-1].strip() or "Unknown"
                )
        if self.provider and route_consistent and observed_api_route:
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
