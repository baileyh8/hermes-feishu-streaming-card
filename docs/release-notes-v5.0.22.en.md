# v5.0.22: exact Hermes v0.23.1 compatibility

## Changes

- **#520 stable compatibility**: add a separate source cohort for official Hermes v0.23.1 `3b421578588243f920886e6fe79a6faa87ea026e`. Verify whole-file bytes for 31 admission/topology, 107 writer and 4 launch sources. Retain loaded-function/default/contextmanager, settlement and producer identity checks, and both current and historical owned v0.23.0 render sets. Mixed and unknown sources remain refused.
- **Installation/configuration**: bind `pm-0231` to upstream schema 53 while earlier families retain schema 52. Include the new writer/launch dependencies in isolated-child source checks. Complete the normal Hermes upgrade/configuration migration first; do not edit the version stamp or bypass protection records.
- **Slash-confirm anchor**: match only the exact historical four-positional-argument AST or reviewed requester-authority form. Preserve the original upstream call and authority expression. Reject changed arguments, unknown keywords, unpacking and ambiguous matches. Fixing this anchor alone does not establish compatibility with an unknown main cohort.
- **CI/dependencies**: retain the original #516 Ruff 0.17.4 and #517 CodeQL v4.39.3 maintenance commits. Explicitly run the three v0.23.1 source suites in the pinned matrix and add preserved/cleared official-image acceptance. Clarify that #510's `latest_stable` is the fixed historical v0.22.4 baseline; preserve earlier coverage.
- **Timeout diagnostics**: atomically replace one bounded `.progress` file with the running node, ordinal and elapsed time. It is not completion evidence. Exact-commit, complete-union and once-only final-report gates remain unchanged. The old #516 logs do not identify the precise stuck stage; no root-cause or timing-target claim is made.

## Acceptance and limits

Publication requires independent ordinary-wheel/real-child install, repair, repeat and byte-exact restore, full PR/main/tag matrices, official images, archive/checksum/actual extraction checks and public-tag installation. Exact SHAs, CI links and measured outcomes are recorded in the published GitHub Release; pending candidate gates are not marked passed.

No card-layout/default change or #521 local repin. The original #497 Windows-host `source_digest` refusal remains independently unconfirmed. Actual Hermes Gateway/model turns, client clicks and desktop/Android/iOS visual acceptance are separate from this automated evidence.

Archives retain a single platform-specific top-level directory: enter `hermes-feishu-card-v5.0.22-<platform>/` before running an installer. Keep existing protection records, backups and published tags/assets. Never edit Hermes source or delete protection files to bypass checks.

## Credits

Thanks to [Lite-G](https://github.com/Lite-G) for main-anchor evidence in [#520](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/520), and [ywarmy](https://github.com/ywarmy) for v0.23.1 stable source, installation and runtime analysis. Dependabot supplied [#516](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/516) and [#517](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/517). Original commits, authors and historical credits are retained; issue evidence is separate from code authorship.
