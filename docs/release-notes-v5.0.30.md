# v5.0.30：修复飞书正文误判、跨轮续卡与 PM 安装恢复

[English](release-notes-v5.0.30.en.md)

## 普通回答与系统通知

- 普通回答或编辑即使逐字引用后台任务、自我改进回顾等通知模板，也会沿原来的飞书发送或编辑路径投递。`card.suppressed_notice_kinds` 只处理经来源验证的系统通知。已核验的场景覆盖 Hermes v0.23.3 的后台任务和英、简中、繁中回顾通知，以及已识别的旧版通知入口。 [PR #550](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/550) · [通知来源与抑制规则](wiki/notice-ownership.md)
- 平台通知明确未送达时，Hermes 沿原路由补发一次正文。结果未知时，Hermes 只尝试发送一次固定提示，降低正文重复的概率。 [通知投递边界](wiki/notice-ownership.md)

## 话题续卡与重启后的正文

- 同一话题连续两轮长答各用本轮的主卡和续卡。旧版事件缺少 `turn_id` 时，HFC 根据可核实的请求人、群和话题匹配原卡。复用消息 ID 时，旧版完成钩子核对 `generation` 和事件时间来识别新一轮。时序无法确定时，正文仍可由 Hermes 原生投递。
- 最终正文确认后，已启用的完成提醒发回原话题。旧交互卡的更新可稍后完成，即使其 PATCH 延迟也能先送达正文和提醒。重放时，已保存为送达的提醒不会重发。
- 飞书已接受占位卡、但展示检查点写入失败时，重启后如果找不到原卡，HFC 可用终局事件自己的 UUID 建卡，即使该事件没有新回合标记。相同事件重试复用这个 UUID，原占位卡可能仍然可见。
- 恢复未确认的旧建卡请求时，只有在飞书支持 UUID 去重且仍在去重期限内，才用原 UUID 重试。超期后保留 `unknown`。CardKit 建卡结果不明时，正文可交还 Hermes，飞书已接受的占位卡可能仍在。
- 带新回合标记或可核实 `generation` 的旧版终局，只有飞书确认整份正文送达后才保留收据。每个投递位置最多保留 16 条，各自从确认时起保留一小时。换卡或重启后，按原事件精确重放可取回送达回执。完成钩子的同事件重试沿用首发的耗时和输出速率。
- 升级时，如果旧版已发建卡请求、回执丢失且缺少可用检查点，跨版本重投可能多出一张卡。旧版已送出但未确认的分段也可能重复。早期检查点缺少终局指纹时，Hermes 的原生回退可能产生重复正文。升级前请等当前回复及分段送完。 [卡片恢复与重试](wiki/card-restart-recovery.md) · [V5 升级指南](upgrade-v4-to-v5.md)

## macOS/Linux 安装与兼容范围

- `install.sh` 为受支持的 Hermes PM 布局复用同一 Hermes HOME 下的独立管理环境。旧解释器退役时，可用可运行的兼容 Python 重建。已有管理环境时，pip 中断或新包来源校验失败会恢复原环境。新 CLI 校验通过后可用于诊断后续的 PM 切代拒绝。保存安装器打印的 `status` 命令，它包含所需的绝对路径，换目录也可运行。 [安装说明](../README-install.md) · [PM 安装与恢复](wiki/pm-recovery.md)
- 兼容性仍以源码、能力和安装归属检查为准。通知回归覆盖固定的 Hermes v0.23.3 源码 `caafe5389ae585ef77387a962aee05b1bcf17029`。安装验证覆盖 macOS 私有 HOME 的实际 pip 中断、包来源校验失败，以及 Hermes v0.17.0 旧布局使用 checkout 自带 Python 的路径。未知或漂移的源码继续拒绝。 [PR #550 验收](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/550)

## CI 与发行证据

- PR #549 用实际 JUnit 用例耗时重新平衡上游分片，保留原有测试范围。失败或取消的 job 若能上传诊断，会按 attempt 分开保存，便于重跑后追查。 [PR #549](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/549)
- PR #550 的产品提交 `40017d4814b5bc1ee184b8fbfde93c322f639ce0` 重跑后通过完整 CI 的 68 项检查。独立验收使用普通 wheel 完成 16 个签名 HTTP 回环场景，飞书投递由替身模拟。该产品 wheel 沿用开发基线版本号 5.0.29。 [CI](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/45235020184) · [独立验收记录](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/550)
- v5.0.30 发行提交、tag、安装包及公开安装分别核验。本版使用隔离 wheel 检查真实飞书测试群的话题投递与桌面显示，结果随 GitHub Release 记录。移动端及实际 Gateway/model 回合未验证。 [发布准备](release-readiness.md) · [GitHub Releases](https://github.com/baileyh8/hermes-feishu-streaming-card/releases)

## 贡献者

baileyh8 提交飞书投递与安装恢复修复，以及 CI 分片校准。Lite-G 在 #500 提出了原有的按会话抑制系统通知需求。 [PR #550](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/550) · [PR #549](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/549) · [#500](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/500)
