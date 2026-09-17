# v4.5.1: CardKit, topic and approval reliability

Based on PR #310 by [mouyong](https://github.com/mouyong), with maintainer corrections.

- Normalize CardKit element IDs to stable unique values of at most 20 characters across creation and updates (#306). Log only hashed request/entity diagnostics.
- Preserve background-task source anchors and resolve missing attachment/card topic anchors (#305/#313).
- Preserve partial output on failed completion and retain approval questions, scope and outcomes (#307/#312).
- Reuse paused approval cards and keep live waiters active. Never renew orphaned tasks or expired native admissions; explain that a new request is required (#314).
- Explain mobile expand versus consent without hiding operation scope (#282). Android/iOS acceptance remains open.
- Separate restart rejection/completion notices from running heartbeats (#311). Never claim a predicted completion time.
- Improve execution titles, tool state and expandable-panel affordances (#304).
- Restrict heartbeat recall to independent heartbeat notices; preserve conversation answers.

Validation includes automated regressions and real unsent CardKit entities, with no chat messages sent. Mobile UI and reporter-specific background/restart workflows still require field acceptance.
