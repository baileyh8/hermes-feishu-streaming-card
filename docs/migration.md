# 从 legacy/dual 迁移到 sidecar-only

[中文](migration.md) | [English](migration.en.md)

本文只覆盖从本仓库历史 legacy/dual/patch 实现迁移到当前 `hermes_feishu_card/` sidecar-only 主线的安全流程。历史入口已归档到 `legacy/`，包括 `legacy/adapter/`、旧 `legacy/sidecar/`、旧 `legacy/patch/`、`legacy/installer.py`、`legacy/installer_sidecar.py`、`legacy/installer_v2.py`、`legacy/gateway_run_patch.py`、`legacy/patch_feishu.py` 等；它们不是 active runtime。

## 迁移原则

- 先备份，再诊断，再安装；任何不确定状态都应 fail-closed。
- 不要混用 legacy/dual hook 和 sidecar-only hook。
- 不要把 App Secret、tenant token、真实 chat_id 写入仓库、文档、日志样例或 issue。
- 不要手工复制旧补丁片段到 Hermes `gateway/run.py`。
- 如果 Hermes 文件已经被用户或其他工具改过，先人工确认差异，再继续。

## 推荐流程

1. 停止当前 sidecar-only 进程，如果已经启动过：

```bash
python3 -m hermes_feishu_card.cli stop --config config.yaml.example
```

2. 保留当前 Hermes 目录的外部备份。最简单的方式是复制整个 Hermes 安装目录到安全位置；不要只备份本仓库文件。

3. 如果当前 Hermes 曾通过本项目 sidecar-only 安装过，先使用当前安装器恢复：

```bash
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

`restore` 只会恢复本插件 manifest 能校验的安装状态。V4.1 的 `manifest_version: 2` 把 `gateway/run.py`、Hermes 0.19 required `gateway/platforms/base.py` 与 optional Cron 作为同一事务；任一目标或 backup 不完整都不会部分恢复。若提示 source/backup changed、`install state incomplete` 或 `newer installer required`，应停止并检查全部受管目标，不能只处理 `run.py`。

4. 如果当前 Hermes 曾运行历史 legacy/dual 安装脚本，例如 `legacy/installer_v2.py`、`legacy/gateway_run_patch.py` 或 `legacy/patch_feishu.py`，先用当时保留的原始备份恢复 Hermes 文件。若没有可信备份，建议重新安装或重新 checkout 对应版本的 Hermes，再迁移。

5. 运行只读诊断：

```bash
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent
```

只有当输出为 `hermes: supported`，且 `version`、`version_source`、`run_py_exists`、`reason` 都符合预期时，才继续安装。

6. 安装 sidecar-only hook：

```bash
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
```

安装器会为全部受管目标创建备份和 `manifest_version: 2`，再以最小 hook 调用 `hermes_feishu_card.hook_runtime`。Hermes 0.19 / `v2026.7.20+` 或检测到 exact ledger 结构时，required Base 必须与 run 一起安装、恢复和回滚。飞书 CardKit、会话状态、健康指标和重试计数都在 sidecar 进程内完成。

7. 启动并检查 sidecar：

```bash
python3 -m hermes_feishu_card.cli start --config config.yaml.example
python3 -m hermes_feishu_card.cli status --config config.yaml.example
```

`status` 应显示 `status: running`、`active_sessions` 和 metrics。未配置飞书凭据时会使用 no-op client；配置真实凭据时只从本机配置或环境变量读取。

## 从 V4.1.0 升级到 V4.1.1

V4.1.1 修复升级后“磁盘已是新版本、运行 sidecar 仍是旧解释器/旧包”以及首次 heartbeat 等待误写 fence 的边界。升级必须继续走官方 setup/install，不要手工修改 Hermes 源码：

```bash
hermes-feishu-card doctor --config CONFIG --hermes-dir HERMES_DIR --explain
hermes-feishu-card stop --config CONFIG
hermes-feishu-card setup --config CONFIG --hermes-dir HERMES_DIR --yes
```

setup 会使用检测到的 Hermes runtime venv 安装并复检 V4.1.1，并依据 `/health` 的 package version 与 Python identity 判断旧 sidecar 是否需要重启。若运行中的旧 sidecar 已无 pidfile，默认不会自动接管或 kill；先人工停止旧服务，再重跑 setup。不要用猜测的 PID 或宽泛 `pkill` 绕过该边界。

仅当 `doctor --explain` 确认 on-disk plan 为 `installed`、sidecar health 已不可达且 state dir 内无 pidfile，但状态仍为 `manual_review_required` 时，才可执行：

```bash
hermes-feishu-card integrity acknowledge-review \
  --config CONFIG \
  --hermes-dir HERMES_DIR \
  --yes
