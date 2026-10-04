# V4.7.0：任务卡片与阅读体验

本版提供可选任务布局，让状态、答案和执行过程更易阅读；非 GPT 模型不再显示 GPT 周额度。已有预设与显式配置保持有效。

## 主要变化

- 新增可选 `card.reading_preset: task`。卡片集中显示状态和当前动作，正文优先，过程可折叠，页脚保留有依据的统计；统一正文、过程和统计的字号层级，减少重复标签。已有预设和显式配置保持有效。
- `card-config` 展示外观有效值及来源，并可生成九种状态的离线 HTML/Card JSON 预览。报告说明“下次加载”的配置，不冒充运行中已生效；预览使用内置样例，不访问聊天或发送飞书消息。
- 订阅周额度只在实际归属于 `openai-codex` 的 GPT 回合显示。其他模型、来源不明或模型切换后晚到的查询结果均隐藏。
- 保留完整工具参数、问题和授权范围，修复长详情裁剪后参数消失、暂停卡跨 JSON 方言以及任务结束后仍显示待操作入口的问题。只有完整独立回执确认送达后，才精简旧卡重复范围。
- Hermes 明确返回中断结果，或受控 `/stop` 确认取消原处理任务后，原任务卡收尾。显示投递不阻塞后续消息。辅助交互卡保存有界、无 token 的展示记录，供过期和重启后更新为失效回执；不会恢复执行、waiter 或旧授权。旧检查点缺少辅助卡身份时不猜测历史消息。
- task 模式在精确任务卡已接管后抑制重复的原生 Working 心跳；classic、未知身份或查询失败继续使用 Hermes 原行为。
- task 正文中可安全识别的代码块使用 `plain_text` 显示，保留原语言标识和完整代码，改善浅色下的语法高亮可读性；canonical 内容与 classic 原行为保留。分块与容量回退已通过定向回归；V16 的指定桌面显示与正文行复制通过，复制末尾换行未纳入实测。

60 秒没有新事件时，任务布局只做一次有界更新，说明“等待新进展、执行情况尚待确认”。展示恢复同样不代表任务仍在执行。

## 升级与启用

通过已有安装器更新，并对实际 Hermes 目录重新运行 `setup` 或 `install`，使包版本与受管 hook 一致。随后通过原服务管理入口重启 sidecar 和 Hermes Gateway。不要手改已安装的 Hermes 源码，也不要用示例覆盖已有配置。

新布局需要主动选择：

```yaml
card:
  reading_preset: task
```

同层显式字段继续优先；全局、profile 和 bot 的覆盖规则不变。切回 `classic`、`focused` 或 `detailed` 并重启 sidecar 即可回退布局，历史卡片不会因此重写。Python 最低版本仍为 3.9。本次发布不自动升级 Hermes 或迁移用户配置。

```bash
hermes-feishu-card card-config --config <配置路径>
hermes-feishu-card card-config --config <配置路径> --preview-dir ./card-preview
```

更多说明见[任务卡片](task-cards.md)与[阅读预设](wiki/reading-presets.md)。离线手机宽度和主题模拟不代替真实客户端验收。

## 验证与已知边界

- 最终运行代码完整回归 **4,854 passed、22 skipped、0 failed**，14 项 CI 通过；普通 wheel 与源码逐文件核对。跳过项属于独立上游矩阵、Windows 语义和 PowerShell，另由对应 CI 覆盖。
- 真实桌面已覆盖深浅主题、窄聊天区、125% 放大、代码复制，以及旧答案保留、空续答停止、超过 180 秒 Working 去重、多选与待审批重启。具体版本、失败记录和复验范围见[验收记录](reviews/2026-10-03-task-cards-acceptance.md)。
- **发布范围：** 2026-10-04，经 Bailey 确认，本版按已验收桌面范围发布。**Android/iOS 真机和超过 5 秒取消的真实时序分支仍未验证**；延迟取消有自动化回归覆盖。桌面窄窗与离线预览不作为手机真机证明。
- 真实代码复制验证了选中的正文行，没有混入语言标签或行号；未选取的末尾换行不在复制结论内。此轮没有正式 WCAG 数值测量或完整流式连续帧覆盖。

精确合并提交、annotated tag、发行资产与公开安装结果以 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.7.0) 为准。正式 Hermes、飞书外观均已恢复，退役测试环境已清理，保留必要证据。

## 贡献记录

本轮代码提交作者为 [baileyh8](https://github.com/baileyh8)。阅读体验继续回应 [jackwude](https://github.com/jackwude) 的 [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) 和 [leavrcn](https://github.com/leavrcn) 的 [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333)；这些署名对应需求与现场证据。保留全部历史代码作者、方案和问题报告记录，不把未合并的其他 PR 计入本版。
