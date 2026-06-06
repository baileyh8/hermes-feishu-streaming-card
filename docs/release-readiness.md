# 发布准备说明

[中文](release-readiness.md) | [English](release-readiness.en.md)

当前包版本为 `3.6.1`。这一版继续保持 sidecar-only 主线，在 V3.6.0 运维诊断基础上修复 issue #47：Hermes `0.15.x` 和无 `v` 前缀的语义版本不再被 `doctor --explain` 误判为 unsupported。

## 已具备

- Hermes `v2026.4.23+` 目录检测和 fail-closed 安装。
- 最小 Hermes hook、备份、manifest、restore/uninstall。
- sidecar `/events`、`/health`、进程 start/status/stop。
- Feishu CardKit HTTP client，已用 mock Feishu server 和真实 Feishu 测试应用覆盖 tenant token、发送和更新。
- 手动 `smoke-feishu-card` 命令。
- E2E 预览材料和生成器。
- 真实长卡压力测试：同一张 Feishu 卡片更新到 16k 中文字符成功。
- 真实 Hermes `v2026.4.23` 目录 `restore -> install` 循环验证。
- Hermes `0.13.0+` / `0.14.0` / `0.15.x` / `v2026.5.16+` 使用 `gateway_run_013_plus` hook strategy，旧版 `v2026.4.x` 保持 `legacy_gateway_run`。
- 飞书卡片按钮交互覆盖 `interaction.requested`、`/card/actions`、`/interactions/{interaction_id}` 的本地 mock 验收。
- Markdown 长表格/长代码块超过 `MAIN_CONTENT_CHUNK_CHARS` 后按完整结构重复切分，避免 raw markdown。
- thinking/interim assistant 使用 `append_block` 完整块追加，避免 delta 累积导致漏字或截断。
- 同一 message id 的 runtime event 发送、sidecar 更新和终态 PATCH 均有排序/合并保护。
- `load_config()` 会读取 config 同目录 `.env`，真实环境变量仍保持最高优先级。
- `install.sh` 白名单读取 `.env` 中的飞书/sidecar 变量，不会执行带空格路径等无关配置。
- `install.sh` 会在 uv/PEP 668 externally managed Python 场景下重试 `--break-system-packages`。
- `doctor --json` / `doctor --explain` 会展示 config、sidecar、Hermes、streaming、install_state 和 recommendations。
- `repair --hermes-dir ... --yes` 和 `setup --repair` 能修复可验证的 manifest/backup 状态，无法验证用户改动时拒绝覆盖。
- 结构化附件、媒体和文件对象会在卡片保留摘要，同时不抑制 Hermes 原生媒体/文件投递路径。
- `smoke-feishu-card --profile-id`、`bots test --profile-id`、CLI `status` 和 `/health.routing.profiles` 支持 profile 维度排障。
- Hermes key release matrix 覆盖 `v2026.4.23`、`v2026.5.7`、`v2026.5.16+`、`v2026.5.29`、`0.13.x`、`0.14.x`、`0.15.x`，并覆盖语义版本带/不带 `v` 前缀。
- GitHub Actions 会在 PR/push 上运行 Python 3.9/3.12 的测试矩阵，并在 Windows 上解析验证 `install.ps1`。
- Release assets workflow 会为 tag 生成 macOS/Linux/Windows 安装包和 checksum。

## 发布前必须验证

```bash
python3 -m pytest -q
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --explain
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

真实飞书联调只能使用本机配置或环境变量提供 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`。不要把 App Secret、tenant token 或真实 chat_id 提交到仓库。公开演示截图入库前需要确认不包含敏感凭据和不可公开的会话内容。

## 当前边界

自动化测试不会访问真实飞书，也不会启动真实 Hermes Gateway。真实联调仍是人工/本机验收流程，成功后只记录脱敏结果，不提交凭据、真实 chat_id 或敏感截图。
