# V4.6.11: readable live thinking and background waits

## V4.6.11: live thinking and background tasks

- Set `card.thinking_body_tail_chars: 2400` to keep the latest live thinking in the body. It applies only with `stream_thinking_to_body: true`, before any answer and before the turn ends. The default `0` preserves unlimited existing behavior; accepted integers are 0–1000000. The character window may shrink further to fit the complete serialized card. It does not truncate answers, the reasoning timeline or tool sections; unrelated overflows retain the existing native-delivery fallback. Restart the sidecar after editing configuration.
- Once `terminal` reports a background process ID, or `process` / `process_manage` reports a command and output, subsequent waits in the same turn show that observed context. At most 32 process associations live in one card session's memory; they are not restored after restart.
- Recent output is the last tool report, not a live log subscription or predicted progress. The requested wait timeout is explicitly not an ETA; Hermes may clamp it further. Missing command context stays unknown. No other turn or profile is queried.

CodeQL init/analyze are upgraded to v4.38.2 with verified full SHA pins and Node 24 action metadata; the corresponding test allowlist is updated (PR #363).

Credits: [leavrcn](https://github.com/leavrcn) (#362, reproduction and patch proposal), [mouyong](https://github.com/mouyong) (#361, user-visible evidence), [Dependabot](https://github.com/apps/dependabot) (PR #363). All historical credits and real code authorship are retained.

Hermes stable v2026.9.24 / 0.21.5 remains the stable target. Final CI, package provenance, public installation and platform acceptance results belong in the GitHub Release. Production upgrades, real Gateway/model execution and mobile acceptance must be recorded separately.

Hermes main source pin: `5912ed81ed945a784f67ca13c1096e2c122cd307` (2026-09-28).
