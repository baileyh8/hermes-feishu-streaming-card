# V5.0.18 — Hermes v0.23.0 真实投递与自管理 PM 安装修复

[English](release-notes-v5.0.18.en.md)

针对 [#504](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/504) 与 [#503](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/503)（承接 [#497](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/497) 与 [#505](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/505)）。未知源码继续拒绝；卡片样式与配置默认值不变。

## 范围

- 精确登记 v0.23.0 的同步 `@contextmanager` 助手 `gateway.session_ingress_media.capture_lease`：按 contextlib 标准 wrapper 逐项核验 code/globals/defaults/kwdefaults 与闭包回指，再对原函数做完整来源校验；无一般性 unwrap，仿造 wrapper、克隆闭包与源码漂移仍拒绝。
- Feishu 准入为 native 或 handoff 未 staged 时，hook 离开并回到 Hermes 原方法，`already_sent`、媒体、footer 与失败投递保持核心归属；降级回合不再重复投递完整正文。旧 scoped hook 块保持精确识别、幂等升级与逐字恢复；renderer 契约标识与包元数据解耦，渲染字节门禁不变。
- 自管理 PM 安装：可见性探针改在官方 bootstrap 实际启动的 store 解释器上运行。解释器解析在独立子进程中完成，不经过会 exec 的 bootstrap；解析失败、非绝对路径、缺失文件、多余输出或非零退出都回退原探针并保持拒绝。插件不可解析仍拒绝，`probe output unreadable` 不放行。

## 验收记录

（发布前填写：完整 CI、精确提交矩阵、普通 wheel 安装/恢复、公开 tag 安装与资产校验的链接与结果。）

## 贡献

感谢 [mouyong](https://github.com/mouyong) 提供 #504 的同步 wrapper 失败定位、双补丁与真实回合复测（PR #505）；感谢 [ywarmy](https://github.com/ywarmy) 的 #503 自管理 PM 现场证据。问题证据与代码作者分开记录，历史贡献保留。
