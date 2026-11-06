# V5.0.17 — Hermes v0.23.0 兼容

[English](release-notes-v5.0.17.en.md)

针对 [#497](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/497)，本版为精确 Hermes v0.23.0 `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19` 增加兼容。未知源码继续拒绝；卡片样式与配置默认值不变。

## 范围

- 精确识别 completion wrapper 新增的同步 `_note_bot_reply(source)` 条件分支；保留原有三语句 wrapper 契约，条件、调用方式或顺序发生未知变化仍拒绝。卡片抑制原生文本时，通过已确认回执补足原有 bot-loop 记账，不伪造 `delivered` 或改写 `agent_result`。
- 核验 admission 的 SessionActor 与 `session_settlement_recovery` / `session_runtime_workers` 的结算、独立 Task 与 shield 等待依赖，登记完整源码 cohort 和运行时绑定；保留原生归属与终局一次性确认。
- 新增 v0.23.0 PM writer/launch family，包含配置 writer、PM admission、后端证明与平台路径的精确核对，不按版本字符串放行。
- CI 增加固定源码与官方镜像 preserved/cleared 验收，保留旧版矩阵。

## 验收记录

发布提交为 `d1bd76d5fcdd76cba810ca3b4ede6e1dba0fd877`。[PR #498 完整 CI](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/42168897620) 59/59、[主线精确提交 CI](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/42174054769) 59/59、[annotated tag 门禁](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/42178168337) 61/61 均通过；[CodeQL](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/42174054777) 通过。支持的 Python、平台、旧上游与 8 个官方镜像场景均保留。PR 末轮曾因 macOS 报告上传 DNS 错误失败，保留原记录后在同一提交单项重跑通过。

普通非 editable wheel 在精确 v0.23.0 的私有环境中完成 install → repair → repeat install → doctor → restore，1 项事务测试通过（70.49 秒），源码与配置恢复逐字节一致。160 个包资源（158 个 Python 文件）与提交、wheel 和安装结果一致。匿名公开 Git tag 安装也通过：版本 5.0.17、`direct_url.json` 的 commit/tag、资源字节和 console `--help` 均符合预期。

公开后的四个资产匿名下载均为 HTTP 200，SHA-256 与 draft 核验副本一致，随后再次通过归档与实际解压检查。

三平台归档校验和、单一顶层目录及 9 文件清单通过。macOS 实际解压至 0700 / 1777 目标时，目录 inode/uid/gid/mode、哨兵和文件字节保持正确；Linux GNU tar 的 root/非 root 验收由 CI 覆盖。

源码与独立审查覆盖 31 来源漂移拒绝、loaded binding/default、多个 owner、重复与迟到回执，以及真实 settlement Task 的正常/取消/unknown 轨迹。本地冷校验约 0.44–0.58 秒，四次热校验约 0.07–0.08 秒；这是校验路径微基准，不是 Gateway/model 或客户端端到端延迟。原有来源拒绝和超时门禁未放宽，详细修正与失败记录见 [#498](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/498)。

报告者 Linux / PM / systemd user / split HOME 现场、真实 Gateway/model 与飞书客户端尚未复测。发布后应通过正常安装器刷新受管 hook，由原服务管理方重启 Gateway，再核对 `/health` 的新版本、generation 和 runtime readiness；不要手改 Hermes 源码或删除保护记录。

## 贡献

感谢 [Lite-G](https://github.com/Lite-G) 提供 #497 的精确源码定位、三层缺口与复测方案。问题证据与代码作者分开记录，历史贡献与提交作者保留。
