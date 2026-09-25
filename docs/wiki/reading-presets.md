# 阅读预设与有效配置

阅读预设是 V4.6.x 中的可选配置组合。升级、全新安装和没有此字段的 YAML 都保持已有默认；不会自动选择新的阅读方式。它回应 [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) 的完成工具降噪和 [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333) 的思考正文分离需求。

| `card.reading_preset` | 实时思考 | 思考/工具过程 | 正常完成的正文工具行 | 失败的正文工具行 |
| --- | --- | --- | --- | --- |
| 缺省 / `classic` | 保持正文回退 | 折叠 | 保留 | 保留 |
| `focused` | 放入有界过程面板，正文等待答案 | 折叠 | 隐藏 | 保留最后活动 |
| `detailed` | 放入有界过程面板，正文等待答案 | 展开 | 保留 | 保留 |

这些是预设提供的默认值，已有显式设置仍会覆盖它们。`detailed` 继续遵守现有条目数、内容长度和卡片容量限制，并非无上限历史归档。预设不改变回答、失败原因、工具计数、审批、附件、终局投递、检查点和执行状态。

## 选择与覆盖顺序

```yaml
card:
  reading_preset: focused
```

顺序为 **全局 → profile → bot**。每一层先应用该层明确选择的预设，再应用该层显式字段；没有选择预设的层只覆盖写出的字段。下层明确选择预设会覆盖上层的预设组合或显式值；同层显式字段优先。例如：

```yaml
card:
  reading_preset: focused
profiles:
  work:
    card:
      show_reasoning: false  # 继承 focused，但隐藏过程面板
    bots:
      default: support
      items:
        support:
          # app_id / app_secret 使用现有配置
          card:
            reading_preset: detailed # bot 明确重选，会重新启用过程显示
            timeline_expanded: false # 同层显式值胜过 detailed 的展开默认
```

显式设置继续优先；V4.6.6 的终态精简保留异常工具：

- `hide_completed_tool_activity: true` 在 completed 和 failed 都隐藏成功工具及旧工具摘要回退，保留失败、取消和中断工具；过程记录和计数不受影响。
- `hide_completed_tool_activity: false` 明确保留 completed 和 failed 的正文工具区，即使继承 `focused`。
- 只有没有显式覆盖该字段的 `focused` 默认采用“正常完成精简成功工具、失败回合保留”；成功回合里的失败/中断工具也保留。
- `stream_thinking_to_body`、`show_reasoning`、`reasoning_format` 和 `timeline_expanded` 的显式值仍各自生效。`show_reasoning: false` 与 `stream_thinking_to_body: false` 同时使用时不显示实时思考。

现有示例 YAML 可能已写出全部旧开关。如果保留它们，选择预设也会保留这些明确选择；使用以下只读检查确认实际值，而不是只看预设名。

## 只读检查

```bash
hermes-feishu-card card-config --config ~/.hermes_feishu_card/config.yaml
hermes-feishu-card card-config --config /path/to/config.yaml --profile-id work --bot-id support --json
```

输出阅读字段的有效值及来源（default、global、profile 或 bot 的预设/显式字段）。`terminal_tool_activity` 是解释结果，不是新 YAML 开关：`visible` 表示终态保留，`failed_only` 表示仅失败保留，`unsuccessful_only` 表示两种终态均仅隐藏成功工具。多个 profile 且没有 default 时要求指定 `--profile-id`，不会猜测。

此命令不启动或重启服务，不修改配置，不请求 Feishu，不输出凭据、路由标识、配置路径或自定义标题。它说明所选 YAML 的下一次加载结果，不证明运行中的进程已经加载该文件；重启后须核实目标进程配置。

## 采用与回退

先备份 YAML，再选择预设并检查来源。如果希望采用整个预设组合，只移除与之冲突的显示字段，不改凭据和路由。重启 sidecar 后生效。恢复备份并再次重启即可回退；没有自动迁移，也不会重写已有历史卡或恢复旧执行。

## 验证边界

自动化使用真实回环 HTTP 覆盖全局/profile/bot 覆盖、两种 streaming 模式、completed/failed、显式 true/false、超长思考、同卡终局和晚到事件。共享 serializer 继续裁决容量。模拟 Feishu 客户端只能证明生成与路由行为；桌面、Android 和 iOS 的真实阅读效果仍需按[飞书验收](feishu-acceptance.md)检查。

## V4.6.5 可选过程面板

```yaml
card:
  timeline_order: chronological       # 默认 newest_first
  timeline_tools_per_reasoning: 2      # 默认 0，不额外限制；可设 0..100
```

顺序只影响过程面板，正文 code 思考仍按正序。每段思考的条数仅限制成功完成工具，保留最近 N 条；失败与运行步骤不被此规则移除，但所有条目仍受全局 `max_timeline_items` 和卡片容量门禁约束。历史、工具总数、审批和正文不变。字段遵循相同 global/profile/bot 显式覆盖顺序，`card-config` 展示其来源，预设不会自动启用它们。

V4.6.6 的总条目窗口优先保留正在运行的工具、前一步及对应思考段，再保留失败记录；正文与过程使用相同调用编号。超过总容量仍折叠条目并显示数量，不承诺无限历史。整轮结束后没有工具终态的条目显示已中断，渲染不篡改原始历史。
