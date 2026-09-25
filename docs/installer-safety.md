# 安装安全

[中文](installer-safety.md) | [English](installer-safety.en.md)

安装器的目标是只做可验证、可恢复的最小写入。版本文案变化可以通过受支持源码 anchors 兜底，但代码结构、备份、manifest 或文件安全校验不确定时仍应 fail-closed。Hermes 0.19.0、`v2026.7.20+` 或检测到精确 delivery-ledger 结构时，`gateway/run.py` 与 `gateway/platforms/base.py` 是不可拆分的必需目标；可选 Cron 目标继续按能力检测。

## 安装前检查

安装前必须确认：

- Hermes 目录存在，且包含预期的 `gateway/run.py`；需要 exact Base 时还必须存在 `gateway/platforms/base.py`。
- Hermes 版本 metadata 可解析，或源码存在当前 hook 可识别的结构。支持 `VERSION=v2026.4.23+`、Git tag `v2026.4.23+`、`0.18.x` / `0.19.x` 语义版本、描述型版本字符串，以及不可解析版本配合可验证 anchors 的兜底。
- `gateway/run.py` 中存在当前 hook 可识别的插入位置；需要 exact Base 时，Base 的媒体提取、obligation、ledger attempting/delivered 与最终 send 结构也必须全部精确匹配。
- 既有安装状态、备份和 manifest 没有互相矛盾。
- 若 Hermes 目录中存在 `venv/bin/python`、`.venv/bin/python` 或 Windows `Scripts/python.exe`，该 runtime Python 必须能 import `hermes_feishu_card.hook_runtime`；不能 import 时，安装器会先把当前插件版本安装到该 venv。

检查失败时不写入 Hermes 文件。

## macOS 行为（transient sidecar 属预期）

