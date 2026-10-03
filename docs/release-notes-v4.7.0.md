# V4.7.0：任务卡片与阅读体验

**发布候选，待最终验收，尚未发布。** 最新稳定版仍为 V4.6.13。本页准备发行说明，不代表已通过全部客户端与发布门禁；实际记录见[本轮验收](reviews/2026-10-03-task-cards-acceptance.md)。

## 主要变化

- 新增可选 `card.reading_preset: task`。卡片集中显示状态和当前动作，正文优先，过程可折叠，页脚保留有依据的统计；统一正文、过程和统计的字号层级，减少重复标签。已有预设和显式配置保持有效。
- `card-config` 展示外观有效值及来源，并可生成九种状态的离线 HTML/Card JSON 预览。报告说明“下次加载”的配置，不冒充运行中已生效；预览使用内置样例，不访问聊天或发送飞书消息。
- 订阅周额度只在实际归属于 `openai-codex` 的 GPT 回合显示。其他模型、来源不明或模型切换后晚到的查询结果均隐藏。
- 保留完整工具参数、问题和授权范围，修复长详情裁剪后参数消失、暂停卡跨 JSON 方言以及任务结束后仍显示待操作入口的问题。只有完整独立回执确认送达后，才精简旧卡重复范围。
- Hermes 明确返回中断结果，或受控 `/stop` 确认取消原处理任务后，原任务卡收尾。显示投递不阻塞后续消息。辅助交互卡保存有界、无 token 的展示记录，供过期和重启后更新为失效回执；不会恢复执行、waiter 或旧授权。旧检查点缺少辅助卡身份时不猜测历史消息。
- task 模式在精确任务卡已接管后抑制重复的原生 Working 心跳；classic、未知身份或查询失败继续使用 Hermes 原行为。

60 秒没有新事件时，任务布局只做一次有界更新，说明“等待新进展、执行情况尚待确认”。展示恢复同样不代表任务仍在执行。

## 升级与启用

版本公开后，通过已有安装器更新，并对实际 Hermes 目录重新运行 `setup` 或 `install`，使包版本与受管 hook 一致。随后通过原服务管理入口重启 sidecar 和 Hermes Gateway。不要手改已安装的 Hermes 源码，也不要用示例覆盖已有配置。

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

## 验收进度

`1e993bd` 的阶段完整回归为 **4,509 passed、22 skipped**。`9d986ad` 的 14 项 GitHub 检查全部通过，真实桌面长任务、多选及深浅主题已有证据；纯审批 `/stop` 暴露另一取消次序，长等待出现重复 Working 消息。`8edb213` 在升级后的 Hermes `3d0a61ac` 上已真实复验纯审批中断和 210 秒长任务去重；严格视觉检查仍发现失效审批语义和重复信息，CI 另暴露旧版 inline 回复布局兼容回归。相关修复需要最终包复验，不能借用早期成功记录宣称最终通过。

最终 SHA 的完整回归与跨平台 CI、全部相关真实流程、视觉门槛、精确合并、annotated tag、资产/checksums 和公开安装来源仍需完成。Android/iOS、深色和放大字号分别记录，缺失证据保持“未验证”。不会用自动化通过替代客户端结论。

## 贡献记录

本轮代码提交作者为 [baileyh8](https://github.com/baileyh8)。阅读体验继续回应 [jackwude](https://github.com/jackwude) 的 [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) 和 [leavrcn](https://github.com/leavrcn) 的 [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333)；这些署名对应需求与现场证据。保留全部历史代码作者、方案和问题报告记录，不把未合并的其他 PR 计入本版。
