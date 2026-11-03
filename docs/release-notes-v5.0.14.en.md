# v5.0.14 — Per-turn rate correlation and Hermes compatibility

[中文](release-notes-v5.0.14.md) | [English](release-notes-v5.0.14.en.md)

This is a release candidate. Pending gates below are not claims of publication or acceptance.

The candidate includes exact Hermes v0.22.6 source support at `f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab`: completion ownership, producer lifecycle, post-delivery callbacks and native rate projection. Native return values and single execution are preserved. The installer accepts a proven `file` config backend; remote/unknown backends and `plugins.disabled_by_update` records are refused before PM mutation. Ordinary installation, upgrade and recovery validation remain pending.

## Scope

- #441: verify correlation between observed output rates and native turns within a session. An isolated counterexample confirms a later turn can consume an earlier sample. The reporter's missing-rate environment has not been independently reproduced; final implementation and validation will follow the frozen candidate.
- #482: restore verified per-turn status callback handoff while retaining native notice delivery and unknown/stale-owner fallback.
- #485/#486: recognize exact Hermes `GatewayBackgroundMixin` extraction cohorts and include `gateway/run_background.py` in source ownership and upgrade checks. Completion ownership and unknown-source guards remain; this is not arbitrary Hermes main support.
- #483/#484: update Ruff and mypy, improve redacted probe diagnostics, and require CI to execute pinned background-extraction cases.

## Upgrade and acceptance

Refresh managed hooks through the normal installer, then restart Gateway through its existing service owner. Do not delete protection records or manually edit Hermes source.

Independent QA/review, ordinary-wheel upgrade and byte-for-byte restore, exact-commit full CI/CodeQL, real Feishu API, public installation and three-platform assets are pending for this candidate. Earlier green commits do not satisfy these gates.

Reporter-environment reproduction, real Gateway/model execution and client visuals/clicks are unverified. Production migration remains separate; controlled API acceptance does not establish real model collection or client acceptance.

## Credits

Thanks to @coder-zhw for #441 retest evidence, @leavrcn for #482, @Lite-G for #485 and the original PR #486 implementation, and Dependabot for #483/#484. Earlier #441 contributions and all historical credits remain intact. Issue evidence is separate from code authorship; original commit authors are preserved.

Hermes v0.22.6 must complete its own config migration to schema 52 before HFC installation. Missing, malformed, older or unknown stamps are refused before PM mutation. Use the normal Hermes upgrade or `hermes config migrate`; do not edit the version stamp manually. HFC does not own browser or left-core migration.

Local evidence: 848 runtime checks passed (13 missing-pin skips, required separately in CI); 153 installer unit checks passed (2 platform skips). Ten ordinary-wheel writer/rollback/restore/source-cache checks and two real PM generation/member checks passed for v0.22.6. These isolated integrations use a PM toolchain port. Independent runtime and installer-admission review passed. Controlled Feishu API create/read/update/unknown-rate omission/recall passed. Exact-commit CI, tag gates, public installation and release-asset verification remain release gates; see the release page for their final evidence.
