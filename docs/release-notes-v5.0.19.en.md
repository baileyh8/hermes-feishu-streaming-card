# V5.0.19 — Self-managed PM sticky profile and Windows NTFS installation fixes

[中文](release-notes-v5.0.19.md)

For [#507](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/507) and [#509](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/509). Unknown sources remain refused; card styles and configuration defaults are unchanged.

## Scope

- Sticky active profile: the binding derives the effective official config target with the same rules as `_apply_profile_override`; prepare/journal record that target; the probe and enable children pass `-p <name>`; every enable read, verification, rollback and restore path targets the same file. A missing profile home refuses before any config write. The postimage byte gate and unrelated-drift protection are unchanged.
- Windows NTFS: `st_ctime_ns` reports a constant delta between path and handle queries, so every file-signature comparison now uses a dev/ino/size/mtime tuple; content integrity stays on the sha256 digest, exact-ABA restores are no longer refused, and real content changes still are.

## Acceptance

(Filled before release: full CI, main exact-commit CI, tag gate, ordinary-wheel install/restore, public tag installation and asset verification links and results.)

## Credits

Thanks to [lvanwei180-lvan](https://github.com/lvanwei180-lvan) for the #507 field report (and the three-blocker #497 field test), [czh9101-lang](https://github.com/czh9101-lang) for the #509 root-cause localization and minimal reproduction, and [tidytouch](https://github.com/tidytouch) for the #510/#511 observations. Issue evidence is separate from code authorship; historical credits are retained.
