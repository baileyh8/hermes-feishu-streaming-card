# Hermes Feishu Streaming Card Installer

[中文](README.md) | [English](README.en.md)

This package contains lightweight installers for `hermes-feishu-streaming-card`.
They install the Python package, configure Feishu credentials, install the Hermes
hook, start the sidecar, and print the health-check command.

From V3.6.2, setup also checks the Python interpreter used by Hermes Gateway
itself. When `HERMES_DIR/venv/bin/python`, `HERMES_DIR/.venv/bin/python`, or the
Windows equivalent exists, the same package release is installed into that
runtime venv before `gateway/run.py` is patched. This prevents a hook from being
installed into Hermes while `hermes_feishu_card.hook_runtime` is only available
in the user's shell Python.

From V3.6.6, if `--hermes-dir` points at the wrong directory and
`gateway/run.py` is missing, `doctor --explain` and `install` read `hermes -V`
and suggest the `Project:` path reported by the Hermes CLI.

From V3.8.0, card rendering separates the primary answer from the auxiliary
reasoning/tool timeline, removes duplicate footer tool summaries, and runs the
Hermes runtime import check from the Hermes project root. Re-run `setup` or
`install` after upgrading so the refreshed hook and runtime package match.

From V3.8.1, high-frequency `thinking.delta` / `answer.delta` events are
coalesced inside the Hermes Gateway process before reaching the sidecar. The
same release adds read-only `/hfc help`, `/hfc status`, `/hfc doctor`, and
`/hfc monitor` cards for Feishu-side diagnostics.

From V3.8.2, pre-tool answer blocks stay in the primary card body until the
next pre-tool answer or terminal event arrives, then move into the auxiliary
timeline. Completed cards strip already archived intermediate prefaces, and the
timeline renders reasoning and tool details with separate compact hierarchy.

From V3.8.3, independent slash-command prompts such as `/new`, `/reset`,
`/undo`, and `/model` can render as standalone Feishu command cards. In the
V3.8.x line, `/update` remained Hermes' native background upgrade command.

From V4.2.0, an exact bare `/update` in a verified Feishu private chat uses a
120-second maintenance confirmation card. After confirmation, an independent
runtime runs only the official `hermes update --yes`, reinstalls the exact HFC
wheel cached by setup, restores the managed hook and services, and verifies the
result. Confirmation authorizes the official updater to fetch the latest
`origin/main` at execution time; a post-confirmation remote advance is reported
after services are safely restored. The card flow refuses secondary/custom
`HERMES_HOME` layouts unless the Gateway proves the drain marker directory
matches the checkout. Group, non-Feishu, alias, and parameterized update commands retain
Hermes' original behavior. Run `hermes-feishu-card maintenance status` before
using the card flow.

V4.2.1 registers the live Gateway runner before runtime control starts. The
first authenticated heartbeat after a restart can therefore prove the complete
turn/cron/API aggregate immediately, and the first bare private-chat `/update`
does not require an unrelated warm-up message. Missing aggregate evidence still
fails closed.

V4.2.2 keeps the native card-action callback fast while asynchronously PATCHing
the original `/update` confirmation card. Cancel now becomes a visible terminal
`已取消更新` state and never launches the updater; confirm first shows the
locking/preparing transition, then schedules the independent maintenance job.

V4.2.3 forwards the update evidence fingerprint through the Feishu/Lark
WebSocket hook to the sidecar. Confirm and cancel therefore reach the existing
evidence-bound transition logic; missing or mismatched evidence remains
fail-closed.

V4.2.5 hardens quoted-turn identity and the maintenance updater, limits doctor
to executable integrity actions, and makes installer `latest` resolve to one
pinned stable release tag or stop before package/setup mutation. Release assets
now require an exact tested annotated tag.

V4.2.6 adds exact Hermes Agent 0.20 Base-ledger compatibility, keeps repeated
choice requests at the latest chat position, preserves a substantial streamed
answer before a short terminal postscript, and repairs bare Feishu `/update`
for standard venv symlinks, slow Git fetches, and Hermes 0.20 version reporting.

