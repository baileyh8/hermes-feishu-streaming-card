# V5.0.17 — Hermes v0.23.0 compatibility

[中文](release-notes-v5.0.17.md)

This release addresses [#497](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/497) for exact Hermes v0.23.0 commit `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`. Unknown sources remain refused; card layout and configuration defaults are unchanged.

## Scope

- Recognize the exact synchronous `_note_bot_reply(source)` conditional in the completion wrapper, retain the earlier three-statement contract and refuse unknown conditions, invocation forms or ordering. When confirmed card delivery suppresses native text, preserve native bot-loop bookkeeping without fabricating `delivered` or changing `agent_result`.
- Verify SessionActor and the extracted `session_settlement_recovery` / `session_runtime_workers` settlement, separate-Task and shield-wait dependencies with a whole-source admission cohort and runtime bindings. Preserve native ownership and single-use completion acknowledgement.
- Add an exact v0.23.0 PM writer/launch family, including config writers, PM admission, backend proof and platform-specific paths. Version strings do not authorize unknown source.
- Add pinned-source and official-image preserved/cleared acceptance to CI while retaining older matrices.

## Acceptance record

Source tests and independent review verify completion, 31-source drift refusal, installed patches and loaded bindings, defaults, multiple owners, duplicate/late receipts, and the actual settlement Task normal/cancel/unknown traces. The older focused regression has 800 passes; missing local pinned-source skips are covered by the CI source matrix. Four hot-path `_verified()` calls take about 0.86–0.97 seconds; this is not complete Gateway/model latency.

Ordinary-wheel installation/restore, exact-commit full platform CI, official-image preserved/cleared acceptance, tag verification, assets and public installation remain separate release gates. Final results and exact commits are recorded in this version’s GitHub Release; detection full is not a substitute.

The reporter's Linux / PM / systemd user / split-HOME environment, real Gateway/model execution and Feishu clients have not been retested. After publication, refresh managed hooks with the normal installer, restart Gateway through its service owner, and verify the new package version, generation and runtime readiness in `/health`. Do not hand-edit Hermes or delete protection records.

## Credits

Thanks to [Lite-G](https://github.com/Lite-G) for the exact-source analysis, three-layer diagnosis and retest plan in #497. Issue evidence is separate from code authorship; historical credits and commit authors are preserved.
