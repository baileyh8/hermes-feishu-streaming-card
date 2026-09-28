# V4.6.11：长思考保持可读，后台等待有上下文

## V4.6.11：长思考与后台任务

- 希望正文持续显示最新思考但不刷满整卡时，设置 `card.thinking_body_tail_chars: 2400`。仅在 `stream_thinking_to_body: true`、未有答案且本轮未结束时生效。默认 `0` 保留旧行为；整数范围为 0–1000000。窗口按字符保留尾部，并在整卡预算不足时尝试进一步缩小；思考时间线、工具区和完整答案不裁剪。其他区域仍超限时沿用原有原生投递兜底。修改后重启 sidecar。
- `terminal` 返回后台进程标识后，或 `process` / `process_manage` 返回明确命令与输出后，同一轮后续 `wait` 会展示已观测命令、最近回报及状态。进程关联最多保留 32 项，限制在当前卡片会话内存中，重启不会恢复该关联。
- “最近输出”是工具上次回报，不是实时日志订阅；进程没有新事件时不推测百分比。`timeout` 显示为“请求等待上限（非预计完成时间）”，Hermes 还可能按自身配置缩短实际等待。没有观测到命令时明确显示未知，不跨轮或跨 profile 查询其他进程。

CodeQL init/analyze 升级至 v4.38.2，核验完整 SHA 与 Node 24 元数据，同步测试白名单（PR #363）。

贡献：[leavrcn](https://github.com/leavrcn)（#362，复现与补丁方案）、[mouyong](https://github.com/mouyong)（#361，现场证据）、[Dependabot](https://github.com/apps/dependabot)（PR #363）。保留全部历史贡献和真实代码署名。

Hermes 最新稳定版 v2026.9.24 / 0.21.5 仍为稳定验证目标。最终 CI、包来源、公开安装与平台验收结果以 GitHub Release 为准。生产升级、真实 Gateway/模型任务及手机验收单独登记。

Hermes main source pin: `5912ed81ed945a784f67ca13c1096e2c122cd307` (2026-09-28).
