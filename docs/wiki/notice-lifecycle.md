# 临时通知的保留与撤回

V4.6.6 继续适配 [PR #338](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/338) 的通知体验，以已知 producer 和实际发送身份确认撤回归属。

## 用户可见行为

- 已知在线、重启、关闭、重启完成和 draining 的原生短提示在确认发送后按 15 秒收尾；同一 profile/bot/chat/thread 后续确认投递可以提前清理。
- Home 使用自己的期限，其他话题活动不触发 Home 提前清理。重启/排队独立 notice 卡不算工作恢复，不触发提前清理。
- 原生后台成功一行提示使用 15 秒计时。原生审批已过期的“命令未执行”纠正记录、失败信息、带输出结果、完整审批决定及所有正式结果卡保持留存。原生 callback 可能已显示 Approved，不能把唯一纠正记录当临时提示删除。
- requester 的原生重启完成提示使用彩色 ♻️。普通回答即使逐字引用相同文本也不会因此改写或撤回。
- task 模式下，已验证的 Working producer 每个原生周期通过认证 `/delivery/policy` 查询精确 turn 的 `accepted_task_card`。只有同 profile/chat/conversation 的实际 owner 已建卡且本轮有效配置为 task，才跳过该次原生发送或编辑；不新增卡片、不更新等待输入的表单，也不把 policy=card 当作投递证明。
- classic、未建卡、身份不完整、旧 sidecar 或查询失败时，Working 保留 Hermes 原地编辑及终轮清理。HFC 不安排周期撤回，避免删除更新目标后每轮重新发送；接管前已有的原生心跳仍由 Hermes 收尾。一次性的压缩、等待审批、重试提示仍使用原有 15 秒策略。
- Redirect、Interrupt、Steer 使用原有策略。未知模板、未知签名、无法验证的 adapter 或 profile 保留原始行为。

## 中文瞬态提示（#434，V5.0.2）

Hermes 开启 `display.language: zh` 后，原有英文判定无法识别中文 busy 回执。现在 redirect、interrupt、steer（含活跃子代理变体）的简体中文和英文完整模板共用同一识别边界，确认发送成功后沿原 profile/chat/thread 调度 15 秒撤回；允许原生单行活动详情。裸 `⏳ 正在压缩上下文` 与其 `...` 形式沿用英文裸压缩提示的撤回策略。没有新增配置，也不读取或改变 Hermes 的当前 locale。

文案依据固定上游 [`e06df9c2ea`](https://github.com/NousResearch/hermes-agent/commit/e06df9c2eae6901f4c88f464e0ae6e753c0428b8) 的 `gateway/run_busy.py`、`locales/en.yaml` 和 `locales/zh.yaml`；`transient_notices.py` 只识别明确列出的模板。未知翻译、用户自定义文案、额外正文和 onboarding 指南保持可见。旧英文 redirect 的宽前缀判定也收紧到完整回执，避免把指南或回答当作一次性通知。

报告中的「子代理正在工作」需区别对待：该修订的真实 busy producer 给它和压缩状态拼接 `demoted_tail`，明确说明用户消息已排队、稍后处理以及 `/stop` 的作用。这些完整消息保留，裸子代理状态也未被新增到撤回清单；它们并非已完成投递的一次性回执。中英文排队提示、持续 Working 心跳的原地编辑及 Hermes 原生收尾保持不变。

自动化使用含 SHA 与文件摘要的最小上游源码切片，执行实际 compose 方法和已生成的 busy send hook，并覆盖 adapter 原生文本出口、真实回环 HTTP 调度、重复登记、路由与失败。固定片段测试不代表完整该版 Hermes/Gateway 执行；尚无真实飞书、桌面或移动端验收。

## 压缩通知复测（#434，V5.0.4）

Timeral 在 HFC 5.0.2、Hermes `e06df9c2eae6901f4c88f464e0ae6e753c0428b8` 的复测中确认中文 busy 回执会撤回，但真正发送的 Preflight 和压缩完成提示未调度撤回；5.0.3 的判定没有变化。本次只把两种完整原生模板接入现有 15 秒策略：`📦 Preflight compression: ~{tokens:,} tokens >= {threshold:,} threshold. This may take a moment.` 与 `✓ Context compaction complete — continuing turn...`。数字遵循 Python 原生非负整数千位分组格式，额外正文、换行、未知翻译和未知标点仍保留。

该上游的 FeishuAdapter 与 BasePlatformAdapter 没有 `send_or_update_status`，Gateway 的同名分流函数在飞书逐条调用 `send`；PRE 为 `lifecycle`，DONE 为 `compacted`。这些消息没有持续编辑目标，上游终局清理只保存待 DELETE 的消息 ID。撤回不会清除未来编辑所需的缓存；晚到的原生清理可以再次尝试 DELETE，失败仍按上游规则吞掉。`🗜️ Compacting context` START/HEARTBEAT、`⏳ Working`、失败警告和排队指南未加入本次匹配，不改原有编辑或收尾生命周期。

固定片段与完整文件 SHA 位于 `tests/fixtures/compression_status_e06df9c.json`。回归执行真实 Gateway egress 函数与 HFC send wrapper，经回环 HTTP 交给现有 recall owner，并覆盖原文及返回对象、profile/chat/thread、pending 去重、晚到 owner 保护、原生终局清理与删除失败。平台发送及删除使用测试端口；不代表真实 Gateway/model、飞书 API 或客户端验收。

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
