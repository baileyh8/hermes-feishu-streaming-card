# V4.6.4 候选：首次交互、顺序续答与可选阅读方式

[中文](release-notes-v4.6.4.md) | [English](release-notes-v4.6.4.en.md)

本说明记录 V4.6.x 的增量候选范围。代码和自动化检查不代表正式发布、生产升级或手机/桌面真实验收已经完成；结果须按[本轮验收清单](wiki/feishu-acceptance-v4.6.4.md)单独登记。

## 首次按钮不用预热

启用的 Feishu turn 和首次交互请求会尽早接好回调，不再要求先显示 slash 确认、model picker 或 resume picker。对于只有 `ctx` 的真实 Hermes 生成闭包，通过原 TurnRunner 的绑定回调恢复 Gateway 归属；只有 source、profile 和同一 live adapter 都匹配才接线。重连时只在 WebSocket loop 更新现有 processor 的 callback，不替换已连接的 SDK dispatcher。未知结构或断连继续 fail-open。

## 选择以后往下读

clarify 与 approval 完成选择后先记录显示分段边界；有实际后续文字、工具/子任务活动或终局内容时，才在同一 bot/profile/chat/topic 新建 schema 2.0 续答卡。连续下一题不会夹一张空续答卡。交互卡保留问题、选项、操作范围和选择结果；整轮答案、工具、附件、时间线与统计不会因分段清空。

已有 schema 2.0 卡不会把 legacy 交互消息直接升为流式 owner。新续答卡确认送达后才切换当前显示位置；失败或结果不明时保留原 owner 和内容，不按每个 delta 盲目重发。若本轮第一张卡就是唯一 legacy 交互卡，失败回退、终局与展示检查点恢复继续用 legacy 方言及无 token 的静态回执，不向它发送 schema 2.0 PATCH，也不恢复旧审批。

增量续答显示选择后的新内容；上游给出的完整终局快照会标为“本轮完整结果”并完整保留，不猜测删除旧前缀。详情见[交互续答契约](wiki/interaction-continuation.md)。

## 显示预设自愿采用

新增 `card.reading_preset: classic|focused|detailed` 和只读 `hermes-feishu-card card-config`。**缺省、全新安装及升级均保持旧默认**；同层显式开关优先，配置按全局 → profile → bot 合并。

- `focused` 将实时思考放入有界面板，正常完成隐藏正文工具行，失败仍保留活动。
- `detailed` 展开过程，继续遵守既有条目和容量限制。
- 显式 `hide_completed_tool_activity: true` 仍在 completed/failed 都隐藏；显式 `false` 仍在两种终态保留，优先于预设。

`card-config` 只解释所选配置的有效值和来源，不证明运行进程已重新加载，也不修改文件或服务。采用或回退后需重启 sidecar。见[阅读预设](wiki/reading-presets.md)。

## 有来源证明才收起通知

只登记已识别、归属明确的临时重启通知。同一 profile/bot/chat/thread 后续成功投递或更新才清理已登记快照；空 thread 不是通配符。失败删除保留所有权，以后有合格投递再重试；容量、重复和迟到删除都有边界。答案、交互回执、失败说明不进入这一清理路径。原生 home 通知缺少明确来源时保留，不因同一 bot/profile 的其他聊天活动推断可删除。

## 提交前检查

`python tools/preflight.py --check-only` 默认只读核对 checkout、解释器/包来源和固定 fixture。显式 `--suite focused|full` 才运行真实 pytest 与所选 diff base 的检查；测试用私有 state、Hermes home 和隔离的 operations target，缺 fixture 不标通过，退出码如实保留。可分享 JSON 不含原始日志、凭据或绝对私有路径。见[测试说明](testing.md)。

## 贡献与边界

感谢 [sthnow](https://github.com/sthnow) 在 [#335](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/335) 提供复现与交互排序证据，及其附补丁的作者 **babypanda**；eager-hook 适配保留 `Co-authored-by`。感谢 [mouyong](https://github.com/mouyong) 在 [PR #331](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/331) 提供续答和通知生命周期的方案、代码，以及 [#330](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/330) 的验证流程需求。阅读预设继续回应 [jackwude](https://github.com/jackwude) 的 [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) 和 [leavrcn](https://github.com/leavrcn) 的 [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333)。全部历史署名继续保留在双语 README。

#331 按子项吸收，不整包合并，不反转旧默认，不把不明 home 路由当成清理授权。#335 的本轮真实客户端复验待记录；#282 等未证明同根因的历史现场问题不能因相邻修复宣称解决。AMD/9Router、默认模型和 Hermes 核心升级不在本候选范围。

发布仍需完整回归、精确合并 CI、固定 Hermes 安装/恢复、SDK 矩阵、三平台资产与 checksum、公开 tag 普通安装来源及真实客户端结果；本说明不代替这些证据。
