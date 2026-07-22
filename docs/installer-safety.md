# 安装安全

[中文](installer-safety.md) | [English](installer-safety.en.md)

安装器的目标是只做可验证、可恢复的最小写入。版本文案变化可以通过 `gateway/run.py` anchors 兜底，但代码结构、备份、manifest 或文件安全校验不确定时仍应 fail-closed。

## 安装前检查

安装前必须确认：

- Hermes 目录存在，且包含预期的 `gateway/run.py`。
- Hermes 版本 metadata 可解析，或 `gateway/run.py` 存在当前 hook 可识别的结构。支持 `VERSION=v2026.4.23+`、Git tag `v2026.4.23+`、`0.18.x` 语义版本、描述型版本字符串，以及不可解析版本配合可验证 anchors 的兜底。
- `gateway/run.py` 中存在当前 hook 可识别的插入位置。
- 既有安装状态、备份和 manifest 没有互相矛盾。
- 若 Hermes 目录中存在 `venv/bin/python`、`.venv/bin/python` 或 Windows `Scripts/python.exe`，该 runtime Python 必须能 import `hermes_feishu_card.hook_runtime`；不能 import 时，安装器会先把当前插件版本安装到该 venv。

检查失败时不写入 Hermes 文件。

安装前可先运行只读诊断：

```bash
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --explain
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --json
```

诊断输出会展示 Hermes 是否支持、Hermes root、`gateway/run.py` 路径、`run_py_exists`、`version_source`、`version`、`minimum_supported_version`、`hook_strategy`、`compatibility`、anchors 和 `reason`。V3.9.1 起，只有 anchors 可用而版本 metadata 缺失的 source-stripped Hermes 会显示 `version: unknown (source-stripped metadata)`，避免把 anchor 策略误解为实际版本号。V3.6.2 起还会展示 `runtime_import`，用于确认 Hermes Gateway 实际运行的 Python 是否能 import `hermes_feishu_card.hook_runtime`。当 Hermes Feishu adapter 使用 `extra_ua_tags` 时，诊断还会检查 Gateway venv 中 `lark_oapi.ws.Client` 的真实构造签名；不兼容时输出 `feishu_sdk_incompatible`。`setup/install` 会安装已验证的 `lark-oapi==1.6.8` 并在能力复检通过后才继续安装 hook。`--explain` 会把 runtime import、Feishu SDK、streaming 配置、manifest/backup/run.py 安装状态和下一步建议整理成人可读摘要；`--json` 会输出包含 `schema_version`、顶层 `status`、`runtime_import`、`feishu_sdk`、`install_state` 和 `recommendations` 的机器可读报告，适合 issue 模板和自动化排障。`doctor` 所有模式都是只读诊断，不会写入 Hermes 文件。

`install` 在拒绝不支持的目录时也会输出同一组 Hermes 检测信息，便于用户判断是版本过低、版本文件不可读、`gateway/run.py` 缺失，还是 hook 锚点结构不兼容。

## Repair 自救

```bash
python3 -m hermes_feishu_card.cli repair --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli setup --repair --hermes-dir ~/.hermes/hermes-agent --config ~/.hermes_feishu_card/config.yaml --yes
```

`repair` 只修复本项目能验证的安装状态文件：backup 缺失但当前 `run.py` 能安全移除本项目 owned patch 时，会重建 backup；manifest 缺失、损坏或因 backup 重建而过期时，会重建 manifest；当前无补丁源码与旧 backup 完全一致时，会自动清理 stale backup/manifest。V3.9.1 还允许恢复严格受限的 marker-only 损坏：manifest 的 patched hash 必须等于从已验证 backup 重建出的预期补丁 hash，并且当前文件与预期补丁只能在本项目 owned BEGIN/END marker 行上不同。

如果 Hermes 确实在升级时替换了无补丁源码，使当前 `run.py`（或 cron source）与已验证的旧 backup 不同，默认恢复会拒绝把它当成普通 stale state。确认差异来自有意的 Hermes 升级后，可显式执行：

```bash
# 一步恢复旧状态并从升级后的源码重新安装
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --accept-hermes-upgrade --yes

# 或分两步执行
python3 -m hermes_feishu_card.cli repair --hermes-dir ~/.hermes/hermes-agent --accept-hermes-upgrade --yes
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
```

`setup` 同样支持 `--accept-hermes-upgrade`。该开关不会用旧 backup 覆盖升级后的 Hermes 源码，只会清理经过校验的旧 HFC backup/manifest；随后安装器以当前升级后源码创建新 backup 并重新打补丁。它仍要求当前源码可解析且具备受支持的 hook anchors、manifest 有效、旧 backup 未变化并与 manifest hash 一致。backup 缺失或损坏、manifest 无效、symlink、文件不可读、未知 marker、当前源码不受支持，或仍残留本项目 owned patch 时都会继续 fail-closed。

`status` 和 `start` 会从显式 `--hermes-dir`、选定 env file、配置旁 `.env` 或进程环境读取 `HERMES_DIR`，只读检查 hook 安装状态。若 Hermes 升级替换了源码但旧 backup/manifest 仍可验证，输出 `hook.status: upgrade_repair_required`，并提示上述显式恢复命令及 `hermes gateway start`；`start` 会在启动 sidecar 前拒绝继续，避免“sidecar 正常但 Gateway hook 已丢失”的静默降级。若检测到用户改动、损坏或不受支持的源码，则输出 `manual_review_required`，不提供 `--accept-hermes-upgrade` 捷径。

## 备份与 manifest

安装会先保存 `gateway/run.py` 备份，再写入 manifest。manifest 至少记录：

- `run_py` 相对路径。
- 已安装后 `run.py` 的 hash。
- `backup` 相对路径。
- 备份文件 hash。

`restore` 和 `uninstall` 会使用 manifest 验证当前 `run.py` 与备份是否仍是安装器认识的状态。若发现用户或其他工具改动过相关文件，命令应拒绝覆盖。

## 原子写入

安装器写入 `run.py`、备份和 manifest 时使用临时文件替换，避免中途失败留下截断文件。若安装流程中任一步失败，应回滚已写入内容并清理半安装状态。

## 恢复和卸载

```bash
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli uninstall --hermes-dir ~/.hermes/hermes-agent --yes
```

`restore` 用于恢复安装前的 Hermes 文件；`uninstall` 当前同样移除本插件拥有的 hook 和安装状态。两者都不应覆盖无法校验的用户改动。

从 legacy/dual 历史安装迁移时，先阅读 `docs/migration.md`。历史 `legacy/installer_v2.py`、`legacy/gateway_run_patch.py`、`legacy/patch_feishu.py` 等入口写入的补丁不属于当前安装器 manifest 管理范围，不能假定当前 `restore` 能自动识别并清理。

## 降级行为

sidecar 不可用、超时或返回错误时，Hermes hook 应让 Hermes 继续原生文本回复。卡片不可用是插件故障，不应升级为 Agent 主流程故障。

hook import 或 emit 异常同样保持 fail-open，但不应完全静默。V3.6.2 起，注入的 hook block 会向 Hermes stderr 写入 `[hermes-feishu-card] hook failed: ...`，便于从 Gateway 日志定位 runtime venv、import 或 sidecar emit 问题。
