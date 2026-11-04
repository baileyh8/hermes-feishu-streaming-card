# 阅读预设与有效配置

阅读预设是 V4.6.x 中的可选配置组合。升级、全新安装和没有此字段的 YAML 都保持已有默认；不会自动选择新的阅读方式。它回应 [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) 的完成工具降噪和 [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333) 的思考正文分离需求。

| `card.reading_preset` | 实时思考 | 思考/工具过程 | 正常完成的正文工具行 | 失败的正文工具行 |
| --- | --- | --- | --- | --- |
| 缺省 / `classic` | 保持正文回退 | 折叠 | 保留 | 保留 |
| `focused` | 放入有界过程面板，正文等待答案 | 折叠 | 隐藏 | 保留最后活动 |
| `detailed` | 放入有界过程面板，正文等待答案 | 展开 | 保留 | 保留 |
| `task`（开发分支 B 方向） | 放入有界过程面板，正文等待答案 | 默认展开，标题为“执行过程” | 保留过程中的成功工具，避免正文重复 | 保留最后活动 |

开发分支按已确认的 B 方向将 `task` 过程默认展开，并保留完成历史；旧版默认折叠，升级后需要保持折叠时显式设置 `timeline_expanded: false`。`task` 同时调整状态标题、字号、交互提示和统计密度，详见[任务卡片与离线预览](../task-cards.md)。现有三个预设的展示默认保持不变。

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

`live_tool_activity: full|summary|hidden` 独立于阅读预设，所有预设均不设置它，默认 `full`。它控制运行中的工具展示；`hide_completed_tool_activity` 控制终态，两者可组合。`summary` 仅显示运行工具的状态、步骤和用时；`hidden` 连同 live 标题/页脚工具计数一起隐藏。终态和 pending approval 不变，模型正文不做过滤。

`card.chat_overrides` 可在 global/profile/bot 层配置，嵌套按字段合并；保留 base → profile/event card → bot 的基线顺序（非空 event `data.card` 替代 profile 层）；类型 `group`/`p2p` 覆盖在该结果之上，`chats.<exact chat ID>` 最后生效。内层只允许上述两个工具开关、`show_reasoning` 和 `stream_thinking_to_body`。缺失/未知 chat_type 不应用类型项，仍检查精确 chat；会话创建时固定。重启后仅新回合使用 YAML 修改。例子见[用户指南](../user-guide.md#运行中工具活动与聊天覆盖)。

`card-config --config config.yaml --chat-type group --chat-id oc_example --running` 核对叠加后的新回合默认值，来源标签使用 `chats[selected]`，不回显 chat ID；未选聊天时仅提示类型和 chat 数量。`--minimal-yaml` 与聊天选择器互斥。旧侧车缺少新字段时保持未核验，不猜测已生效。

```bash
hermes-feishu-card card-config --config ~/.hermes_feishu_card/config.yaml
hermes-feishu-card card-config --config /path/to/config.yaml --profile-id work --bot-id support --json
```

输出阅读字段的有效值及来源（default、global、profile 或 bot 的预设/显式字段）。`terminal_tool_activity` 是解释结果，不是新 YAML 开关：`visible` 表示终态保留，`failed_only` 表示仅失败保留，`unsuccessful_only` 表示两种终态均仅隐藏成功工具。多个 profile 且没有 default 时要求指定 `--profile-id`，不会猜测。

此命令不启动或重启服务，不修改配置，不请求 Feishu，不输出凭据、路由标识、配置路径或自定义标题。默认说明所选 YAML 的下一次加载结果。

开发分支新增可选 `--running`：通过本机受管侧车的私有控制接口核对已加载值，并逐项显示与下次加载值的差异。命令要求私有 pidfile、进程身份、配置文件路径和所选 profile/bot 均匹配；旧版侧车、未托管进程或无法核对的范围显示 `unavailable`，不把磁盘值冒充运行值。

```bash
hermes-feishu-card card-config --config /path/to/config.yaml --running --json
```

`verified` 仅证明当前侧车为新回合使用的已加载阅读默认值，不代表已经接受的卡片，也不包含逐事件覆盖。`auto` 与运行中解析出的 `callback` 按相同效果比较。修改 YAML 后仍需按原流程重启侧车；该命令不会替用户重启。

## 精简阅读配置

开发分支的 `--minimal-yaml` 输出所选层需要保留的阅读字段：

```bash
hermes-feishu-card card-config --config /path/to/config.yaml --minimal-yaml
hermes-feishu-card card-config --config /path/to/config.yaml --profile-id work --bot-id support --minimal-yaml
```

输出注释说明目标是 global、profile 还是 bot；不会输出真实范围名称、凭据、路由或标题。将片段用于该层的 `card`：替换其中的阅读字段，保留其他字段和上层配置。它不是完整配置文件，也不是可以追加到任意层的通用片段。

每次删除一个冗余字段都会用原配置合并器重新解释，确保与当前有效阅读值、预览一致。抵消上层设置的 `false`、字号覆盖，以及覆盖预设的旧开关都会按需保留。命令不写文件或重启；`--preview-dir` 可同时生成这组设置的离线示例。需要核对进程已加载值时，单独运行 `--running`。

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

## V4.6.11：长思考与后台任务

- 希望正文持续显示最新思考但不刷满整卡时，设置 `card.thinking_body_tail_chars: 2400`。仅在 `stream_thinking_to_body: true`、未有答案且本轮未结束时生效。默认 `0` 保留旧行为；整数范围为 0–1000000。窗口按字符保留尾部，并在整卡预算不足时尝试进一步缩小；思考时间线、工具区和完整答案不裁剪。其他区域仍超限时沿用原有原生投递兜底。修改后重启 sidecar。
- `terminal` 返回后台进程标识后，或 `process` / `process_manage` 返回明确命令与输出后，同一轮后续 `wait` 会展示已观测命令、最近回报及状态。进程关联最多保留 32 项，限制在当前卡片会话内存中，重启不会恢复该关联。
- “最近输出”是工具上次回报，不是实时日志订阅；进程没有新事件时不推测百分比。`timeout` 显示为“请求等待上限（非预计完成时间）”，Hermes 还可能按自身配置缩短实际等待。没有观测到命令时明确显示未知，不跨轮或跨 profile 查询其他进程。
