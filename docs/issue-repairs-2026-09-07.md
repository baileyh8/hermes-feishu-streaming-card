# September issue repair tracking

Baseline: v4.4.0 / ea2cb11f6a5a7009f0b414fc5d9a2d0e1237bcd5.

## Scope and acceptance

- #254, #255, #256 / PR #257: independently validate decomposed Hermes source detection, reversible installation, recovery and update diagnostics against real source revisions.
- #252 / PR #251: preserve reply anchors, queued and redirected card lifecycle; verify integration with decomposed patching and keep unrelated turns isolated.
- #83, #259: support a shared sidecar without requiring a default profile, separate setup validation from runtime profile routing, and verify concurrent named-profile events.
- #258: readable approval command/context and reachable controls without reducing authorization detail.
- #253: configurable expanded reasoning with legible multiline content.
- #250: display the effective response provider/model, including fallback, without inventing provider identity.
- #73: verify existing v0.17 callback and missing-start recovery coverage, document current diagnostic steps.
- PR #247, #248: update CodeQL init/analyze together and retain SHA validation.

## Integration

The repair branch integrates all four contributor PRs with original commits and authorship preserved. Existing primary checkout files are untouched. GitHub CI and release records are the authority for merge and publication status.

## Gates

Focused regressions; full suite using a regular-wheel test runtime and the exact fixed Hermes fixture; real decomposed-source install/remove round trips; wheel/sdist and isolated import; GitHub CI; exact merged revision verification; release assets and public installation verification. Real Feishu checks must be explicitly distinguished from automated tests.
