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

`restore` 只会恢复本插件 manifest 能校验的安装状态。若提示 `run.py changed since install`、`backup changed since install` 或 `install state incomplete`，说明文件状态无法自动确认，应停止并人工检查 Hermes `gateway/run.py`。

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

安装器会创建备份和 manifest，并以最小 hook 调用 `hermes_feishu_card.hook_runtime`。飞书 CardKit、会话状态、健康指标和重试计数都在 sidecar 进程内完成。

7. 启动并检查 sidecar：

```bash
python3 -m hermes_feishu_card.cli start --config config.yaml.example
python3 -m hermes_feishu_card.cli status --config config.yaml.example
```

`status` 应显示 `status: running`、`active_sessions` 和 metrics。未配置飞书凭据时会使用 no-op client；配置真实凭据时只从本机配置或环境变量读取。

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

若 `restore` 拒绝覆盖，说明当前 Hermes 文件、备份或 manifest 已与安装时不一致。此时不要强行删除 hook；应先对比 Hermes `gateway/run.py`、备份文件和外部备份，再选择人工恢复或重新安装 Hermes。

## 验证清单

- `doctor --config ... --hermes-dir ...` 输出 `hermes: supported`。
- `install --hermes-dir ... --yes` 输出 `install ok`。
- `start --config ...` 输出 `start ok` 或 `start: already running`。
- `status --config ...` 输出 `/health` metrics。
- Hermes 原生文本回复在 sidecar 不可用时仍能降级运行。
- 不存在 legacy/dual hook 与 sidecar-only hook 同时驻留在 `gateway/run.py` 的情况。
