# v5.0.7 — Hermes 0.22.5 兼容与回调交接

本版本处理 [#456](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/456)、[#453](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/453) 和 [#451](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/451)。当前为待验候选，最终发行及验收结果以 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.7) 为准。

## 修复与验证范围

- #456：核验 v0.22.5 的完成投递包装器、继承归属及用量限制提示顺序，保留原始投递方法、`agent:end` 和消息所有权。v0.22.4 保留独立回归。
- #453：修复当前轮思考与工具回调被后续配置覆盖的问题；保留原审批回调和缓存 Agent 的跨轮隔离。
- #451：4/5 API、footer、normal/waiter 与真实 Base 完成函数的对照均未复现重复。补充固定分类诊断以定位现场断点；issue 保持开放，不宣称真实多工具问题已修复。
- 官方配置写入：新增 v0.22.5 独立受检来源族，保留准确字节规划、官方写入对照、失败回滚与 PM 跨代 wheel 来源。
- 拒装退出码：当前源代码和隔离 CLI 对照均返回 `1`；报告的 `0` 尚未复现，不列为已修复项。

## 升级与验收

沿用实际 Hermes checkout、HOME、配置、凭据和原服务管理器。更新 HFC 后按安装器指示刷新受管 hook；未知漂移拒绝继续，不手动修改 Hermes 源码或删除 manifest。重启 sidecar 与 Gateway，使运行进程加载同一版本。

安装包继续使用 v5.0.6 引入的版本化顶层目录。解压后进入 `hermes-feishu-card-v5.0.7-<platform>/` 再运行安装器；旧版 tag 和资产保持不变。

候选需要完成局部失败回归、精确提交完整 CI、普通 wheel 与公开安装、真实 Feishu API 和资产/checksums 检查。实际 Gateway/model、报告者现场、桌面/Android 视觉与点击未验证；iOS 和新用户试用不在本轮范围。待验项不能视为通过。

## 贡献记录

感谢 [coder-zhw](https://github.com/coder-zhw) 的 #456 报告与版本对照，[Kevinzz224](https://github.com/Kevinzz224) 和 [ywarmy](https://github.com/ywarmy) 的 #453 分析及复测，以及 [coytus](https://github.com/coytus)、[Lite-G](https://github.com/Lite-G)、[leavrcn](https://github.com/leavrcn) 的 #451 报告与排查证据。这些是问题证据和分析署名，代码作者以 Git 提交为准；此前全部贡献记录保留。
