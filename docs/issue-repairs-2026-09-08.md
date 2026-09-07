# v4.4.1 后续反馈修复

本页记录修复分支的行为与验收边界，不代表这些改动已经发版或用户部署已验收。

| 反馈 | 修改或处理 | 尚需验证 |
| --- | --- | --- |
| #261 | 已验证旧 hook 的整文件 ownership 后，使用原始备份评估新模板锚点，支持安全重装 | 报告者升级后 doctor 输出 |
| #263、#264 | 无 Git 单文件/多文件安装生成本地 ownership 快照；补 s6 同用户启动示例 | 报告者实际镜像、UID 与目录挂载 |
| #265 | 多文件 integrity migration 验证所有目标；同目标旧 fence 可沿官方 migrate/ack 流程解除 | 报告者升级前遗留状态复验 |
| #83、#266 | 补装 secondary profile adapters；复用上游 transport ownership 选择正确 bot；补零事件认证排障 | 不能以该修复单独解释远端 heartbeat_missing，需实际进程证据 |
| #258 | 短编号按钮、正文完整说明、整卡限额预检；已接管审批超时拒绝，原 topic 卡更新 | 手机/桌面客户端布局与超时复验 |
| #262 | 验证合法 ID 的 at 标签保真，说明普通昵称并不构成用户提及 | 需要可信身份映射及客户端通知验收，未自动猜测 ID |
| #73 | 保留旧版本事件全部 ignored 的独立排障边界 | 本轮没有该用户环境，不能宣称同一根因 |

最新上游验证固定在正式版 `v2026.8.31`（`29112bef099274229cadff79cdff7bf7b99c4b77`）
及 2026-09-07 主分支 `a7198a8855ad98681114ff5138eb01fe132a62e7`。
两者均标记 Hermes 0.21.0，但源码布局不同。CI 增加 Python 3.12/3.13 的真实源码
安装、重复安装、诊断、编译与逐字卸载还原门禁。

本地快照不是 Git 来源证明，不能用于自动接受源码升级。迁移事务在写入前后重验，
发现源码漂移时回滚自有 metadata，保留用户源码。已有 Git 单文件安装继续保留 Git
ancestry 门禁，不把 Git 错误静默降级。

感谢 [ywarmy](https://github.com/ywarmy)、[Ricadre](https://github.com/Ricadre)、
[mouyong](https://github.com/mouyong) 提供本轮报告与复现材料。

相关指南：[多 profile 排障](wiki/shared-profile-routing.md)、
[Docker/s6 示例](wiki/docker-s6-startup.md)、[完整性迁移](wiki/hermes-decomposed-patcher.md)、
[审批阅读方式](wiki/card-readability.md)。
