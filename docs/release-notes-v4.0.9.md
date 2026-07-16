# V4.0.9

V4.0.9 修复 Issue #130：安装 hook 后，Feishu/Lark WebSocket 可能停止接收消息，并在周期性断连后触发 Gateway 重启循环。

## 根因

- Hermes 建立 WebSocket 时，Lark SDK 会保存一个完整的 `EventDispatcherHandler`，其中包含消息、卡片、reaction、bot 生命周期、drive 和 meeting 等 processor。
- 旧 HFC startup hook 在连接已经建立后再次调用 `_build_event_handler()`，并直接替换 `adapter._event_handler` 与 live `ws_client._event_handler`。
- 这是 HFC 对已连接 Lark WS client 内部状态的唯一写入；它改变了 SDK receive loop 后续处理消息时使用的 handler identity，与 Issue #130 中“装 hook 后不再收到消息、移除 hook 后恢复”的 A/B 一致。
- 周期性 close 后的 reconnect exhaustion 是 Hermes 上游独立问题 NousResearch/hermes-agent#64712/#64741；V4.0.9 不接管 Hermes 的 reconnect ownership，而是移除 HFC 自身的 live-handler 风险写入。

## 修复

- 不再重建或替换任何 live `EventDispatcherHandler`。
- 仅定位既有 handler 中的 `p2.card.action.trigger` processor，更新它的 callback，使 slash/model/resume/operations 等 HFC 原生按钮继续返回内联卡片。
- WebSocket 模式通过 adapter 的 `_ws_thread_loop.call_soon_threadsafe(...)` 在 SDK 自己的线程更新 callback，避免跨线程改写 live handler。
- 如果未来 Lark SDK 改变内部 processor 结构，HFC 会 fail-open 跳过 callback refresh，不会回退到整体 handler 替换。

## 贡献

- 感谢 @Jasonsun77 提供 Issue #130 的 Linux clean-versus-patched A/B、3–6 分钟断连时间线、Python/Lark/websockets 精确版本、sidecar 健康证据，以及上游 reconnect issue/PR 关联。

## 验证

- TDD 回归证明旧实现会重建 live handler，新实现保持 adapter 与 WS client 的 handler identity 不变。
- hot-file 矩阵：`404 passed, 1 skipped`；skip 是默认测试环境未安装 Hermes 可选 Feishu SDK。
- Python 3.11.15 + `lark-oapi==1.6.8` + `websockets==15.0.1` 精确兼容 smoke 通过。
- 完整自动化：`1330 passed, 4 skipped`，并通过 `git diff --check`。
- 真实 Hermes v2026.7.7.2 / Feishu WebSocket：pre-idle 消息、420 秒空闲、post-idle 和额外 liveness 消息全部完成；Gateway/sidecar PID 保持稳定，sidecar 10/10 个事件全部应用，3 次发送与 7 次更新成功、失败为 0。
- `/model` Provider 回调原位进入模型列表，Bailey 随后手动切换模型并确认成功；没有 callback timeout。
- sdist/wheel 构建成功；干净 Python 3.12 环境从 wheel 导入 `hermes_feishu_card==4.0.9` 且 CLI 入口可用。
- GitHub Actions 新增 Ubuntu/Python 3.11 精确 SDK job；四个公开资产 checksums 与公共 `v4.0.9` tagged installer fixture smoke 均通过。

## Release assets

- `hermes-feishu-card-v4.0.9-macos.tar.gz`
- `hermes-feishu-card-v4.0.9-linux.tar.gz`
- `hermes-feishu-card-v4.0.9-windows.zip`
- `hermes-feishu-card-v4.0.9-checksums.txt`
