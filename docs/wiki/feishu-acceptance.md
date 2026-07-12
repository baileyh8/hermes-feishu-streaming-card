# 真实飞书验收清单

自动化测试不能完全证明 Feishu/Lark 客户端体验。涉及卡片 UX、topic、系统提示、命令卡片的版本，发布前需要真实飞书 smoke。

## 准备

- 本机 Hermes Gateway 正常运行。
- sidecar 已启动：`python -m hermes_feishu_card.cli status --config ~/.hermes_feishu_card/config.yaml`
- `doctor --explain` 通过，且 `Runtime import` 指向当前版本。
- 飞书 bot 已在目标会话可用。
- 不在仓库、issue 或日志中暴露 App Secret、tenant token、真实 chat id。

## V4.0.0 实时双轨卡片

使用刻意准备、适合公开展示的任务文案验证以下状态；截图只保留真实飞书卡片区域，不包含群名、头像、真实 chat/open id、无关会话或桌面内容。

- 运行中：Header title 保留用户配置；Hermes 每次发送非空 `progress_callback.preview` 时，subtitle 根据工具名原地更新为动作摘要，不直接暴露完整命令、URL query 或私有路径；正文独立累积公开 `thinking.delta`。
- 等待用户：clarify/approval 问题只在 Header 出现一次，正文保留必要说明和原生按钮/选项；点击后继续 PATCH 同一张卡。群聊必须由任务发起者点击，其他成员点击应被拒绝且不消耗交互。
- 失败：Header 保留最后一条有效工具预览，正文明确显示 Hermes 的失败原因；footer 只有失败状态。
- 已完成：飞书原生回复引用作为唯一 Header 显示用户原指令，Card JSON 不再叠加配置标题；正文只显示最终答案，完成态 footer 才显示时长、模型、token 和 context 数据。没有有效 reply anchor 的兼容路径仍使用配置标题 fallback。
- 回归：工具 timeline、附件顺序、topic 锚点、command/operations 卡片、原 footer 布局和原生灰色消息抑制保持不变。

推荐展示提示词：

```text
请查询广州未来两小时的天气变化，并给我一份简洁的通勤建议。请先核对天气数据，再整理结论。
```

```text
请把广州周末出行建议整理到演示文件中。覆盖现有演示内容前，请先让我确认。
```

```text
请读取演示天气数据并生成摘要；如果数据源不可用，请明确报告失败原因。
```

## V3.10.0 `/resume` 与 footer

- 私聊：发送裸 `/resume`，确认只出现一张原生下拉卡、当前会话有标记；选择其他会话后先显示恢复中，再在原卡显示结果，无灰色文本列表和 callback timeout。
- 当前会话：再次打开 picker 并选择当前项，确认 original Hermes handler 返回 already-on 结果，不执行第二套 switch 逻辑。
- 过期/无效：使用过期 state 或无效 option，确认原卡显示失效提示且不切换会话。
- 群聊：发起者可选择；另一位用户点击被拒绝且 state 保留，随后发起者仍可完成。
- topic：picker 与结果留在原 topic；reply anchor/thread metadata 不丢失。
- fallback：session DB 空、adapter/card 不可用、群聊发起者 `open_id` 无法验证时，Hermes 原生文本列表仍可用。
- footer：常见模型名只有文本颜色变化；divider、字段顺序、分隔符、字号和普通卡 footer/layout 不变，未知/特殊字符模型名无 markup 注入。

## V3.9.1 可靠性热修

- 完成答案：构造 completed event 带较长 suffix 的任务，确认卡片保留完整正文且没有灰色重复 reply。
- 打断任务：旧任务仍在更新时发起新任务，确认旧卡收束为中断终态，迟到更新不再恢复运行态。
- 模型选择：打开 `/model`，选择模型后 callback 不出现超时 toast；先显示切换中，随后原卡显示成功或失败终态，不额外重复发送结果卡。
- 安装恢复：在临时 Hermes sandbox 验证 marker-only 安全恢复；不要在真实运行目录手工编辑 `gateway/run.py`。
- 回归：普通流式卡 footer/layout 不变。

## V3.9.0 运维卡（部分通过）

以下项目必须在真实飞书完成后才可标记通过；当前自动化证据不替代这些 smoke。

- 私聊：`/hfc doctor` 打开运维卡，执行重新检测、两步安全修复、重启确认；确认普通流式卡 footer/layout 快照不变。
- 群聊（group）：发起者能够完成 repair/restart；第二位操作者确认时被拒绝；再次由发起者确认后完成，并检查没有泄漏 chat id、token 或 transport secret。
- topic：在话题内打开运维卡后，普通 topic 流仍更新原卡，运维卡不改写普通 footer/layout。
- cron：cron 投递和普通定时完成卡不被运维操作阻断。
- profile route mismatch：以 main/child profile 或错误 `HERMES_FEISHU_CARD_PROFILE_ID` / endpoint 配置复现 mismatch，确认 `status`/`doctor` 仅显示脱敏 route chain，并修正后恢复。

