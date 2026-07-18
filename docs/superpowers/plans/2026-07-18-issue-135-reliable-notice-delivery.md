# Issue #135 Reliable Notice Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make initial Feishu card delivery retry-safe and expose `delivered` / `not_sent` / `unknown` outcomes so system notices can fall back without silently disappearing or duplicating their original text.

**Architecture:** `FeishuClient` owns HTTP classification, stable-UUID retries, and a structured send result while retaining the existing `send_card() -> str` compatibility wrapper. `server.py` converts client results and failures into a small `CardDeliveryResult`, response JSON, metrics, and redacted diagnostics. `hook_runtime.py` uses only the validated response outcome to choose native original-text fallback, one generic uncertainty warning, or duplicate suppression.

**Tech Stack:** Python 3.9+, asyncio, aiohttp, aiohttp test server, pytest.

## Global Constraints

- Do not retry hook-to-sidecar `/events` requests.
- Retry only initial Feishu send/reply calls, at most three attempts, with the same UUID.
- Treat HTTP 429/502/503/504, connection errors, and timeouts as `unknown`; treat permanent request validation and non-retryable 4xx as `not_sent`.
- An unparseable sidecar response is `unknown`, never `not_sent`.
- A known `not_sent` notice may fall back to the original text; an `unknown` notice may emit only `⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。`.
- Do not put credentials, raw chat/message IDs, UUIDs, URLs, or message bodies in exceptions, logs, diagnostics, or `/health`.
- Preserve event-auth replay protection, topic reply routing, and existing PATCH retry behavior.

---

### Task 1: Structured Feishu errors and stable delivery UUIDs

**Files:**
- Modify: `hermes_feishu_card/feishu_client.py:14-216`
- Modify: `tests/unit/test_feishu_client.py`
- Modify: `tests/integration/test_feishu_client_http.py:24-170`

**Interfaces:**
- Produces: `FeishuAPIError(status_code, api_code, retryable, outcome, retry_after_seconds, retry_count)`.
- Produces: `FeishuSendResult(message_id: str, retry_count: int)`.
- Produces: `build_delivery_uuid(*, bot_id, chat_id, reply_to_message_id, session_key, delivery_kind) -> str`.
- Produces: `FeishuClient.send_card_delivery(chat_id, card, thread_id=None, reply_to_message_id=None, delivery_uuid=None) -> FeishuSendResult`.
- Preserves: `FeishuClient.send_card(chat_id, card, thread_id=None, reply_to_message_id=None, delivery_uuid=None) -> str` for CLI and existing direct callers.

- [ ] **Step 1: Write failing unit tests for the error object and UUID builder**

Add these tests:

```python
from hermes_feishu_card.feishu_client import (
    FeishuAPIError,
    build_delivery_uuid,
)


def test_delivery_uuid_is_stable_bounded_and_route_isolated():
    values = dict(
        bot_id="default",
        chat_id="oc_secret",
        reply_to_message_id="om_secret",
        session_key="profile:message-1",
        delivery_kind="notice",
    )
    first = build_delivery_uuid(**values)
    assert first == build_delivery_uuid(**values)
    assert first.startswith("hfc_")
    assert len(first) == 44
    assert first != build_delivery_uuid(**{**values, "bot_id": "sales"})
    assert "oc_secret" not in first
    assert "om_secret" not in first


def test_feishu_api_error_exposes_only_structured_safe_metadata():
    error = FeishuAPIError(
        "Feishu API HTTP failure",
        status_code=503,
        api_code=999,
        retryable=True,
        outcome="unknown",
        retry_after_seconds=1.5,
        retry_count=2,
    )
    assert error.status_code == 503
    assert error.api_code == 999
    assert error.retryable is True
    assert error.outcome == "unknown"
    assert error.retry_after_seconds == 1.5
    assert error.retry_count == 2
    assert "secret" not in str(error).lower()
```

- [ ] **Step 2: Run the new unit tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_feishu_client.py -q
```

Expected: collection/import failure because `build_delivery_uuid` and structured `FeishuAPIError` fields do not exist.

- [ ] **Step 3: Implement the structured types and UUID builder**

Add imports for `asyncio`, `hashlib.sha256`, and `Literal`. Implement these public internal types:

```python
DeliveryFailureOutcome = Literal["not_sent", "unknown"]


class FeishuAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        api_code: int | str | None = None,
        retryable: bool = False,
        outcome: DeliveryFailureOutcome = "not_sent",
        retry_after_seconds: float | None = None,
        retry_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.api_code = api_code
        self.retryable = retryable
        self.outcome = outcome
        self.retry_after_seconds = retry_after_seconds
        self.retry_count = retry_count


@dataclass(frozen=True)
class FeishuSendResult:
    message_id: str
    retry_count: int = 0


def build_delivery_uuid(
    *,
    bot_id: str,
    chat_id: str,
    reply_to_message_id: str,
    session_key: str,
    delivery_kind: str,
) -> str:
    raw = "\x1f".join(
        (bot_id, chat_id, reply_to_message_id, session_key, delivery_kind)
    ).encode("utf-8")
    return "hfc_" + sha256(raw).hexdigest()[:40]
```

Use `typing.Literal` on supported Python versions through the project's existing Python floor; do not add a dependency.

- [ ] **Step 4: Write failing HTTP tests for create/reply UUID payloads**

Extend the existing create and reply tests so each calls `send_card_delivery` with its existing positional arguments plus `delivery_uuid="hfc_" + "a" * 40`, and asserts:

```python
assert send_request[2]["uuid"] == "hfc_" + "a" * 40
assert reply_request[2]["uuid"] == "hfc_" + "a" * 40
```

Also add validation cases rejecting a blank UUID, a value longer than 50 characters, and non-string values with `ValueError("delivery_uuid")`.

- [ ] **Step 5: Run the focused HTTP tests and verify RED**

Run:

```bash
python -m pytest \
  tests/integration/test_feishu_client_http.py::test_send_card_fetches_token_and_posts_interactive_message \
  tests/integration/test_feishu_client_http.py::test_send_card_replies_in_thread_when_reply_anchor_present -q
```

Expected: FAIL because `send_card_delivery` is missing and no UUID is included.

- [ ] **Step 6: Implement `send_card_delivery` and keep the compatibility wrapper**

Refactor the current `send_card` body into:

```python
async def send_card(
    self,
    chat_id: str,
    card: Dict[str, Any],
    thread_id: Optional[str] = None,
    reply_to_message_id: Optional[str] = None,
    delivery_uuid: Optional[str] = None,
) -> str:
    result = await self.send_card_delivery(
        chat_id,
        card,
        thread_id=thread_id,
        reply_to_message_id=reply_to_message_id,
        delivery_uuid=delivery_uuid,
    )
    return result.message_id


async def send_card_delivery(
    self,
    chat_id: str,
    card: Dict[str, Any],
    thread_id: Optional[str] = None,
    reply_to_message_id: Optional[str] = None,
    delivery_uuid: Optional[str] = None,
) -> FeishuSendResult:
    if delivery_uuid is not None:
        if not isinstance(delivery_uuid, str) or not delivery_uuid.strip():
            raise ValueError("delivery_uuid must be a non-empty string")
        if len(delivery_uuid) > 50:
            raise ValueError("delivery_uuid must not exceed 50 characters")
    # Build the existing payload and add uuid only when provided.
    # The retry loop is added in Task 2; return retry_count=0 here.
