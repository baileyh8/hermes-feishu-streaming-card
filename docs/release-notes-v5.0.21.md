# V5.0.21 — 完成事件额度归属与子进程编码

[English](release-notes-v5.0.21.en.md)

本轮处理 [#518](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/518) 与 [#497](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/497) 的新反馈。是否可安装以 GitHub Releases 为准。

## 范围

- 从本轮 Hermes 结果成对传递 model/provider，使已启用 `subscription_usage` 的符合条件 Codex 完成回合进入额度查询；不从全局配置、账号可用性或 GPT 名称猜测本轮归属。
- 固定 v0.23.0 的结果构造点捕获实际 fallback 路由，未知、冲突或混合归属保守隐藏额度；查询异常不阻断完成卡片，重复完成不重复查询。既有默认值和配置保持。已安装源码证明同时识别精确 v5.0.20 与当前 hooks；两套都须逐字还原固定原版，混合或陌生字节继续拒绝。
- 为受控 PM/backend Python 子进程明确 UTF-8 协议与解码，防止父进程非 UTF-8 locale 将子进程输出变成未分类的解码异常；安全诊断继续脱敏，失败保留拒绝与事务保护。

## 诊断结论与限制

Windows 报告者在原机仍看到 `source_digest`。正式 setup 两条路径会显式传入选中的 `pm-0230` sources；直接调用无 sources 参数的内部 helper 会用旧默认合同，不能据此认定正式安装选错 family。原机正式拒绝原因尚未独立复现，未列为已解决。无 pip 的运行时使用预构建 `.whl` 及精确 `HFC_INSTALL_SPEC`，不通过修改受管运行时或删除保护记录绕过拒绝。

本轮 #512 的旧版现场证据仅属于精确 v5.0.20、同一机器人四 profile 桌面测试，不是本候选验收。新候选的 Windows 原机、真实 Gateway/model、客户端及多 Codex 账号归属需分别登记。

## 验证

定向回归覆盖 locale 解码、成对终局路由、fallback、混合路由、重复/迟到完成与额度查询失败。发布门禁要求独立 QA、普通 wheel/真实子进程、完整 PR/main/tag CI、归档与 checksums 和隔离公开安装；公开 Release 登记精确 CI 与包证据。原机 Windows 拒绝、真实 Codex Gateway/model 回合、客户端视觉和多个订阅账号选择仍未验证。

## 贡献

感谢 [github-liu](https://github.com/github-liu) 提供 #518 的完成卡和额度诊断，[DMarshall0114](https://github.com/DMarshall0114) 提供 #497 的 Windows 安装、编码与复测证据。问题证据与代码作者分开记录；既有作者、tag、发行与贡献记录保留。
