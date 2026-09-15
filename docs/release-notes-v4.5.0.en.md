# V4.5.0 — Topic interactions, CardKit streaming and approval pause

- Route Feishu onboarding and Chinese deferred-compaction notices using the current inbound topic anchor; preserve private notice delivery.
- Keep long mobile questions readable in the card body. A committed choice no longer waits for a slow Feishu PATCH before callback acknowledgement; the Gateway callback network budget is two seconds.
- Resolve mentions only for the known requester. Preserve code, links and unknown names. `completion_notify.placement: card` places a mention before the answer; the default `message` retains separate notifications.
- Preserve the bound conversation ID across active-turn redirects so original callbacks complete the new card instead of leaving it empty (#283).
- Label successful model turns as response completion, without asserting completion of the business task.
- Add opt-in `card.streaming_mode: true` using CardKit entities, ordered cumulative text updates, bounded mutation rate, delivery reuse and explicit shutdown. Requires `cardkit:card:write`.
- Live synchronous Gateway approvals pause at expiry, revoke old consent tokens and require fresh review and an explicit decision. Resolve delayed choices against the original request ID when supported. This does not restore execution stacks after restart or extend native admission proofs.
- Add hash-bound installation, migration and exact restoration checks for the reported Hermes 0.17 and monolithic 0.21 sources, plus real container UID ownership checks.

Upgrade through the official installer and restart Gateway to load the new hooks. See the maintainer wiki for CardKit configuration, mention placement, live approval scope and container startup.

Release gates cover full pytest, cross-platform CI, exact merge verification, annotated tag provenance, asset checksums and a normal installation from the public tag. Real Feishu client acceptance is recorded separately from simulated HTTP tests.