2026-07-11 已通过的私聊基线：

- `/hfc doctor` 只生成一张运维卡，没有灰色原生未知命令。
- 中文诊断摘要与详情可见，footer 保持不变。
- 连续两次重新检测均快速返回，后台 successor 按钮仍可点击，最终 PATCH 同一张卡片。
- 本轮回调可靠性复测中，“查看诊断”和连续两次“重新检测”均在 156–201 ms 内 ACK；没有新增“目标回调服务超时未响应”，过渡态与终态继续 PATCH 原卡。
- 临时 Hermes sandbox 中两步安全修复成功；卡片实际重启 Gateway，先显示进行态，随后同卡显示完成态。
- 普通流式卡从生成中到完成态保持一张卡，完成 footer/layout 不变，没有灰色重复答案。
- 本轮 sidecar 发送与更新均成功，Gateway 日志没有新的 operations forward timeout。
- no-agent 一次性 cron 的结果正文进入普通完成卡；sidecar 的 event receive/apply/card-send 指标均成功且没有 native fallback。
- Hermes 上游 `cron run` 会在成功的一次性任务自动删除后再次读取 `last_status`，因此本次终端显示 `Ran now: failed`。这属于上游 CLI 状态误报；以飞书卡片、sidecar metrics 和保存的 cron 输出三方一致判定 cron 卡片验收通过。
- 临时设置错误 `HERMES_FEISHU_CARD_PROFILE_ID` 后，`doctor --explain` 显示 `profile_unknown` 与缺失 route，不暴露 chat id、token 或 secret；移除临时环境后恢复默认 profile，持久配置未变。

仍待真实验收：群聊发起者与换操作者拒绝、topic。existing-container Docker 见 release-readiness 单独门禁。

真实验收状态：**部分通过**。

## 普通会话

提示词：

```text
查一下广州明天天气
```

验收：

- 首张卡片出现。
- 正文持续更新。
- 工具调用进入“思考与工具”。
- 完成后只有一张最终卡片，没有额外灰色最终答案。

## Feishu topic / thread

在飞书会话中创建或打开话题，在话题回复框里发送：

```text
请验证当前 Hermes 飞书卡片插件是否已经支持话题内卡片连续更新。不要直接回答，先说明你会从哪些证据判断；然后依次检查本地版本、CHANGELOG、测试用例和运行状态；每次工具调用前先给一句阶段性判断。
```

验收：

- 右侧话题面板中出现卡片。
- 后续工具和答案持续更新同一张卡片。
- `思考与工具` 折叠区可展开，并显示工具 timeline。
- 完成态仍在话题面板内。
- 没有重复外溢的灰色 `system.notice`。

## 系统提示 suppression

提示词：

```text
V3.8.9 notice suppress smoke: please run terminal command date, then reply exactly topic smoke ok
```

验收：

- 卡片完成并回复 `topic smoke ok`。
- 如果触发 `Codex gpt-5.5 caps context...` 等上下文提示，不应额外出现在卡片外灰色消息里。
- Gateway 日志允许出现 `system notice native fallback suppressed`，表示已识别并抑制原生 fallback。

## Slash command cards

发送：

```text
/new
```

验收：

- 出现独立确认卡片。
- 点击“允许一次”或“始终允许”后有状态反馈。
- 允许后的 reset 结果以卡片反馈，不退回灰色文本。

发送：

```text
/model
```

验收：

- 出现模型选择卡片，第一层 Provider 数量、名称和当前项与本机 Hermes CLI `/model` 一致。
- 进入 DeepSeek 等已知 Provider，确认第二层模型数量和模型名称与 CLI 一致，不出现其他 Provider 的模型。
- 点击“返回”后恢复 Provider 列表；再次进入 Provider 后选择模型。
- 结果卡片显示模型已更新。
- 再问“现在是什么模型”，模型应与选择一致。
- 全程没有 callback timeout toast，也没有灰色重复消息。

## 长内容和 Markdown

提示词：

```text
生成一个包含 20 行、4 列的 Markdown 表格，并在后面附一个 80 行 Python 代码块。要求保持表格和代码块结构完整。
```

验收：

- 长表格没有被飞书渲染成 raw markdown。
- code fence 完整，没有半截围栏。
- 卡片完成后没有重复灰色全文。

## 记录方式

验收完成后可记录到 release notes 或 issue comment：

```text
真实飞书验收：
- 普通会话：通过
- 话题回复：通过
- system.notice suppression：通过
- /new：通过
- /model：通过
- 长 Markdown：通过
```

截图入库前需要遮挡私人头像、姓名、chat id、群名和不适合公开的上下文。
