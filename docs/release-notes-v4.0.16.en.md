# V4.0.16

V4.0.16 is a focused UX and compatibility hotfix for V4.0.15's tool timeline. It removes duplicate initial loading text and reliably carries real Hermes tool durations into Feishu/Lark cards.

## Fixes

- During initial loading, the Header shows only `Hermes Agent`; the animated `正在加载上下文…` placeholder remains in the body.
- After a tool starts, the Header subtitle shows the current action. If model content has not started, the body no longer keeps a stale loading placeholder.
- Hermes progress callbacks expose the real elapsed time as `kwargs.duration`. The hook now converts it to `duration_ms`, producing compact rows such as `✓ web_search · 1.75s`.
- When a completion event carries only duration, the query summary and arguments from the started event remain visible.

## Reliability boundaries

- Explicit Hermes `kwargs.duration` / `duration_ms` values are authoritative.
- If upstream omits duration, the sidecar falls back to the timestamp delta between matching started and completed events.
- A terminal-only compatibility event never invents elapsed time without a trustworthy start event.
- Hermes progress callbacks currently correlate by tool name; sequential repeated tools are supported, while strictly parallel calls with the same name remain an upstream correlation boundary.

## Validation

- A smoke using the real Hermes callback shape rendered `kwargs.duration=1.75` as `✓ web_search · 1.75s`, preserved the query and arguments, and removed the empty loading body after tool start.
- Full automation passed: `1504 passed, 4 skipped`; `git diff --check` passed.
- The release flow rechecks sdist/wheel, isolated `site-packages` import, the public tagged installer, and the local runtime provenance.

## Release assets

- `hermes-feishu-card-v4.0.16-macos.tar.gz`
- `hermes-feishu-card-v4.0.16-linux.tar.gz`
- `hermes-feishu-card-v4.0.16-windows.zip`
- `hermes-feishu-card-v4.0.16-checksums.txt`