V4.3.0 adds a source-proven Hybrid integration for Hermes `v2026.8.3`,
transactional V3 install/restore ownership, direct single-owner runtime
interactions, and an optional linger-verified persistent systemd user service.
V4.2.11 freezes superseded streaming cards as read-only interaction handoff
snapshots while the newest interaction card remains authoritative. V4.2.10
authenticates non-loopback sidecar callbacks and result reads, enforces
absolute interaction expiry, and strengthens cross-platform CI/security gates.
V4.2.9 includes authenticated clarify forms, non-blocking slash confirmations,
and answer-backed quote summaries. V4.2.8 persists process-supplied Feishu credentials into the selected private
dotenv file across macOS/Linux, Docker, and Windows installers. V4.2.7 hardened
Windows installation and process startup: cold SDK/runtime probes
allow 30 seconds, manifests use portable POSIX relative paths while safely
reading exact legacy backslash paths, parent `HERMES_HOME` layouts are detected,
and detached venv runners can rebind only a strictly verified owned pidfile.
PowerShell now propagates native install failures instead of printing `done`.

V4.2.4 gives every new Feishu/Lark topic reply its real incoming message ID.
Consecutive replies quoting the same message therefore open independent cards
instead of overwriting the first card; in-turn stream events still resolve
through the reply alias.

V4.2.6 recognizes Hermes Agent 0.20's awaited
delivery-ledger calls, keeps a substantial streamed answer visible when a short
terminal validation postscript follows it, and promotes every agent choice
request to a fresh latest-position card for long multi-turn conversations. The
maintenance flow also preserves standard venv `python` symlink paths when it
binds the Hermes runtime and launches its independent updater.

From V3.8.4, those standalone command cards also work in Feishu/Lark WebSocket
long-connection deployments by patching the Feishu adapter's native interactive
card action path; local/private sidecars no longer have to fall back to gray
native text for `/new` or `/model`, and no public HTTP callback is required for
these native slash/model command cards.

From V3.8.5, always-allowed or no-confirm slash-command results also stay in
Feishu/Lark interactive cards. Re-run `install` after upgrading so the Hermes
Gateway hook passes the current event into the command-card adapter patch.

From V3.8.8, native Hermes runtime notices such as `Working` heartbeats,
context-window/compression notices, automatic session resets, skill loading, and
self-improvement reviews prefer Feishu/Lark cards or compact standalone notice
cards instead of scattered gray native text.

From V3.8.9, Feishu/Lark topic replies keep the same card session even when
Hermes emits later stream events with a different internal `message_id`. Tool
timeline updates and `system.notice` messages resolve through the original reply
anchor instead of freezing the topic card or leaking duplicate gray messages.

From V3.8.10, group `/hfc status` reports chat binding state, fallback/default
routing, and slash-command behavior boundaries while leaving real @robot and
allowlist admission to Hermes Gateway. Tool timeline entries can also show
argument summaries, duration, and failure reason when Hermes exposes them.

From V3.8.11, accepted `/hfc` diagnostic commands return to Hermes Gateway
before slow Feishu/Lark card delivery completes. This keeps `/hfc status`
card-only and prevents the duplicate gray native `Unknown command /hfc` reply.

From V3.8.12, completed cards that include attachment summaries such as
`colors.csv` or `styles.csv` remain card-only after successful Feishu/Lark card
delivery. Real file/media paths still keep Hermes' native attachment delivery
path available.

From V3.8.13, Hermes upgrades are more resilient: version metadata accepts
`v2026.7.7.2`, `0.18.2`, `v2026.7.20`, `0.19.0`, and descriptive strings such as
`Hermes Agent v0.18.2 (...)`; if readable version metadata is unparseable,
verified `gateway/run.py` anchors can still decide support. `repair` also
clears stale backup/manifest state left after an upstream Hermes upgrade
replaces `gateway/run.py` with an unpatched file.

