# 临时通知的保留与撤回

V4.6.x 从 [PR #331](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/331)
吸收重启通知的生命周期思路，并保留现有卡片、失败说明和交互回执的边界。

## 用户可见行为

- 已发布的 Working、Redirect、Interrupt、Steer 临时提示继续使用原有 15 秒策略。
- HFC 专用通知生成路径发送的 `♻️ Gateway online — Hermes is back and ready.`，
  在成功投递并带有显式生成来源时，可以登记为临时重启通知。这个新增路径没有 15 秒倒计时。
  泛化 `adapter.send`、原生 home/重启/关机文本无法证明生成来源时继续保留；
  即使普通答案逐字等于这条模板，也不能仅凭内容授权撤回。
- 同一 profile、bot、chat、thread 后续成功发送或更新普通卡片、命令卡或交互卡，
  或成功发送已启用的独立完成提醒后，可以撤回登记在这次投递开始前的重启提示。
- 重启/排队独立 notice 卡不代表已经恢复工作，不触发这类撤回。
  普通 Gateway 原生文本也没有新增全局清理请求。
- 最终答案、失败解释、后台任务结果、审批决定与过期回执保持留存。
  引用、附加说明或未知重启文案不纳入新增文本白名单。既有 draining 提示卡保持原语义。

这里的撤回依据是后续成功投递，不是用户已读信号，也不是任务执行成功证明。

## 身份与失败边界

sidecar 的 `RestartNoticeRegistry` 保存服务端解析后的精确 scope 和已投递消息 ID，
不保存正文。空 thread 表示空 thread，不匹配其他话题；缺少 thread 的新话题首回复使用
reply anchor 隔离。任何话题的活动都不会顺带清理 home。
卡片 owner 的 profile 在创建时从已校验事件固定，重启后从检查点已有的路由字段恢复；
不从 turn ID 或 session key 拆分猜测，合法的 `opaque:turn` 不会变成另一个 profile。

发送/更新开始前先取得登记代次快照，只有确认投递成功后才安排撤回。
因此正在投递期间新登记的重启提示不会被一个迟到的成功结果删掉。
发送失败或结果不确定保持原提示；撤回任务容量不足或 Feishu DELETE 失败保留登记，
由后续同 scope 成功投递重试，不无限后台循环。DELETE 失败后至少等待 30 秒才允许下次重试，
避免每次卡片动画更新都重复请求平台。

登记最多 500 个 scope、每个 scope 8 条消息，标识字段最多 256 字符。
达到上限拒绝新登记并保留已有记录，不静默挤掉待重试项。重复登记幂等，
同一消息 ID 不能转移到另一个 scope。实际 DELETE 成功后释放登记容量。
答案 owner、卡片摘要及当前交互卡在登记和删除时都会检查，不允许按临时文本撤回。
已登记文本后来承载这些保留内容时，取消其临时通知身份；会话清理也不会让它重新变为可撤回消息。

registry 位于 sidecar 内存，可跨 Gateway 重启，但不承诺跨 sidecar 重启保存。
sidecar 重启后旧提示可能保留，后续不会凭内容猜测并删除历史消息。

## 实现与验收

- `notice_lifecycle.py`：有界 scope、成员代次与归属。
- `hook_runtime.py`：专用生成来源、精确模板、成功发送结果和 route 登记；
  注册失败不改变原发送结果。
- `server.py`：认证 `/recall/schedule` 的 `notice_family=restart`、`record_only=true` 分支；
  已验证路由、投递快照、现有撤回任务及成功后清理。

回归入口：

```bash
python -m pytest tests/unit/test_notice_lifecycle.py tests/integration/test_server_notice_lifecycle.py -q
python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_server.py -q
```

本实现的自动化包含 loopback HTTP 与模拟 Feishu 客户端；真实桌面/手机撤回和多 profile
验收仍需按 [稳定性策略](stability-test-policy.md) 和 [飞书验收](feishu-acceptance.md) 单独记录。
