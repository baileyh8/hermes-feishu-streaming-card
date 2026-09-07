# Hermes Feishu Streaming Card V4.4.2

V4.4.2 修复 Hermes 0.21 安装完整性迁移及单进程 multiplex 卡片投递，并改善审批与澄清卡片。

## 修复

- 按完整 ownership manifest 校验旧 hook，支持拆分源码的整组迁移，避免成功安装后仍被陈旧 integrity fence 阻塞。
- 无 `.git` 的源码安装可记录明确标识的本地 ownership 快照；不授予 Git ancestry 或自动升级权限。迁移写入前后重验源码，发生变化时回滚自有元数据并保留用户修改。
- 为 multiplex 的次级 profile adapter 安装 wrapper，保留 Hermes transport ownership。
- 审批和澄清卡片显示编号按钮及完整选项说明；已接管但超时的审批按 deny 处理，超大交互在准入前拒绝。
- 补充 Docker/s6 用户、启动路径与诊断指引；缺少 `.git` 与缺少 Git 可执行文件是两个问题，从 `git+` URL 引导安装仍需要 Git。

## 验证边界

修复提交已通过全平台 CI，并针对固定 Hermes stable `29112bef099274229cadff79cdff7bf7b99c4b77` 和 main `a7198a8855ad98681114ff5138eb01fe132a62e7` 执行 Python 3.12/3.13 源码安装、重复安装、编译和恢复契约。最终版本以发布门禁结果为准。

报告者的实际 Docker/multiplex 环境、飞书客户端通知和历史 #73 环境尚未复验。#262 普通 @昵称缺少可信用户 ID，不猜测身份映射，不宣称已修复该问题。

## 贡献者

- [ywarmy](https://github.com/ywarmy): [#261](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/261), Hermes 0.21 completion-marker report.
- [Ricadre](https://github.com/Ricadre): [#265](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/265), stale integrity migration reproduction.
- [mouyong](https://github.com/mouyong): [#83](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/83), [#263](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/263), [#264](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/264), [#266](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/266), Docker/source-only and multiplex evidence; [#258](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/258), approval readability feedback.

Historical credits are preserved in [README](../README.md).