From V3.8.14, agent clarify/approval buttons also work in Feishu/Lark
WebSocket long-connection deployments. Native `interaction.select` card-action
clicks are forwarded to the sidecar `/card/actions` endpoint and can update the
same card without requiring a public callback URL.

From V3.8.15, input file context such as `.docx` values in Hermes `files` locals
stays as a card attachment summary without forcing Hermes' native final text
reply. Explicit `MEDIA:/tmp/...` and output media fields still keep native
file/media delivery available.

From V3.8.16, Feishu/Lark topic groups that reuse the same `message_id` across
consecutive turns send a fresh card for the second and later messages, while
duplicate `message.started` events during an active turn still stay ignored.

From V3.8.17, cron jobs using routing-intent delivery values such as `origin`,
`all`, or `origin,all` resolve to Feishu targets and send cards again. The
release preserves `deliver=local` as local-only/no delivery and keeps explicit
dict-shaped `deliver` configs compatible.

From V3.8.18, cron jobs created from Feishu topic-group threads preserve
`thread_id` and return cards to the originating thread. Thread ids from
non-Feishu origins are ignored.

From V3.9.0, setup accepts explicit `--profile-id`, `--event-url`, and
`--env-file` routing inputs. For profile and event URL, precedence is explicit
argument, process environment, selected env file, then the safe default.
Only `doctor` prints the complete redacted identity/profile/event-endpoint route
chain; `status` summarizes runtime routing and profile events, while `/health`
reports routing health. Install/setup automatically repair only known-safe hook state;
pass `--no-repair` to opt out, and unverifiable user edits are never replaced.
Feishu/Lark operations cards are an optional UI for diagnosis, recheck, safe
repair, and restart: private chats do not compare operators, while group
confirmation stays with the initiating operator. If the card is unavailable,
use the corresponding CLI command. This does not alter normal card layout or
footer behavior. PR #84 / @Zanetach contributed card progress-status routing and `.env` allowlist expansion for profile environment support. The transport root is created with private permissions in the
sidecar state directory, so no secret needs to be configured.

From V3.9.1, completed-answer archival, interrupted-session terminal updates,
and model-picker callbacks include focused reliability fixes. Repair can also
recover a verified marker-only hook state when the manifest, backup, expected
patched hash, and all non-marker content agree; unknown edits still fail
closed. Source-stripped Hermes roots are shown as `version: unknown
(source-stripped metadata)`. Local health checks bypass ambient proxies. These
changes do not alter the normal streaming-card footer/layout.

From V3.10.0, bare Feishu/Lark `/resume` can use a native session dropdown;
typed `/resume <target>` and every unavailable/empty/unsupported path continue
through Hermes' original text handler. Group/topic callbacks require the
initiating user, while private chats do not add an extra identity comparison.
Recognized model names receive HTML-escaped semantic color inside the existing
footer; its layout, field order, separators, and text size are unchanged.

V4.1.0 adds exact `bindings.native_chats`, lossless
`card.table_overflow_mode: compact`, signed `runtime.hello` /
`runtime.heartbeat`, and explicit `service.manager`. New setup files use
`integrity.mode: safe`; an existing config without the section loads as
`notify` and is not silently migrated. To opt an older verified install into
safe mode, run `hermes-feishu-card integrity migrate-safe --config CONFIG
--hermes-dir HERMES_DIR --yes`, then restart the sidecar as reported by
`sidecar.restart_required: true`. The migration itself reports
`gateway.restart_required: false`; a later strict repair may require an
operator-chosen Gateway restart, but HFC never performs it automatically.

V4.1.1 makes the upgrade path identity-aware. `setup/install` probes the
detected Hermes runtime venv with isolated Python, requires the package to come
from that venv's `site-packages`, and compares package/Python identity from
sidecar `/health` before a managed restart. The verified canonical Hermes root
is passed directly to the runner, so a conflicting selected environment cannot
retarget monitoring. A detached child verifies its exact PID/token manager
record before reading config or listening; failed parent registration makes the
child exit itself. Detached V4.1.1 sidecars stop by a loopback process-token
request and never by signalling a numeric PID/PGID. A specifically configured
non-loopback listener receives a same-family loopback management listener for
local health and shutdown; wildcard listeners are not duplicated. Stop
pre-V4.1.1 or pidfile-less sidecars manually before rerunning setup. Waiting for
the first authenticated heartbeat does not create a fence when the verified
disk plan is already installed. Use `integrity acknowledge-review` only for a
twice-verified installed plan, stopped sidecar, absent pidfile, matching
target-bound fence, and unchanged CAS snapshot; then manually restart sidecar
and Hermes Gateway.

