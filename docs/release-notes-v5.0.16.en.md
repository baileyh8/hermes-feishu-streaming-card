# V5.0.16 — Install diagnostics and diagnosable #462 failures

[中文](release-notes-v5.0.16.md)

This release addresses the diagnosability half of #462 on Hermes v0.22.6 PM layouts: install failures no longer reduce to a bare `official plugin enable failed`, and PM layouts now run a plugin-visibility probe before writing any config. PM's sys.path switch itself is unchanged and generation creation before a refusal is not guaranteed; full closure depends on the reporter's retest.

## Scope

- **#462 official-enable diagnostics**: `_run_official_enable` failures (non-zero exit, timeout, OSError) now carry a sanitized stdout/stderr tail in the refusal message. The reporter's real error text is printed on stdout by the official Rich console and was previously discarded; it now surfaces, e.g. `No plugin named 'hermes-feishu-card'.`. When both streams are empty the original fixed message is kept verbatim. Child processes decode with `errors="replace"` so non-UTF-8 output cannot escape classification.
- **#462 preflight visibility probe**: for PM layouts (checkout root contains `pm/environments.py`) the installer runs the official `plugins list` path with the same child launch contract before writing any config, verifying the HFC entry point resolves through the real startup path (including PM's sys.path switch). Unresolvable plugins refuse with fixed categories (`official plugin enable visibility probe failed (plugin not resolvable in the selected runtime)` and friends); probe errors, non-JSON output and banners are fail-closed. Ordinary `.venv` layouts skip the probe and keep their behavior.
- **Bounded install-refusal forwarding**: the fixed-tag install reason whitelist forwards printable, length-bounded enable/probe diagnostics (≤700 chars, `)`-terminated); control characters or oversized unknown text still fold back to the fixed refusal.
- **Diagnostic redaction**: new `display_redaction.redact_diagnostic_text()` handles key/value assignments, Authorization Bearer/Negotiate/Basic headers, quoted values (spaces and escaped quotes), quoted CLI flags and URL userinfo before legacy masking; `redact_tool_detail()` and other frozen contracts are unchanged.

## Acceptance

Automation: PR #496 full matrix passed 59/59 checks (Python 3.10–3.14 full runs, ten macOS shards, Windows, official images v0.22.1–v0.22.5 preserved/cleared, pinned upstream shards, CodeQL, SDK compatibility and Docker Compose smoke) on merge commit `e607efc8e04eea229ec0be4733f43a4be4a04443`. Local and independent verification: focused install regression 2444 tests / 0 failures / 0 errors / 85 skipped (independent venv + pinned Hermes fixture); four classes of credential canaries (api_key assignment, Bearer, quoted values with spaces, escaped quotes, quoted flags) all redacted through the real `enable_plugin → install` refusal chain; 0xff child output classified normally; good-layout `install --yes` still succeeds and the bad layout produces the visibility-probe refusal with its category.

Known unverified: the reporter's managed macOS upgrade, real Hermes Gateway/model execution and real Feishu client visuals/clicks are out of scope for this release. This release does not fix PM's sys.path rewrite; the probe surfaces the failure earlier with its cause. If the retest still fails, attach the redacted stderr/stdout text (the new build includes it automatically).

## Release acceptance record

- Exact main merge commit `e607efc8e04eea229ec0be4733f43a4be4a04443`: PR #496 CI 59/59 passed.
- Tag gate (release-assets), asset checksums, anonymous download and ordinary-package install acceptance: executed by CI after the tag push and recorded in the GitHub Release.

