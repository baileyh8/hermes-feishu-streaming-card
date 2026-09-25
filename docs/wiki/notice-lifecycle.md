# 临时通知的保留与撤回

V4.6.6 继续适配 [PR #338](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/338) 的通知体验，以已知 producer 和实际发送身份确认撤回归属。

## 用户可见行为

- 已知在线、重启、关闭、重启完成和 draining 的原生短提示在确认发送后按 15 秒收尾；同一 profile/bot/chat/thread 后续确认投递可以提前清理。
- Home 使用自己的期限，其他话题活动不触发 Home 提前清理。重启/排队独立 notice 卡不算工作恢复，不触发提前清理。
- 原生后台成功一行提示使用 15 秒计时。原生审批已过期的“命令未执行”纠正记录、失败信息、带输出结果、完整审批决定及所有正式结果卡保持留存。原生 callback 可能已显示 Approved，不能把唯一纠正记录当临时提示删除。
- requester 的原生重启完成提示使用彩色 ♻️。普通回答即使逐字引用相同文本也不会因此改写或撤回。
- Working 心跳由 Hermes 原地更新并负责终轮清理，HFC 不再安排 15 秒撤回，避免删除更新目标后每轮重新发送。一次性的压缩、等待审批、重试提示仍使用原有 15 秒策略。
- Redirect、Interrupt、Steer 使用原有策略。未知模板、未知签名、无法验证的 adapter 或 profile 保留原始行为。

## 身份、期限和失败边界

重启账本从 V4.6.5 起位于私有 `restart-notices-v1/owned.json`，记录实际 profile/bot/chat/thread、应用摘要、message ID、代次、创建与重试时间，不保存正文。V4.6.6 使用已有创建时间恢复 15 秒期限；无需有人再次发言。普通短提示计时仍为进程内 best-effort。

发送/更新前取得代次快照，明确投递成功后才可提前唤醒同 scope 的现有计时；不会取消已在进行的 DELETE。迟到投递不清理后来登记的提示。空 thread 不通配其他话题；新话题首回复用 reply anchor 隔离。卡片 owner profile 来自已校验事件与检查点，不从 turn ID 或 session key 猜测。

DELETE 失败保留归属与至少 30 秒冷却，由后续同 scope 确认投递或下一次 sidecar 启动重试，不无限后台循环。计时容量不足保留登记，后续投递可重试。答案 owner、卡片摘要、当前交互卡在登记和删除时均受保护；取得正式内容后永久取消临时身份。

账本最多 500 个 scope、每个 8 条、7 天、4 MiB，标识最多 256 字符；0700 目录、0600 文件、原子写和摘要校验。损坏、应用变化或路由不允许 card 时拒绝恢复；7 天过期只忘记本地归属，不扫平台历史。发送与登记并非原子事务。详见[通知归属](notice-ownership.md)。

## 实现与验收

`notice_producers.py` 包装精确已知签名，以 task-local 来源绑定真实发送。Base 延后发送的 drain 返回值只在同一 delivery invocation 中携带来源，退出即清理；并发会话不共享授权。`notice_lifecycle.py` 管理有界归属与期限；`server.py` 验证路由、身份并执行有界撤回任务。

```bash
python -m pytest tests/unit/test_notice_producers.py tests/unit/test_notice_lifecycle.py tests/integration/test_server_notice_lifecycle.py -q
python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_server.py -q
```

自动化与真实平台、桌面、手机证据分开登记，见 [V4.6.6 验收](feishu-acceptance-v4.6.6.md)。
