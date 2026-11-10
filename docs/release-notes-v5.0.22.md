# v5.0.22：Hermes v0.23.1 精确兼容

## 修复范围

- **#520 stable 兼容**：新增官方 Hermes v0.23.1 `3b421578588243f920886e6fe79a6faa87ea026e` 的独立来源集合。31 个 admission/topology 文件、107 个 writer 文件及 4 个 launch 文件按完整字节校验；运行态继续检查真实加载的函数、defaults、contextmanager、settlement 与 producer 绑定。保留 v0.23.0 当前和历史受管渲染集合，混合或未知来源仍拒绝。
- **安装与配置**：`pm-0231` 使用上游真实 schema 53，早期 family 保持 schema 52；把新增 writer/launch 依赖纳入隔离子进程来源检查。不手改版本戳，也不自动替用户执行未知配置迁移。请先完成 Hermes 官方升级和配置迁移。
- **slash-confirm 锚点**：以精确 AST 定位旧四位置参数调用，或已审查的 requester-authority 调用，完整保留上游调用与权限表达式。错误参数、未知关键字、解包与歧义匹配仍拒绝。只修复这个锚点不构成未知 main cohort 的兼容证明。
- **CI 与依赖**：合入 #516 Ruff 0.17.4、#517 CodeQL v4.39.3 原始维护提交；固定上游矩阵显式执行 v0.23.1 三组测试，并加入官方镜像 preserved/cleared 验收。#510 的 `latest_stable` 明确为历史 v0.22.4 固定基线，既有覆盖保留。
- **超时诊断**：pytest worker 原子替换一个 `.progress` 文件，记录当前 node、顺序与耗时；该文件不算完成证据。最终报告的精确 commit、完整并集与每例一次门禁保留。旧 #516 超时日志未能定位精确卡点，本版不宣称已修复其根因或达到耗时目标。

## 验收与限制

发布需分别通过独立普通 wheel/真实子进程安装、修复、重复安装与逐字恢复，完整 PR/main/tag 矩阵、官方镜像、发行包校验和实际解压，以及公开 tag 安装。精确 SHA、CI 链接和实测结果在正式 GitHub Release 中登记；候选阶段不提前记为通过。

本版不改卡片布局或默认设置，不加入 #521 本地 repin。#497 报告者 Windows 原机的 `source_digest` 拒绝仍未独立复现。真实 Hermes Gateway/model 回合、飞书客户端点击与桌面/Android/iOS 视觉未由本轮自动化替代。

安装包保持三平台独立顶层目录：解压后进入 `hermes-feishu-card-v5.0.22-<platform>/` 再运行安装器。保留既有保护记录、备份及已发表 tag/资产；不要手改 Hermes 源码或删除保护文件绕过检查。

## 贡献记录

感谢 [Lite-G](https://github.com/Lite-G) 在 [#520](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/520) 提供 main 锚点证据，[ywarmy](https://github.com/ywarmy) 提供 v0.23.1 stable 字节、安装与运行分析。Dependabot 提供 [#516](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/516) 和 [#517](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/517)；原始提交、作者和历史署名保留，问题证据与代码作者分开记录。
