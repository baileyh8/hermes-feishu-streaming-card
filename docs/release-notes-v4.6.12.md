# V4.6.12：按会话配置卡片宽度

新增 `card.width_mode`，无需修改安装包即可选择 `default`、`compact` 或 `fill`。默认保持原布局；全局、profile、bot 逐层覆盖，显式 `default` 可重置继承的宽度。修改配置后重启 sidecar。

```yaml
card:
  width_mode: fill
```

- 覆盖 JSON 2.0 的流式/终态卡、超限交接和修复卡、诊断与维护卡；JSON 1.0 审批卡保持原格式。
- 与正文思考尾部窗口共同生效，整卡预算仍会验证；不改变最终答案、身份隔离或默认阅读布局。
- 宽度由飞书客户端最终呈现，不承诺固定像素宽高；桌面与手机验收应分别记录。
- Hermes 最新稳定版目标仍为 v2026.9.24 / 0.21.5；main 源码快照更新至 `ea114c3e98c3339e13004adfc6098cf28ed7d754`（2026-09-29）。修复发送账本新增 `stop_reply_clock(delivery_adapter, event.source.chat_id, result)` 导致的安装拒绝，只接受发送后、finalize 前的精确同步调用一次；参数、位置、重复或 await 漂移仍拒绝。

感谢 [cbatbj](https://github.com/cbatbj) / chenbing1 在 [PR #351](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/351) 提供实现与测试；保留原始提交作者及全部历史贡献。

最终 CI、wheel/公开安装来源、发布资产校验及真实平台展示证据见 GitHub Release。本机生产升级、真实 Gateway/模型任务与手机端验收单独登记。
