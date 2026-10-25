# v5.0.8 — PM 安装与迁移恢复

本版本合并 [#458](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/458)、[#461](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/461) 的外部 wheel 与安全重绑，并处理 [#462](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/462) 的 PM 启动与官方镜像安装反馈。公开版本及最终验收以 [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.8) 为准。

## 变化

- 外部 wheel 位于 workspace member 外的 SHA256 目录，owner 绑定准确路径、摘要和包资源；缺少 archive hash 时只从已验证的准确 file URL 补读。不因文件存在就信任来源。旧代对象保留，拒绝 symlink、Windows junction/reparse point 及未知文件状态。
- `integrity rebind-legacy` 默认只读预览，显式 `--yes` 才重绑；重新验证同一 HOME、源码、受管 hooks、manifest、备份、observer 记录和普通 wheel。保留无关配置及旧 journal/preimage；已归属的旧 wheel 按其准确版本核验 entry point，并再次核对 owner；普通安装仍要求当前版本。默认 install/restore 继续严格拒绝漂移。仅支持已核验的 POSIX PM decomposed 安装，Windows 和旧 single-file 会明确拒绝。
- 官方 enable 子进程从所选 checkout 加载 CLI，核验 main/init 摘要及目录链，避免 PM workspace 改变安装 key。单独设置 `HERMES_INSTALL_ROOT` 不能代替入口来源检查。
- 新增 v0.22.1 对应的准确 writer 组合，依据所选 checkout 的安装身份及官方初始化记录决定是否在私有副本执行受检迁移，规划 writer 写出的准确 schema 版本戳，其他配置字段仍须匹配，实际落盘字节仍须等于规划。family 名称与 tag 不保证一一对应。
- 官方 0.22.5 镜像把 Python 标准库放在 Hermes 树内；配置规划将所选解释器的准确 sysconfig 库根与 Hermes 源码分别处理，保留未知项目模块和不安全路径拒绝。诊断仅公开固定类别，避免泄露配置、私有路径、源地址或原始错误输出。

## 升级与恢复

使用实际 Hermes checkout、HOME、配置及现有服务管理器。停止相应 Gateway/sidecar 和自动更新器后先诊断；不要删除 marker、manifest、preimage 或 journal 来绕过保护。安全重绑命令和条件见 [PM 恢复](wiki/pm-recovery.md)。

更换安装来源可能在 observer 检查前创建另一个 PM generation。需要恢复旧绑定时，按“显式核验并重绑 → 受控 restore → fresh install”的顺序处理；同一 wheel 的重复安装证据不能代替所有旧版升级验收。独立的 `manual_review_required` fence 不会由重绑自动清除，仍需独立身份/CAS 及停服检查。

归档保留版本化顶层目录；解压后进入 `hermes-feishu-card-v5.0.8-<platform>/` 再运行安装器。历史版本和资产保留。

## 验收记录

本地失败复现、普通 wheel/实际 PM、固定官方镜像与精确 CI 分别留证；发布前核验候选/merge/tag、CodeQL、公开下载、checksums、完整归档与实际解压。最终结果与精确提交链接记录在 Release；没有对应证据的边界不记为通过。

报告者原环境迁移、实际 Gateway/model、飞书 API 与桌面/Android 视觉或点击没有新增验收证据；本次为安装与恢复改动。iOS 和新用户试用不纳入本轮范围。四个镜像的报告不推断全部 0.22.x 版本均有同一故障。

## 贡献

感谢 [mingyangtech666](https://github.com/mingyangtech666) 提供 #458 的外部 wheel 原实现，[ywarmy](https://github.com/ywarmy) 提供 #459 迁移与配置恢复证据，[mouyong](https://github.com/mouyong) 提供 #462 的 PM 与官方镜像对照。原始提交、作者和此前贡献记录保留；问题证据署名与代码作者分开记录。
