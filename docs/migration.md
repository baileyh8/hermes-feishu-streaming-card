# 从 legacy/dual 迁移到 sidecar-only

本文只覆盖从本仓库历史 legacy/dual/patch 实现迁移到当前 `hermes_feishu_card/` sidecar-only 主线的安全流程。历史入口包括 `adapter/`、旧 `sidecar/`、旧 `patch/`、`installer.py`、`installer_sidecar.py`、`installer_v2.py`、`gateway_run_patch.py`、`patch_feishu.py` 等；它们不是 active runtime。

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

4. 如果当前 Hermes 曾运行历史 legacy/dual 安装脚本，例如 `installer_v2.py`、`gateway_run_patch.py` 或 `patch_feishu.py`，先用当时保留的原始备份恢复 Hermes 文件。若没有可信备份，建议重新安装或重新 checkout 对应版本的 Hermes，再迁移。

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
