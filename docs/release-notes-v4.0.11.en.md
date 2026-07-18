# V4.0.11

V4.0.11 fixes Issue #135: unreliable Feishu create/reply results no longer let runtime notice cards disappear silently or cause a blind fallback to duplicate sensitive notice text.

## Reliable delivery

- Every initial card delivery gets a stable `delivery_uuid`; all attempts for the same logical delivery reuse it, for both Feishu create and reply requests.
- Only HTTP 429/502/503/504, network errors, and timeouts receive bounded retries, with at most 3 attempts. Permanent 4xx responses are not retried, and each sidecar `/events` request is still single-shot.
- The sidecar reports `delivered`, `not_sent`, or `unknown`. Only `not_sent` permits the Hermes native path to fall back to the original notice. An `unknown` result attempts only a generic warning, never a second copy of notice content that may already have arrived.
- The generic warning is: `⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。`

## Observability and safety

- Adds `feishu_send_retries`, `feishu_send_unknown_outcomes`, `notice_native_fallbacks`, and `notice_uncertain_warnings`.
- `last_send_error` retains only safe classification/status fields. Card-safe diagnostics exclude raw chat/message IDs, UUIDs, response bodies, URLs, tokens, secrets, and notice text.
- Feishu API failures are structured so callers can classify HTTP/code outcomes without placing raw response bodies in exception messages.

## Compatibility

- Old sidecars, unparseable responses, and hook transport failures remain fail-open. Uncertain notice results use the generic warning instead of repeating the original notice.
- Normal messages, card updates, command cards, topic/thread routing, and the loopback event transport remain compatible.

## Verification

- Automated coverage includes recovery after 503 responses, permanent 400, uncertain connection/response outcomes, stable UUID reuse, topic replies, metrics and diagnostics redaction, and all three hook outcome branches.
- The final full suite reported `1389 passed, 4 skipped`, and `git diff --check` passed.
- `uv build` produced both sdist and wheel. A clean Python 3.12 environment imported `hermes_feishu_card==4.0.11` from the wheel with the expected console entry point metadata.
- After reloading the current worktree into a real Hermes `v2026.7.7.2` installation, Feishu DM create and topic reply through loopback `/events` both returned `delivered/applied`; both sends succeeded, failures/retries/unknown did not increase, and diagnostics contained neither smoke text nor UUIDs.
- This direct `/events` smoke does not traverse the Hermes native-message branch, so it does not claim a completed client-side gray-text visual check or real Feishu fault injection; automated integration tests cover those branches.

## Release assets

- `hermes-feishu-card-v4.0.11-macos.tar.gz`
- `hermes-feishu-card-v4.0.11-linux.tar.gz`
- `hermes-feishu-card-v4.0.11-windows.zip`
- `hermes-feishu-card-v4.0.11-checksums.txt`
