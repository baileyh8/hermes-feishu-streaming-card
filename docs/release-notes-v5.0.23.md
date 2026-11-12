# v5.0.23：完成通知门槛、身份路由与有界分段

## 变更范围

- **#502 通知最短时长**：新增 `completion_notify.min_duration_seconds`。该门槛只作用于单独 message 完成通知，且前提是 `completion_notify.enabled` 已显式设为 `true`（默认关闭）；`placement: card` 的卡内完成呈现不经过这道门槛。低于阈值的轮次不发通知，重放的 `message.completed` 也不会补发。时长优先取 Hermes 上报的本轮耗时，缺失时回退会话记录的实际存活时间。`min_duration_seconds: 0`（默认）表示不按时长过滤。
- **#508 sender 身份路由**：较新网关事件的 `message.completed` 只带租户 `user_id`。mention 开启时通过 contact API 按需解析 `open_id` 并缓存成功结果，失败不缓存、通知仍以无 @ 形式发出。合成回合（后台进程/队列完成）不发通知；事件声称非法 sender id 视为伪造信号，整条不发。话题投递保持严格 guard：缺 reply anchor 时不做 top-level 兜底发送。
- **#513 有界分段**：新增 `card.over_limit: segmented`（默认 `native`）。终态答案在所有卡内降级后仍超预算时，投递一张主卡（开头正文＋统计＋可折叠过程面板）加编号续卡；总卡数不超过 5（含主卡），续卡按整节边界切分、逐卡过预算校验。任一续卡不可行或投递失败时回退既有 native 全文 handoff，已发出的续卡允许与全文回退并存。delivery_key 保证同一终态事件重投不重复发卡；这不构成永久 exactly-once。流式期间仍是单张实时卡。

## 验收与限制

三个 PR 的聚焦测试已在该分支提交；冻结候选提交上的完整 CI、普通 wheel/真实子进程安装、真实 Hermes Gateway/model 回合、真实飞书客户端 smoke 与桌面/Android/iOS 视觉验收尚未执行，公开发布前如实记为 pending，不提前写为通过。API 成功或 CI 绿色不代表真实客户端已验收。

配置示例见 README 配置段与 user-guide；`enabled` 默认 `false`，启用后单独通知按上文 #502/#508 规则发送。安装包契约与目录布局不变。

## 贡献记录

感谢 [tidytorch](https://github.com/tidytorch) 在 [#513](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/513) 提交有界分段投递（4 个原始提交保留），[Cassius0924](https://github.com/Cassius0924) 在 [#508](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/508) 提交 mention fallback 身份路由（4 个原始提交保留），[jackwude](https://github.com/jackwude) 在 [#502](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/502) 提交通知最短时长门槛。代码署名以 Git 原始提交为准；历史贡献记录保留。
