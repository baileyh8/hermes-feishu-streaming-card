# 发布准备说明

当前包版本为 `0.1.0`。这一版定位为 sidecar-only 主线的首个可审阅版本，已完成真实 Hermes Gateway + 真实 Feishu 测试应用验收，适合通过 PR 继续做代码审阅和小范围试用。

## 已具备

- Hermes `v2026.4.23+` 目录检测和 fail-closed 安装。
- 最小 Hermes hook、备份、manifest、restore/uninstall。
- sidecar `/events`、`/health`、进程 start/status/stop。
- Feishu CardKit HTTP client，已用 mock Feishu server 和真实 Feishu 测试应用覆盖 tenant token、发送和更新。
- 手动 `smoke-feishu-card` 命令。
- E2E 预览材料和生成器。
- 真实长卡压力测试：同一张 Feishu 卡片更新到 16k 中文字符成功。
- 真实 Hermes `v2026.4.23` 目录 `restore -> install` 循环验证。
- GitHub Actions 会在 PR/push 上运行 Python 3.9/3.12 的测试矩阵。

## 发布前必须验证

```bash
python3 -m pytest -q
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

真实飞书联调只能使用本机配置或环境变量提供 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`。不要把 App Secret、tenant token、真实 chat_id 或真实聊天截图提交到仓库。

## 当前边界

自动化测试不会访问真实飞书，也不会启动真实 Hermes Gateway。真实联调仍是人工/本机验收流程，成功后只记录脱敏结果，不提交凭据、真实 chat_id 或敏感截图。
