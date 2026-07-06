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
`/undo`, and `/model` can render as standalone Feishu command cards. `/update`
remains Hermes' background upgrade command and does not use an interactive
command card.

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

Current installers default `PIP_ROOT_USER_ACTION=ignore` so Debian/Ubuntu root
installs do not print pip's root-user warning. If Python reports
`externally-managed-environment`, `install.sh` and `install-docker.sh` retry with
`--break-system-packages` and print a concise recovery message after the package
install succeeds.

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
| `HFC_VERSION` | `latest` | Git tag or branch to install, such as `v3.8.9`, `v3.6.6`, or `main`. |
| `HFC_REPO` | `baileyh8/hermes-feishu-streaming-card` | GitHub repository to install from. |
| `HERMES_DIR` | `~/.hermes/hermes-agent` | Hermes Agent root directory. |
| `HFC_CONFIG` | `~/.hermes/config.yaml` | Sidecar config path. |
| `HFC_ENV_FILE` | Same directory as `HFC_CONFIG`, named `.env` | Feishu credential file. |
| `FEISHU_APP_ID` | unset | Feishu/Lark app id. |
| `FEISHU_APP_SECRET` | unset | Feishu/Lark app secret. |
| `HFC_SKIP_START` | `0` | Set to `1` to install hook without starting sidecar. |
| `HFC_NO_PROMPT` | `0` | Set to `1` for non-interactive installs. |

## Docker Containers

Use `install-docker.sh` inside an existing Hermes container. It defaults to
`/opt/hermes` for Hermes and `/opt/data/config.yaml` for sidecar config. The
script selects Hermes venv Python and does not fall back to system Python unless
`HFC_PYTHON` is set.

```
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export HFC_VERSION=v3.8.9
bash install-docker.sh
```

V3.8.6 also supports Docker/source-stripped Hermes roots that contain
`gateway/run.py` but no top-level `VERSION` file or `.git` metadata. In that
case `doctor --explain` reports `version_source: gateway anchors` and uses the
verified Gateway code anchors to choose the hook strategy.

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
