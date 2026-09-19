# V4.6.2：维护证明与终态工具区

原生插件与 Gateway hook 共存时，native lease 只提供自身活动计数，旧逻辑却要求它独立提供 Gateway drain/HOME 证明，导致正常 Gateway 的共享心跳一直保守显示正在 drain、HOME 未验证。现在原生观察者显式依赖同进程 Gateway 的准入证明，仍保留自身活动计数。缺少 Gateway、未知 owner、计数不完整、HOME 不符和并发 owner 变化仍拒绝自动停服，不通过伪造空闲或常量 HOME 校验解除门禁。

新增 `card.hide_completed_tool_activity`，默认 `false` 保持升级前的显示。设置为 `true` 后，completed/failed 卡隐藏正文工具行和旧摘要回退；运行态、审批、答案、折叠记录和 footer 工具计数不变。配置后重启 sidecar 生效。

## 贡献与范围

感谢 [jackwude](https://github.com/jackwude) 的 [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) 需求，以及对 4.6.1 [#329](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/329) 的复现贡献。感谢 [mouyong](https://github.com/mouyong) 在 [#331](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/331) 提出的 `hide_completed_tool_activity` 配置；本版适配此部分，采用默认 false 与 completed/failed 边界，保留代码归属。该 PR 的其他通知、排序、耗时和中断改动未整包合并。

## 验证边界

真实插件 bootstrap 复现维护心跳错误；回归覆盖 Gateway 消失、native 活跃/未知计数与 HOME 不符。真实本机 HTTP 覆盖新配置、完成/失败、重复终局、同卡和正文保留。全量、精确合并 CI、资产与公开安装均为发布门禁；最终证据在 Release 补充。模拟 Feishu 客户端不等于移动端真机验收。本版不更换模型或供应商凭据。