```

空 `pre_repair_runtime_hash` 表示 runtime 无法自行证明新旧进程，人工确认可解除该不可自清 fence；非空 hash 只解除 manual-review 位，Gateway restart fence 必须保留，直到不同 runtime id 且 generation/package 匹配的新 `runtime.hello` 到达。随后人工重启 sidecar 与 Hermes Gateway，再用 `doctor` / `/health` 确认 ready。任何 dirty target、未知 manifest/backup、非私有 state/fence 或残留 pidfile 都必须先人工处理，不能把 `acknowledge-review` 当强制清除。

旧版 `0644` pidfile 只有位于当前用户拥有的私有 `0700` state dir、形状与 identity 均严格匹配时才能原 inode 收紧为 `0600`；其他情况 fail-closed。

## 从 V4.1.3 升级到 V4.1.4

V4.1.4 修复 Issue #171 的 Windows 旧版安装迁移缺口。若 `.hermes_feishu_card_manifest` 缺失，但旧版 owned Gateway hook 与 `.hermes_feishu_card.bak` 仍在，重新运行官方 `install` / `setup`。安装器只在旧 hook 去除 owned blocks 后与 backup 逐字一致、backup 是可解析干净源码、Cron/Base 证据也分别一致时重建 manifest 并升级 hook。

不要手工创建 manifest，不要直接调用内部 `apply_patch()`。Unicode 注释、CRLF 与 Windows 原生路径分隔符不是该问题的已复现根因。owned marker 外存在用户改动、backup 不一致、symlink 或源码不可解析时仍会 fail-closed，应先保留文件并人工审查。

```bash
hermes-feishu-card doctor --config CONFIG --hermes-dir HERMES_DIR --explain
hermes-feishu-card install --hermes-dir HERMES_DIR --yes
```

Windows PowerShell 按官方 `install.ps1` / `setup` 流程安装 V4.1.4。看到 `manifest: rebuilt` 与 `install ok` 后，重启 Hermes Gateway，再确认 doctor 的 install state 为 `installed`。

## 从 V4.1.2 升级到 V4.1.3

V4.1.3 修复 Issue #158 中真实 Hermes upstream 更新后的恢复收敛问题：官方 `install` 重新注入 hook 后，当前 integrity plan fingerprint 会变化，而旧 manual-review fence 仍绑定升级前 plan。`integrity acknowledge-review` 现在可在双重验证当前 installed plan、双重确认 sidecar 已停止、旧新 binding 指向同一 Hermes target 且 fence CAS 未变化时，原子更新 plan binding 并解除 manual-review。不同 target、残留 pidfile/health、dirty 或不可验证 plan、未知 legacy fence 仍拒绝；已有非空 restart hash 继续保留，直到新 matching `runtime.hello` 自行解除 restart fence。

该版本也合入 PR #168 的同名 answer-delta callback 选择，并修复 Issue #169：Hermes `1a3a9de` 将 callbacks 移入 `TurnRunner` 后，官方 patcher 会通过 `TurnContext` 恢复 stable tool、answer、thinking、clarify、approval 与 status hook。doctor 以实际 patchability 为准；无法安全识别的新 TurnRunner 结构会停止安装并报告 `not safely patchable`，不能靠手工修改 `gateway/run.py` 绕过。

不要手工编辑 `runtime-integrity-fence.json` 或调用内部 Python 函数。若 `doctor --explain` 报告 `integrity_migration_required`，按输出运行 `integrity migrate-safe`；其他已验证 manual review 先停止 sidecar，再运行 doctor 给出的完整 `integrity acknowledge-review --config CONFIG --hermes-dir PATH --state-dir STATE --yes`，最后人工重启 sidecar 与 Hermes Gateway。

## 从 V4.1.1 升级到 V4.1.2

V4.1.2 修复 Gateway 正常重启期间 heartbeat 短暂 stale 被误写为持久化 restart fence 的竞态。按官方 `setup` 升级并只重启 Gateway 一次；在新 matching `runtime.hello` 到达后，readiness 应直接恢复 `runtime_ready`。如果仍显示 restart required，不要反复重启，先用 `doctor --explain` 检查 generation/package、control auth 与既有 fence。

## 升级到 V4.1.0

V4.1.0 保持旧会话的卡片默认与旧配置的非自动变更边界。建议先升级包并重新运行 setup/install，再按需加入：

```yaml
bindings:
  native_chats: []
card:
  table_overflow_mode: compact  # compact | truncate
integrity:
  mode: notify  # 旧配置保持 notify；显式迁移后再 safe
service:
  manager: auto  # auto | systemd-user | systemd-system | detached
