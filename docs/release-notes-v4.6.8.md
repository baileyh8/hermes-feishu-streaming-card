# V4.6.8：macOS 安装与恢复提示

macOS 的 `setup` 在持久服务不可用时明确说明平台限制，不再建议执行仅支持 Linux systemd 的 `enable`。需要登录后启动时，用户可自行配置一次性的 LaunchAgent：使用 `RunAtLoad` 调用 `hermes-feishu-card start`，不要启用 `KeepAlive`，并使用实际的绝对程序、config、env-file 和 Hermes 路径。HFC 不负责安装、卸载或管理 launchd 服务。

缺少可验证 pidfile 时仍拒绝管理已有进程，不能据此推断进程由 launchd 托管。只有确认实际 owner 和 LaunchAgent label 后，才采用相应的手动停止步骤；其他进程由其实际管理者停止。一次性 `start` 会创建 detached 子进程，停止 LaunchAgent 本身不等于已经停止该子进程。中英文安装安全说明同步解释这两种路径。

本版仅调整 CLI 平台分支提示、文档和版本标记，保留 Linux 提示、PID/token/health ownership 校验及现有卡片行为。感谢 [coder-zhw](https://github.com/coder-zhw) 提供 [PR #347](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/347) 的 macOS 现场分析、实现和回归测试。

## 验收范围

新增验收面为 CLI 的 macOS/Linux 平台分支、未知进程 owner 的恢复提示，以及普通安装包中的相同命令路径。完整测试、平台 CI、精确合并提交、发布资产校验和公开 tag 普通安装仍是发版门禁，最终结果按 GitHub Release 的交付记录登记。

本版不以命令提示测试证明真实 LaunchAgent 安装、登录后启动或停服成功，也不新增手机视觉或飞书卡片交互验收结论。生产上线结果与安装包验证分开记录。
