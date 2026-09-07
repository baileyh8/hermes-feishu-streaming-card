# Hermes Feishu Streaming Card V4.4.2

Fixes Hermes 0.21 integrity migration and single-process multiplex card delivery, and improves approval and clarification cards.

- Validate the complete ownership manifest and migrate decomposed targets together, clearing verified stale fences.
- Record explicitly local ownership snapshots for source-only installs without granting Git ancestry or automatic upgrade authority. Recheck source before and after metadata writes; roll back owned metadata on drift while preserving user edits.
- Wrap secondary-profile adapters while retaining Hermes transport ownership.
- Show numbered interaction buttons and full option descriptions; accepted approvals expire to deny; reject oversized interactions before admission.
- Document Docker/s6 startup ownership. Missing checkout `.git` and a missing Git executable are distinct; bootstrapping from `git+` still requires Git.

## Validation boundaries

The repair head passed all CI checks, including Python 3.12/3.13 source-only install/repeat/compile/restore contracts against pinned Hermes stable `29112bef099274229cadff79cdff7bf7b99c4b77` and main `a7198a8855ad98681114ff5138eb01fe132a62e7`. Final version validation is recorded by the release gate.

The reporters' actual Docker/multiplex environments, Feishu client notifications, and historical #73 environment remain unverified. Ordinary @display-name in #262 lacks a trustworthy recipient ID; no identity guessing or resolution claim is made.

## Contributors

- [ywarmy](https://github.com/ywarmy): [#261](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/261), Hermes 0.21 completion-marker report.
- [Ricadre](https://github.com/Ricadre): [#265](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/265), stale integrity migration reproduction.
- [mouyong](https://github.com/mouyong): [#83](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/83), [#263](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/263), [#264](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/264), [#266](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/266), Docker/source-only and multiplex evidence; [#258](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/258), approval readability feedback.

Historical credits are preserved in [README](../README.en.md).
