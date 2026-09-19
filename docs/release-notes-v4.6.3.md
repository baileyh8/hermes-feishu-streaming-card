# V4.6.3：实时思考正文开关与工具状态修复

新增 `card.stream_thinking_to_body`，默认 `true` 保持旧行为。设置 `false` 后，答案尚未输出时正文保留等待状态或工具活动，实时思考改为受 `max_reasoning_chars` 约束的折叠预览，仍由 `show_reasoning` 控制。预览不写入持久时间线，`reasoning_format: code` 的历史片段格式不变；完成/失败内容保留、审批和原始答案不变。默认预算下超长原始思考不再单独撑爆正文，但手动放大的面板预算或超长答案仍可能触发整卡容量回退。修改配置后重启 sidecar。

适配 PR #331 中独立的工具排序、耗时和中断用量修复：按调用 ordinal 排序，每个运行工具保留其前一步；结束工具保留实际或推导耗时及私有展示检查点；排队接续打断旧轮时传递模型、耗时、tokens/context，缺失或非法数值不抹掉已有测量。生成的 hook block 验证实际执行顺序与来源身份。

`hide_completed_tool_activity` 继续默认 `false`；显式 `true` 仍覆盖 completed/failed。未采用 PR #331 的默认值反转、失败态例外、时间线顺序改动或尚不明确的工具ID重用计数变更。

## 贡献与未纳入范围

感谢 [leavrcn](https://github.com/leavrcn) 提出 [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333) 并提供长思考复现与配置建议；感谢 [mouyong](https://github.com/mouyong) 在 [PR #331](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/331) 提供工具显示、耗时和中断用量实现，适配部分保留 Co-authored-by。

#331 的通知撤回仍独立审查：`fc2a6a0` 的发卡/完成通知调用未传入 bot/profile，默认身份清理仍会选错通知，未纳入本版，也未将整PR标为合并。

## 验证边界

新增功能与缺陷先保留基线失败证据，再验证正文/面板分离、长思考容量、失败保留、审批、恢复、HTTP配置布尔值与单卡终局。工具覆盖并行前驱、耗时、非法数值、旧检查点及生成补丁实际执行。发布必须通过全量、精确合并CI、三平台资产校验和公开tag普通安装。模拟Feishu客户端不等于移动端真机验收；本版不修改AMD路由或默认模型。
