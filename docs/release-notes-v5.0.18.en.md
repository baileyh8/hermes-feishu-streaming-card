# V5.0.18 — Hermes v0.23.0 real delivery and self-managed PM installation fixes

[中文](release-notes-v5.0.18.md)

For [#504](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/504) and [#503](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/503), carrying [#497](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/497) and [#505](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/505). Unknown sources remain refused; card styles and configuration defaults are unchanged.

## Scope

- The explicitly registered sync `@contextmanager` helper `gateway.session_ingress_media.capture_lease` for exact Hermes v0.23.0 is verified as the standard contextlib wrapper (code/globals/defaults/kwdefaults identity plus a single closure cell pointing back), then the unwrapped function still passes the full source checks. No generic unwrapping; forged wrappers, cloned closures and source drift stay refused.
- When Feishu admission is native or handoff without an exact stage, the hook leaves and the enclosing Hermes method continues, keeping `already_sent`, media, footer and failure delivery in the core path; degraded turns no longer duplicate the full body. Old scoped hook blocks keep exact identification, idempotent upgrade and byte-identical restore; renderer contract identity is decoupled from package metadata with render digests unchanged in effect.
- Self-managed PM installs run the plugin visibility probe on the store interpreter the official bootstrap relaunches to. Resolution happens in a dedicated child that never imports the exec-ing bootstrap; resolution failures, non-absolute paths, missing files, ambiguous output or nonzero exits keep the existing bound-runtime probe and refusal. Unresolvable plugins still refuse; `probe output unreadable` is never accepted.

## Acceptance

The release commit is 4b823e39d6fce031b9d0a5ce579c13162e76834a (merge of PR #506). PR #506 full CI 59/59, main exact-commit CI 59/59, annotated tag gate 61/61, and CodeQL all passed. Supported Python 3.9-3.14, three platforms, prior upstream matrices and the 8 official-image scenes are retained. Three-platform archive checksums, single top-level directory, 9-file manifest and the macOS real-extraction checks (0700/1777 targets, normalized modes, byte-identical files) passed. Reporter-environment (self-managed PM / real v0.23.0 turns), real Gateway/model and Feishu-client retests remain unverified. After upgrading, refresh the managed hook through the normal installer and restart Gateway through its service owner, then verify the new version, generation and runtime readiness in /health. Do not hand-edit Hermes source or delete protection records.

## Credits

Thanks to [mouyong](https://github.com/mouyong) for the #504 sync-wrapper localization, patches and real-turn retest (PR #505), and to [ywarmy](https://github.com/ywarmy) for the #503 self-managed PM field evidence. Issue evidence is separate from code authorship; historical credits are retained.