Hermes compatibility evidence is reported at two different levels:

| Hermes release | Automated strategy detection | Real-source validation |
|---|---|---|
| Hermes 0.19.0 / `v2026.7.20` | Requires exact run + Base anchors and writes `manifest_version: 2` for run, required Base, and optional Cron | A read-only check against real local source confirmed V4.1 startup-before-redelivery, recovery-before-send, exact Base patch idempotency, and multi-target restore behavior |

The real local source check does not claim that a real Gateway process or a
real Feishu conversation passed E2E. Those remain separate release gates.
This automated strategy detection and the real local source validation are
separate evidence: the former selects and enforces the managed target set,
while the latter checks the exact `v2026.7.20`, `0.19.0` source anchors.
Legacy manifest v1 cannot prove Base ownership. Upgrade/repair migrates it only
when every current source, backup, owned marker, and reversible patch verifies;
future manifest versions fail closed and require the matching newer installer.

`card.text_sizes` can configure the `body`, `reasoning`, `tool`, `notice`, and
`footer` roles, with optional `default` / `pc` / `mobile` mappings. Physical
card width/height are controlled by the Feishu/Lark client and are not an
installer or sidecar setting.

The default `127.0.0.1` / `localhost` deployment uses a local-process trust
boundary: hook event requests remain compatible with existing local installs.
Binding the sidecar to a non-loopback address is rejected unless
`server.allow_non_loopback: true` is set explicitly. In that mode, event authentication
is mandatory and the hook signs the exact `/events` request
body with the private transport root stored in the sidecar state directory.
The signature prevents unauthenticated injection and replay; it does not
encrypt traffic. Keep the route on a private trusted network, and place TLS or mTLS
in front of the sidecar before any public or cross-host deployment. Never put
the transport root in `config.yaml`, environment variables, logs, cards, or
screenshots.

Current installers default `PIP_ROOT_USER_ACTION=ignore` so Debian/Ubuntu root
installs do not print pip's root-user warning. If Python reports
`externally-managed-environment`, `install.sh` and `install-docker.sh` retry with
`--break-system-packages` and print a concise recovery message after the package
install succeeds.

`install.sh` prefers the Python interpreter under the selected Hermes venv. Set
`HFC_PYTHON` only when an explicit interpreter override is required.
`service.manager` accepts `auto`, `systemd-user`, `systemd-system`, and
`detached`. `auto` uses a working user manager when available and otherwise
uses the owned detached process; it never probes the system bus, invokes
`sudo`, or silently crosses a privilege boundary. `systemd-system` is an
explicit Linux-only transient-unit opt-in and writes no persistent unit under
`/etc`. Docker and other containers should select `detached`.

On Linux, guided `setup` now prefers the owned persistent path when the selected
user manager works and linger is already enabled. It never enables linger or
crosses into a system service. If the capability is unavailable, setup starts
the existing transient sidecar, prints that it will not survive a reboot, and
shows the exact `enable` command to run after the user/admin policy enables
linger. Use `setup --transient` to opt out even when persistence is available.

The equivalent explicit persistent command is:

```bash
hermes-feishu-card enable --config /absolute/config.yaml \
  --hermes-dir /absolute/hermes-agent --yes
```

This writes a real systemd user unit plus a private SHA-256 ownership manifest,
then verifies the running package/Python identity. It refuses missing linger,
unknown same-name units, drift, or unsafe shutdown. Remove the owned unit with
`hermes-feishu-card disable`; standalone `start` remains transient by default.

## macOS / Linux

