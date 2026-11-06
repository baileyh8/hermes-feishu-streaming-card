# V5.0.17 — Hermes v0.23.0 兼容

[English](release-notes-v5.0.17.en.md)

针对 [#497](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/497)，本版为精确 Hermes v0.23.0 `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19` 增加兼容。未知源码继续拒绝；卡片样式与配置默认值不变。

## 范围

- 精确识别 completion wrapper 新增的同步 `_note_bot_reply(source)` 条件分支；保留原有三语句 wrapper 契约，条件、调用方式或顺序发生未知变化仍拒绝。卡片抑制原生文本时，通过已确认回执补足原有 bot-loop 记账，不伪造 `delivered` 或改写 `agent_result`。
- 核验 admission 的 SessionActor 与 `session_settlement_recovery` / `session_runtime_workers` 的结算、独立 Task 与 shield 等待依赖，登记完整源码 cohort 和运行时绑定；保留原生归属与终局一次性确认。
- 新增 v0.23.0 PM writer/launch family，包含配置 writer、PM admission、后端证明与平台路径的精确核对，不按版本字符串放行。
- CI 增加固定源码与官方镜像 preserved/cleared 验收，保留旧版矩阵。

## 验收记录

源码与独立审查已验证 completion、31 来源漂移拒绝、已安装补丁及 loaded binding、默认参数、多 owner/重复/迟到回执，以及真实 settlement Task 的正常/取消/unknown 轨迹。旧版相关聚焦回归 800 项通过；缺失本地固定来源的跳过项由 CI 固定矩阵覆盖。4 次热路径 `_verified()` 合计约 0.86–0.97 秒，此数值不是完整 Gateway/model 延迟。

普通 wheel 完整安装/恢复事务、精确提交完整跨平台 CI、官方镜像 preserved/cleared、tag、资产与公开安装分别作为发布门禁；最终结果和精确提交见本版 GitHub Release，不以检测 full 代替这些证据。

报告者 Linux / PM / systemd user / split HOME 现场、真实 Gateway/model 与飞书客户端尚未复测。发布后应通过正常安装器刷新受管 hook，由原服务管理方重启 Gateway，再核对 `/health` 的新版本、generation 和 runtime readiness；不要手改 Hermes 源码或删除保护记录。

## 贡献

感谢 [Lite-G](https://github.com/Lite-G) 提供 #497 的精确源码定位、三层缺口与复测方案。问题证据与代码作者分开记录，历史贡献与提交作者保留。
