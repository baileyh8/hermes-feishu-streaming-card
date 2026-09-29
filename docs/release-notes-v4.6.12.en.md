# V4.6.12: configurable card width

Set `card.width_mode` to `default`, `compact`, or `fill` without editing installed package files. The default preserves the existing layout. Global, profile and bot scopes override in order; explicit `default` resets an inherited width. Restart the sidecar after changing configuration.

```yaml
card:
  width_mode: fill
```

- Covers JSON 2.0 streaming/final cards, overflow handoff and repair, diagnostics and maintenance cards; JSON 1.0 approval cards retain their dialect.
- Works with the live-thinking tail window while validating the complete card budget. Final answers, ownership and default reading layout remain unchanged.
- Feishu clients control actual dimensions; desktop and mobile acceptance are recorded separately, without fixed-pixel promises.
- Stable target: Hermes v2026.9.24 / 0.21.5. Updated main source snapshot: `ea114c3e98c3339e13004adfc6098cf28ed7d754` (2026-09-29). Accept the exact optional synchronous `stop_reply_clock(delivery_adapter, event.source.chat_id, result)` once, after send and before finalize. Mutated arguments, placement, duplicates and awaited variants remain rejected.

Thanks to [cbatbj](https://github.com/cbatbj) / chenbing1 for the implementation and tests in [PR #351](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/351). Original commits/authors and all historical credits are retained.

Final CI, wheel/public-install provenance, release assets and platform-display evidence belong in the GitHub Release. Production upgrades, actual Gateway/model tasks and mobile acceptance remain separate gates.
