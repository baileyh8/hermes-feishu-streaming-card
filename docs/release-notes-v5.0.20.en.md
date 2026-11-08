# V5.0.20 — Official Docker multiplex profile roots

[中文](release-notes-v5.0.20.md)

Addresses [#512](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/512): default and named profiles in the official `HERMES_HOME=/opt/data` Docker layout were refused as `profile-default-unverified`.

## Scope

- Recognize the task-local Hermes home against the native data root, with the same ownership rules for the default home and `profiles/<name>`.
- Check the database directory before interpreting an absolute authority home. Outside-root paths, mismatched databases and inspection failures retain native delivery.
- Keep the multiplex global-profile override disabled and retain the original owner, generation and single-use terminal checks.

## Acceptance

- Reproduced the original refusal with native ContextVars and the standard-layout database mismatch from the previous source. Focused routing regression: 276 passed, no skips. CI tool contracts: 170 passed; docs/metadata: 104 passed. Independent final QA/review remains a pre-release gate.
- Publication also requires full PR/main/tag CI, official-image acceptance, ordinary-wheel provenance, public installation and release-asset verification. Final evidence is linked from the GitHub Release.
- The reporter deployment and real Feishu turns are unverified; doctor or health readiness does not establish card delivery.

## Credit

Thanks to [hnzwx](https://github.com/hnzwx) for the official Docker multiplex layout, redacted diagnostics and source analysis. Issue evidence is separate from code authorship. Historical credits remain.