`enable` / 持久 user service 依赖 Linux systemd；本项目不代管 macOS LaunchAgent（见 [#183](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/183)）。macOS 上 `setup` / `install.sh` 默认启动 detached sidecar，显示持久化不可用提示，并不再建议运行必然失败的 `enable`。

如需用户登录后自启动，可自行配置用户级 LaunchAgent：使用 `RunAtLoad=true` 一次执行 `hermes-feishu-card start`，**不要设置 `KeepAlive=true`**。`start` 会分离 sidecar 子进程、写入 HFC 受管 pidfile 后退出；持续重启这个短命启动命令会造成反复拉起。LaunchAgent 的 executable、`--config`、`--hermes-dir` 及需要的 `--env-file` 均使用已确认的绝对路径。此方案不提供登录前的开机启动或崩溃后自动拉起。

这与自建 LaunchAgent **直接运行 runner** 不同：后者没有 HFC 受管 pidfile。若安装器报告 `a running sidecar cannot be managed safely without a verified pidfile`，先确认现有进程的实际管理者；缺少 pidfile 本身不能证明是 launchd。如果确认由自建 LaunchAgent 直接托管，核对 label 后使用 `launchctl bootout gui/$(id -u)/<label>`（或对已确认 plist 执行 `launchctl unload /absolute/path/to/job.plist`）停止该实例，再重跑官方安装器。若归属不同或未知，先识别并手动停止对应服务，安装器不会猜测或强杀未知进程。

安装前可先运行只读诊断：

```bash
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --explain
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --json
```

诊断输出会展示 Hermes 是否支持、Hermes root、`gateway/run.py` / exact Base 路径与存在状态、`base_required`、`exact_delivery_contract`、`version_source`、`version`、`minimum_supported_version`、`hook_strategy`、`compatibility`、anchors 和 `reason`。V3.9.1 起，只有 anchors 可用而版本 metadata 缺失的 source-stripped Hermes 会显示 `version: unknown (source-stripped metadata)`，避免把 anchor 策略误解为实际版本号。V3.6.2 起还会展示 `runtime_import`，用于确认 Hermes Gateway 实际运行的 Python 是否能 import `hermes_feishu_card.hook_runtime`。当 Hermes Feishu adapter 使用 `extra_ua_tags` 时，诊断还会检查 Gateway venv 中 `lark_oapi.ws.Client` 的真实构造签名；不兼容时输出 `feishu_sdk_incompatible`。`setup/install` 会安装已验证的 `lark-oapi==1.6.8` 并在能力复检通过后才继续安装 hook。`--explain` 会把 runtime import、Feishu SDK、streaming 配置、manifest/backup/多目标安装状态和下一步建议整理成人可读摘要；`--json` 会输出包含 `schema_version`、顶层 `status`、`runtime_import`、`feishu_sdk`、`install_state` 和 `recommendations` 的机器可读报告，适合 issue 模板和自动化排障。`doctor` 所有模式都是只读诊断，不会写入 Hermes 文件。

`install` 在拒绝不支持的目录时也会输出同一组 Hermes 检测信息，便于用户判断是版本过低、版本文件不可读、`gateway/run.py` / required Base 缺失，还是 hook 锚点结构不兼容。

## Repair 自救

```bash
python3 -m hermes_feishu_card.cli repair --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli setup --repair --hermes-dir ~/.hermes/hermes-agent --config ~/.hermes_feishu_card/config.yaml --yes
```

`repair` 只修复本项目能验证的安装状态文件。V4.1 多目标安装要求 run、required Base 与 optional Cron 的 ownership 证据一致：任何 Base marker、backup 或 manifest 证据存在时，都不能只恢复 run 后清理状态。backup 缺失但对应源码能安全移除本项目 owned patch 时，可在完整事务证据下重建；manifest 缺失、损坏或因 backup 重建而过期时，也只能在全部受管目标一致时重建。当前无补丁源码与旧 backup 完全一致时可清理 stale state。V3.9.1 的 marker-only 恢复仍要求 manifest patched hash 等于从已验证 backup 重建的预期补丁 hash，且当前文件与预期补丁只能在本项目 owned BEGIN/END marker 行上不同。

如果 Hermes 确实在升级时替换了无补丁源码，使当前 run、required Base 或 optional Cron 与已验证的旧 backup 不同，默认恢复会拒绝把它当成普通 stale state。确认差异来自有意的 Hermes 升级后，可显式执行：

```bash
# 一步恢复旧状态并从升级后的源码重新安装
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --accept-hermes-upgrade --yes

# 或分两步执行
python3 -m hermes_feishu_card.cli repair --hermes-dir ~/.hermes/hermes-agent --accept-hermes-upgrade --yes
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
```

`setup` 同样支持 `--accept-hermes-upgrade`。该开关不会用旧 backup 覆盖升级后的 Hermes 源码，只会清理经过校验的旧 HFC backup/manifest；随后安装器以当前升级后源码为全部必需目标创建新 backup 并重新打补丁。它仍要求当前源码可解析且具备受支持的 hook anchors、manifest 有效、每份旧 backup 未变化并与 manifest hash 一致。backup 缺失或损坏、manifest 无效、symlink、文件不可读、未知 marker、required Base 不兼容、当前源码不受支持，或仍残留本项目 owned patch 时都会继续 fail-closed。

`status` 和 `start` 会从显式 `--hermes-dir`、选定 env file、配置旁 `.env` 或进程环境读取 `HERMES_DIR`，只读检查 hook 安装状态。若 Hermes 升级替换了源码但旧 backup/manifest 仍可验证，输出 `hook.status: upgrade_repair_required`，并提示上述显式恢复命令及 `hermes gateway start`；`start` 会在启动 sidecar 前拒绝继续，避免“sidecar 正常但 Gateway hook 已丢失”的静默降级。若检测到用户改动、损坏或不受支持的源码，则输出 `manual_review_required`，不提供 `--accept-hermes-upgrade` 捷径。

## V4.1 runtime 完整性

新安装写入 `integrity.mode: safe`；旧配置缺段时保持 `notify`。旧安装只有在 provenance 可验证时才可运行 `integrity migrate-safe --config CONFIG --hermes-dir HERMES_DIR --yes`。成功时输出 `sidecar.restart_required: true` 与 `gateway.restart_required: false`，表示要重启 sidecar 读取新模式，但迁移动作本身不要求重启 Gateway。

重启后，Gateway runtime 以独立签名域发送 `runtime.hello` / `runtime.heartbeat`。这些事件只证明当前 HFC runtime generation 与活性，不携带路径、源码 hash、chat id 或 secret，也不能单独授权写文件。`safe` 仍要求当前 Git HEAD 是已记录 HEAD 的后代、目标 blob 等于当前 HEAD、backup/manifest/anchors/reversible patch 全部一致，并在 mutation 前重新检查 fingerprint。

如果 strict repair 成功重新安装 hook，readiness 会显示 `gateway.restart_required: true`。HFC 不会自动 restart 或 kill Gateway；用户选择合适窗口手动重启，后续匹配的 `runtime.hello` 才清除状态。缺少认证 control secret、source-stripped root、symlink、dirty target、branch rewind、用户编辑、旧 manifest 或变化中的证据都拒绝自动 repair。

## 备份与 manifest

V4.1 安装会先保存所有受管源码备份，再写入 `manifest_version: 2`。manifest 至少记录：

- `run_py` 相对路径。
- 已安装后 `run.py` 的 hash。
- `backup` 相对路径。
- 备份文件 hash。
- required Base 的 `base_py`、`base_patched_sha256`、`base_backup`、`base_backup_sha256`；四项必须全有或全无。
- optional Cron 的对应路径和 hash 字段（仅检测到受支持目标时存在）。

`restore` 和 `uninstall` 会使用 manifest 验证 run、required Base、optional Cron 与各自备份是否仍是安装器认识的同一事务。旧 manifest v1 不能证明 Base ownership；只有经过严格验证的 repair/install 才能迁移到 v2。任一目标被用户或其他工具改动、任一必需 backup/字段缺失，命令都应拒绝部分恢复或清除 ownership。

## 原子写入

安装器写入 Base（先于 run）、run、optional Cron、各自备份和 manifest 时使用临时文件替换，避免中途失败留下截断文件。若安装或恢复流程中任一步失败，整个多目标事务回滚到开始前状态，不留下“run 已恢复但 Base 仍 patched”的孤儿状态。

## 恢复和卸载

```bash
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli uninstall --hermes-dir ~/.hermes/hermes-agent --yes
```

`restore` 用于恢复安装前的全部受管 Hermes 文件；`uninstall` 当前同样移除本插件拥有的 hook 和安装状态。两者都必须全目标验证、全目标成功或不做任何改变，不覆盖无法校验的用户改动。

从 legacy/dual 历史安装迁移时，先阅读 `docs/migration.md`。历史 `legacy/installer_v2.py`、`legacy/gateway_run_patch.py`、`legacy/patch_feishu.py` 等入口写入的补丁不属于当前安装器 manifest 管理范围，不能假定当前 `restore` 能自动识别并清理。

## 降级行为

sidecar 不可用、超时或返回错误时，Hermes hook 应让 Hermes 继续原生文本回复。卡片不可用是插件故障，不应升级为 Agent 主流程故障。

hook import 或 emit 异常同样保持 fail-open，但不应完全静默。V3.6.2 起，注入的 hook block 会向 Hermes stderr 写入 `[hermes-feishu-card] hook failed: ...`，便于从 Gateway 日志定位 runtime venv、import 或 sidecar emit 问题。

## 远程版本解析

安装脚本中的 `latest` 只表示“解析一次最新稳定 Release”：成功后必须变成精确 `vX.Y.Z` Git ref。Release API 查询、JSON 解析或 tag 校验失败时，脚本会在凭证提示、pip、doctor、setup 和 Docker 状态写入前 fail closed。显式 release tag 保持固定并跳过 API；只有显式 `--version main` 才选择移动的开发分支。
