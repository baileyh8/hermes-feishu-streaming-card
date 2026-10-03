# AGENTS.md — hermes-feishu-streaming-card

## Scope and authority

HFC is the Feishu/Lark presentation and delivery sidecar for Hermes Agent.
Hermes owns task execution, model calls, conversation ownership and permission
resolution. HFC owns card presentation, bounded display state, delivery,
diagnostics and its reversible installation. Do not build a second agent loop,
approval queue or execution-recovery engine here.

- Active code: `hermes_feishu_card/`; tests: `tests/`; maintainer docs: `docs/wiki/`.
- `legacy/` is archived, not active runtime. Do not edit it without explicit scope.
- Talk to Bailey in Chinese; keep code identifiers and protocol names unchanged.
- An audit or roadmap request authorizes the requested documentation, not the
  proposed runtime features, a production upgrade or a release. Existing explicit
  authorization remains valid within its scope; do not ask for it repeatedly.
- Keep unrelated working changes. Verify checkout, branch, base and package
  origin before editing; verify host/service/config before touching a live system.

## Start with the smallest useful context

1. Read `pyproject.toml`, the relevant implementation/tests and `git status`.
2. Run `python tools/preflight.py --check-only` with the intended Python.
   `ready` is environment readiness; `pytest.status=not_run` is not a test pass.
3. Read [maintenance routing](docs/wiki/maintenance-guide.md) before changing
   runtime, server, patcher, process/service, installer or release code.
4. Use [development rules](docs/wiki/development-rules.md) for dependencies,
   modules and validation; use [feature rules](docs/wiki/feature-rules.md) for
   user-facing changes. Read only the pages relevant to this change.
   Card layout, typography, color and motion follow
   [card visual guidelines](docs/wiki/card-visual-guidelines.md).
5. [Architecture](docs/architecture.md), [event flow](docs/wiki/event-flow.md),
   [stability policy](docs/wiki/stability-test-policy.md) and
   [release playbook](docs/wiki/release-playbook.md) are the detailed references.
   Dated reviews and roadmaps are evidence/proposals, not implementation status.

## Stack and ownership

The current stack is Python + asyncio/aiohttp, PyYAML, dataclasses/manual boundary
validation, setuptools and pytest. Read the supported Python floor and package
version from `pyproject.toml`; do not implement a proposed V5 floor implicitly.
Hermes supplies the Feishu SDK environment. There is no configured formatter,
linter or typechecker; do not claim their checks ran or mass-format existing code.

| Concern | Primary code |
| --- | --- |
| Hermes capability/plugin/legacy adapters | `hermes_plugin*`, `hook_runtime.py`, `install/native_hooks.py` |
| Event and interaction contracts | `events.py`, `session.py`, `runtime_interaction_transport.py` |
| Orchestration, identity and delivery ownership | `server.py`, `native_handoff.py`, `delivery_policy.py` |
| Presentation and payload budgets | `render.py`, `reading.py`, `card_limits.py`, `text.py` |
| Feishu I/O and CardKit sequencing | `feishu_client.py`, `cardkit.py` |
| Config, installation and safe recovery | `config.py`, `cli.py`, `install/` |
| Process credentials and service ownership | `process.py`, `runner.py`, `persistent_service.py` |
| Bounded display/notice persistence | `session_store.py`, `notice_lifecycle.py` |

Keep event normalization, state decisions, rendering and external I/O separable.
New substantial behavior belongs in a focused module with a tested boundary,
not another unrelated branch in the largest coordinator. Extract incrementally;
do not combine rebranding, broad rewrites and behavior fixes in one PR.

## Non-negotiable runtime boundaries

- Do not hand-edit installed Hermes source. Only
  `hermes_feishu_card/install/patcher.py` may patch managed Hermes targets.
- Prefer verified native extension points. A version string or documentation
  example is not capability proof: retain source hashes, exact call-site checks,
  drift rejection, idempotence and byte-for-byte restore tests.
- Unknown/unsupported delivery paths remain fail-open to Hermes. Once HFC has
  accepted a card path, preserve its ownership and suppress duplicate native gray
  text. Installation, authorization and uncertain filesystem mutation fail closed.
- Preserve profile/bot/chat/thread/sender/turn boundaries. A reply alias is not a
  new turn identity, and group membership does not authorize replacing another
  sender's card. Test terminal, duplicate, late and reordered events.
