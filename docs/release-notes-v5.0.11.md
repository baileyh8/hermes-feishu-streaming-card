# v5.0.11 — 固定 Hermes main 的 admission 归属核验

本补丁处理 [#471](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/471)：针对固定 Hermes 源码 `9b904dcc22c23fe8b82834339bbdd96495072e59`，校验 admission 完成交接与首次展示策略的执行归属。该版本的发布状态以 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.11) 为准。

## 修复范围

- 增加该固定源码的完整 ingress/authority 摘要对，并继续核对已加载代码、原生 claim、row、路由、generation、epoch 和终局单次确认。这个源码族只针对 admission completion，不代表支持任意 Hermes main 版本。
- 首次选择卡片、通知或交互事件的展示策略前，借用原执行 Task 的 claim 核验归属，并在策略查询结束后再次核验。归属无法确认时，从首次事件起固定为 Hermes 原生交付，避免使用过期 profile 或路由启动无法完成交接的卡片。复核不保证所有并发变化都是原子的。
- 首事件尚未固定策略，且来自无法取得原 `execute_admission` frame 的独立 Task 或线程时，保守回退原生交付；已接管卡片的跨 Task 终局交接仍遵循原协议。共享群的首位 sender 与本轮 sender 可以不同，终局仍核验本轮完整身份。

## 安装与兼容边界

这条新迁移路径依赖安全 POSIX dirfd 事务；9b904 在 Windows 的成功安装尚未验证，不能用旧来源族的 Windows 检查代替。

新适配器只接受精确 `9b904dcc22` 和已核验的官方 PM、配置及启动来源。发布候选的支持前态为：已有官方 PM generation、schema52 配置与有效 boot receipt；canonical HFC 已启用、`allow_tool_override: false`；已有 schema2 HFC member，但没有 observer marker；SDK 不存在，或为官方 `feishu` extra 管理的 1.7.5。成功迁移使用 PM 管理的 HFC 与 SDK 依赖，并保留配置与其他 profile。

全新安装、未初始化 PM、旧 v1 receipt、flag 缺失或 true、手装 SDK 1.6.8、managed overlay、外部 secrets 或无法证明的配置/来源继续在 PM admission 前拒绝。这不是所有历史安装的升级支持；报告者的具体前态尚未知。拒绝不意味着整条 CLI 零文件写入，例如私有锁仍可能创建。不要删除保护 marker 或手改 Hermes 源码绕过检查。

同版本且同 `spec_sha`（同安装声明）的重复安装与 `restore` 后重新安装使用同一份已验证 receipt。`restore` 还原共享 checkout 的受管源码，保留 PM、SDK、配置及 receipt；它不等于完整卸载，也不只影响一个 profile。必须通过原服务管理方重启 Gateway。HOME 级 `uninstall` 通过官方 PM 将当前 HOME 的 HFC 从 enabled 移至 disabled，保留依赖 selection、member、SDK、共享源码和原 receipt；需原服务管理方重启 Gateway 后生效。它不删除共享安装材料，也不承诺运行中的旧进程立即停用。配置漂移或卸载后直接重新安装会拒绝；不要手删撤销记录绕过保护。

版本正式发布后，安装包解压进入 `hermes-feishu-card-v5.0.11-<platform>/` 再运行安装器。保留旧安装的 manifest、marker、备份和恢复资料。

## 验收与待验

[PR #473](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/473) 的修复候选包含固定源码、真实 SQLite 与受控模型和传输端口的复现；普通 wheel 子进程覆盖固定 `9b904dcc22` 的 18 个场景及 v0.22.5 的 5 个场景。这些检查不能代替真实服务验收。PR head `66a29a702414669eecb2283d31e9b8e6884a46a1` 的[完整 CI](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/40490693602) 通过 57 个 job，CodeQL 通过；这些结果绑定该提交，不能自动算作包含发行材料的新提交通过。

生产开关开启的普通 wheel `202a0c0bb7845d6d30a10b2233043df2c5e3c4a872812ebdefb9de9253c0aada` 在私有真实 PM 中完成受限 5.0.10→5.0.11 迁移。卸载覆盖 PM 前后故障、未知 ticket、成功重试及新进程双 HOME loopback 投递：目标 HOME 停止请求，另一 HOME 保持投递。实际选中 generation 没有重建，不能把结果描述为另一个 C generation。较早普通 wheel 已完成同版本同声明的源码恢复→重新安装循环；该证据与最终 wheel 的新增卸载证据分开记录。

发布须完成包含版本和发行材料的精确提交完整 CI、公开安装、tag 和发行资产验收，最终证据记录于 GitHub Release。报告者现场、真实 Gateway/model 对话，以及 Feishu 客户端视觉与交互未复测；不能据此宣称 #471 的现场现象已解决。

## 贡献

感谢 [lv86snorlax](https://github.com/lv86snorlax) 在 #471 提供源码修订、故障报告与源码分析。修复和测试提交由 [baileyh8](https://github.com/baileyh8) 编写；问题证据与代码作者分别记录，既有贡献署名全部保留。
