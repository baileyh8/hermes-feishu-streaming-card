# Hermes Feishu Streaming Card V4.4.4

V4.4.4 修复 Issue #270：Hermes 在飞书话题中关闭或重启时，运行通知不再错投父群主会话。

## 修复

- HFC runtime wrapper 会保留 Hermes 已提供的 `reply_to_message_id`，让 Feishu adapter 通过 reply API 和 `reply_in_thread=true` 把通知送回原 Topic。
- 当前 Hermes `GatewayRunner.start()` 布局下，启动 hook 会在 restart/startup 通知与 pending delivery redelivery 之前安装，因此重启后的首批消息也使用相同路由规则。

## 安全边界

- 只在平台为 Feishu，且 `thread_id` 与 reply anchor 同时存在时补充元数据；其他平台及无 anchor 的发送保持原行为。
- 不直接编辑 Hermes `gateway/run.py`；所有源码 hook 仍由 patcher 的 owned block 安装、校验和逐字恢复。
- home channel 的兜底启动广播策略仍由 Hermes 配置控制，本次只修复有明确 Topic anchor 的错投。

## 验证

- 聚焦 hot-file 回归为 `943 passed, 1 skipped`，文档与包元数据回归为 `101 passed`；完整 pytest 为 `3533 passed, 9 skipped in 752.17s`，`git diff --check` 通过。
- PEP 517 sdist/wheel 构建通过；全新 Python 3.12 venv 从普通 wheel 安装后，包和 distribution 均为 `4.4.4`，唯一 Hermes plugin entrypoint、24 个 provenance slices 与 CLI help 均通过。
- 本机生产 Hermes 0.21.0 从 runtime venv 加载 4.4.4，官方 patcher 安装后仅 managed `gateway/run.py` 发生预期变化；sidecar 与 Gateway 重启后达到 `runtime_ready / integrity=safe`。
- 2026-09-08 真实飞书群 Topic 中执行 `/restart` 后，重启成功通知留在原 Topic，未落入父群主会话；精确的活跃任务 shutdown 通知继续由同一 metadata wrapper 的自动化回归覆盖。

## 贡献者

- [mouyong](https://github.com/mouyong)：[#270](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/270) 的复现说明与截图。