```bash
bash install.sh
```

## Windows PowerShell

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `HFC_VERSION` | `latest` | Git tag or branch to install, such as `v3.10.0`, `v3.9.1`, `v3.8.18`, `v3.6.6`, or `main`. |
| `HFC_REPO` | `baileyh8/hermes-feishu-streaming-card` | GitHub repository to install from. |
| `HERMES_DIR` | `~/.hermes/hermes-agent` | Hermes Agent root directory. |
| `HFC_PYTHON` | Hermes venv, then `PYTHON`/`python3` fallback | Explicit Python interpreter override. |
| `HFC_PIP_USER` | automatic | Hermes venv installs omit `--user`; system Python uses `--user`. Set `0` or `--user` only to override. |
| `HFC_CONFIG` | `~/.hermes/config.yaml` | Sidecar config path. |
| `HFC_ENV_FILE` | Same directory as `HFC_CONFIG`, named `.env` | Feishu credential file. |
| `FEISHU_APP_ID` | unset | Feishu/Lark app id. |
| `FEISHU_APP_SECRET` | unset | Feishu/Lark app secret. |
| `HFC_SKIP_START` | `0` | Set to `1` to install hook without starting sidecar. |
| `HFC_NO_PROMPT` | `0` | Set to `1` for non-interactive installs. |
| `HERMES_FEISHU_CARD_SERVICE_MANAGER` | `auto` | `auto`, `systemd-user`, `systemd-system`, or `detached`; containers use `detached`. |
| `HERMES_FEISHU_CARD_INTEGRITY_MODE` | config/migration value | `safe`, `notify`, or `off`; do not place transport secrets here. |

## Docker Containers

Use `install-docker.sh` inside an existing Hermes container. It defaults to
`/opt/hermes` for Hermes and `/opt/data/config.yaml` for sidecar config. The
script selects Hermes venv Python and does not fall back to system Python unless
`HFC_PYTHON` is set.

Compose uses `HERMES_FEISHU_CARD_SERVICE_MANAGER=detached`. The setup container
runs the published `install-docker.sh` as root so it can prepare shared-volume
ownership. The sidecar, patched Gateway, and probe then run as non-root ordinary
container processes. The topology does not start systemd, invoke `sudo`, request
a privileged container, or mount host system-service directories.

```
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export HFC_VERSION=v4.4.1
bash install-docker.sh
```

V3.8.6 also supports Docker/source-stripped Hermes roots that contain
`gateway/run.py` but no top-level `VERSION` file or `.git` metadata. In that
case `doctor --explain` reports `version_source: gateway anchors` and uses the
verified Gateway code anchors to choose the hook strategy.

The V4.1.0 automated Compose gate runs the published installer against a fixture
Hermes tree, imports the patched Gateway, waits for signed `runtime.hello`
readiness, sends a signed `POST /events`, and checks the resulting receipt and
sanitized health metrics. A passing automated gate is required before release,
but it does not replace acceptance on a real Docker deployment or in real
Feishu. Real Docker and real Feishu scenarios remain pending acceptance until
their respective release evidence is recorded.

V4.1.1 must repeat this gate with the Hermes-venv package/Python identity and
upgrade-restart branches. That result remains pending until the release
candidate workflow completes.

## One-Line Install

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.ps1 | iex
```

## After Install

```bash
python3 -m hermes_feishu_card.cli status --config ~/.hermes/config.yaml
python3 -m hermes_feishu_card.cli doctor --config ~/.hermes/config.yaml --hermes-dir ~/.hermes/hermes-agent --explain
```

The installer stores missing Feishu credentials in a local `.env` file next to
the selected config path. Do not commit this file.

### Installer version resolution

`latest` resolves once through the GitHub latest stable release API and installs the pinned `vX.Y.Z` Git ref. If lookup, JSON parsing, or tag validation fails, the installer stops before pip, setup, doctor, credentials, or Docker state mutation. An explicit release tag stays pinned and bypasses the release API; `--version main` (PowerShell: `-Version main`) is the only opt-in moving development branch.
