# Open Issues #133 / #135 解决路线设计

## 1. 当前范围

截至 2026-07-17，仓库只有两个 open issues：

| Issue | 子问题 | 判断 | 设计文档 |
|---|---|---|---|
| #135 | system notice 瞬时失败、静默丢失 | 接受用户风险；拒绝在 hook `/events` 层盲重试和超时后复制原文 | `2026-07-17-issue-135-reliable-system-notice-delivery-design.md` |
| #133 | compaction 期间卡片不可见 | 接受；已确认 Hermes status 在进入 Feishu adapter 前被过滤 | `2026-07-17-issue-133-compaction-visibility-design.md` |
| #133 | 字号与卡片尺寸 | 接受正文/timeline/footer 字号；拒绝承诺整卡像素宽高 | `2026-07-17-issue-133-card-text-size-config-design.md` |

non-loopback 后续安全方案不在本轮范围；已发布的 v4.0.10 事件鉴权保持不变并纳入回归。

## 2. 为什么拆成三个子项目

#135 改变的是飞书 API 投递语义、幂等性和 fallback；#133 compaction 改变 Hermes patcher/status callback 与 session runtime phase；#133 字号只改变配置与 renderer。三者的风险、测试矩阵和可回滚边界不同，不能做成一个不可审查的大提交。

它们存在一条单向依赖：compaction status 最终也走 system-notice/event 投递路径，因此先完成 #135 能让 #133 在更可靠的底座上验收。字号配置与二者无运行依赖。

## 3. 推荐交付顺序

### Release A：通知可靠性

只实现 #135：

1. 飞书 send/reply UUID 去重。
2. transient error 有界重试。
3. delivered/not_sent/unknown 结果语义。
4. 明确失败原文 fallback、未知结果通用告警。
5. metrics、脱敏 diagnostics、真实通知 smoke。

建议作为 `v4.0.11` 独立发布。发布后在 #135 说明采纳了问题现象，但修复位于 Feishu sidecar 层而非 hook `/events` 层；邀请报告者用原 502/503 场景复测，再关闭 issue。

### Release B：compaction 与字号

实现 #133 的两个独立 commit：

1. 精确 status callback hook 与 compaction runtime phase。
2. 受控 CardKit text-size 配置。

建议作为后续修复版本发布。compaction 必须完成真实长会话 smoke；字号必须完成桌面/移动端视觉验收。发布后在 #133 明确：compaction 可见性和字体配置已交付，整卡物理宽高由飞书客户端控制、不纳入功能承诺；等待报告者复测后关闭。

## 4. 共同约束

- sidecar-only：不得手改安装后的 Hermes `gateway/run.py`。
- 所有 Hermes 改动只能由 `install/patcher.py` 的可检测、可移除 marker block 完成。
- 已接管卡片路径不得恢复重复原生灰色正文。
- topic、群聊、multi-bot、multi-profile、WebSocket callback、cron 和附件行为必须回归。
- 不记录或提交 App Secret、tenant token、真实 chat/user/message id、事件 proof 或未脱敏截图。
- loopback 与 v4.0.10 non-loopback event auth 都必须通过回归。
- 每个子项目先 TDD focused tests，再跑全量 pytest 和 `git diff --check`。

## 5. Issue 闭环规则

1. 设计审批不修改 GitHub issue 状态。
2. 实现 PR 中引用对应 issue，但不在未经真实验收时写“完全修复”。
3. merge、tag、release assets、公共 tagged install 和核心 smoke 全部通过后，向 issue 回复修复说明与复测步骤。
4. 有报告者可复现场景时优先等待其复测；没有回复且维护者已完成等价复现/验收时，再按项目既有规则关闭。
5. 若实现发现 issue 的前提不成立，保留证据并解释，不为了“关闭 issue”接受错误补丁方向。

## 6. 审批时需要确认的决策

审批人只需逐项确认或提出修改：

1. #135 是否接受“明确未发送才 fallback 原文；未知结果只发通用异常提示”。
2. #133 compaction 是否接受新增一个可移除的 `_status_callback_sync` patcher anchor，旧 Hermes 缺 anchor 时降级而不阻断安装。
3. 字号是否接受五角色白名单和 PC/mobile mapping，并明确不支持整卡物理宽高。

三项可以分别批准；未批准项不会阻塞已批准子项目进入 implementation plan。

## 7. 本阶段完成定义

当前设计阶段完成于：三份子项目 spec 与本路线文档通过 placeholder、矛盾、范围和歧义自审，提交到独立设计分支，等待 Bailey 审批。审批前不修改运行代码、不回复/关闭 issue、不创建发布。
