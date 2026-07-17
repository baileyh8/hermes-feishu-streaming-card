# Event Transport Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep existing loopback installations compatible while making every explicitly enabled non-loopback sidecar require authenticated `/events` requests.

**Architecture:** Reuse the private `operations.transport.key` already shared by the Hermes runtime and sidecar. Add domain-separated HMAC request proofs in a focused `event_auth.py` module; the hook signs `/events`, the sidecar verifies before parsing events, and the runner rejects non-loopback binding unless the operator explicitly opts in. Loopback continues accepting unsigned requests for upgrade compatibility.

**Tech Stack:** Python 3.9+, `aiohttp`, `urllib.request`, HMAC-SHA256, pytest.

## Global Constraints

- Do not edit an installed Hermes `gateway/run.py` by hand; installer changes remain routed through `hermes_feishu_card/install/patcher.py`.
- Unknown hook paths remain fail-open, but authenticated non-loopback `/events` verification fails closed.
- Do not write the root secret or request signature into config, env files, cards, health output, logs, or diagnostics.
- Preserve current loopback behavior for existing installations.
- A non-loopback listener is opt-in and must never accept an unsigned event.
- Do not turn the in-process session model into shared or distributed storage.

---

### Task 1: Event proof primitive

**Files:**
- Create: `hermes_feishu_card/event_auth.py`
- Modify: `tests/unit/test_operations_transport.py`

**Interfaces:**
- Consumes: the existing 32-byte root returned by `ensure_transport_root_secret(...)` or `read_transport_root_secret(...)`.
- Produces: `sign_event_request(secret, body, timestamp, nonce) -> dict[str, str]`, `EventProofVerifier.verify(headers, body) -> None`, and `is_loopback_host(host) -> bool`.

- [x] **Step 1: Write failing tests for signed-body verification, wrong secret, stale timestamp, replay, and host classification**

```python
def test_event_proof_binds_raw_body_and_rejects_replay():
    body = b'{"event":"message.started"}'
    headers = sign_event_request(b"r" * 32, body, timestamp=100, nonce="nonce-1234567890")
    verifier = EventProofVerifier(b"r" * 32, now=lambda: 100.0)
    verifier.verify(headers, body)
    with pytest.raises(EventAuthenticationError, match="replayed"):
        verifier.verify(headers, body)
```

- [x] **Step 2: Run the new unit tests and confirm import/behavior failures**

Run: `python -m pytest tests/unit/test_operations_transport.py -q`

Expected: FAIL because `hermes_feishu_card.event_auth` does not exist.

- [x] **Step 3: Implement the minimal domain-separated proof and bounded nonce verifier**

```python
signature = hmac.new(
    secret,
    f"hfc-event-v1\0{timestamp}\0{nonce}\0{sha256(body).hexdigest()}".encode(),
    sha256,
).hexdigest()
```

- [x] **Step 4: Run the unit tests and confirm they pass**

Run: `python -m pytest tests/unit/test_operations_transport.py -q`

Expected: PASS.

### Task 2: Sidecar verification and observable rejection

**Files:**
- Modify: `hermes_feishu_card/server.py`
- Modify: `hermes_feishu_card/metrics.py`
- Modify: `tests/integration/test_server.py`

**Interfaces:**
- Consumes: `EventProofVerifier` and the existing `operations_transport_root_secret` passed to `create_app(...)`.
- Produces: `create_app(..., event_auth_required: bool = False)`, authenticated `_events`, `metrics.event_auth_rejections`, and `/health.event_auth_required`.

- [x] **Step 1: Write failing integration tests**

Cover unsigned `401`, wrong signature `401`, signed success, replay `401`, generic non-secret error text, rejection metrics, and loopback-compatible unsigned success when auth is not required.

- [x] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/integration/test_server.py -k 'event_auth or health' -q`

Expected: FAIL because `create_app` has no `event_auth_required` behavior.

- [x] **Step 3: Verify proof before event JSON/schema processing**

```python
if request.app[EVENT_AUTH_REQUIRED_KEY]:
    body = await request.read()
    try:
        request.app[EVENT_AUTH_VERIFIER_KEY].verify(request.headers, body)
    except EventAuthenticationError:
        metrics.events_rejected += 1
        metrics.event_auth_rejections += 1
        return web.json_response(
            {"ok": False, "error": "event authentication failed"}, status=401
        )
```

- [x] **Step 4: Run focused server tests and confirm GREEN**

Run: `python -m pytest tests/integration/test_server.py -k 'event_auth or health' -q`

Expected: PASS.

### Task 3: Hook signs `/events` fail-open

**Files:**
- Modify: `hermes_feishu_card/hook_runtime.py`
- Modify: `tests/unit/test_hook_runtime.py`
- Modify: `tests/integration/test_hook_runtime_integration.py`

**Interfaces:**
- Consumes: `read_transport_root_secret()` and `sign_event_request(...)`.
- Produces: signed headers only when the request path ends with `/events`; missing/insecure root keeps the existing unsigned fail-open request.

- [x] **Step 1: Write failing request-header tests**

Assert that `/events` carries a valid proof when the state secret exists, `/commands` does not receive the event proof, and missing/insecure root secret leaves the request unsigned without raising into Hermes.

- [x] **Step 2: Run focused hook tests and verify RED**

Run: `python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_hook_runtime_integration.py -k 'event_auth or post_json' -q`

Expected: FAIL because `/events` requests contain only `Content-Type`.

- [x] **Step 3: Add the minimal event-only header builder**

```python
def _post_headers(url: str, body: bytes) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if parse.urlsplit(url).path.rstrip("/").endswith("/events"):
        secret = read_transport_root_secret()
        if secret is not None:
            headers.update(sign_event_request(secret, body))
    return headers