```

`bindings.native_chats` 只精确匹配。用 `chats use-native`、`chats use-card`、`chats list` 管理；多 profile 必须加 `--profile-id` 并写入对应 profile。`table_overflow_mode: compact` 无损转换第 6 张及后续表格，`truncate` 保留显式旧行为；终态 card JSON 超过 28,000 byte 时交还完整 Hermes 原生答案。

旧配置缺少 `integrity` 段时会按 `notify` 加载。只有已验证 Git provenance、backup、manifest、owned blobs 和 anchors 的安装才能显式迁移：

```bash
hermes-feishu-card integrity migrate-safe \
  --config ~/.hermes/config.yaml \
  --hermes-dir ~/.hermes/hermes-agent \
  --yes
```

成功后输出 `sidecar.restart_required: true`、`gateway.restart_required: false`；重启 sidecar 后，认证 `runtime.hello` / `runtime.heartbeat` 才开始按 safe 模式评估。若 strict repair 真的重新安装 hook，状态会改为 `gateway.restart_required: true`，但 HFC 不会自动重启 Gateway。证据不足、用户编辑、symlink、dirty target、branch rewind 或 source-stripped root 都保持 fail-closed。

`service.manager: auto` 只选择 `systemd-user` 或 `detached`，不隐式进入 `systemd-system`，不调用 sudo。`systemd-system` 是 Linux transient unit 的显式 opt-in；Docker 继续用普通容器进程和 `detached`。Hermes 0.19.0 / `v2026.7.20` 使用 AST-owned run + Base hooks；旧 run-only manifest 只有在严格证据下才会补建 Base backup、重打 Base patch 并迁移为 manifest v2。升级覆盖通过 runtime 监控和 strict repair 处理，不安装 import-hook bridge。

## 升级到 V3.4.0

V3.4.0+ 会根据 Hermes 版本和 `gateway/run.py` 代码 anchor 选择 hook strategy。Hermes `0.13.0+`、`0.14.0` / `v2026.5.16+` 使用 `gateway_run_013_plus`，旧版本 Hermes `v2026.4.23` 到 `v2026.4.x` 继续使用 `legacy_gateway_run`。升级插件后必须重新安装 hook，不能只重启 sidecar。

```bash
python3 -m hermes_feishu_card.cli stop --config ~/.hermes_feishu_card/config.yaml
pip install -e ".[test]" --upgrade
python3 -m hermes_feishu_card.cli doctor --config ~/.hermes_feishu_card/config.yaml --hermes-dir ~/.hermes/hermes-agent
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli start --config ~/.hermes_feishu_card/config.yaml
```

`doctor` 输出应包含 `hook_strategy`、`compatibility` 和 anchors。若 Hermes 已升级到 `0.13.0+`、`0.14.0` 或 `v2026.5.16+`，确认 `hook_strategy: gateway_run_013_plus` 后再安装；`v2026.4.x` 旧版本 Hermes 应继续显示 `legacy_gateway_run`。

多个独立 Hermes profile 以多个进程运行时，推荐为每个进程设置稳定的 `HERMES_FEISHU_CARD_PROFILE_ID`，避免依赖自动推断导致 profile 与 bot 路由不明确。单个 sidecar 服务多 profile 的配置仍使用 `profiles` 段管理各自凭据、bot 和 card title。

## 从 V3.1 升级到 V3.2.1

V3.2.1 在 V3.1 的 sidecar-only 架构上**向后兼容**。单 bot 配置无需更改即可继续运行；如需使用多 bot / 群聊绑定新功能，需扩展配置。

### 升级步骤

1. **备份当前配置**

   ```bash
   cp ~/.hermes_feishu_card/config.yaml ~/.hermes_feishu_card/config.yaml.v3.1.backup
   ```

2. **停止 sidecar（可选但推荐）**

   ```bash
   python3 -m hermes_feishu_card.cli stop --config ~/.hermes_feishu_card/config.yaml
   ```

3. **更新代码到 V3.2.1**

   ```bash
   cd /path/to/hermes-feishu-streaming-card
   git checkout v3.2.1  # 或更新到最新 tag
   python3 -m pip install -e ".[test]" --upgrade
   ```

4. **更新配置文件**

   方式 A：使用 CLI 生成新版模板（保留原配置，新增 V3.2.1 字段）
   ```bash
   python3 -m hermes_feishu_card.cli setup --hermes-dir ~/.hermes/hermes-agent --config ~/.hermes_feishu_card/config.yaml --yes
   ```
   该命令会在现有 `config.yaml` 中补充 `bots`、`bindings` 等新字段的默认值，不覆盖已有项。

   方式 B：手动合并（参考 `config.yaml.example` 的完整示例）
   - 在 `hermes:` 层级下新增 `bots:` 列表（至少包含一个 bot，其 `app_id`/`app_secret` 可从原配置继承）
   - 新增 `bindings:` 层级，配置 `fallback_bot` 和可选的 `chats:` 映射
   - 原 `feishu.app_id` / `feishu.app_secret` 仍有效（单 bot 模式），但建议迁移到 `bots[0]` 以统一管理

5. **验证配置**

   ```bash
   python3 -m hermes_feishu_card.cli doctor --config ~/.hermes_feishu_card/config.yaml
   ```
   确认输出 `config: valid`，且 `bots` / `bindings` 字段被正确识别。

6. **重启 sidecar**

   ```bash
   python3 -m hermes_feishu_card.cli start --config ~/.hermes_feishu_card/config.yaml
   python3 -m hermes_feishu_card.cli status --config ~/.hermes_feishu_card/config.yaml
   ```

7. **功能验证**
   - 向单聊或群聊发送消息，确认卡片正常渲染
   - 如配置了多 bot，使用 `/health.routing` 查看路由统计
   - 使用 `cli bots list` 确认 bot 列表正确

### 兼容性说明

- V3.1 的单 bot 配置在 V3.2.1 中**无需修改**即可运行（旧字段仍受支持）
- V3.2.1 的多 bot 功能为可选；未配置 `bindings.chats` 时，所有会话路由到 `bindings.fallback_bot`
- 环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 在 V3.2.1 中仍有效，但配置文件中 `bots[]` 优先级更高
- 回退：如需回退到 V3.1，停用 sidecar，恢复备份的 `config.yaml` 并重新安装旧版本即可

### 注意事项

- 多 bot 模式下，请确保每个 bot 在飞书开放平台均已创建并具备 `send_message` / `update_message` 权限
- 群聊绑定需使用 `chat_id`（可在飞书客户端或通过 API 获取），而非群名称
- 升级后建议运行一次 `pytest -q` 确保测试通过（本地开发环境）

---

## 回退流程

如果安装后需要回退，优先使用：

```bash
python3 -m hermes_feishu_card.cli stop --config config.yaml.example
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

