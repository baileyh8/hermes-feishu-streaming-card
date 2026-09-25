# V4.6.3: Live thinking visibility and tool-state fixes

Add `card.stream_thinking_to_body`, default `true` for compatibility. Set it to `false` to keep waiting/tool activity in the body until an answer arrives, with live raw thinking in a bounded panel preview controlled by `show_reasoning` and `max_reasoning_chars`. The preview is render-only; archived `reasoning_format: code` blocks and completed/failed content remain unchanged. Approval and answer delivery retain their existing behavior. Large raw thinking no longer overflows the body with the default panel budget; oversized custom panels and answers still pass through the global card-limit gate. Restart the sidecar after configuration changes.

Adapt the independent tool-order, duration and interruption-metric changes from PR #331: order tools by call ordinal, retain each running tool's predecessor, preserve terminal tool durations and checkpoints, and carry measured model/duration/tokens/context into interrupted cards. Missing or invalid values cannot erase known measurements. Execute the generated hook block to verify old/new turn identity and event order.

`hide_completed_tool_activity` remains `false` by default and still covers completed/failed when enabled. The proposed default reversal, failed-turn exception, timeline reorder and ambiguous reused-tool-ID counter change are not included.

## Credits and scope

Thanks to [leavrcn](https://github.com/leavrcn) for [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333), reproduction and configuration proposal, and [mouyong](https://github.com/mouyong) for the adapted tool display/duration/interruption implementation in [PR #331](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/331). Code authorship is retained with Co-authored-by.

Notice retirement from #331 remains separate: at `fc2a6a0`, card-send/completion call sites still omit bot/profile and can clear the wrong default identity. This release does not merge the entire PR.

## Validation boundary

Baseline failures and regressions cover body/panel isolation, long-thinking limits, failed content, approval, recovery, HTTP boolean configuration, one-card completion, parallel predecessors, durations, invalid values, old checkpoints and actual generated-hook execution. Full tests, exact-merge CI, platform asset verification and public-tag ordinary installation are release gates. Simulated Feishu clients are not mobile-device acceptance. AMD routing and the default model are untouched.
