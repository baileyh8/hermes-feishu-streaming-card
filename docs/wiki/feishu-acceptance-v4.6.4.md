# V4.6.4 真实验收记录与清单

本页分别记录自动化、普通安装、平台 API 投递、真实桌面点击、手机体验及正式发布；未运行项不算通过，历史版本实测不代替本轮证据。

## 2026-09-20 已有证据

- 代码提交 `87bf424af6a77a579225124ed137e2a784f537b3` 的完整回归 **4049 passed, 18 skipped**、diff 检查通过；PR #336 的 14 项检查通过。跳过项包含平台限制、未提供的额外上游 fixture 与本地未装 SDK/pwsh；对应 SDK、Windows、PowerShell 和上游矩阵由 CI 单独覆盖。
- 普通 wheel 的 `python -I` 导入来自 `site-packages`，包与 distribution 均为 4.6.4；59 项 SDK/CLI/HTTP smoke 通过。固定 Git fixture 校验来源、提交和工作区；干净的当前 Hermes 源码副本安装、重复安装、移除及精确还原通过。
- 既有授权应用与测试会话上的隔离 sidecar 收到实际平台确认：6/6 事件、3/3 新建、6/6 更新、0 发送/更新失败。选择由协议事件模拟，未经过真人点击和完整 Gateway 链路。初次临时脚本过早退出的终局更新，已通过检查点恢复写回原 owner，未新增卡片。
- 实际客户端界面工具不可用，API 读取返回兼容提示，无法据此确认卡片视觉；桌面/手机首次点击与视觉均未运行。
- 原生产实例受管源码与安装 manifest 漂移，安全安装器拒绝覆盖；保留本地改动、原服务和 HFC 4.6.2。升级原实例不计入本版完成项。
- 精确合并、tag、资产和公开安装的最终证据随 [Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.6.4) 登记。

## 目标与证据

复用已经授权的 Hermes/HFC 实例和测试会话。先核对 hostname、配置来源、Python/包来源、Hermes/HFC 版本、Gateway/sidecar PID、profile/bot 和真实会话；多个实例或会话不能混为一项。检查当前工作是否允许重启，使用既有安全安装/维护流程，保留用户配置和本地定制。

公开结果只记录脱敏身份摘要、版本/提交、设备、计数、状态与结论。真实 chat/user/message ID 仅在必要的受控本地目标记录中保留；凭据和 callback token 不写入验收产物，原始聊天正文与私人截图不进入仓库。`lark-cli` 按[CLI 手册](feishu-cli-playbook.md)使用；CLI 应用身份不能代替实际 Hermes 应用身份。

## 本轮场景

| 场景 | 通过条件 | 当前记录 |
| --- | --- | --- |
| 冷启动首次 clarify | 重启候选 Gateway 后不先发 slash/model/resume，真实用户触发首轮 clarify；首次点击到达原等待方且只执行一次 | 真实客户端未运行 |
| 冷启动首次 approval | 独立冷启动，真实审批首个按钮可用；允许/拒绝含义正确，重复点击和旧按钮不再次执行 | 真实客户端未运行 |
| 顺序续答 | 先有回答，再 clarify/approval，再有真实输出；续答出现在选择之后，问题、范围、选择仍可回看 | 真实客户端未运行 |
| 连续题目与输入 | 单选、多选、自定义答案和两道连续题不串值；中间无输出时不夹空卡，输入中无无关 PATCH 清空控件 | 真实客户端未运行 |
| 取消、超时和重审 | 拒绝不写成正在执行；过期入口失效；只有仍存活且支持暂停的原等待方才能重审，旧 token 无效 | 真实客户端未运行 |
| 失败与历史 | 选择前后文字、工具历史、附件及整轮统计不丢；失败保留已输出内容，不出现虚假成功 | 真实客户端未运行 |
| 续答 create 失败/不确定 | 不切到未确认 owner，不按 delta 重发；原卡继续保留内容并解释去向 | 真实客户端未运行 |
| 首张仅为 legacy 交互卡 | 后续失败、容量回退或展示重启不向其 PATCH schema 2.0；问题/决定回执保留，旧审批不可复活 | 真实客户端未运行 |
| 路由与通知 | 私聊、群聊、topic、首回复建 thread 位置正确；重启提示只按已证明的同 profile/bot/chat/thread 清理，来源不明 home 提示保留 | 真实客户端未运行 |
| 阅读预设与回退 | 缺省保持旧行为；focused/detailed 与同层显式 true/false 一致，失败信息可见；恢复原 YAML 后行为恢复 | 真实客户端未运行 |
| 桌面与手机 | 实际点击命中按钮、回执和新结果可顺序阅读，长问题/代码/表格不遮挡操作；未测设备明确标未运行 | 真实客户端未运行 |

受控故障注入和受控协议卡只能证明相应投递/显示分支，不能冒充真实 provider 故障、完整上游授权执行或冷启动 Gateway 链路。不要在真实实例制造不可逆操作来测试审批。

## 发布前独立检查

- `python tools/preflight.py --check-only`，再按模块运行 focused；缺固定 fixture、依赖或错误解释器分别修正，不以扩大 skip 消除失败。
- 完整 pytest 与 `git diff --check`；固定 Hermes 源码的 install/repeat/remove/restore、生成 hook 实际执行及真实 SDK matrix。
- 精确合并 SHA 的 CI、annotated tag、三平台资产/checksum；公开 tag 普通安装的包版本、`site-packages` 来源与源码一致性。
- 审核本页仍待记录的项目和 issue 原始触发条件。只有相应真实缺陷得到证据，才对其宣称修复；#331 按子项记录，不能把整 PR 当已合并。

范围和契约见[版本说明](../release-notes-v4.6.4.md)、[交互续答](interaction-continuation.md)、[阅读预设](reading-presets.md)及[实施状态](../superpowers/plans/2026-09-20-v4.6.x-experience.md)。
