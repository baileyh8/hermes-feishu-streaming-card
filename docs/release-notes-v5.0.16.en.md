# V5.0.16 — Install diagnostics and diagnosable #462 failures

[中文](release-notes-v5.0.16.md)

This release addresses the diagnosability half of #462 on Hermes v0.22.6 PM layouts: install failures no longer reduce to a bare `official plugin enable failed`, and PM layouts now run a plugin-visibility probe before writing any config. PM's sys.path switch itself is unchanged and generation creation before a refusal is not guaranteed; full closure depends on the reporter's retest.

## Scope

- **#462 official-enable diagnostics**: `_run_official_enable` failures (non-zero exit, timeout, OSError) now carry a sanitized stdout/stderr tail in the refusal message. The reporter's real error text is printed on stdout by the official Rich console and was previously discarded; it now surfaces, e.g. `No plugin named 'hermes-feishu-card'.` When both streams are empty the original fixed message is kept verbatim. Child processes decode with `errors="replace"` so non-UTF-8 output cannot escape classification.
- **#462 preflight visibility probe**: for PM layouts (checkout root contains `pm/environments.py`) the installer runs the official `plugins list` path with the same child launch contract before writing any config, verifying the HFC entry point resolves through the real startup path (including PM's sys.path switch). Unresolvable plugins refuse with fixed categories; probe errors, non-JSON output and banners are fail-closed. Ordinary `.venv` layouts skip the probe and keep their behavior.
- **Bounded install-refusal forwarding**: the fixed-tag install reason whitelist forwards printable, length-bounded enable/probe diagnostics; control characters or oversized unknown text still fold back to the fixed refusal.
- **Diagnostic redaction**: new `display_redaction.redact_diagnostic_text()` handles key/value assignments, Authorization Bearer/Negotiate/Basic headers, quoted values (spaces and escaped quotes), quoted CLI flags and URL userinfo before legacy masking; `redact_tool_detail()` and other frozen contracts are unchanged.

## Acceptance

Automation: exact merge/publish commit `b943017010eb6d79db1ca59c0f7b63348401e311` (including 9623437 and b943017). PR #496 full matrix passed 59/59 checks; the main-branch tests full matrix passed after the test_docs version assertions were completed; the tag gate `release-assets` run 42127350923 finished 59/59 jobs successful (the first round failed one macOS shard on `test_real_regular_wheel_plugin_manager_probe_reports_exactly_four_capabilities` with a `git_head` 5-second probe timeout, a timeout flake; the rerun passed the same test, and the PR #496 run also passed it). Local and independent verification: focused install regression 2444 tests / 0 failures / 0 errors / 85 skipped; credential canaries (api_key assignment, Bearer, quoted values with spaces, escaped quotes, quoted flags) all redacted through the real enable-to-install refusal chain; 0xff child output classified normally; good-layout `install --yes` still succeeds and the bad layout produces the visibility-probe refusal with its category.

Release acceptance record: all four assets downloaded anonymously and checksum-verified OK; the Linux and macOS archives each contain a single top-level directory with the nine-file manifest; a public ordinary-package install `git+...@v5.0.16` succeeded with module version 5.0.16, working console `--help`, and `direct_url.json` commit `b943017010eb6d79db1ca59c0f7b63348401e311`, requested_revision `v5.0.16`.

Known unverified: the reporter's managed macOS upgrade, real Hermes Gateway/model execution and real Feishu client visuals/clicks are out of scope for this release. This release does not fix PM's sys.path rewrite; the probe surfaces the failure earlier with its cause. If the retest still fails, attach the redacted stderr/stdout text (the new build includes it automatically).

## Credits

Thanks to saulgoodmanngabriel for the exact v0.22.6 reproduction, the manual enable stdout/stderr comparison and the layered repair suggestions for #462. Issue evidence is separate from code authorship; historical credits remain intact.

