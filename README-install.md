# Hermes Feishu Streaming Card Installer

[中文](README.md) | [English](README.en.md)

This package contains lightweight installers for `hermes-feishu-streaming-card`.
They install the Python package, configure Feishu credentials, install the Hermes
hook, start the sidecar, and print the health-check command.

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
| `HFC_VERSION` | `latest` | Git tag or branch to install, such as `v3.6.1` or `main`. |
| `HFC_REPO` | `baileyh8/hermes-feishu-streaming-card` | GitHub repository to install from. |
| `HERMES_DIR` | `~/.hermes/hermes-agent` | Hermes Agent root directory. |
| `HFC_CONFIG` | `~/.hermes/config.yaml` | Sidecar config path. |
| `HFC_ENV_FILE` | Same directory as `HFC_CONFIG`, named `.env` | Feishu credential file. |
| `FEISHU_APP_ID` | unset | Feishu/Lark app id. |
| `FEISHU_APP_SECRET` | unset | Feishu/Lark app secret. |
| `HFC_SKIP_START` | `0` | Set to `1` to install hook without starting sidecar. |
| `HFC_NO_PROMPT` | `0` | Set to `1` for non-interactive installs. |

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
```

The installer stores missing Feishu credentials in a local `.env` file next to
the selected config path. Do not commit this file.
