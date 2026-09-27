# V4.6.10: Current Hermes compatibility and independent follow-up cards

## Changes

- Accept only the exact guarded turn-marker release between ledger recording and sending; retain drift rejection and delivery ordering (#352/#353, PR #355).
- Resolve outbound ownership through `_delivery_adapter_for`, with legacy API support. An authoritative refusal never falls through to another bot (#354).
- Bind queued/idle internal turns to independent source identities, keeping synthetic IDs out of Feishu reply anchors and retaining old-hook removal compatibility (PR #355).
- Give a reopened terminal session a fresh create UUID so Feishu deduplication cannot overwrite the prior answer with a short follow-up. Failed creates preserve the previous display, never old authorization; normal retention still applies (#359).
- Resolve Hermes PM's committed runtime venv in CLI/POSIX installation and avoid `pip --user` in external venvs (PR #358). Reinstall the plugin after Hermes dependency updates.

## Validation scope

Pinned current stable `v2026.9.24` / 0.21.5 (`f97608f178d1ffeca59860195ab7da295f7c8e5f`) and main snapshot `6f7a7991bb069db07ae74a479823ce8310f8c7e0` pass source-only installation, repeat installation, compilation, integrity diagnosis and byte-exact uninstall. Main has unknown static version metadata and is accepted through verified source anchors; future main revisions are not automatically covered. Both snapshots join the hash-bound CI matrix without replacing historical coverage.

Full regression, ordinary wheel, exact-commit CI, assets and public installation evidence are recorded on the final Release. Production upgrades and desktop/mobile acceptance remain separate evidence.

## Credits

[Nevoker](https://github.com/Nevoker) (PR #355), [shichenshuo-star](https://github.com/shichenshuo-star) (PR #358); [leavrcn](https://github.com/leavrcn) (#359), [lanx214](https://github.com/lanx214) (#352), [kite40](https://github.com/kite40) (#353), [ywarmy](https://github.com/ywarmy) and [mslchy](https://github.com/mslchy) (#354), [Love4yzp](https://github.com/Love4yzp) (PR #355 deployment evidence).
All historical credits and original code authors are preserved.
