# V4.0.11

V4.0.11 修复 Issue #135：当 Feishu 初始 create/reply 的 HTTP 结果不可靠时，避免运行提示卡片静默丢失或因为盲目回退而重复泄露通知正文。

## 可靠投递

- 每次初始卡片投递生成稳定的 `delivery_uuid`；同一次逻辑投递的所有重试复用该值，Feishu create 与 reply 路径均携带 UUID。
- 仅对 HTTP 429/502/503/504、网络异常和超时做有界重试，最多 3 次；永久 4xx 不重试。sidecar 收到的单个 `/events` 请求仍只处理一次。
- sidecar 明确返回 `delivered`、`not_sent` 或 `unknown`。只有 `not_sent` 才允许 Hermes 原生通道回退原通知文本；`unknown` 只尝试通用警告，不重复可能已经送达的私有通知内容。
- 通用警告固定为：`⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。`

## 可观测性与安全

- 新增 `feishu_send_retries`、`feishu_send_unknown_outcomes`、`notice_native_fallbacks` 与 `notice_uncertain_warnings`。
- `last_send_error` 只保留分类和安全状态；card-safe diagnostics 不输出原始 chat/message id、UUID、响应正文、URL、token、secret 或通知正文。
- Feishu API 响应错误改为结构化分类，保留调用方判断所需的 HTTP/code 信息，不把原始响应体拼进异常消息。

## 兼容性

- 旧 sidecar、无法解析的响应和 hook 传输异常继续 fail-open，但对结果不明的 notice 使用通用警告，避免把原通知正文重复发送。
- 普通消息、卡片更新、命令卡、topic/thread 路由和 loopback 事件传输协议保持兼容。

## 验证

- 自动化覆盖 503 后成功、永久 400、连接/响应结果不明、稳定 UUID、topic reply、指标与诊断脱敏，以及 hook 的三种 outcome 分支。
- 最终完整自动化为 `1389 passed, 4 skipped`，并通过 `git diff --check`。
- `uv build` 成功生成 sdist/wheel；干净 Python 3.12 环境从 wheel 导入 `hermes_feishu_card==4.0.11`，console entry point metadata 正确。
- 真实 Hermes `v2026.7.7.2` 重载当前工作树后，loopback `/events` 的 Feishu 私聊 create 与 topic reply 均返回 `delivered/applied`；2 次发送全部成功，失败、重试和 unknown 未增加，诊断未包含验收正文或 UUID。
- 本次直接 `/events` smoke 不经过 Hermes 原生消息分支，因此不把客户端灰字去重或真实 Feishu 故障注入写成已完成；相关分支由自动化集成测试验证。
- annotated tag `v4.0.11` 正确指向合并提交 `2a806d3`；`release-assets` workflow 成功，四个公开资产齐全并逐项通过 SHA-256 checksums。
- 公共 `v4.0.11` tagged installer fixture 从 Git tag 安装到独立 Python 3.12 `site-packages`，版本为 `4.0.11`；临时 Hermes fixture 的 hook install state 完整一致。

## Release assets

- `hermes-feishu-card-v4.0.11-macos.tar.gz`
- `hermes-feishu-card-v4.0.11-linux.tar.gz`
- `hermes-feishu-card-v4.0.11-windows.zip`
- `hermes-feishu-card-v4.0.11-checksums.txt`
