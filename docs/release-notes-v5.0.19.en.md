# V5.0.19 — Self-managed PM sticky profile and Windows NTFS installation fixes

[中文](release-notes-v5.0.19.md)

For [#507](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/507) and [#509](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/509). Unknown sources remain refused; card styles and configuration defaults are unchanged.

## Scope

- Sticky active profile: the official visibility probe and enable children pass `-p default` for an ordinary Hermes home, preventing a sticky profile from redirecting writes to another config. A named home under `profiles/<name>` retains the official environment binding. Config reads and writes, journals, ownership and restore continue to use the user-bound home; the postimage byte gate and unrelated-drift protection remain in force.
- Windows NTFS: `st_ctime_ns` reports a constant delta between path and handle queries, so environment-layer, PM-admission and related legacy-evidence signatures now use a dev/ino/size/mtime tuple; content integrity stays on the sha256 digest, exact-ABA restores are no longer refused, and real content changes still are.

## Acceptance

- Local docs and version metadata: 104 passed; ruff passed and mypy passed for 60 source files.
- Related install, config and restore regression: 390 passed, 208 skipped. Skips include platform conditions and missing specific historical/upstream fixtures; this is not complete compatibility acceptance.
- NTFS divergence injection and CI workflow contracts: 23 passed, no skips; the new regression is included in the Windows portable suite. Explicit mtime drift avoids relying on filesystem clock granularity.
- An isolated child-process comparison on pinned Hermes v0.23.0 confirms that an unpinned launch follows the sticky profile, `-p default` keeps an ordinary bound root, and a named profile home retains its environment binding.
- Complete CI, the exact main commit, annotated-tag gate, ordinary-wheel install/restore, public installation and asset verification remain required before publication. Final records will be in the GitHub Release.
- Installation and real Feishu turns on the reporters' original hosts remain field verification; this release does not claim real Gateway or client acceptance.

## Credits

Thanks to [lvanwei180-lvan](https://github.com/lvanwei180-lvan) for the #507 field report (and the three-blocker #497 field test), [czh9101-lang](https://github.com/czh9101-lang) for the #509 root-cause localization and minimal reproduction, and [tidytouch](https://github.com/tidytouch) for the #510/#511 observations. Issue evidence is separate from code authorship; historical credits are retained.
