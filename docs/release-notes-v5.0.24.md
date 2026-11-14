# v5.0.24：PM 恢复顺序与可选审批规则提示

## 变更范围

- **#524 恢复边界**：`repair --hermes-home` 在修源码前验证 observer 的 HOME 归属。PM 重建与 hook 重置同时发生时，先使用已有的受管源码 `repair`，再预览当前 owned runtime 的 `integrity rebind-legacy`；review fence 仍走精确旧 target 与 CAS 校验。补齐安装拒绝后的指导和 [PM 恢复说明](wiki/pm-recovery.md)，不删除 marker、fence、service 记录，不跳过 runtime/member/wheel 来源证明，也不自动重启服务。
- **#525 原生规则提示**：新增 `card.approval_rule_hints`，默认 `false`。启用后，把 Hermes 原生 `pattern_keys`（缺失时使用单个 `pattern_key`）中的有限已知类别转为中文，采用 A 行内方案，放在完整命令下方、原审批控件前。只说明原生命中的类别，不代表完整影响判断；未知 key 不原样回显。HFC 不解析命令、不调用模型推断影响，也不建议放行。提示不持久化；最终组合卡超预算时先省略提示，原命令和授权范围保持完整，原按钮与 resolver 保持不变。

## #526 的当前证据

报告为 Windows 上 HFC v5.0.22 / Hermes v0.23.1 的真实 Gateway 私聊无卡。目前正文缺少所引错误的完整日志与实际安装策略。精确 v0.23.1 源码/拓扑通过只能证明能力检测，不能证明真实首卡已投递。原环境故障尚未独立复现，不能把本候选写成已解决该报告。

## 验收状态

这是候选源码说明，尚未公开发布。精确冻结提交的完整 CI、普通 wheel 与真实子进程、PM 安装恢复、真实 Feishu API、桌面及移动客户端验收在完成后分别记录；未完成项保留为未验证。源码测试、平台 API、客户端显示与实际 Gateway/model 执行是不同证据，不能互相替代。

## 贡献记录

感谢 [lvanwei180-lvan](https://github.com/lvanwei180-lvan) 提供 [#524](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/524) 的 PM 重建现场，[jackwude](https://github.com/jackwude) 提出 [#525](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/525) 的审批提示建议，[nmtlcxb](https://github.com/nmtlcxb) 提供 [#526](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/526) 的 Windows 回合反馈。报告、建议与代码作者分开记录，全部历史贡献和发布记录保留。