```

- [x] **Step 4: Run focused hook and hook/server integration tests**

Run: `python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_hook_runtime_integration.py -k 'event_auth or post_json' -q`

Expected: PASS.

### Task 4: Secure non-loopback startup policy

**Files:**
- Modify: `hermes_feishu_card/config.py`
- Modify: `hermes_feishu_card/runner.py`
- Modify: `config.yaml.example`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_runner.py`
- Modify: `tests/integration/test_cli_process.py`

**Interfaces:**
- Consumes: `is_loopback_host(host)` and `server.allow_non_loopback`.
- Produces: backward-compatible loopback startup; non-loopback refusal unless explicitly enabled; required event authentication for every enabled non-loopback listener.

- [x] **Step 1: Write failing startup-policy tests**

Cover IPv4/IPv6/localhost loopback, `0.0.0.0` refusal by default, explicit non-loopback opt-in, missing root-secret refusal, and propagation of `event_auth_required=True` into `create_app`.

- [x] **Step 2: Run focused runner/config tests and verify RED**

Run: `python -m pytest tests/unit/test_config.py tests/unit/test_runner.py tests/integration/test_cli_process.py -q`

Expected: FAIL because non-loopback listeners are currently accepted unconditionally.

- [x] **Step 3: Implement startup policy**

```python
non_loopback = not is_loopback_host(str(server["host"]))
if non_loopback and not server.get("allow_non_loopback", False):
    raise ValueError("non-loopback sidecar binding requires server.allow_non_loopback: true")
if non_loopback and operations_transport_root_secret is None:
    raise RuntimeError("non-loopback sidecar binding requires event authentication")
```

- [x] **Step 4: Run focused policy/process tests and confirm GREEN**

Run: `python -m pytest tests/unit/test_config.py tests/unit/test_runner.py tests/integration/test_cli_process.py -q`

Expected: PASS.

### Task 5: Current architecture and security documentation

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/architecture.en.md`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/user-guide.en.md`
- Modify: `README-install.md`
- Modify: `config.yaml.example`
- Modify: `tests/unit/test_docs.py`

**Interfaces:**
- Consumes: the implemented loopback/non-loopback behavior.
- Produces: a current V4 architecture description and an explicit security/deployment matrix.

- [x] **Step 1: Add failing documentation assertions**

Assert that architecture no longer says real Feishu integration is pending and that README/user guide/config document loopback trust, `allow_non_loopback`, mandatory event proof, and no public unauthenticated exposure.

- [x] **Step 2: Run documentation tests and verify RED**

Run: `python -m pytest tests/unit/test_docs.py -q`

Expected: FAIL until the documentation is updated.

- [x] **Step 3: Update Chinese and English documentation together**

Describe the current V4 flow, state lifecycle, endpoint trust boundary, Docker single-container behavior, and non-loopback opt-in requirements without exposing any secret values.

- [x] **Step 4: Run documentation tests and confirm GREEN**

Run: `python -m pytest tests/unit/test_docs.py tests/unit/test_package_metadata.py -q`

Expected: PASS.

### Task 6: Fail-open audit of the security/suppression boundary

**Files:**
- Modify: `hermes_feishu_card/hook_runtime.py`
- Modify: `hermes_feishu_card/server.py`
- Modify: relevant focused tests only when an unobservable branch is found
- Create: `docs/wiki/fail-open-boundaries.md`
- Modify: `docs/wiki/README.md`

**Interfaces:**
- Consumes: authenticated event delivery and existing suppression decisions.
- Produces: a bounded classification of security, recovery, terminal, and suppression exceptions; metrics or `last_error` only for confirmed observability gaps.

- [x] **Step 1: Inventory only the four approved boundary classes**

Record Hermes isolation, event authentication, recovery mutation/fingerprint, and accepted-card native suppression. Do not mechanically rewrite all broad exceptions.

- [x] **Step 2: Add one failing test per confirmed observability gap**

Each test must prove the missing metric/error state or incorrect fallback before implementation.

- [x] **Step 3: Add the minimal metric or bounded diagnostic**

Do not include message content, chat ids, paths, tokens, proof headers, or secret material.

- [x] **Step 4: Run runtime/server focused tests**

Run: `python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_server.py -q`

Expected: PASS.

### Task 7: Release gate

**Files:**
- Modify version/release files only if the runtime behavior is shipped as `v4.0.10`.

**Interfaces:**
- Consumes: all completed tasks.
- Produces: verified release candidate, not an automatic public release.

- [x] **Step 1: Run the focused security matrix**

```bash
python -m pytest tests/unit/test_operations_transport.py tests/unit/test_runner.py \
  tests/unit/test_config.py tests/unit/test_hook_runtime.py \
  tests/integration/test_hook_runtime_integration.py \
  tests/integration/test_server.py tests/integration/test_cli_process.py -q
```

- [x] **Step 2: Run full verification**

Run: `python -m pytest -q && git diff --check`

Expected: zero failures and no whitespace errors.

- [x] **Step 3: Build and import smoke**

Build wheel/sdist and import `hermes_feishu_card` in a clean Python 3.12 environment.

- [x] **Step 4: Run real Feishu acceptance**

Verify private, group, topic, `/model`, background/system notice, no duplicate gray text, terminal completion, and zero delivery failures.

- [x] **Step 5: Review the exact diff and decide whether to publish `v4.0.10`**

Do not tag or publish until all prior evidence is current and the release checklist is complete.
