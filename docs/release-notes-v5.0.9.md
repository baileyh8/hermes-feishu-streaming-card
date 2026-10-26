# v5.0.9 — 原生身份、Windows 安装与旧 PM 恢复

本补丁处理 [#451](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/451)、[#459](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/459)、[#462](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/462) 和 [#463](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/463)。公开版本与精确验收记录以 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.9) 为准。

## 修复范围

- **原生 admission 身份**：单独传递 Hermes `source.user_id`，Feishu `sender_open_id` 保留 mention 语义。显式身份非法或冲突时拒绝交接；空用户在卡片接管前保留原生交付。原 execution claim、作用域、owner、generation 和 epoch 校验保留。
- **Windows 安装**：原始文件描述符按二进制模式读写，避免 Ctrl-Z/CRLF 改写 wheel 字节；官方配置子进程使用私有 HOME、USERPROFILE 和临时目录，并只允许精确核验的 Git 查询。恢复事务验证文件、目录和 reparse 归属，并覆盖失败回滚。
- **旧 PM wheel 恢复**：仅接纳有完整归属证明的 PR #458 schema-1 flat wheel，核对历史声明、同名 wheel、摘要、属主与权限、精确 file URL、安装版本及全部包资源。保留历史 workspace schema-1 和内容寻址 schema-2。缺失来源或 VCS 安装仍不能凭资源相似跳过校验。
- **恢复诊断与操作顺序**：固定、脱敏的 ack 类别和处理提示；区分新版 management CLI 与仍旧版的 owned runtime。先停止实际服务管理器、KeepAlive 和自动更新器，保留 PID/health 双重停止检查、旧 fence 的 CAS 与恢复证据。见 [PM 恢复](wiki/pm-recovery.md)。
- **CI 维护**：吸收 Dependabot #464/#465/#466，保留原提交作者；固定核验过的 action SHA 和 Ruff 版本。并发确认测试明确区分执行中和完成后的重复请求，继续要求一次修复和准确 successor。

## 验收边界

失败复现、聚焦测试、独立 QA/review、精确候选/主线/tag 完整矩阵、原生 Windows 3.12/3.14、普通非 editable wheel 与公开安装分别核验。checksums、完整归档清单、实际解压元数据和匿名下载另作发行门禁；最终记录链接放在 Release 中，待验项不写成通过。

本轮不宣称已完成报告者真实 Gateway/model 或飞书客户端复测；桌面/Android 视觉与点击未验证，iOS 和新用户试用不纳入范围。未知 Hermes 源码仍拒绝不安全安装，未知身份仍保留原生交付。旧迁移现场的 v5.0.8 成功证据与本补丁新增恢复覆盖分别记录。

安装包继续使用版本化顶层目录，解压后进入 `hermes-feishu-card-v5.0.9-<platform>/` 再运行安装器。所有既有 tag、资产、发行说明和贡献历史保留。

## 贡献

感谢 [coytus](https://github.com/coytus) 的 #451 反馈，以及 [leavrcn](https://github.com/leavrcn) 在 #451 提供的原生身份对照、复现和候选验证；感谢 [mingyangtech666](https://github.com/mingyangtech666) 的 #463 反馈与 #458 原始实现、[mouyong](https://github.com/mouyong) 的 #462 旧格式对照，以及 [ywarmy](https://github.com/ywarmy) 的 #459 迁移与配置恢复证据。感谢 Dependabot 的 #464/#465/#466。问题证据与代码作者分开记录；原始提交和此前署名全部保留。
