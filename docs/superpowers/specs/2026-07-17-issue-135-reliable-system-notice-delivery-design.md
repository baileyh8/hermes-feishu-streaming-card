# Issue #135 系统通知可靠投递设计

## 1. 背景与裁决

Issue #135 报告：系统通知卡片在飞书 API 瞬时失败时可能丢失，并建议在 `hook_runtime._post_json_response()` 重试以及失败后恢复原生文本。

报告描述的用户风险成立，但建议修复层次不成立：

- `_post_json_response()` 负责 Hermes hook 到 HFC sidecar `/events` 的传输，不直接调用飞书 API。在这里盲目重试会重复事件、扰乱顺序；non-loopback 模式下复用同一 proof 还会命中 nonce 防重放。
- sidecar 的卡片 PATCH 已有有界重试，缺口主要是首次 `send_card()` / reply card 只尝试一次。
- 卡片发送超时是“结果未知”，不能直接把原系统通知全文再发成灰色文本，否则卡片实际已成功时会产生重复内容。
- 飞书官方的发送消息和回复消息 API 都支持 `uuid` 去重：相同 UUID 的请求在一小时内最多成功一次。这应作为安全重试的基础，而不是在 hook 层重复 POST `/events`。

官方依据：

- [发送消息 API：`uuid` 请求去重](https://open.larksuite.com/document/server-docs/im-v1/message/create?lang=zh-CN)
- [回复消息 API：`uuid` 请求去重](https://open.larksuite.com/document/server-docs/im-v1/message/reply)

## 2. 目标

1. 对飞书发送/回复卡片的 429、502、503、504 和短暂网络错误执行有界重试。
2. 每次逻辑投递使用稳定 UUID，确保重试最多生成一条飞书消息。
3. 区分“明确未发送”“结果未知”“已发送”三种结果，不用一个布尔值混淆。
4. 明确未发送时允许原始系统通知文本 fallback；结果未知时不复制原文，只发一条不含原内容的简短投递异常提示。
5. 将重试、fallback 和不确定结果暴露为脱敏 metrics/diagnostics，避免静默失败。
6. 保持已接管卡片路径不出现重复灰色正文的既有硬规则。

## 3. 非目标

- 不在 hook 到 sidecar 的 `/events` 传输层添加通用 HTTP 重试。
- 不承诺在飞书整体不可用时仍能送达；此时只能有界重试并留下可观测证据。
- 不引入持久化消息队列、数据库 outbox 或多实例 HA。
- 不改变普通 answer、cron 附件和 command card 的 native suppression 语义。

## 4. 方案比较

### 方案 A：hook 层重试 `/events`

优点是改动小；缺点是重试层次错误，无法判断 sidecar 是否已调用飞书，可能重复事件，并与 HMAC replay protection 冲突。拒绝。

### 方案 B：卡片失败后始终发送原始灰色文本

优点是用户更可能看到内容；缺点是超时场景无法证明卡片未送达，会恢复项目已经消除的重复消息。拒绝。

### 方案 C：sidecar 幂等重试 + 结果分级 fallback（推荐）

飞书调用层使用稳定 UUID 重试；sidecar 返回结构化结果；hook 只在明确未发送时 fallback 原文，结果未知时发送不重复原内容的异常提示。该方案同时满足“不静默丢失”和“不重复正文”。

## 5. 组件设计

### 5.1 飞书错误分类

`hermes_feishu_card/feishu_client.py` 的 `FeishuAPIError` 扩展为脱敏结构化错误，至少携带：

- `status_code: int | None`
- `api_code: int | str | None`
- `retryable: bool`
- `outcome: "not_sent" | "unknown"`

分类规则：

- 本地参数校验、明确的非重试 4xx：`not_sent`。
- HTTP 429、502、503、504、连接中断、读取超时：`unknown` 且 `retryable=True`。
- 飞书返回 `code != 0` 时只按显式可重试错误码分类；未知业务错误默认不可重试。
- 日志和异常文本不得包含 access token、App Secret、chat id、message id、UUID 或消息正文。

### 5.2 稳定 delivery UUID

首次发送卡片时由 sidecar 生成 `delivery_uuid`，输入只使用逻辑标识的哈希：bot/profile、chat、reply anchor、HFC session message id、delivery kind。输出格式为 `hfc_` 加 40 位十六进制摘要，总长不超过飞书 50 字符限制。

同一逻辑投递的所有重试必须复用同一 UUID；不同会话、不同独立 notice 或不同 bot 不得碰撞。UUID 不视为凭据，但仍不进入日志和 `/health`。

`FeishuClient.send_card(...)` 增加可选 `delivery_uuid`，并同时写入：

- `/im/v1/messages` 请求体的 `uuid`
- `/im/v1/messages/{message_id}/reply` 请求体的 `uuid`

### 5.3 有界重试

重试只位于 sidecar 的首次发送路径：

- 最多 3 次尝试。
- 建议退避：0 秒、0.4 秒、1.2 秒；若服务端提供可信 `Retry-After`，取其值但单次不超过 2 秒。
- 总预算不超过 hook 的 10 秒 terminal/notice 等待窗口；每次调用使用剩余预算，不能沿用 30 秒默认超时把 hook 拖成未知结果。
- 只重试 `retryable=True` 的错误；永久错误立即停止。
- token 获取失败也进入同一总预算，但不会记录凭据。

现有 PATCH 更新重试保持原职责；本设计不叠加第二套 PATCH 循环。

### 5.4 投递结果协议

sidecar `/events` 对需要首次发送卡片的事件返回以下语义之一：

- `delivered`：飞书返回 message id，卡片已建立。
- `not_sent`：明确的前置/永久失败，没有创建卡片，可安全 fallback 原文。
- `unknown`：所有幂等重试后仍无法确认结果，不可重复原始通知内容。

HTTP 状态仍可保持 2xx/4xx/5xx 语义，但 hook 必须依据经过校验的响应体字段决定 fallback；无法解析的响应一律视为 `unknown`。

### 5.5 system notice fallback

`_hfc_send_with_native_command_result_card()` 按结果处理：

- `delivered`：保持当前 suppression。
- `not_sent`：调用 original adapter 发送原始系统通知文本。
- `unknown` 或 hook-sidecar timeout：不重复原始通知；调用 original adapter 发送一次固定短句，例如“⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 `/hfc status`。”
- 固定短句本身不再进入 system-notice classifier，避免递归；同一 logical notice 只允许发一次。

这样即使结果未知，用户也不会完全无感，同时不复制可能已经出现的通知正文。

### 5.6 可观测性

新增有界计数：

- `feishu_send_retries`
- `feishu_send_unknown_outcomes`
- `notice_native_fallbacks`
- `notice_uncertain_warnings`

`last_send_error` 仅保存 bot/profile 的非敏感别名、错误类别和 HTTP/API code；不保存消息正文、真实 ID、URL query 或凭据。

## 6. 数据流

```text
Hermes system notice
  -> hook POST /events（不重试）
  -> sidecar classify + render
  -> Feishu send/reply with stable UUID
       -> success: delivered
       -> retryable: bounded retry with same UUID
       -> permanent: not_sent
       -> exhausted/timeout: unknown
  -> hook fallback policy
       delivered  -> suppress native duplicate
       not_sent   -> original full-text fallback
       unknown    -> one generic uncertainty warning
```

## 7. 测试设计

### Unit

- create 与 reply 请求都携带相同 `delivery_uuid`。
- UUID 长度、稳定性、bot/session 隔离。
- 429/502/503/504 和网络超时分类为 retryable/unknown。
- 400 参数错误分类为 not_sent。
- 错误文本脱敏。

### Integration

- 前两次 503、第三次成功：只产生一个 message id，尝试 3 次，UUID 相同。
- reply card 使用同一去重策略并保留 `reply_in_thread`。
- 永久失败触发一次原文 fallback。
- 超时/未知结果只触发一次固定异常提示，不发送原始通知正文。
- 非系统消息仍 fail-open 到原 adapter。
- event-auth 非 loopback 请求不因重试设计产生 replay。
- metrics 与 diagnostics 不包含正文、真实 ID 或 secret。

### Release gate

运行 hook runtime、Feishu client、server、topic/notice 回归与全量 pytest；真实飞书 smoke 至少覆盖普通独立通知、topic reply、模拟一次 transient retry 后单卡成功，以及 0 条重复原文灰色消息。

## 8. 兼容与发布

- `delivery_uuid` 为内部可选参数，第三方 fake client 可通过默认值保持兼容。
- 默认配置无需新增用户字段。
- 推荐先独立发布为一个可靠性修复版本，再进入 #133 的 patcher/render 改动，降低回归面。

## 9. 验收标准

1. 瞬时 502/503/504 下最多创建一张通知卡，且能在 3 次内恢复。
2. 明确未发送时用户能看到原文 fallback。
3. 结果未知时用户能看到一次通用异常提示，但不会看到重复通知正文。
4. `/health` 能解释重试与未知结果，且不泄露敏感信息。
5. 现有 accepted-card native suppression、topic 和 WebSocket 行为不回归。
