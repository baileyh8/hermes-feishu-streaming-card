# v5.0.30: Feishu reply, continuation card, and PM installer recovery fixes

[中文](release-notes-v5.0.30.md)

## Replies and system notices

- Ordinary replies and edits follow the existing Feishu send or edit path even when they quote a background task or self-improvement notice verbatim. `card.suppressed_notice_kinds` applies only to notices from verified producers. Coverage includes Hermes v0.23.3 background tasks and English, Simplified Chinese and Traditional Chinese review notices, along with recognized legacy notice paths. [PR #550](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/550) · [Notice ownership and suppression](wiki/notice-ownership.md)
- If a platform notice is confirmed unsent, Hermes sends the text once on its original route. If delivery is unknown, Hermes tries a single generic notice to reduce the chance of repeating the body. [Notice delivery behavior](wiki/notice-ownership.md)

## Topic continuations and delivery after restart

- Two consecutive long answers in one topic each use their own main and continuation cards. When legacy events lack a `turn_id`, HFC matches the verified requester, chat and topic to the original card. If Hermes reuses a message ID, the legacy completion hook checks `generation` and event time to identify a new turn. Hermes can still deliver natively when event order is uncertain.
- When completion notifications are enabled, HFC sends them to the original topic after it confirms final answer delivery. The answer and notification can arrive while an earlier interaction card's PATCH is still pending. Replaying the terminal event skips notifications recorded as delivered.
- If Feishu accepted a placeholder before the checkpoint write failed, HFC may be unable to find it after restart. The final result can go on a new card using the terminal event's own create UUID, even without a new-turn marker. Retrying the same event reuses that UUID. The old placeholder may stay visible.
- HFC retries an earlier unconfirmed create with its original UUID only while Feishu supports deduplication for the request and the window is still open. After expiry, its outcome stays `unknown`. If a CardKit create is unconfirmed, Hermes can deliver the body natively and any placeholder already accepted by Feishu may remain visible.
- Legacy terminal events with a new-turn marker or a verified `generation` get a receipt only once Feishu confirms delivery of the entire body. HFC keeps at most 16 receipts per delivery location for one hour from each confirmation. An exact replay can recover the receipt after card replacement or restart. Same-event completion retries reuse the original duration and output rate.
- If an older version lost acknowledgement for a create it already sent and has no usable checkpoint, retrying across versions may produce an extra card. Segments sent without acknowledgement can also repeat. Early checkpoints without a terminal fingerprint can cause duplicate native text on fallback. Wait for current replies and their segments to finish before upgrading. [Card recovery and retries](wiki/card-restart-recovery.md) · [V5 upgrade guide](upgrade-v4-to-v5.en.md)

## macOS/Linux installer and compatibility

- For supported Hermes PM layouts, `install.sh` reuses a separate management environment under the same Hermes HOME. If its interpreter is retired, the installer can rebuild with an available compatible Python. An interrupted pip replacement or failed package provenance check restores the previous environment when one exists. Once the new CLI passes provenance checks, it remains available to diagnose later PM setup refusals. Save the printed `status` command, which includes the absolute paths needed to run it from another directory. [Installation guide](../README-install.md) · [PM installation and recovery](wiki/pm-recovery.md)
- Compatibility still depends on source, capability and installation ownership checks. Notice regressions cover the pinned Hermes v0.23.3 source `caafe5389ae585ef77387a962aee05b1bcf17029`. Installer validation covers a real pip interruption and package provenance rejection in a private macOS HOME, plus a legacy Hermes v0.17.0 layout using its checkout Python. Unknown or drifted source still fails validation. [PR #550 validation](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/550)

## CI and release evidence

- PR #549 recalibrates upstream shards using measured JUnit test durations and keeps the existing coverage. When a failed or cancelled job can upload diagnostics, the artifacts are retained by attempt so a rerun preserves earlier evidence. [PR #549](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/549)
- The implementation commit `40017d4814b5bc1ee184b8fbfde93c322f639ce0` passed all 68 CI checks on rerun. Independent validation covered 16 signed HTTP loopback scenarios using non-editable wheels and a Feishu test double. The implementation wheel retained the 5.0.29 baseline version. [CI](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/45235020184) · [Independent validation](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/550)
- The v5.0.30 release commit, tag, packaged assets and public installation have separate checks. Field validation covers delivery to the Feishu test group and desktop rendering using an isolated wheel. See the GitHub Release for results. Mobile clients and live Gateway/model turns are unverified. [Release readiness](release-readiness.en.md) · [GitHub Releases](https://github.com/baileyh8/hermes-feishu-streaming-card/releases)

## Contributors

baileyh8 contributed the Feishu delivery and installer recovery fixes and recalibrated the CI shards. Lite-G proposed the original per-chat notice suppression in #500. [PR #550](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/550) · [PR #549](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/549) · [#500](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/500)
