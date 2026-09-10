# AGENTS.md — hermes-feishu-streaming-card

## Project

Sidecar-only plugin for Hermes Agent Gateway Feishu/Lark streaming cards.

- Active source: `hermes_feishu_card/`
- Tests: `tests/`
- Public maintainer wiki: `docs/wiki/`
- V2 archive: `legacy/` — do not edit unless explicitly asked.

The Hermes process should keep only minimal hook logic. Feishu/Lark delivery,
card state, rendering, diagnostics, installer behavior, and release assets live
in this repository.

## Hard Rules

- Do not edit an installed Hermes `gateway/run.py` by hand. Only
  `hermes_feishu_card/install/patcher.py` may patch Hermes.
- Do not commit secrets: Feishu App Secret, tenant token, real chat id, local
  `.env`, or screenshots with private/unredacted content.
- Keep hook behavior fail-open for unknown/unsupported paths, but suppress
  duplicate Feishu native gray text after this plugin has accepted a card path.
- `legacy/` is not active runtime.
- User-facing conversation with Bailey is Chinese. Code identifiers, filenames,
  tool names, and protocol names stay in English.

## Commands

```bash
python -m pytest -q
python -m pytest tests/unit/test_docs.py -q
python -m pytest tests/unit/test_package_metadata.py -q
python -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --explain
```

No formatter, linter, or typechecker is configured. CI is pytest-based.

## Change Routing

Read `docs/wiki/maintenance-guide.md` before touching these hot areas:

- `hermes_feishu_card/hook_runtime.py`
- `hermes_feishu_card/server.py`
- `hermes_feishu_card/install/patcher.py`
- installer scripts, Docker install, release workflow

Use these wiki pages for context:

- `docs/wiki/README.md` — maintainer wiki entry
- `docs/wiki/maintenance-guide.md` — hot files, risk boundaries, test matrix
- `docs/wiki/event-flow.md` — Hermes event to Feishu card lifecycle
- `docs/wiki/feishu-acceptance.md` — real Feishu/Lark smoke checklist
- `docs/wiki/release-playbook.md` — release checklist

## Testing Expectations

Run focused tests while developing, then full suite before release or broad
claims.

- Runtime/topic/notice changes:
  `python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_server.py -q`
- Patcher/install changes:
  `python -m pytest tests/unit/test_patcher.py tests/integration/test_cli_install.py -q`
- Docs/version changes:
  `python -m pytest tests/unit/test_docs.py tests/unit/test_package_metadata.py -q`
- Release gate:
  `python -m pytest -q && git diff --check`

## Release Checklist

1. Bump `pyproject.toml` and `hermes_feishu_card/__init__.py`.
2. Update `CHANGELOG.md`, `docs/release-notes-vX.Y.Z.md`, README files, and
   affected docs/wiki pages.
3. Audit every merged/absorbed PR and issue since the previous tag. Credit the
   relevant GitHub users, with links and accurate contribution descriptions,
   in both README languages and the release notes; do not omit issue reporters
   whose evidence materially shaped a fix.
   Before publishing, also compare the README contributor sections against the
   repository's full tag/release history, merged or materially absorbed PRs,
   accepted issue evidence, commit authors, and `Co-authored-by` trailers. A
   new release must preserve and, when needed, restore credits from earlier
   versions rather than replacing them with only the latest cycle's names.
4. Preserve real code authorship when merging, cherry-picking, squashing, or
   reimplementing a PR. Keep the original author where Git permits, or add an
   accurate `Co-authored-by` trailer, so code contributors are represented in
   Git history and GitHub's commit-based Contributors graph. Never fabricate a
   commit or authorship merely to change that graph. Issue-only contributors
   may not appear in the graph and must still be acknowledged in README and
   release notes.
5. Run full tests and `git diff --check`.
6. Commit, create annotated tag, push branch and tag.
7. Ensure GitHub Release exists and release-assets workflow uploaded packages.

## Obsidian / LLM Wiki

Project-public knowledge belongs in `docs/wiki/`.

When adding durable project knowledge, update both the repo wiki and Bailey's
Obsidian LLM Wiki mirror if it should be reusable across future Codex sessions.

## Stability regression requirements

Follow `docs/wiki/stability-test-policy.md` for stability fixes. Each bug needs a failing reproduction where feasible and assertions on user-visible results, including failure and late/duplicate events. Patcher compatibility must test rejection of contract drift and execute the patched delivery bracket, not only match markers. Report missing fixtures, failed checks and unrun real-client acceptance explicitly. Test totals alone do not establish release readiness.
