# Hermes Feishu Streaming Card V4.4.3

V4.4.3 fixes older-hook migration and local source customization integrity issues found on a real local Hermes 0.21.0 installation, and omits an empty reasoning/tool timeline as requested in Issue #269.

## Fixes

- Explicit `--accept-hermes-upgrade` can now recognize Hermes sources that have valid manifest/backup ownership but still carry an older HFC primary marker block. The installer verifies old ownership before a bounded lenient removal; unknown edits outside owned blocks remain refused.
- `integrity migrate-safe --yes` may bind a healthy, byte-reversible hook in a Git checkout with intentional local source customization as `verified_owned_snapshot`. This evidence proves only the current installation and cannot authorize automatic repair after a later Hermes upgrade.
- Cards with no reasoning, tool, subagent, or notice entries omit both the “思考与工具 · 0 次工具调用” panel and the redundant zero-tool summary. The timeline appears unchanged after the first real entry.
- The pinned compatibility gate includes source hashes from local production Hermes 0.21.0 commit `180291162ff4df0d42b5dc4fecd08005cf7cebf9`.

## Production validation

- The real Gateway venv loaded the candidate wheel. Hook recovery reported `installed`, and after explicit integrity migration the sidecar reached `healthy / runtime_ready / integrity=safe`.
- A real Feishu DM smoke succeeded, followed by an actual Hermes inbound turn whose events were received and applied without a send failure.
- The production Gateway has only the `default` profile, so this is not claimed as real multiplex acceptance. An executable two-bot/profile/topic Hermes-handler regression proves HFC preserves profile, conversation, and thread identity; Issue #268 still needs the reporter's deployment retest.

## Contributor

- [mouyong](https://github.com/mouyong): the multiplex production report in [#268](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/268) and empty-timeline feedback in [#269](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/269).
