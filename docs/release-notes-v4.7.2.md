# V4.7.2：更新停机保护与卡片回调修复

- **#375 更新保护**：持久 sidecar 服务运行时，卡片 `/update` 在停服前明确拒绝，并在确认后再次检查。sidecar 停止失败且原 HEAD / hook 仍通过验证时，请求恢复 Gateway；不会将“重启命令成功”冒充健康验收。持久服务自动更新暂不支持，请走终端维护流程。
- **#374 SDK 回调**：修复已核验 lark-oapi 1.6.8 WebSocket 实现直接丢弃 CARD 帧的路径。仅更新当前实例，在所属 WS loop 执行，保留 SDK 分片、响应编码和其他事件；不改 SDK 文件，全局 Client 类和未知实现保持原状。真实个人版 / Termux 现场仍需复验，不能单凭原 Hermes 回调日志缺失判断飞书未投递。
- **#372 完整性恢复**：新增 `integrity acknowledge-review --rebind-target OLD_TARGET_SHA256`。显式指定旧身份，验证当前安装和停止状态两次，并以原记录 CAS 提交；保留独立重启证据。不自动忽略设备号或跨目标解除告警。
- **#370 配置地图**：中英文手册前部按卡片区域解释可调项和限制，README 提供入口，修正 task 预设的未发布注释。
- **PR #373**：保留 Nevoker 原提交，支持 Python 3.14 的 slice 常量语义指纹，未知常量继续拒绝。

## 升级与恢复

通过原安装入口升级 HFC，重新运行 setup/install，并经现有服务管理方式重启 sidecar 和 Gateway。重新发送 `/new` 使用新卡；旧过期卡不会恢复授权。

持久 sidecar 的 `/update` 会显示明确原因且不停止 Gateway。终端维护时先记录原 config/env/Hermes 路径，通过原服务 owner 停止服务，使用官方 Hermes 更新与 HFC 安装器完成升级，再恢复服务并运行 `status` / `doctor --explain`。不要为绕过检查手删 unit、manifest 或状态记录。

身份恢复的完整命令、安全前提和边界见[迁移手册](migration.md#显式恢复孤立的身份绑定)。

## 验证边界

发布门禁结果以本版本 GitHub Release 的最终验证记录为准。SDK 帧测试使用真实 SDK/protobuf 分发与合成数据，不等于飞书个人版、Android 或 iOS 现场验收。无新的卡片布局变更。未确认的外部反馈继续保持开放。

## 贡献

感谢 Nevoker（PR #373）、DaveWang888（#375）、ffdxdynotable（#374）、ywarmy（#372）与 jackwude（#370）提供代码、复现和建议。历史贡献记录保留。
