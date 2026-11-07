# V5.0.18 — Hermes v0.23.0 真实投递与自管理 PM 安装修复

[English](release-notes-v5.0.18.en.md)

针对 [#504](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/504) 与 [#503](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/503)（承接 [#497](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/497) 与 [#505](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/505)）。未知源码继续拒绝；卡片样式与配置默认值不变。

## 范围

- 精确登记 v0.23.0 的同步 `@contextmanager` 助手 `gateway.session_ingress_media.capture_lease`：按 contextlib 标准 wrapper 逐项核验 code/globals/defaults/kwdefaults 与闭包回指，再对原函数做完整来源校验；无一般性 unwrap，仿造 wrapper、克隆闭包与源码漂移仍拒绝。
- Feishu 准入为 native 或 handoff 未 staged 时，hook 离开并回到 Hermes 原方法，`already_sent`、媒体、footer 与失败投递保持核心归属；降级回合不再重复投递完整正文。旧 scoped hook 块保持精确识别、幂等升级与逐字恢复；renderer 契约标识与包元数据解耦，渲染字节门禁不变。
- 自管理 PM 安装：可见性探针改在官方 bootstrap 实际启动的 store 解释器上运行。解释器解析在独立子进程中完成，不经过会 exec 的 bootstrap；解析失败、非绝对路径、缺失文件、多余输出或非零退出都回退原探针并保持拒绝。插件不可解析仍拒绝，`probe output unreadable` 不放行。

## 验收记录

发布提交为 4b823e39d6fce031b9d0a5ce579c13162e76834a（merge of PR #506）。PR #506 完整 CI 59/59、主线精确提交 CI 59/59、annotated tag 门禁 61/61、CodeQL 均通过。支持的 Python 3.9–3.14、三平台、旧上游矩阵与 8 个官方镜像场景全部保留。三平台归档 checksums、单一顶层目录、9 文件清单与 macOS 实际解压（0700/1777 目标、目录/权限归一、字节一致）已通过。报告者现场（自管理 PM / v0.23.0 真实回合）、真实 Gateway/model 与飞书客户端尚未复测。发布后应通过正常安装器刷新受管 hook，由原服务管理方重启 Gateway，再核对 /health 的新版本、generation 与 runtime readiness；不要手改 Hermes 源码或删除保护记录。

## 贡献

感谢 [mouyong](https://github.com/mouyong) 提供 #504 的同步 wrapper 失败定位、双补丁与真实回合复测（PR #505）；感谢 [ywarmy](https://github.com/ywarmy) 的 #503 自管理 PM 现场证据。问题证据与代码作者分开记录，历史贡献保留。
