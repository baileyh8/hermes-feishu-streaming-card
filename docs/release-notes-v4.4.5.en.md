# V4.4.5 — Terminal outcome and installation stability

- Preserve explicit failed, interrupted, partial and completed=false Gateway outcomes in final cards; suppress successful completion notifications for those turns. This addresses the display aspect of #285/#274, not the upstream execution-stop root cause.
- Preserve partial content when a new turn supersedes an active card, and explicitly state that completion is unconfirmed. Late callbacks remain isolated from the new turn.
- Support the decomposed `send_final_ledgered` contract contributed in #281/PR #286, with reversible installation and strict rejection of argument, adapter, guard and control-flow drift. Thanks to tidytorch for the compatibility contribution.

Regression coverage now executes the patched ledger bracket with successful/failed sends and present/absent obligation IDs. Maintainer instructions and the PR template require scenario-based evidence and explicit real-client acceptance boundaries.

Implementation candidate: 3562 passed, 9 skipped. Platform, SDK and pinned-upstream source checks remain subject to exact-commit CI gates.

First-click approval (#282), long-running blank cards (#283), and Topic reports (#275/#278) remain under investigation. Integrity fences are not bypassed. Compatibility applies to the verified source contract, not every distribution labeled Hermes 0.21.1.
