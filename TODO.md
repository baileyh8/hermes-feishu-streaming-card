# Sidecar-only 主线任务清单

当前 active runtime 是 `hermes_feishu_card/`。legacy adapter、dual mode、旧 `sidecar/`、旧 `patch/` 和 `installer_v2.py` 不再作为主线运行时推进，只保留作历史参考或迁移资料。

## P0

- [x] 建立 `hermes_feishu_card/` 包作为 sidecar-only 主线入口。
- [x] 提供 `doctor`、`install`、`restore`、`uninstall` CLI。
- [x] 安装器写入最小 Hermes hook、备份和 manifest。
- [x] 安装失败时回滚，避免半安装状态。
- [x] 恢复和卸载拒绝覆盖用户改动。
- [x] 补齐基于 Hermes fixture 和 mock sidecar 的最小 hook 事件转发验证。
- [x] 补齐官方 Hermes `v2026.4.23` Git tag 源码的安装/恢复 smoke test。
- [x] 在真实 Hermes Gateway 进程中做人工 smoke test。

## P1

- [x] 定义 `message.started`、`thinking.delta`、`tool.updated`、`answer.delta`、`message.completed`、`message.failed` 事件语义。
- [x] 文档明确卡片正常状态只有 `思考中` 和 `已完成`。
- [x] 文档明确旧 adapter/sidecar/patch/installer_v2 代码不是 active runtime。
- [x] 将 sidecar 进程管理从占位 `status` 扩展为可启动、可停止、可探活。
- [x] 实现 Feishu CardKit HTTP client，并用 mock server 验证 tenant token、发送和更新。
- [x] 提供 `smoke-feishu-card` 手动命令用于真实飞书卡片发送/更新验证。
- [x] 使用真实飞书应用做人工 CardKit smoke test，凭据仅使用本机配置或环境变量。
- [x] 完成真实飞书长卡片压力测试，同一张卡片更新到 16k 中文字符。

## P2

- [x] 增加 sidecar 健康检查和重试指标。
- [x] 增加安装前 Hermes 版本展示和更友好的错误提示。
- [x] 增加端到端截图或录屏验证材料。
- [x] 编写从 legacy/dual 安装迁移到 sidecar-only 的安全迁移说明。
