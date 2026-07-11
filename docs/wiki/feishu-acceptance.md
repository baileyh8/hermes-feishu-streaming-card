# 真实飞书验收清单

自动化测试不能完全证明 Feishu/Lark 客户端体验。涉及卡片 UX、topic、系统提示、命令卡片的版本，发布前需要真实飞书 smoke。

## 准备

- 本机 Hermes Gateway 正常运行。
- sidecar 已启动：`python -m hermes_feishu_card.cli status --config ~/.hermes_feishu_card/config.yaml`
- `doctor --explain` 通过，且 `Runtime import` 指向当前版本。
- 飞书 bot 已在目标会话可用。
- 不在仓库、issue 或日志中暴露 App Secret、tenant token、真实 chat id。

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
- 临时 Hermes sandbox 中两步安全修复成功；重启 Gateway 确认卡正常，并在最终执行前取消。
- 普通流式卡从生成中到完成态保持一张卡，完成 footer/layout 不变，没有灰色重复答案。
- 本轮 sidecar 发送与更新均成功，Gateway 日志没有新的 operations forward timeout。

仍待真实验收：卡片实际执行 Gateway 重启、群聊发起者与换操作者拒绝、topic、cron、profile route mismatch。existing-container Docker 见 release-readiness 单独门禁。

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

- 出现模型选择卡片。
- 使用下拉或选项选择模型。
- 结果卡片显示模型已更新。
- 再问“现在是什么模型”，模型应与选择一致。

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