```

For create requests add `uuid` to the existing body. For reply requests add the same field next to `reply_in_thread`.

- [ ] **Step 7: Run Task 1 tests and commit**

Run:

```bash
python -m pytest tests/unit/test_feishu_client.py tests/integration/test_feishu_client_http.py -q
```

Expected: PASS.

Commit:

```bash
git add hermes_feishu_card/feishu_client.py tests/unit/test_feishu_client.py tests/integration/test_feishu_client_http.py
git commit -m "feat: add idempotent Feishu delivery identifiers"
```

### Task 2: Bounded retries with safe outcome classification

**Files:**
- Modify: `hermes_feishu_card/feishu_client.py`
- Modify: `tests/integration/test_feishu_client_http.py`

**Interfaces:**
- Consumes: `FeishuAPIError` and `FeishuSendResult` from Task 1.
- Produces: `send_card_delivery()` performs no more than three attempts and records `retry_count`.
- Produces: `_request_json(method, path, token=None, params=None, json_body=None, timeout_seconds=None)` classifies errors without leaking response text.

- [ ] **Step 1: Write failing classification tests**

Create parametrized aiohttp tests for 429, 502, 503, 504 and 400. For each call `_request_json()` through `send_card_delivery()` and assert:

```python
assert error.status_code == expected_status
assert error.retryable is expected_retryable
assert error.outcome == expected_outcome
```

Add a connection-reset or unused-port case asserting `retryable=True`, `outcome="unknown"`, and a message containing only the exception class, not URL/body/token values.

- [ ] **Step 2: Run classification tests and verify RED**

Run:

```bash
python -m pytest tests/integration/test_feishu_client_http.py -k "classifies or network_failure" -q
```

Expected: FAIL because current exceptions have no structured fields.

- [ ] **Step 3: Implement HTTP/network classification**

In `_request_json`:

```python
retryable_statuses = {429, 502, 503, 504}
if response.status >= 400:
    retryable = response.status in retryable_statuses
    raise FeishuAPIError(
        "Feishu API HTTP failure",
        status_code=response.status,
        api_code=_safe_api_code(payload.get("code")),
        retryable=retryable,
        outcome="unknown" if retryable else "not_sent",
        retry_after_seconds=_retry_after_seconds(response.headers.get("Retry-After")),
    )
```

Classify `aiohttp.ClientError`, `asyncio.TimeoutError`, and non-JSON retryable HTTP responses as `unknown/retryable`. Treat missing `message_id` after an otherwise successful response as `unknown` because delivery may have occurred. Do not include the raw API `msg`, URL, payload, token, or identifiers in the exception string.

- [ ] **Step 4: Write a failing three-attempt idempotency test**

Use an aiohttp handler that returns 503 twice and succeeds on the third request. Monkeypatch `asyncio.sleep` in the module to collect delays. Assert:

```python
result = await client.send_card_delivery(
    "oc_abc",
    {"schema": "2.0"},
    delivery_uuid="hfc_" + "b" * 40,
)
assert result.message_id == "om_message_1"
assert result.retry_count == 2
assert attempts == 3
assert request_uuids == ["hfc_" + "b" * 40] * 3
assert delays == [0.4, 1.2]
```

Add a permanent-400 case asserting one attempt, and an exhausted-503 case asserting `error.outcome == "unknown"` and `error.retry_count == 2`.

- [ ] **Step 5: Run retry tests and verify RED**

Run:

```bash
python -m pytest tests/integration/test_feishu_client_http.py -k "retries_initial_send" -q
```

Expected: FAIL with only one HTTP attempt.

- [ ] **Step 6: Implement the retry loop**

Use constants:

```python
_SEND_MAX_ATTEMPTS = 3
_SEND_RETRY_DELAYS_SECONDS = (0.4, 1.2)
_MAX_RETRY_AFTER_SECONDS = 2.0
```

Wrap token acquisition plus create/reply request in the attempt loop. Reuse the same validated `delivery_uuid`. Sleep only after a retryable error and before another attempt. The delay is `min(max(retry_after, configured_delay), 2.0)` when `Retry-After` is valid, otherwise the configured delay. Before re-raising the final exception, set `retry_count = attempt_index` on a newly constructed `FeishuAPIError` with the same safe fields.

Do not add retries to `update_card_message()`.

- [ ] **Step 7: Run Task 2 tests and commit**

Run:

```bash
python -m pytest tests/unit/test_feishu_client.py tests/integration/test_feishu_client_http.py -q
```

Expected: PASS.

Commit:

```bash
git add hermes_feishu_card/feishu_client.py tests/integration/test_feishu_client_http.py
git commit -m "fix: retry initial Feishu delivery safely"
```

### Task 3: Sidecar delivery outcomes, metrics, and diagnostics

**Files:**
- Modify: `hermes_feishu_card/metrics.py:7-39`
- Modify: `hermes_feishu_card/server.py:154-245,293-349,1971-2269,2649-2688`
- Modify: `tests/integration/test_server.py`

**Interfaces:**
- Consumes: real clients may expose `send_card_delivery`; fake/legacy clients may expose only `send_card`.
- Produces: `CardDeliveryResult(message_id, outcome, retry_count, error_kind)` in `server.py`.
- Extends: `_send_card_for_app(app, chat_id, card, bot_id, thread_id=None, reply_to_message_id=None, delivery_key="", delivery_kind="chat") -> CardDeliveryResult`.
- Produces: response field `delivery: {"outcome": "delivered|not_sent|unknown"}` for initial delivery decisions.
- Produces metrics: `feishu_send_retries`, `feishu_send_unknown_outcomes`, `notice_native_fallbacks`, `notice_uncertain_warnings`.

- [ ] **Step 1: Write failing server tests for all three outcomes**

Add fake clients:

```python
class PermanentFailureClient(FakeFeishuClient):
    async def send_card_delivery(self, *args, **kwargs):
        raise FeishuAPIError(
            "permanent failure",
            status_code=400,
            retryable=False,
            outcome="not_sent",
        )


