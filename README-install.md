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
| `HFC_VERSION` | `latest` | Git tag or branch to install, such as `v3.8.2`, `v3.6.6`, or `main`. |
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
export HFC_VERSION=v3.8.2
bash install-docker.sh
```

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
