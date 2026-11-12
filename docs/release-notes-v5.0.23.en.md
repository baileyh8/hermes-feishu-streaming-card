# v5.0.23: notification threshold, identity routing and bounded segmentation

## Changes

- **#502 notification minimum duration**: add `completion_notify.min_duration_seconds`. The threshold applies only to the standalone message completion notification and only when `completion_notify.enabled` is explicitly `true` (default `false`); the in-card completion presentation with `placement: card` does not pass through this gate. Turns shorter than the threshold stay quiet, and a replayed `message.completed` cannot re-notify later. The turn duration prefers the value Hermes reports and falls back to the session record's actual age when the report is missing. `min_duration_seconds: 0` (default) means no duration filtering.
- **#508 sender identity routing**: newer gateway `message.completed` events carry only the tenant `user_id`. With mention enabled, the mention target's `open_id` is resolved on demand through the contact API and successful lookups are cached; a failed lookup is not cached and the notice still sends unmentioned. Synthetic turns (background-process and queue completions) never notify, and a claimed-but-invalid sender id is treated as a spoof signal that sends nothing. Topic placement keeps the strict guard: no top-level fallback send when the reply anchor is missing.
- **#513 bounded segmentation**: add opt-in `card.over_limit: segmented` (default `native`). When a terminal answer still exceeds the safe card budget after every in-card degradation, deliver one main card (bounded lead, statistics, collapsible process panel) plus numbered continuation cards with a hard cap of five cards in total (main card included). Segments break at section boundaries and each segment is verified against the card budget. Any infeasible or failed segment falls back to the existing native full-text handoff; already-sent continuation cards may coexist with that fallback. Delivery keys prevent duplicate cards when the same terminal event is redelivered; this is not a permanent exactly-once guarantee. Streaming still updates a single live card.

## Acceptance and limits

Focused tests for the three PRs are committed on this branch. Full CI on the frozen candidate commit, ordinary-wheel/real-child installs, real Hermes Gateway/model turns, real Feishu client smokes and desktop/Android/iOS visual acceptance are still pending and are not marked passed before publication. API success or a green CI run is not client acceptance.

Configuration examples live in the README configuration section and the user guide; `enabled` defaults to `false`, and the standalone notification follows the #502/#508 rules above once enabled. Archive contracts and directory layout are unchanged.

## Credits

Thanks to [tidytorch](https://github.com/tidytorch) for bounded segmented delivery in [#513](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/513) (4 original commits preserved), [Cassius0924](https://github.com/Cassius0924) for the mention-fallback identity routing in [#508](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/508) (4 original commits preserved), and [jackwude](https://github.com/jackwude) for the notification minimum-duration gate in [#502](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/502). Code authorship follows the original Git commits; historical credits are retained.