class UnknownFailureClient(FakeFeishuClient):
    async def send_card_delivery(self, *args, **kwargs):
        raise FeishuAPIError(
            "transient failure",
            status_code=503,
            retryable=True,
            outcome="unknown",
            retry_count=2,
        )
```

Post an independent `system.notice` and assert the failure response body has the exact outcome; a successful fake returns `delivered`. Assert `/health` includes retry/unknown counters and `diagnostics.last_send_error` contains only outcome, error class, status/api code, and a hashed bot/profile alias.

- [ ] **Step 2: Run the new server tests and verify RED**

Run:

```bash
python -m pytest tests/integration/test_server.py -k "delivery_outcome or send_error_diagnostics" -q
```

Expected: FAIL because send failures currently collapse to `None` and generic HTTP 502.

- [ ] **Step 3: Implement `CardDeliveryResult` and client compatibility adapter**

Add:

```python
@dataclass(frozen=True)
class CardDeliveryResult:
    message_id: str | None
    outcome: str
    retry_count: int = 0
    error_kind: str = ""

    @property
    def delivered(self) -> bool:
        return self.outcome == "delivered" and bool(self.message_id)
```

In `_send_card_for_app`, derive one UUID with `build_delivery_uuid(bot_id=bot_id or "default", chat_id=chat_id, reply_to_message_id=reply_to_message_id or "", session_key=delivery_key, delivery_kind=delivery_kind)`. If the client has callable `send_card_delivery`, use it; otherwise call the legacy `send_card` without new keywords and wrap the string as a delivered result. Catch `FeishuAPIError` separately; catch an unknown exception as outcome `unknown`.

Increment logical attempts once, successes only for delivered, failures for both failure outcomes, retries by `retry_count`, and unknown outcomes only for `unknown`.

- [ ] **Step 4: Add safe response/diagnostic helpers**

Implement:

```python
def _delivery_payload(result: CardDeliveryResult) -> dict[str, str]:
    return {"outcome": result.outcome}


def _record_send_error(app, result, *, bot_id, status_code=None, api_code=None):
    app[DIAGNOSTICS_KEY]["last_send_error"] = {
        "outcome": result.outcome,
        "error_kind": result.error_kind,
        "bot_hash": _diagnostic_id_hash(bot_id or "default"),
        "status_code": status_code,
        "api_code": api_code,
    }
