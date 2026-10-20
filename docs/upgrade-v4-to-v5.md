# 从 V4.x 升级到 V5

[English](upgrade-v4-to-v5.en.md) · [返回 README](../README.md)

适用于已经安装 HFC 的用户。本文固定目标为 `v5.0.4`，请在该版本出现在 [GitHub Releases](https://github.com/baileyh8/hermes-feishu-streaming-card/releases) 后执行。HFC 升级不会替你升级 Hermes，也不需要重新创建飞书机器人。兼容性由实际源码、能力和安装来源检查决定，不能保证任意旧版或手改安装都能直接覆盖。

## 先确认当前安装

记录当前 HFC/Hermes 版本、Hermes 源码目录、实际 `HERMES_HOME`、HFC 配置与 `.env` 路径，以及 Gateway、Desktop、sidecar 的启动方式。HFC 配置可能是 `~/.hermes/config.yaml`，也可能是独立的 `~/.hermes_feishu_card/config.yaml`；下面路径只是示例。

用当前安装所用的 CLI 运行 `status` 和 `doctor --explain`。如果 shell 中的 `hermes-feishu-card` 不存在或指向其他环境，使用安装输出记录的绝对 CLI 路径；不要用一个无关的系统 Python 给另一套 Hermes 安装包。

```bash
hermes-feishu-card status --config /path/to/config.yaml
hermes-feishu-card doctor --config /path/to/config.yaml --hermes-dir /path/to/hermes-agent --explain
```

备份选中的配置、凭据文件、现有版本记录和安装器生成的 manifest/backup。凭据备份仅放在私有目录，不提交仓库。保留 Hermes 会话、用户数据和容器持久卷。

## 停止旧服务，再安装 V5

等当前任务完成，通过原服务管理器停止 Gateway 与 Desktop，再停止 sidecar。Linux 常驻服务应使用原 systemd 管理方式；macOS 应避免 LaunchAgent 自动拉起第二个实例；容器应使用原 Compose/编排方式。不要同时运行两个接收同一机器人事件的 Gateway。

临时 sidecar 可使用：

```bash
hermes-feishu-card stop --config /path/to/config.yaml --env-file /path/to/.env
```

### macOS / Linux

修改下面三个路径，保持与旧安装一致。分离 HOME 时另外设置真实 `HERMES_HOME`；它不一定是源码目录的父目录。

```bash
export HERMES_DIR="$HOME/.hermes/hermes-agent"
export HFC_CONFIG="$HOME/.hermes/config.yaml"
export HFC_ENV_FILE="$HOME/.hermes/.env"
curl -fsSL https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/v5.0.4/install.sh | bash -s -- --version v5.0.4 --config "$HFC_CONFIG" --env-file "$HFC_ENV_FILE"
```

若旧安装使用特定 profile，继续传入相同的 `--profile-id`。不要为了升级改成另一份配置或凭据文件。

### Windows PowerShell

保留原路径；若原来使用分离 HOME，先设置 `$env:HERMES_HOME`。把脚本保存到临时文件后带参数执行：

```powershell
$env:HERMES_DIR = Join-Path $HOME ".hermes/hermes-agent"
$HfcConfig = Join-Path $HOME ".hermes/config.yaml"
$HfcEnvFile = Join-Path $HOME ".hermes/.env"
$HfcInstaller = Join-Path $env:TEMP "hfc-install-v5.0.4.ps1"
Invoke-WebRequest "https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/v5.0.4/install.ps1" -OutFile $HfcInstaller
& $HfcInstaller -Version v5.0.4 -Config $HfcConfig -EnvFile $HfcEnvFile
```

使用 profile 的安装继续传入 `-ProfileId`。不需要更改机器范围的 PowerShell 执行策略；如被策略阻止，按本机既有管理方式运行。

### Docker / NAS

在原镜像构建或容器安装流程中把 `HFC_VERSION` 固定为 `v5.0.4`，继续使用原 `HERMES_DIR`、`HFC_CONFIG`、`HFC_ENV_FILE`、HOME 和持久卷。按 [Docker 安装说明](../README-install.md)运行 `install-docker.sh`，然后通过原编排方式重建/重启服务。不要把宿主机示例路径复制进容器，也不要删除持久卷来“清理旧版”。[Compose 示例](../docker-compose.example.yml)是参考拓扑，不是可以覆盖现有配置的官方镜像。

### 安装器拒绝继续时

如果提示旧 hook revision 需要恢复，按 `doctor --explain` 提供的官方 `restore` 指令恢复原受管文件后，再运行安装。若出现未知源码漂移、无法验证的 backup、共享源码/HOME 冲突或不支持的能力，保留诊断并停止。不要手改 `gateway/run.py`、删除 manifest/backup 或绕过检查。详见[安装安全](installer-safety.md)与[恢复说明](migration.md)。

## 配置会怎样变化

| 配置 | 升级时的处理 |
| --- | --- |
| App ID、App Secret、群聊/profile/bot 路由 | 沿用原配置；不要用完整示例覆盖 |
| `classic`、`focused`、`detailed` | 不自动切换为 `task` |
| 已使用 `task` | V5 的过程默认展开；要保持收起，显式设 `timeline_expanded: false` |
| 同层显式显示字段 | 仍优先于预设；旧开关可能覆盖你新选的预设 |
| 原生表格 | 仍为主动启用，默认 `native_tables: false` |
| 已发出的卡片 | 不因修改配置立即重绘；以重启后新的任务验证 |

建议先在现有全局 `card` 下合并这两个字段：

```yaml
card:
  reading_preset: task
  timeline_expanded: false
```

检查有效配置及来源，再决定是否修改 profile/bot 的显式覆盖：

```bash
hermes-feishu-card card-config --config /path/to/config.yaml
hermes-feishu-card card-config --config /path/to/config.yaml --minimal-yaml
hermes-feishu-card card-config --config /path/to/config.yaml --preview-dir ./hfc-v5-preview
```

这些命令不会写配置或自动重启。`--minimal-yaml` 输出的是所选范围的阅读字段，不能拿来替换整个配置文件。预览目录需是新的输出目录。更多规则见[阅读预设](wiki/reading-presets.md)。

## 启动并验证

按安装器输出恢复原服务管理器并重启 Gateway / Desktop。安装器已启动的 sidecar 不要再另开一份。随后使用同一份配置检查：

```bash
hermes-feishu-card status --config /path/to/config.yaml
hermes-feishu-card doctor --config /path/to/config.yaml --hermes-dir /path/to/hermes-agent --explain
hermes-feishu-card card-config --config /path/to/config.yaml --running
```

确认实际加载的是 V5，readiness 正常，配置的 loaded / next-load 差异符合预期。不能确认运行身份时，`--running` 会明确不可用，不把磁盘值冒充已加载值。

最后在测试会话发一条普通消息，检查流式输出、最终正文和 footer；再用你熟悉的低风险审批/澄清任务检查交互。只有续答成功送达且有可靠定位时才显示“查看续答”，没有按钮不自动等于丢失结果。不要在验证中选择“始终允许”来省步骤。

## 回退

先停止本轮新任务并通过原服务管理器停止相关进程。使用当前 V5 的官方 `restore` 按诊断恢复受管 Hermes 文件，再把安装版本固定回升级前记录的 V4.x tag，使用相同目录、配置和凭据重新安装。需要恢复配置时使用升级前备份，而不是仓库示例；保留会话和用户数据。最后重启并重复 `status`、`doctor` 与实际消息检查。

`restore` 负责受管文件恢复，不等同于降级 Python 包、恢复全部配置或回退 Hermes 自身。若 Hermes 源码也发生了变化，先按实际兼容诊断处理，不能强行应用旧 backup。完整边界见[安装安全](installer-safety.md)。
