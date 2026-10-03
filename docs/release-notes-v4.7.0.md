# V4.7.0：任务卡片与阅读体验

**发布候选，待最终验收，尚未发布。** 最新稳定版仍为 V4.6.13。本页准备发行说明，不代表已通过全部客户端与发布门禁；实际记录见[本轮验收](reviews/2026-10-03-task-cards-acceptance.md)。

## 主要变化

- 新增可选 `card.reading_preset: task`。卡片集中显示状态和当前动作，正文优先，过程可折叠，页脚保留有依据的统计；统一正文、过程和统计的字号层级，减少重复标签。已有预设和显式配置保持有效。
- `card-config` 展示外观有效值及来源，并可生成九种状态的离线 HTML/Card JSON 预览。报告说明“下次加载”的配置，不冒充运行中已生效；预览使用内置样例，不访问聊天或发送飞书消息。
- 订阅周额度只在实际归属于 `openai-codex` 的 GPT 回合显示。其他模型、来源不明或模型切换后晚到的查询结果均隐藏。
- 保留完整工具参数、问题和授权范围，修复长详情裁剪后参数消失、暂停卡跨 JSON 方言以及任务结束后仍显示待操作入口的问题。只有完整独立回执确认送达后，才精简旧卡重复范围。
- Hermes 明确返回中断结果后，原任务卡正确收尾。辅助交互卡保存有界、无 token 的展示记录，供过期和重启后更新为失效回执；不会恢复执行、waiter 或旧授权。旧检查点缺少辅助卡身份时不猜测历史消息。

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

`1e993bd` 的阶段完整回归为 **4,509 passed、22 skipped**。真实桌面 `/stop`、辅助回执恢复和多选关键流程已有执行证据；后续视觉密度修复仍需绑定最终候选复验。这些结果不构成对后续提交的完整通过声明。

最终 SHA 的完整回归与跨平台 CI、全部相关真实流程、视觉门槛、精确合并、annotated tag、资产/checksums 和公开安装来源仍需完成。Android/iOS、深色和放大字号分别记录，缺失证据保持“未验证”。不会用自动化通过替代客户端结论。

## 贡献记录

本轮代码提交作者为 [baileyh8](https://github.com/baileyh8)。阅读体验继续回应 [jackwude](https://github.com/jackwude) 的 [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) 和 [leavrcn](https://github.com/leavrcn) 的 [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333)；这些署名对应需求与现场证据。保留全部历史代码作者、方案和问题报告记录，不把未合并的其他 PR 计入本版。
