# PM 运行环境迁移与 wheel 来源恢复

本页描述 #459 / #458 的修复候选；尚未随公开版本发布，不能用已发布的 v5.0.7 执行下述新命令。

## 两类变化分别核验

Hermes PM 可以重建 venv。HFC 的 legacy observer 记录绑定 Python 启动器身份、HOME、官方插件配置 preimage/journal；即使包版本相同，新 venv 的 prefix、site-packages 或文件身份也可能变化。普通 install/restore 仍拒绝未经核验的绑定变化。

编辑 config.yaml 的无关模型配置也会改变整份文件摘要。这与 Python 绑定变化是独立触发，不能把相关配置被修改或证据损坏当成普通环境迁移。

## 安全重绑

使用实际 Hermes checkout 与 HOME，先停止正在使用这些安装记录的 Gateway、sidecar 和自动更新器，再诊断。保持原 marker、manifest、配置备份与 journal，不要手工删除保护记录或整个 `.hermes_feishu_card` 目录。

候选提供显式 `integrity rebind-legacy`：不带 `--yes` 时只读预览，带 `--yes` 才执行。完整参数以本候选的 `--help` 为准。

```sh
hermes-feishu-card integrity rebind-legacy --hermes-dir /actual/hermes --hermes-home /actual/home
hermes-feishu-card integrity rebind-legacy --hermes-dir /actual/hermes --hermes-home /actual/home --yes
```

重绑须验证受管源码/manifest/备份、observer 记录与配置 journal、同一 HOME、已归属 PM dependency member，以及当前 runtime 的普通 wheel 来源和包资源。未知来源、证据损坏、不可检查文件和并发变化继续拒绝。仅支持已核验的 decomposed 安装和可用目录描述符安全事务的平台。Windows 的重绑当前明确拒绝；非 PM 或旧 single-file 安装的未知 venv 重建不在此候选的接纳范围。

仅当插件配置分支仍等于已证明的官方写入结果时，才接纳无关配置编辑；恢复保留其它当前配置字节。插件分支修改、重复 YAML key、别名或其它无法证明归属的结构需要人工处理。重绑使用新 preimage/journal，并保留迁移前证据；失败保持可审计状态。重绑不恢复旧审批、任务执行或运行中 Gateway。

成功后重新运行普通 install 或 restore；源码恢复与配置重绑是不同步骤，重绑成功不能代替 hooks 恢复或服务启动验收。

## PM 外部 wheel

PM dependency member 的 `tool.uv.sources` 引用 HOME 内 workspace 之外的普通 wheel。候选使用 `.hermes_feishu_card/wheel-store/<sha256>/<wheel-name>`，按内容摘要保存不可变对象；owner schema 2 同时绑定外部路径和摘要，schema 1 的旧 member 仍可校验，并在携带准确安装来源的 ensure 流程迁移。

创建与读取校验目录身份、普通文件和摘要；POSIX 校验私有权限与属主，Windows 使用继承 ACL 的 portable 边界，不将 Unix mode 视为 ACL 证明，拒绝符号链接、Windows junction/reparse point 与不确定文件状态。相同文件名但不同内容使用不同摘要目录，不原地覆盖旧 wheel。新安装或 PM admission 失败不改写旧代引用。

finish、rollback 和 uninstall 保守保留已归属外部 wheel：旧 PM generation 可能仍引用它们。此候选不扫描未知 PM 状态，也不提供自动垃圾回收；不要手工删除仍被代次使用的对象。保存的对象不是 HFC runtime producer，不会重复注册事件。

## 验收边界

单元测试覆盖身份迁移、无关配置保留、漂移拒绝、事务失败、同名不同 wheel、路径替换与旧 schema。完整矩阵、真实 PM lock/sync、普通 wheel 子进程与源码逐字恢复须分别记录证据。报告者原环境迁移、服务重启及飞书 Gateway/model 仍需现场复测；CI 通过不替代这些结论。
