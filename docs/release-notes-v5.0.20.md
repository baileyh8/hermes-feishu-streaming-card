# V5.0.20 — 官方 Docker 多 profile 数据根识别

[English](release-notes-v5.0.20.en.md)

针对 [#512](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/512)。修复官方 Docker 的 `HERMES_HOME=/opt/data` 布局中，default 与命名 profile 被 `profile-default-unverified` 拒绝的问题。

## 范围

- 任务当前 Hermes home 以原生数据根为边界识别；default home 与 `profiles/<name>` 使用相同归属规则。
- absolute authority home 先核对其数据库目录，再判断 profile；root 外路径、数据库错配和不可核实状态保留 Hermes 原生投递。
- multiplex 继续忽略进程全局 profile 覆盖，原有 owner、generation 与终局一次性回执校验保留。

## 验收记录

- 已以真实原生 ContextVar 复现旧版拒绝，并验证标准路径数据库错配的红例。路由聚焦回归 276 项通过，无跳过；CI 工具契约 170 项通过，文档与元数据 104 项通过。独立最终 QA/review 仍为发布前门禁。
- 发布还须通过完整 PR/main/tag CI、官方镜像验收、普通 wheel 来源核验、公开安装和发行资产验证；最终证据见 GitHub Release。
- 报告者原部署与真实飞书回合尚未验证，不凭 doctor 或 health ready 推断真实出卡成功。

## 贡献

感谢 [hnzwx](https://github.com/hnzwx) 提供官方 Docker 多 profile 布局、脱敏诊断与源码分析。问题证据与代码作者分开记录，历史贡献保留。
