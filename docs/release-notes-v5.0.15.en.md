# V5.0.15 - Live Tool Activity, Chat-Scoped Controls and Hermes v0.22.7

[中文](release-notes-v5.0.15.md)

This release delivers #489, #491, #492 and #494. #493 remains an investigation awaiting reporter details. Upgrades, fresh installs and configs without the new keys keep their existing defaults.

## Scope

- **#489 live tool activity**: `card.tool_activity: full | summary | hidden` defaults to `full`. `summary` shows generic steps and elapsed time; `hidden` removes the header subtitle, tool rows, footer tool count and action phrase. Both also remove tool entries from the live reasoning timeline. Terminal cards keep their existing behavior under `hide_completed_tool_activity` and reading presets. The reporter confirmed the three modes and the decision not to keep tool names in the panel.
- **#492 chat-scoped controls**: `bindings.default_delivery` accepts a scalar `card`/`native` or a `{group: native, p2p: card}` mapping; `bindings.card_chats` lists exceptions; `card.chat_overrides` allows only `tool_activity`, `show_reasoning`, `stream_thinking_to_body` and `hide_completed_tool_activity`. Precedence is single chat, chat type, then the existing global/profile/bot merge; overlays are fixed when a session opens. An absent or unknown chat type skips type-level settings while single-chat rules still apply. Conflicting lists, invalid enums and unsupported fields are rejected at config load. `card-config --chat-type/--chat-id` reports effective values and sources without echoing chat IDs.
- **#491 Hermes v0.22.7**: reproduced `partial` (only `admission_completion` reported `unsupported-source`) on exact commit `b200f5bc60e31588ba2cb60e288029c4a54ba5fa`, then added a whole-file cohort and runtime topology verification. Ingress, post-delivery and follow-up ACK files are byte-identical; authority, producer lifecycle and Base changed, while the Base extraction seam is unchanged. Unknown upstream sources stay fail-closed. CI adds a pinned checkout, a source digest manifest and per-file drift negative controls.
- **#494 convergent managed PM upgrades**: a CLI installed from a Git commit now derives its install spec from `direct_url.json` instead of requiring a manual `HFC_INSTALL_SPEC`; an index wheel still needs an explicit spec. A repeat install with a verified identical generation, version, spec digest and ordinary wheel reuses the existing PM generation instead of creating a new environment. A version upgrade still creates one generation by design, and failed transactions retain their fail-closed rollback evidence.
- **#493**: the old card in the screenshot shows only the superseded-turn notice. Since v4.4.5 `server.py` appends that notice to `answer_text`; there is no `server.py` diff between v5.0.13 and v5.0.14, so there is no evidence of overwritten code. Reporter environment details were requested and the rendering path is unchanged in this release.

## Validation

`card-config` gains `--chat-type` and `--chat-id`; `--running` accepts the same selectors and scope hashes without selectors are unchanged. `--minimal-yaml` is mutually exclusive with chat selectors. `chats use-native/use-card` still manages only `native_chats`; `card_chats` is edited in the profile bindings directly.

Automation covers three modes across all render surfaces, unchanged terminal cards, invalid-value rejection, `card-config` values and sources, exact v0.22.7 detection with per-file drift controls, type/single-chat/unknown-type/conflict/profile delivery decisions, overlay layering without selector leakage, PM spec derivation and repeat-install convergence with negative controls, plus full focused regression, ruff, mypy and `git diff --check`. Complete CI, tag gate, asset checksums, anonymous public downloads and ordinary package installation results are recorded on this release page.

Limits: reporter environments, real Hermes Gateway/model execution, real Feishu group/private visual and click acceptance, and the managed macOS launchd + PM + legacy observer upgrade remain outside this automation. The #489 reporter will retest `summary` and `hidden` after publication; #494 needs an isolated retest; #493 awaits details. Archives keep one top-level directory and preserve the extraction target ownership and mode; Linux root/non-root and actual macOS extraction are covered by CI.

## Credits

Thanks to [Lite-G](https://github.com/Lite-G) for #489, #491, #492 and the [PR #490](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/490) reference implementation, [WenhuaXia](https://github.com/WenhuaXia) for the #494 managed-deployment reproduction, and [mouyong](https://github.com/mouyong) for the #493 screenshot. Issue evidence is separate from code authorship; original #486 commit authors and historical credits are preserved.