- Resolve interactions through the original Hermes handle exactly once. Never
  revive expired approvals or infer successful completion from missing events.
- Display checkpoints restore presentation, not execution or old authorization.
  Keep state, caches, replay windows and retries bounded; define failure behavior.
- Use the shared serializer and `card_limits.py` for every final combined payload.
  Do not truncate final answers or permission scope merely to fit a card.
- Keep slow external I/O outside session/message locks where the contract permits;
  preserve the documented post-lock work and terminal ordering when extracting.
- Never commit credentials, control/interaction tokens, raw chat/user identifiers,
  local `.env`, private checkpoints or unredacted screenshots. Managed launchers
  use private token files, with no insecure argv fallback. Keep health/logs redacted.

## Feature and configuration rules

Every substantive feature must name a user problem, authoritative data source,
current behavior, intended behavior, failure/unknown behavior and acceptance test.
Reuse existing capabilities before proposing a new one. Review new settings for
scope, default, precedence, validation, restart needs, migration and rollback.
Global -> profile -> bot precedence and explicit user values must remain clear.

Preserve existing defaults unless an explicitly approved migration says otherwise.
Progress is observed state, not invented percent/ETA. UI actions need a valid
current owner and authorization path. Desktop, Android and iOS visual acceptance
are separate evidence. See [feature rules](docs/wiki/feature-rules.md).
Quota, usage and cost must be attributed to the actual turn/model/provider;
an available account does not justify displaying its allowance on unrelated turns.
Visual quality is a core deliverable: compare the same content across states and
clients, preserve readable hierarchy and stable streaming layout, and validate
supported card components. Present visual options for Bailey's direction approval
before a card redesign; a rules/specification task does not authorize UI implementation.

## Validation: isolate first, then scale to risk

Run from the checkout root using its compatible environment. Prefer the existing
preflight tool so tests use private HOME/state and do not inspect production by
accident. Never repair production or install global dependencies to make tests pass.

```bash
python tools/preflight.py --check-only
python tools/preflight.py --suite focused --module docs
python tools/preflight.py --suite focused --module runtime --module render
python tools/preflight.py --suite focused --module install
python tools/preflight.py --suite focused --module process
python tools/preflight.py --suite full
git diff --check
```

Choose only the relevant commands, not every group for every change. `AGENTS.md`
and some modules are not auto-mapped: select groups explicitly and add focused
tests from the maintenance matrix. Pure docs/rules changes need docs/metadata and
link/contract checks; they do not require a new product release or a redundant
local full run. Runtime/stability changes need meaningful failing reproduction,
normal/failure/duplicate/late-event assertions and the affected integration paths.
Run full regression before release, broad runtime claims or substantial refactors.

Full runs require `HFC_FIXED_TAG_SOURCE_ROOT` pointing to the verified clean Git
fixture described in `docs/testing.md`; never substitute production or an archive
without Git identity. Missing fixtures/skips must be reported. The current
preflight sanitizes `HFC_UPSTREAM_*`; latest stable/main compatibility therefore
needs the separate pinned matrix in `.github/workflows/tests.yml` (or a documented
isolated equivalent). Do not claim those checks from a preflight full pass alone.

Installed-runtime changes also require ordinary non-editable wheel provenance and
real child-process checks. UI changes need relevant real-client checks. Distinguish
source tests, mock/local HTTP, public installation, real platform API, visual QA
and actual Gateway/model execution. Preserve failures before a justified rerun.

## Delivery and release

- PRs state the user-visible problem, final change, affected boundary, tests and
  remaining uncertainty. Keep runtime fixes and unrelated cleanup separate.
- Report what changed, what passed and what remains unverified. Test totals are
  not branch coverage, service readiness or proof of production acceptance.
- Follow `docs/wiki/release-playbook.md` for an authorized release: version/docs,
  full checks, exact merge/tree, annotated tag, CI, assets/checksums and isolated
  public install. Do not move a published tag to hide a failure.
- Preserve full tag/release history, merged or materially absorbed PRs,
  accepted issue evidence, commit authors and accurate `Co-authored-by` trailers.
  Never fabricate authorship. Credit relevant contributors in both READMEs and
  release notes; retain earlier credits. Issue evidence is distinct from code authorship.
  Audit both the release interval and historical credits before publishing.
- Public project knowledge belongs in repository docs. Synchronize personal/global
  memory or an external wiki only when explicitly requested; do not make that a
  hidden prerequisite for completing repository work.
