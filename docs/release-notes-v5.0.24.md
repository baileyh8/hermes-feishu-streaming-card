# v5.0.24：PM 恢复顺序与可选审批规则提示

## 变更范围

- **#524 恢复边界**：`repair --hermes-home` 在修源码前验证 observer 的 HOME 归属。PM 重建与 hook 重置同时发生时，先使用已有的受管源码 `repair`，再预览当前 owned runtime 的 `integrity rebind-legacy`；review fence 仍走精确旧 target 与 CAS 校验。补齐安装拒绝后的指导和 [PM 恢复说明](wiki/pm-recovery.md)，不删除 marker、fence、service 记录，不跳过 runtime/member/wheel 来源证明，也不自动重启服务。
- **#525 原生规则提示**：新增 `card.approval_rule_hints`，默认 `false`。启用后，把 Hermes 原生 `pattern_keys`（缺失时使用单个 `pattern_key`）中的有限已知类别转为中文，采用 A 行内方案，放在完整命令下方、原审批控件前。只说明原生命中的类别，不代表完整影响判断；未知 key 不原样回显。HFC 不解析命令、不调用模型推断影响，也不建议放行。提示不持久化；最终组合卡超预算时先省略提示，原命令和授权范围保持完整，原按钮与 resolver 保持不变。

## #526 的当前证据

报告为 Windows 上 HFC v5.0.22 / Hermes v0.23.1 的真实 Gateway 私聊无卡。目前正文缺少所引错误的完整日志与实际安装策略。精确 v0.23.1 源码/拓扑通过只能证明能力检测，不能证明真实首卡已投递。原环境故障尚未独立复现，不能把本候选写成已解决该报告。

## 验收范围与证据

- 普通 wheel 的 165 个内容文件与已审 Git 版本一致；非 editable 子进程正确加载 5.0.24。
- 真实 Feishu API 投递 8 张合成审批卡，macOS 飞书桌面检查默认关闭、已知规则、未知 key 不回显、完整长命令与 SmartDENY 两选项。测试未执行命令或提交授权；8 张消息最终均核对归属后撤回，正式配置保持原样。
- [候选完整 CI](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/43556419366) 与 [CodeQL](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/43556419380) 已通过。实际测试的 PR 合并快照与已审候选 tree 一致；PM v0.22.4/v0.22.5 的 repair→rebind→reinstall→restore 用例在普通上游与 uv 0.13.5 两路均实际通过，无跳过。
- Bailey 明确限定本轮发布范围：Android/iOS 视觉与展开、真实授权点击、实际 Gateway/model 审批回合记为**未验证**。#526 原环境尚未独立复现，不宣称修复。

最终 main/tag 完整 CI、公开普通安装及发行资产均通过后发布，证据汇总见 [v5.0.24 GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.24)。源码测试、平台 API、客户端显示与 Gateway/model 执行分别记录。

## 贡献记录

感谢 [lvanwei180-lvan](https://github.com/lvanwei180-lvan) 提供 [#524](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/524) 的 PM 重建现场，[jackwude](https://github.com/jackwude) 提出 [#525](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/525) 的审批提示建议，[nmtlcxb](https://github.com/nmtlcxb) 提供 [#526](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/526) 的 Windows 回合反馈。报告、建议与代码作者分开记录，全部历史贡献和发布记录保留。
