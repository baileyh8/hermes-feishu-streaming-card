# V5.0.6：安装包解压目录与元数据修复

本版处理 [#454](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/454)。v5.0.5 的 Linux/macOS tar 包包含 uid/gid 1001:1001、mode 0755 的 `./` 条目，解压器可能把这些属性写到解压目标目录。本机已在非 root macOS bsdtar 的 `-p` 模式复现 0700/1777 目标被改为 0755；报告者的 Linux root 属主变化与宿主服务故障尚未独立复测。

## 修复范围

三平台资产均使用一个与资产名匹配的版本化顶层目录：

- `hermes-feishu-card-v5.0.6-linux/`
- `hermes-feishu-card-v5.0.6-macos/`
- `hermes-feishu-card-v5.0.6-windows/`

每个目录包含原有九个文件。tar 条目的 uid/gid 统一为 0；目录和 shell 安装器为 0755，其他普通文件为 0644。ZIP 采用相同目录与文件权限约定。归档不再含直接指向解压目标的 `.` 条目。仅归零 owner 仍会保留旧目录 mode 问题，因此版本化顶层目录也是必需修复。

构建与验证共享归档契约，检查完整清单、文件字节与元数据，拒绝绝对路径、上级路径、反斜杠路径、链接、特殊文件和重复条目。实际解压回归针对 0700 与 1777 的独占 scratch 目录，核对目标 inode/uid/gid/mode、已有哨兵及展开文件，并保留旧布局负向控制。Linux root 验证仅在经归属核验的 CI scratch 内执行。

## 安装路径变化

旧包解压后可在目标目录直接运行 `./install.sh`；新包必须先进入对应平台子目录。以下 Linux 示例以已下载且通过 checksums 核验的资产为前提，版本正式发布后使用：

```bash
mkdir hfc-install-v5.0.6
# 在专用新目录中展开，不使用共享临时目录。
tar -xzf hermes-feishu-card-v5.0.6-linux.tar.gz -C hfc-install-v5.0.6
cd hfc-install-v5.0.6/hermes-feishu-card-v5.0.6-linux
./install.sh --version v5.0.6
```

macOS 进入 `hermes-feishu-card-v5.0.6-macos/`；Windows 解压 ZIP 后进入 `hermes-feishu-card-v5.0.6-windows/` 再运行 `.\install.ps1`。详见[安装说明](../README-install.md)。已有 v5.0.5 资产与 tag 保留历史，不以替换旧包掩盖问题。

## 验证范围

旧包 mode 改写和版本化目录原型的本地对照已有证据。最终候选的聚焦检查、Linux GNU tar root/非 root、macOS 实际解压、精确提交完整 CI/CodeQL、普通 wheel、公开资产与匿名下载验收，分别登记在[发布准备](release-readiness.md)；待验项不能由原型或历史 v5.0.5 的结果替代。本版准备时 Linux/root 仍待 CI 证明，未声称报告者现场已经恢复。

此改动位于打包与发布边界，未新增 Gateway/model 或真实飞书客户端验收。既有真实 Gateway/model、桌面及移动端未验范围继续保留。

## 贡献

感谢 [leavrcn](https://github.com/leavrcn) 提供 #454 问题报告和复现证据。该署名对应问题证据，代码作者以 Git 提交记录为准，不据此添加 `Co-authored-by`。发行区间为 v5.0.5 之后；既有贡献者、提交和全部历史发行说明继续保留。
