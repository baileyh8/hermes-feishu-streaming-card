# 2026-09-15 Issues 与 PR 集中处理

基线：`origin/main` 的 `f6be2d1`（v4.4.5）。GitHub 盘点为 27 个 open issue、5 个 open PR。
修复位于候选分支，尚未发布，也未据此关闭现场问题。表中“保留”表示仍缺对应证据或需要独立功能设计。

## PR 审查

| PR | 结论与处理 |
| --- | --- |
| [#291](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/291) | 吸收安装兼容实现并保留 tidytorch 作者；补充跨两种形态的唯一性检查，避免同时出现新旧调用仍被接受。 |
| [#292](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/292) | 吸收并保留原作者的回归测试；实现与 #291 合并为一个共享 matcher。未引入临时源码改写脚本。 |
| [#297](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/297) | 不采用原补丁。仅接受新形态会使旧 split-ledger fixture 失败；远端 CI 的 5 个独立失败均在这组测试。新增分支还扩大了未经充分验证的契约范围。其安装问题由双形态 matcher 覆盖。 |
| [#299](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/299)、[#300](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/300) | 同时采用 CodeQL 4.38.0 的 init/analyze。单独升级造成运行配置版本混用；同步 SHA 测试，并为后续更新加入 Dependabot group。官方 annotated tag 解引用为 `b96794f015dfd88f77b49b1c93e0fa7110f94c63`。 |

## Issues 逐项结论

| Issue | 处理与剩余边界 |
| --- | --- |
| #301 最大迭代数误报完成 | 修复 legacy/native 对明确预算退出的状态解释，native completed=false 也发送失败终态。原截图未能读取，不能宣称完整复现报告者环境。 |
| #298 终局被吞 | 复现接受 ACK 后 PATCH 耗尽但无补发；补发完整终局卡，固定原 topic 与稳定 UUID。真实 hook → sidecar 回归断言无原生重复，且正文尾部可见。飞书不可用时补发也可能失败，health 保留结果。 |
| #296、#294、#288 安装拒绝 | 吸收 #291/#292 并收紧歧义拒绝；固定最新上游 0.21.3 源码进行 install/repeat/doctor/restore 回归。各报告者的实际运行环境仍需升级后验证。 |
| #295 授权过期后等待恢复 | 保留为功能设计。当前过期会拒绝并使旧 token 失效；持久暂停、恢复原执行、重新核验操作范围及重启后的安全恢复需要 Hermes 执行层支持，不能靠延长超时或接受旧按钮实现。 |
| #293 CardKit streaming_mode | 保留为独立功能。现有 IM 全卡 PATCH 确实不同于 CardKit streaming entity；需新增 card_id 生命周期、文本元素更新、终局关闭、大小/限流处理与双端视觉验收。 |
| #290 文字与卡片双发 | 修复 split-ledger hook 识别缺口；终局恢复由 sidecar 独占，不增加网关第二发送方。原截图未读取，缺完整事件顺序，不能据相关修复认定原场景已解决。 |
| #289 报错后原内容难以查看 | 已有 answer.delta 在 message.failed 后仍直接显示，错误追加在正文，晚到成功不能覆盖失败。 |
| #285 失败显示完成 | v4.4.5 已修复显式 failed/interrupted/partial/completed=false；本轮补齐退出原因与 native incomplete 分支。 |
| #283 长时间空卡 | #298 补发覆盖“终局更新失败”子场景；上游不再输出事件或未结束执行仍待现场证据，保留。 |
| #282 首次点击无反应 | 纳入已有未发布的拒绝/不确定回调提示。报告者 9 月 14 日明确主要使用手机且可能先展开；本轮未运行真实手机飞书，保留。 |
| #281 split-ledger 拆分不支持 | v4.4.5 已修复，报告者已确认安装和卡片正常；可按已发布解决处理。后续新增 kwarg 属于 #288。 |
| #280 选择后回看不知道问了什么 | legacy/v2 终态正文保留原问题、选项、选择结果；不再依赖 hover；按钮和凭据移除。 |
| #279 @ 位置 | 保留。原图未读取，缺明确目标布局。当前 completion_notify 与正文提及是两个显示路径，不能只凭标题改布局。 |
| #278 压缩后跑到主会话 | 保留。已有 compaction/topic 回归不等于报告现场；需原消息/话题关联与通知来源。 |
| #277 容器用户权限 | 已有同用户、共享认证状态目录的 Docker/s6 文档与门禁；原图未读取，缺 UID/目录权限证据，保留。 |
| #276 manual_review_required 下一步不清晰 | status 直接打印带当前 config/env/Hermes 路径的只读 doctor 命令，保留审查边界；补充 README 排障入口。 |
| #275 /sethome 跑到主会话 | 保留。命令上下文已统一路由，但未获得报告者具体元数据及真实 topic 复现。 |
| #274 承诺继续却停止 | 显式未完成状态已加强；是否继续工具由 Hermes/provider 决定，不从正文承诺推断或自动重跑。保留执行停止原因调查。 |
| #266 multiplex | 报告者称本机已解决、Docker 在验证；已有多 profile 回归，保留 Docker 现场验收。 |
| #265 decomposed integrity migrate-safe | v4.4.2 已加入逐受管文件验证与迁移；本轮固定源码全量回归复核。缺报告者升级后状态，不删除或绕过 fence。 |
| #264 无 Git 与启动顺序 | 文档已有 source-only ownership 与先装 hook、后启 Gateway 的顺序；本轮最新源码安装回归特意使用无 .git 副本。保留实际容器验收。 |
| #263 容器安装失败 | source-only 证据支持已在 v4.4.2 实现；新锚点兼容本轮补充。缺原镜像具体来源与源码，保留。 |
| #262 @ 失败 | 文档已区分昵称文本与合法 open ID 的真实提及；已有标签保真回归。原图未读取，不猜测用户身份，保留。 |
| #258 手机授权阅读 | 已有完整命令与编号短按钮；本轮补足完成态回看。手机自动折叠行为仍需真实客户端验收。 |
| #73 历史 v0.17 无卡片 | 当前缺首事件建卡与 hook 有回归，但历史环境缺新版诊断，保留。 |

## 验证口径

- 安装修复前：复现 2 个失败；补强前：歧义调用、预算退出、报错正文保留共复现 5 个失败。
- 终局补发前：3 个回归失败；native incomplete 前：3 个回归失败；均保存本地日志。
- 验证正常、失败、重复终局、消息数量、正文尾部、原 topic 与恢复失败的可观测结果；没有只用 HTTP 200 或 marker 作为通过依据。
- 最新上游源码固定为 `2179a279ae04bfadf8efbc49a01ca0abfb738000`；保留旧 stable/production 两组 SHA256 证据。固定 Hybrid fixture 仍为 `3c27eb6234bf91b8ceee9e9071591b31e9b148cb`。
- 本轮没有升级或重启生产 Hermes，没有真实飞书发送，没有合并/发版。GitHub 附图的网络读取及浏览器读取失败，涉及纯截图的问题按证据不足保留。

合并前检查候选 CI；发布时按 release-playbook 更新版本与中英文贡献者记录。整合 #291/#292 时保留代码作者，终局补发应感谢 #298 的现场分析与证据，状态和交互改进应感谢 @mouyong 的持续反馈。
