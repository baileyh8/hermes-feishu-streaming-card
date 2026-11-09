# V5.0.21 — Completion attribution and subprocess encoding

[中文](release-notes-v5.0.21.md)

This release addresses new reports in [#518](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/518) and [#497](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/497). Installable availability follows GitHub Releases.

## Scope

- Carry model/provider together from the authoritative Hermes turn result so eligible Codex completions can query enabled `subscription_usage`. Global configuration, account availability and an unqualified GPT name do not establish turn attribution.
- Capture the actual fallback route at the pinned v0.23.0 result construction point. Unknown, conflicting or mixed attribution keeps quota hidden. Quota-query errors cannot block the terminal card; duplicate completions do not query again. Existing defaults and configuration remain.
- Make controlled PM/backend Python subprocess output encoding explicit for non-UTF-8 parent locales, retaining redacted diagnostics, source refusal and transaction protection.

## Findings and limits

The reporter's Windows setup `source_digest` refusal has not been independently reproduced. Both formal setup paths pass the selected `pm-0230` source map explicitly; calling an internal helper without sources exercises its older default contract and does not establish wrong family selection in setup. A pip-less runtime can use a prebuilt `.whl` with an exact `HFC_INSTALL_SPEC`; do not modify managed runtimes or delete protection evidence to bypass refusal.

The earlier #512 live evidence belongs only to exact v5.0.20, one bot/four profiles and desktop. It is not evidence for this candidate. Original-host Windows, actual Gateway/model, client and multiple Codex-account attribution require separate evidence.

## Verification

Focused regression covers locale decoding, coherent terminal routes, fallback, mixed routes, duplicate/late completions and quota-query failure. Release gates require independent QA, ordinary-wheel/real-child checks, complete PR/main/tag CI, archive checksums and isolated public installation. The published Release records exact CI and package evidence. Original Windows-host refusal, live Codex Gateway/model execution, client visuals and multiple subscription-account selection remain unverified.

## Credits

Thanks to [github-liu](https://github.com/github-liu) for #518 completion/quota evidence, and [DMarshall0114](https://github.com/DMarshall0114) for #497 Windows installation, encoding and retest evidence. Issue evidence is separate from code authorship; existing authors, tags, releases and credits are preserved.