若 `restore` 拒绝覆盖，说明当前 Hermes 文件、备份或 manifest 已与安装时不一致。此时不要强行删除 hook；应先对比 run、required Base、optional Cron、各自备份和外部备份，再选择人工恢复或重新安装 Hermes。未来版本 manifest 必须由对应新版安装器处理。

## 验证清单

- `doctor --config ... --hermes-dir ...` 输出 `hermes: supported`。
- `install --hermes-dir ... --yes` 输出 `install ok`。
- `start --config ...` 输出 `start ok` 或 `start: already running`。
- `status --config ...` 输出 `/health` metrics。
- Hermes 原生文本回复在 sidecar 不可用时仍能降级运行。
- 不存在 legacy/dual hook 与 sidecar-only hook 同时驻留在 `gateway/run.py` 的情况。

## Integrity 人工确认边界

`acknowledge-review` 不是通用修复命令。只有当 doctor 同时验证 recovery state 为 `installed`、recovery actions 为空、integrity plan 为 `installed` 且 reason 为 `recovery_not_required` 时，才会建议先停止 sidecar 再确认。其他 manual-review reason 必须先修复报告中的安装状态，再重新运行 doctor。

## 显式恢复孤立的身份绑定

macOS 重启或卷重新挂载后，设备号可能变化，旧完整性记录仍绑定旧身份。HFC 不会自动把它认作同一安装；哈希本身也不能证明只有设备号变化。

先核对目标 Hermes 目录与原配置，按原服务 owner 停止 sidecar，确认 `doctor --explain` 的安装计划可以验证。只读查看自己 state 目录下 `runtime-integrity-fence.json` 的 `target_identity`（不要编辑文件），然后显式执行：

```bash
hermes-feishu-card integrity acknowledge-review \
  --config <实际配置路径> --hermes-dir <实际Hermes目录> \
  --state-dir <实际sidecar状态目录> \
  --rebind-target <旧target_identity的64位哈希> --yes
```

这表示你明确授权把这条旧记录绑定到当前已核验的安装，不表示系统自动证明目录没有被替换。旧身份必须精确匹配，当前安装计划和 sidecar 停止状态均检查两次，写入时原记录必须未变化。脏文件、无法验证的安装、运行中的 sidecar、错误旧身份及竞态都拒绝。已知的 Gateway 重启证据保留；完成后按原 owner 重启 sidecar / Gateway，再检查健康状态。普通 `acknowledge-review` 的跨目标拒绝行为不变。

重新绑定会同时更新 `target_identity` 和当前已核验计划的 `plan_fingerprint`。如果随后显示 `gateway_restart_required`，表示仍需按原服务 owner 重启 Gateway；这不表示重新绑定失败。确认 sidecar 真正停止后再执行恢复：例如 launchd 的 `KeepAlive` 可能在普通 stop 后立即拉起进程。
