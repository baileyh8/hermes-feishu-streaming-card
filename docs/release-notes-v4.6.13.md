# V4.6.13：控制凭据不再出现在进程命令行

修复 #367：受管 sidecar 启动改用 `--token-file`，命令行和 transient systemd 的启动参数只包含文件路径。控制 token 保存在私有 state 目录的 `sidecar-control.token`，原子写入、POSIX 权限 0600；读取拒绝链接、非普通文件、错误所有者、宽松权限、过长或非法内容。

- 凭据文件不可安全写入或读取时明确失败，不回退到明文参数；不改变 PID/token/health 归属校验和认证停止语义。
- 文件保留供 systemd 自动重启读取，下次受管启动旋转内容；detached、显式 systemd user/system 与 persistent 服务的启动路径均使用文件。旧 `--token` 仅为手工调用兼容而保留，不可与 `--token-file` 混用。
- 升级后通过原有服务管理入口重新启动 sidecar，已运行进程的旧 argv 不会自动消失。同一 OS 用户仍在本地信任边界内；本修复减少命令行、进程列表与截图暴露，不宣称防御同用户文件读取。Windows ACL 隐私仍沿用既有平台边界，POSIX mode 不代替 ACL 验证。
- 保留 PR #366 的动画帧稳定性修复。Hermes 稳定版仍是 v2026.9.24 / 0.21.5；本轮 main 源码快照为 `7817bf522af3caf54b30ae59f16157469d7638fc`。

感谢 [ffdxdynotable](https://github.com/ffdxdynotable) 在 [#367](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/367) 报告控制 token 的命令行暴露，提供 Termux 现场证据与私有文件方案。 保留全部历史贡献记录；报告者提供的是问题证据和方案，不冒充本仓库代码作者。

最终完整 CI、普通安装包、真实子进程启动/认证停止和发布资产验证见 GitHub Release。本机生产、Termux 真机与真实飞书 Gateway 任务未升级或验收。
