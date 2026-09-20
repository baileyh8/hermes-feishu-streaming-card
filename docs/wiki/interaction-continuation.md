# 交互后的续答与显示归属

V4.6.4 已发布，使 clarify 和 approval 之后的结果按聊天创建时间自然向下排列。普通问答仍更新原卡；只有交互选择边界之后的实际输出才按需创建续答。相关入口：[事件流](event-flow.md)、[阅读预设](reading-presets.md)、[本轮真实验收](feishu-acceptance-v4.6.4.md)。

## 三种身份不能混用

- **执行回合**：`CardSession` 的 canonical turn/source/profile/chat 不变，工具、答案、时间线、附件和 footer 统计属于整轮。
- **交互消息**：legacy callback 卡承载问题、选项、操作范围、token 与选择结果；完成后留下不可再次批准的回执。
- **当前显示段**：schema 2.0 普通卡承接流式输出。独立记录显示边界和 generation，不用平台消息 ID 改写执行回合身份。

有 schema 2.0 owner 时，legacy 交互卡只负责交互，不会直接被提升为流式 owner。若交互是整轮第一张、也是唯一一张 legacy 卡，则在合法续答卡确认之前暂时保留它；所有回退更新必须保持原方言。

## 选择后的时序

1. 投递交互卡，保留原阶段内容。pending/paused 时继续冻结无关 PATCH 与动画，避免清空表单输入。
2. 在原交互锁与身份校验内接纳选择，保留问题和选择结果，记录 `display_segment` 边界。callback 不推进 Hermes `/events` 的 transport sequence。
3. 等待实际 answer/thinking、工具/子任务活动或带正文的终局；如果直接进入下一题，就显示下一题，不创建空白续答。
4. 新续答发送使用绑定 interaction 与边界序号的稳定 delivery key，继承 bot/profile/chat/thread、reply anchor 与 `reply_in_thread`。先停止旧动画，只有确认送达才切换 `FEISHU_MESSAGE_IDS_KEY` 并保存检查点。
5. 后续流式 PATCH 只更新当前显示 owner。原交互卡保留回执，旧题 token、重复/迟到选择不得作用于下一题。

`display_view` 是展示投影，不清空 canonical 历史。增量段只展示本段答案和相关活动；完整终局快照会以“本轮完整结果”标记并完整保留。不能通过文本前缀猜测删掉旧内容。续答段数有界，达到预算后保留既有 owner 与完整内容，不无限开卡。

## 失败、唯一 legacy owner 与重启

续答 create 失败或结果不明时，保持原 owner，说明续答尚未确认送达并保留内容；不得按每个后续 delta 用新身份再次 create。容量超限继续使用共享 serializer 和原有有界 native handoff，不能先发送半截正文。

只有 legacy owner 时，保存去掉 callback controls/token 的静态问题/决定回执。普通回退、失败终局、容量回退和恢复后的显示更新通过 `legacy_owner` 生成 legacy payload；不得对这条消息发送 schema 2.0 PATCH。新的 schema 2.0 续答确认送达后，才清除这一临时 legacy owner 状态。

展示检查点保留有界分段状态和静态回执，旧格式仍可读取。重启不恢复执行、pending waiter 或审批 token；旧 pending 入口显示已失效，不能因为卡片可恢复就重新批准旧操作。

## 首次回调接线

无需先调用 slash/model/resume。首次 enabled Feishu turn 或 interaction 请求会确保 command-card callback 已接线。真实生成的嵌套 Hermes callback 可能只有 `ctx`：只从绑定回调的 TurnRunner 或已记忆 Gateway 恢复归属，并验证 source、profile-aware resolver 和同一 live adapter。无法证明则保持原生 fail-open。

已连接的 Lark `EventDispatcherHandler` 保持原对象；通过对应 WebSocket loop 更新现有 processor 的 callback。重连后的 handler/client/loop 要重新核对，旧 loop 不得修改新 transport。验收必须从真实 Gateway 首轮触发；直接往 sidecar 注入请求不能单独证明这条链路。

## 自动化与真实边界

重点用例：`test_cold_start_card_callbacks.py`、`test_interaction_continuation.py`、`test_legacy_owner_fallback.py`、`test_legacy_owner_checkpoint.py` 以及真实 SDK compatibility matrix。覆盖真实生成闭包、首次选择、多个 profile、连续题目、失败/重复/迟到事件、方言保持、检查点与内容/统计保留。

自动化不证明飞书手机排版、原生点击手感、推送或真实上游执行已完成。按[本轮清单](feishu-acceptance-v4.6.4.md)记录实际候选、设备、触发方式和结果。

## V4.6.5 旧段收尾

新续答确认送达后，旧 schema-2 卡使用新输出之前的快照保留原文、工具历史与选择回执，并标记“本段已转入续答”。这是显示归属转移，不是整轮执行成功；局部工具/子任务也改为转交状态，canonical 数据不改。创建失败/不确定时不收尾，唯一 legacy 回执不跨方言更新。旧 PATCH 失败保留新 owner 并记录诊断，不能阻断终局。参考 PR #339，未采纳无条件移除已决定审批的部分。

文本模式的独立回执在决定后展示“交互结果已记录”，保留问题/选择/历史而不继续旋转。连续题的后续状态不回写旧回执，唯一 legacy 回执保持原方言。选择后等待续答期间停止旧显示 owner 的动画；创建失败后仍可在当前 owner 保留新输出。