```

Omit `None` values. Never store the exception message.

Update all three initial-send callers (`_send_command_card`, `message.started`, and `SESSION_CREATING_EVENTS`) to consume `CardDeliveryResult`. On `/events` failure return HTTP 502 with the existing safe error label plus `delivery`; command-card delivery returns its existing failure result object. On `/events` success return `delivery.outcome=delivered`.

- [ ] **Step 5: Add the four metrics**

Add integer fields to `SidecarMetrics`. For independent/session system notices, increment `notice_native_fallbacks` on `not_sent` and `notice_uncertain_warnings` on `unknown` before returning the response. These counters describe the fallback decision requested from the hook; they do not claim native Feishu delivery succeeded.

- [ ] **Step 6: Run server tests and commit**

Run:

```bash
python -m pytest tests/integration/test_server.py tests/unit/test_metrics.py -q
```

If `tests/unit/test_metrics.py` does not exist, run only `tests/integration/test_server.py`; do not create a one-assertion file solely for dataclass serialization.

Expected: PASS.

Commit:

```bash
git add hermes_feishu_card/metrics.py hermes_feishu_card/server.py tests/integration/test_server.py
git commit -m "feat: expose card delivery outcomes"
```

### Task 4: System-notice native fallback policy

**Files:**
- Modify: `hermes_feishu_card/hook_runtime.py:2285-2362,2479-2523`
- Modify: `tests/unit/test_hook_runtime.py:1064-1145,1680-1830`
- Modify: `tests/integration/test_hook_runtime_integration.py`

**Interfaces:**
- Consumes: sidecar response `delivery.outcome`.
- Produces: `_hfc_notice_delivery_outcome(result) -> delivered|not_sent|unknown`.
- Produces: original text fallback only for `not_sent`, fixed generic warning only for `unknown`.

- [ ] **Step 1: Replace the timeout-suppression test with explicit outcome tests**

Add these tests around `_hfc_send_with_native_command_result_card` (the recognized notice classifier is monkeypatched so the tests target only fallback policy):

```python
@pytest.mark.parametrize(
    ("error", "expected_content"),
    [
        ("delivery_outcome=not_sent", "原始系统通知"),
        (
            "delivery_outcome=unknown",
            "⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。",
        ),
        (
            "sidecar response invalid",
            "⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。",
        ),
    ],
)
async def test_system_notice_delivery_outcome_selects_safe_native_fallback(
    monkeypatch, error, expected_content
):
    calls = []

    class Adapter:
        pass

    async def original(self, chat_id, content, reply_to=None, metadata=None):
        calls.append((chat_id, content, reply_to, metadata))
        return SimpleNamespace(success=True, message_id="native-1", error="")

    async def failed_notice(self, **kwargs):
        return SimpleNamespace(success=False, message_id="", error=error)

    Adapter._hfc_original_send = original
    monkeypatch.setattr(
        hook_runtime, "_hfc_send_system_notice_card", failed_notice
    )
    monkeypatch.setattr(
        hook_runtime,
        "_hfc_classify_system_notice",
        lambda content: {"notice_kind": "system"},
    )

    result = await hook_runtime._hfc_send_with_native_command_result_card(
        Adapter(),
        "oc_test",
        "原始系统通知",
        reply_to="om_test",
        metadata={"thread_id": "omt_test"},
    )

    assert result.success is True
    assert calls == [
        ("oc_test", expected_content, "om_test", {"thread_id": "omt_test"})
    ]
```

Also retain the delivered case proving no native call occurs.

- [ ] **Step 2: Run the focused hook tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_hook_runtime.py -k "system_notice and (not_sent or unknown or unparseable)" -q
```

Expected: FAIL because all recognized notice failures are currently suppressed.

- [ ] **Step 3: Implement strict outcome parsing**

Add:

```python
_NOTICE_UNCERTAIN_WARNING = (
    "⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。"
)


def _hfc_notice_delivery_outcome(result: Any) -> str:
    if not isinstance(result, dict):
        return "unknown"
    delivery = result.get("delivery")
    if not isinstance(delivery, dict):
        return "unknown"
    outcome = delivery.get("outcome")
    if outcome in {"delivered", "not_sent", "unknown"}:
        return outcome
    return "unknown"
```

Change `_hfc_send_system_notice_card` so a delivered response returns success. For failures, return a local result whose safe `error` includes only `delivery_outcome=<value>`; do not include the response object.

Add `_hfc_send_result_delivery_outcome(result)` which accepts only exact errors `delivery_outcome=not_sent` and `delivery_outcome=unknown`; every absent or different value resolves to `unknown`.

- [ ] **Step 4: Implement fallback selection in the adapter wrapper**

