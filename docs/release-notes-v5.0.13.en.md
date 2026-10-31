# v5.0.13 — Native delivery isolation and model picker repair

Addresses [#479](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/479) and [#478](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/478). Availability follows the [published Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.13).

## Scope

Only Feishu enters HFC completion processing. Other platforms retain Hermes already_sent, media, footer and final-response handling. Existing Feishu handoff and fallback semantics remain unchanged. Historical managed completion blocks retain exact ownership checks, idempotent upgrade and byte-for-byte restore.

The initial model-picker view no longer preselects the current provider, allowing it to be selected explicitly. The current provider stays labeled. Picker cards use shared updates. A DM callback without a chat identifier requires verified original-message and admitted-actor bindings; unknown or conflicting identity remains rejected.

## Installation and acceptance

Refresh managed hooks through the normal installer/repair entry and restart Gateway through its service owner; replacing only the wheel does not reload installed hooks. Keep backups, manifests and protection records. Existing source and PM migration limits remain. Extract packages and enter hermes-feishu-card-v5.0.13-<platform>/ before invoking the installer.

Candidate validation is in progress. Exact-commit CI, ordinary-wheel upgrade/restore, Feishu API, archive and public-install evidence will be recorded in the final Release. Source tests and controlled upstream-method comparisons do not prove actual Gateway/model execution or client clicks. Reporter field retests remain separate.

The #476 avatar design and #477 model icons are not included.

## Credits

Thanks to [Lite-G](https://github.com/Lite-G) for #479 and [Kevinzz224](https://github.com/Kevinzz224) for #478. Bailey supplied the initial #479 patch from a separate ChatGPT repair session. Issue evidence is distinct from code authorship; historical credits and releases remain intact.
