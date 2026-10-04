# v4.7.1：命令确认回调修复

本补丁修复 `/new` 等命令确认按钮的两处回调缺陷：回调重绑定后可能漏接管，以及旧 dispatcher 路径完成确认后没有刷新原卡。保留已有卡片布局和配置默认值。

## 修复内容

- 安装回调时同时检查同步、异步实际方法，避免继承或重绑定后残留的“已安装”标记使 HFC 按钮落入 Hermes 的 `/card button …` 普通命令路径。
- 重载 HFC 时保留真实原生处理函数，避免把旧 HFC 包装器再次作为 fallback，其他原生按钮继续交由 Hermes 处理。
- 旧 dispatcher 进入异步确认路径时，也更新原确认卡；更新失败使用既有补发路径。确认/取消仍通过 Hermes 原始 handle 只执行一次，使用发送时保存的 IM message ID，不把回调 token 当作回复目标。

## 验证范围

- 聚焦回归 **1,137 passed、0 failed、0 skipped**，包括回调修复、冷启动、SDK、adapter resolver、runtime/server 和文档契约。
- Hermes **0.19.0**（`3ef6bbd`）、当前正式版 **0.21.5**（`f97608f`）、核验时的 main（`af90026`）：24 项隔离验证通过。执行上游原始回调代码、真实 Lark SDK dispatcher 与 Hermes 原生确认状态，覆盖原生失败对照、正常入口、旧 dispatcher、重绑定和重复点击。
- 精确合并提交的完整 CI、普通 wheel、annotated tag、发行资产校验与公开安装结果登记在 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.7.1)。

收到的 0.19.0 日志确认了“按钮进入 `/card` → 未识别命令 → 错误回复使用非 IM ID → 99992354”的顺序，但尚缺报告者的 HFC 版本和按钮 `action.value`。本版修复的是已复现的代码缺陷，不能据此声称已确认该用户漏接管的具体触发原因。未在报告者环境完成真实飞书复验；本轮不新增桌面或手机视觉验收结论。

## 升级

通过现有安装器更新到 `v4.7.1`，再按安装输出通过原服务管理方式重启 sidecar 和 Hermes Gateway，让 Gateway 加载新的 HFC 回调。只更新磁盘上的包，不会替换已运行进程中的函数。重启后重新发送 `/new` 并操作新确认卡；不要用旧确认卡验证新进程，也不要手改 Hermes 源码。

## 贡献记录

本版代码作者为 [baileyh8](https://github.com/baileyh8)，修复见 [PR #371](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/371)。感谢通过维护者提交日志的用户；尚未获得可公开的署名，不推断其身份。历史代码、方案和问题报告的贡献记录继续保留在 README、CHANGELOG 与历史版本中。