In `_hfc_send_with_native_command_result_card`, when content is a classified system notice:

```python
outcome = _hfc_send_result_delivery_outcome(notice_result)
if outcome == "not_sent" and callable(original):
    return await original(self, chat_id, content, reply_to=reply_to, metadata=metadata)
if callable(original):
    return await original(
        self,
        chat_id,
        _NOTICE_UNCERTAIN_WARNING,
        reply_to=reply_to,
        metadata=metadata,
    )
return _send_result(False, error="original Feishu send unavailable")
```

The generic warning goes directly to `_hfc_original_send`, so it never re-enters the notice classifier. Preserve the current non-system fail-open path.

- [ ] **Step 5: Add integration coverage with the local sidecar**

In `tests/integration/test_hook_runtime_integration.py`, use sidecar fake clients that raise `not_sent` and `unknown` errors. Execute the installed hook wrapper and assert the native adapter calls and `/health` counters match the intended branch. Include a topic reply case retaining `reply_to` metadata.

- [ ] **Step 6: Run runtime/server integration tests and commit**

Run:

```bash
python -m pytest \
  tests/unit/test_hook_runtime.py \
  tests/integration/test_hook_runtime_integration.py \
  tests/integration/test_server.py -q
```

Expected: PASS.

Commit:

```bash
git add hermes_feishu_card/hook_runtime.py tests/unit/test_hook_runtime.py tests/integration/test_hook_runtime_integration.py
git commit -m "fix: make notice fallback outcome-aware"
```

### Task 5: Documentation, regression gate, and release-A readiness

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/wiki/event-flow.md`
- Modify: `docs/wiki/maintenance-guide.md`
- Modify: `docs/wiki/feishu-acceptance.md`
- Modify: `README.md`
- Modify: `README.en.md`
- Test: `tests/unit/test_docs.py`

**Interfaces:**
- Documents: retries occur at Feishu initial delivery, not `/events`.
- Documents: explicit fallback/unknown semantics and the four health metrics.

- [ ] **Step 1: Write failing documentation assertions**

Extend `tests/unit/test_docs.py` to require the maintainer docs to mention `delivery_uuid`, `not_sent`, `unknown`, and the generic-warning/no-original-duplication rule.

- [ ] **Step 2: Run docs tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_docs.py -q
```

Expected: FAIL because the new operational contract is absent.

- [ ] **Step 3: Update docs and acceptance checklist**

Document:

- stable UUID retry for create and reply;
- three attempts only for retryable initial delivery errors;
- `not_sent` original-text fallback versus `unknown` generic warning;
- `/health` counter meanings and redaction;
- real smoke steps for ordinary notice and topic notice with zero duplicate gray originals.

Do not claim that a generic warning is guaranteed when Feishu itself is entirely unavailable.

- [ ] **Step 4: Run focused and full gates**

Run:

```bash
python -m pytest \
  tests/unit/test_feishu_client.py \
  tests/integration/test_feishu_client_http.py \
  tests/unit/test_hook_runtime.py \
  tests/integration/test_hook_runtime_integration.py \
  tests/integration/test_server.py \
  tests/unit/test_docs.py -q
python -m pytest -q
git diff --check
```

Expected: all tests pass and `git diff --check` prints nothing.

- [ ] **Step 5: Commit release-A implementation documentation**

```bash
git add CHANGELOG.md README.md README.en.md docs/wiki tests/unit/test_docs.py
git commit -m "docs: document reliable notice delivery"
```

- [ ] **Step 6: Perform the real Feishu smoke before version/tag work**

Using the project's Feishu CLI and no Computer Use, verify:

1. independent system notice succeeds;
2. topic reply notice stays in the topic;
3. a controlled first-two-503 test creates one card with one UUID;
4. permanent failure chooses one original-text fallback;
5. unknown outcome chooses one generic warning and zero original-text duplicates;
6. `/health` contains no raw IDs, UUIDs, message text, URL, token, or secret.

Record only redacted evidence in `docs/wiki/feishu-acceptance.md`. Version bump, tag, release assets, public install verification, and issue reply belong to the release plan after this implementation gate passes.
