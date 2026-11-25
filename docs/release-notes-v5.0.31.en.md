# v5.0.31: Hermes v0.23.4 compatibility and streamed interim commentary deduplication

[中文](release-notes-v5.0.31.md)

## Hermes v0.23.4 compatibility

- Support pinned Hermes `v0.23.4` source (`8fc0374447313e276f6b9a2764bfc750190c966a`, config schema 54): register the `pm-0234` official config-writer and launch family (117 writer files including the newly imported `hermes_cli/profiles_run_elsewhere.py`, plus 5 launch files), resolving the `v5.0.30` refusal (`official config writer is unknown; PM admission refused`) caused by source drift. [PR #554](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/554) · [#553](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/553)
- Add the 45-module `v0.23.4` `Persistence` admission source and rendered digest cohort, explicitly binding `persistence.authority.vocabulary.retry_input` and reporting `admission_completion=True` in `doctor`.
- Preserve fail-closed boundaries: unknown or drifted sources remain rejected, and profiles marked under `profiles_run_elsewhere/<profile>.json` fail closed (`unbound_read`) without blocking unmarked local profiles in the same `HERMES_HOME`.
- Add pinned `v0.23.4` source-binding, official-writer, and official Docker image (`nousresearch/hermes-agent@sha256:ef0650d523ee1b5ab32c779acabc446d9936869a9932591553688a62bcf0c42f`, `preserved` and `cleared`) CI gates.

## Deduplicate native Feishu interim commentary

- Fix duplicate native Feishu replies quoting the user message below the card when `display.platforms.feishu.interim_assistant_messages: true` is enabled and `stream_deltas_enabled=False` (triggered by default `display.show_reasoning=True` or global `streaming.enabled=False`). Because `ANSWER_DELTA_PATCH` already streams text into the card, Hermes calls `interim_assistant_cb(text, already_streamed=True)`; previously `THINKING_DELTA_PATCH` skipped `already_streamed=True` and fell through to Hermes native `on_commentary`. [PR #554](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/554) · [#552](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/552)
- Bump `RENDER_CONTRACT_VERSION` to `completion-native-fallthrough-v3` (updating pinned `v0.23.0`–`v0.23.4` rendered digests). When `already_streamed=True`, card policy owns the turn, and pure preview event validation passes, HFC suppresses the duplicate native commentary without emitting a duplicate `thinking.delta`.
- Retain recognition of the historical `v5.0.30` thinking-delta patch block so existing `v5.0.30` installs restore and upgrade byte-for-byte; non-Feishu routes, `policy=native`, and unidentifiable turns remain fail-open to native delivery.

## CI and release evidence

- Fix `Restore exact Hermes fixed-tag source` in `.github/workflows/tests.yml` to invoke `/usr/bin/git` (matching `native_hooks._TRUSTED_GIT`) rather than PATH-resolved Homebrew `git`, avoiding cold first-launch latency on fresh `macos-latest` runners inside the 5-second `_git_head` timeout without weakening timeouts or provenance checks.
- PR #554 head `4bfaeb6f2ffd0fa71c74ee30fa81522a5bc0f6bc` passed all 70 CI checks on the first attempt (Linux Python 3.9–3.14, macOS 3.12, Windows 3.12/3.14, upstream compatibility, all official Docker images including `v0.23.4` `preserved`/`cleared`, and CodeQL). [PR #554 CI](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/45572354976)
- Independent validation covered isolated ordinary-wheel `install` / `reinstall` / `doctor` / `restore` / `uninstall` on official `v0.23.4`, real PM Gen A → Gen B survival and removal, per-file drift rejection across all 119 writer/launch files and all 45 admission modules, the `profiles_run_elsewhere` matrix, and a 6-case synthetic multi-tool reproduction of `#552`.
- The `v5.0.31` release commit, `main` CI, annotated tag, packaged platform assets, and public tag ordinary installation are gated separately and recorded on the GitHub Release. Live Hermes Gateway/model execution and real Feishu API, desktop, Android, and iOS field acceptance are unverified in this release. [Release readiness](release-readiness.en.md) · [GitHub Releases](https://github.com/baileyh8/hermes-feishu-streaming-card/releases)

## Contributors

[baileyh8](https://github.com/baileyh8) contributed the Hermes `v0.23.4` compatibility implementation, interim-commentary deduplication fix, and macOS CI fixture-git fix ([PR #554](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/554)). Thanks to [github-liu](https://github.com/github-liu) for reporting the Hermes `v0.23.4` compatibility failure in [#553](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/553) and to [Lite-G](https://github.com/Lite-G) for the duplicate interim-commentary configuration, screenshot, and reproduction details in [#552](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/552). Issue evidence is distinct from code authorship; original commits and historical credits remain intact.
