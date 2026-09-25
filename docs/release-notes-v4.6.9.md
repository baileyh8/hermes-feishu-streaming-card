# V4.6.9：群聊并发隔离与更清晰的上手体验

## 本次更新

- 修复 [#348](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/348)：同一群聊的不同用户同时提问时，新轮不再仅凭群和话题相同就把其他活跃卡片判为“已停止”。旧 hook 的首事件 fallback 同样保留发送者与聊天类型；身份未知时保留旧轮。原生插件使用 Gateway 执行会话的哈希范围隔离，redirect 仅结束明确指定的来源 turn。
- 各轮最终答案继续更新自己的卡片；失败、重复及晚到事件仍遵守终态保护。本版不会复活已停止任务或恢复升级前丢失的进程内执行状态。
- 合入 [PR #349](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/349)：所有单选 choice 复用紧凑列布局，保留选项说明、顺序、回调数据、自定义输入和多选行为。
- 重写中英文 README，以项目优势、效果、安装与配置引导新用户。保留全部历史贡献，首页展示近期版本，完整旧记录移至历史更新页。

## 验收与边界

- main 上红绿对照：26 个失败与 4 个通过；修复后同组 30 个通过。覆盖旧 hook、原生插件、缺少 started 的首事件、三人并发且一人失败、重复/晚到完成与 redirect。
- 候选普通 wheel、完整测试、精确 merge SHA 的 CI、annotated tag、发布资产及公开安装验证分别记录，不用源码测试代替安装 provenance。
- 飞书平台验收使用隔离候选包和模拟生命周期事件向现有测试群调用真实 API；不等于多位真人通过 Gateway/模型同时运行，也不声明手机视觉验收。最终执行结果登记在 GitHub Release。
- #344 仍缺报告者版本及完整复现信息，保持开放。

## 贡献

感谢 [cainiaozp](https://github.com/cainiaozp) 提供 #348 的并发复现和清理条件根因证据。感谢 [mouyong](https://github.com/mouyong) 提供 PR #349 的实现、回归与红绿对照；合并保留原始代码提交作者。所有历史贡献继续保留在 README。
