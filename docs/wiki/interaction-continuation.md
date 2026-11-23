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

交互失败或过期后也建立同一按需续答边界，使原生回退问题之后的实际输出落在新卡。sidecar 本地过期可能先于 Hermes 的失败事件；两种顺序均保留同一边界，重复或迟到失败不重置已送达续答。暂停审批仍冻结输入并等待原有明确选择，任务终态关闭未决交互本身不创建空卡。

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

续答卡确认送达后，sidecar 在后台更新旧段。旧段更新最多同时运行 64 项，每项限时 10 秒。达到上限时保留旧段原样，当前正文和终局确认继续使用新卡。会话重开后，上一轮的任务停止重试和回执精简。

如果本轮启用了完成提醒，sidecar 在最终正文确认送达后，按本轮的机器人、话题和提醒配置发送。即使上一张卡的更新仍在等待，提醒也能先发出。有显示检查点时，已发送状态随即保存，旧卡的收尾可以稍后完成。

文本模式的独立回执在决定后展示“交互结果已记录”，保留问题/选择/历史而不继续旋转。连续题的后续状态不回写旧回执，唯一 legacy 回执保持原方言。选择后等待续答期间停止旧显示 owner 的动画；创建失败后仍可在当前 owner 保留新输出。

## V4.6.6 审批重复精简

整轮 completed/failed 后，sidecar 显式 PATCH 独立完整审批回执，确认问题、操作范围、选项和决定仍在平台后，才从正文或旧段卡移除重复审阅/选择块。指纹绑定会话路由、问题与决定；唯一 owner、pending/paused/failed 交互、容量不足、PATCH 失败或会话已被替换时保留。连续审批最多登记 32 个旧段，仅保存展示副本，不保存 callback token 或 admission；不写入检查点，不恢复执行。clarify 不在此精简范围。可选清理失败不阻断最终答案。

## 开发候选：查看续答

新续答明确送达后，旧 schema-2 卡可以提供“查看续答”按钮。优先验证该次 IM create/reply 返回的 `message_app_link`；该字段缺失或为空时，只用同一回执的消息、聊天与精确位置字段生成官方品牌 AppLink。位置必须是非负整数或最多 20 位的十进制数字字符串，不能从消息 ID 推算。话题必须同时具备匹配的 `thread_id` 与 `thread_message_position`，缺失时不退回父聊天定位。非空但非法的原链接不会被忽略后另造链接。CardKit 使用最终 IM 消息回执，不能使用卡片容器 ID。真实平台 create/reply 回执可能只有消息与聊天 ID。仅在续答确认送达、旧卡需要导航且回执未提供可用导航时，对新消息 ID 做一次精确 GET 查询；不列举聊天历史。查询包含取 token 在内最多 1.5 秒，响应最多 64 KiB，不跟随重定向、不重试。必须返回唯一、未删除且消息与聊天身份完全匹配的条目；查询后再次核对当前 owner，改变则不回写旧卡。查询失败、话题定位不完整或品牌未知时保留原提示，不改变已确认的发送结果，也不补发消息。

按钮只打开已确认的消息，不重新执行任务或批准操作。仅按钮使整卡超限时省略按钮，原文与审批范围不裁剪；旧卡更新失败不回滚新 owner。后续审批重复精简保留导航，不为导航增加检查点字段或恢复旧授权。

来源：[官方发送回执模型](https://github.com/larksuite/oapi-sdk-python/blob/e321c555061b60cfbb3a3166b22c8ebc8a45fa82/lark_oapi/api/im/v1/model/create_message_response_body.py)、[官方消息 AppLink 实现](https://github.com/larksuite/cli/blob/1d26c2283915a4f8e7d03a880ed0e4fae91309a8/shortcuts/im/convert_lib/content_convert.go#L288)、[JSON 2.0 按钮](https://open.larkenterprise.com/document/feishu-cards/card-json-v2-components/interactive-components/button)。本候选采用同一回执或精确消息查询的位置构造链接的方式，但拒绝浮点、布尔或猜测位置，并在话题信息缺失时保留不导航的边界。自动化与桌面/移动端真实点击仍分别验收，尚未发布。

## 开发候选：旧段内容与拒绝语义

`task` 的续答明确送达后，旧段保持蓝色“已转入续答”标题，将原文和工具历史放入默认收起的“此前内容”面板；当前回复卡仍按原阅读设置展示过程。这样避免旧段与当前过程同时展开重复，也保留可追溯内容。只包装已支持的 Markdown/分隔线，展开已有过程面板后单次搬入，避免嵌套面板。包含未知组件、表格或表单，或包装后超限时，保持原旧卡；不删除内容来换取精简。导航按钮仍在历史面板外，只有真实有效目标才显示。

审批拒绝与工具执行失败不同。原始审批回调必须与尚待处理的完整身份匹配，才在同一轮显示状态里记录工具拒绝/超时。对应工具结果到达时，仅将明确 `ok/error` 转成已确认的 `blocked/timeout`；未知、取消等状态保留，任意工具返回的自由文本或 JSON 不授予这种状态覆盖。该记录不决定执行或授权，同一调用重试保持相同结果，并随原轮次生命周期结束。每轮最多保留 128 条精确结果；达到上限后，无法精确归属的 `ok/error` 以“结果待核对”保守展示，不能误报执行成功，显式取消等状态不变。拒绝、超时、待核对均属于工具终态，不显示运行旋转或成功对勾。

Android 验收必须同时查看旧段、审批回执和最终卡：不能仅检查最后一张而漏掉跨卡重复，也不能把绿色工具对勾当作“拒绝流程完成”。API 消息类型和客户端外观分别记录，深色旧卡外观类似原生文本不代表其实际消息类型。
