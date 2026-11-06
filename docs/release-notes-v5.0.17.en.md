# V5.0.17 — Hermes v0.23.0 compatibility

[中文](release-notes-v5.0.17.md)

This release addresses [#497](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/497) for exact Hermes v0.23.0 commit `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`. Unknown sources remain refused; card layout and configuration defaults are unchanged.

## Scope

- Recognize the exact synchronous `_note_bot_reply(source)` conditional in the completion wrapper, retain the earlier three-statement contract and refuse unknown conditions, invocation forms or ordering. When confirmed card delivery suppresses native text, preserve native bot-loop bookkeeping without fabricating `delivered` or changing `agent_result`.
- Verify SessionActor and the extracted `session_settlement_recovery` / `session_runtime_workers` settlement, separate-Task and shield-wait dependencies with a whole-source admission cohort and runtime bindings. Preserve native ownership and single-use completion acknowledgement.
- Add an exact v0.23.0 PM writer/launch family, including config writers, PM admission, backend proof and platform-specific paths. Version strings do not authorize unknown source.
- Add pinned-source and official-image preserved/cleared acceptance to CI while retaining older matrices.

## Acceptance record

Release commit: `d1bd76d5fcdd76cba810ca3b4ede6e1dba0fd877`. [PR #498 full CI](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/42168897620) passed 59/59 jobs, [exact main-commit CI](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/42174054769) passed 59/59, and the [annotated-tag gate](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/42178168337) passed 61/61. [CodeQL](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/42174054777) passed. Supported Python/platform coverage, earlier upstream contracts and all eight official-image cases were retained. A macOS report upload encountered a DNS failure in the final PR run; the failure was preserved and the affected job passed on the same commit after a targeted infrastructure rerun.

The ordinary noneditable wheel completed install → repair → repeat install → doctor → restore against exact v0.23.0 in a private environment: one transaction test passed in 70.49 seconds, with byte-for-byte source/configuration restoration. All 160 package resources (158 Python files) match the commit, wheel and installed files. Anonymous public Git-tag installation also passed: version 5.0.17, the `direct_url.json` commit/tag, resource bytes and console `--help` were verified.

After publication, all four assets returned HTTP 200 without authentication and matched the draft copies by SHA-256; archive and actual-extraction checks passed again.

All three platform archives passed checksums, the single top-level directory and nine-file inventory checks. Actual macOS extraction into 0700 / 1777 targets preserved parent inode/uid/gid/mode, the sentinel and file bytes; Linux GNU tar root/non-root extraction was covered by CI.

Source tests and independent review cover 31-source drift refusal, loaded bindings/defaults, multiple owners, duplicate/late receipts, and actual settlement Task normal/cancel/unknown traces. Local cold checks took about 0.44–0.58 seconds and four warm checks about 0.07–0.08 seconds. These are verifier microbenchmarks, not Gateway/model or client end-to-end latency. Source refusal and timeout gates were not relaxed; [#498](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/498) records the corrections and earlier failures.

The reporter's Linux / PM / systemd user / split-HOME environment, real Gateway/model execution and Feishu clients have not been retested. After publication, refresh managed hooks with the normal installer, restart Gateway through its service owner, and verify the new package version, generation and runtime readiness in `/health`. Do not hand-edit Hermes or delete protection records.

## Credits

Thanks to [Lite-G](https://github.com/Lite-G) for the exact-source analysis, three-layer diagnosis and retest plan in #497. Issue evidence is separate from code authorship; historical credits and commit authors are preserved.
