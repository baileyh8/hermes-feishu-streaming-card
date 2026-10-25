# PM 运行环境迁移与 wheel 来源恢复

本页描述 v5.0.8 的 PM 安装与恢复流程（#458/#459/#462）；公开可用状态以对应 GitHub Release 为准。v5.0.7 不包含下述重绑命令。

## 两类变化分别核验

Hermes PM 可以重建 venv。HFC 的 legacy observer 记录绑定 Python 启动器身份、HOME、官方插件配置 preimage/journal；即使包版本相同，新 venv 的 prefix、site-packages 或文件身份也可能变化。普通 install/restore 仍拒绝未经核验的绑定变化。

编辑 config.yaml 的无关模型配置也会改变整份文件摘要。这与 Python 绑定变化是独立触发，不能把相关配置被修改或证据损坏当成普通环境迁移。

## 安全重绑

使用实际 Hermes checkout 与 HOME，先停止正在使用这些安装记录的 Gateway、sidecar 和自动更新器，再诊断。保持原 marker、manifest、配置备份与 journal，不要手工删除保护记录或整个 `.hermes_feishu_card` 目录。

v5.0.8 提供显式 `integrity rebind-legacy`：不带 `--yes` 时只读预览，带 `--yes` 才执行。完整参数以本候选的 `--help` 为准。

```sh
hermes-feishu-card integrity rebind-legacy --hermes-dir /actual/hermes --hermes-home /actual/home
hermes-feishu-card integrity rebind-legacy --hermes-dir /actual/hermes --hermes-home /actual/home --yes
```

重绑须验证受管源码/manifest/备份、observer 记录与配置 journal、同一 HOME、已归属 PM dependency member，以及当前 runtime 的普通 wheel 来源和包资源。新 CLI 可以核验已归属的旧版本 runtime：entry point 必须与已验证的 member/wheel 版本一致，探针后再次核对 owner；普通安装探针仍要求当前版本。不得先升级 member 再绕过原绑定检查。未知来源、证据损坏、不可检查文件和并发变化继续拒绝。仅支持已核验的 decomposed 安装和可用目录描述符安全事务的平台。Windows 的重绑当前明确拒绝；非 PM 或旧 single-file 安装的未知 venv 重建不在此候选的接纳范围。

仅当插件配置分支仍等于已证明的官方写入结果时，才接纳无关配置编辑；恢复保留其它当前配置字节。插件分支修改、重复 YAML key、别名或其它无法证明归属的结构需要人工处理。重绑使用新 preimage/journal，并保留迁移前证据；失败保持可审计状态。重绑不恢复旧审批、任务执行或运行中 Gateway。

重绑成功后，若需要更换 HFC 安装来源或升级 wheel，先执行受控 restore，再 fresh install；更换来源可能先创建另一个 PM generation，直接 install 会再次触发旧 observer 的身份保护。同一已验证来源的重复安装与任意旧版升级须分别验收。restore 保留已归属的 PM dependency。源码恢复与配置重绑是不同步骤，重绑成功不能代替 hooks 恢复或服务启动验收。

独立的 `manual_review_required` fence 不因重绑而自动清除；它仍适用原来的已安装/无需恢复、停服、准确路径、身份和 CAS 校验。流式主链路工作正常也不能代替这条校验。

## PM 外部 wheel

PM dependency member 的 `tool.uv.sources` 引用 HOME 内 workspace 之外的普通 wheel。v5.0.8 使用 `.hermes_feishu_card/wheel-store/<sha256>/<wheel-name>`，按内容摘要保存不可变对象；owner schema 2 同时绑定外部路径和摘要，schema 1 的旧 member 仍可校验，并在携带准确安装来源的 ensure 流程迁移。

创建与读取校验目录身份、普通文件和摘要；POSIX 校验私有权限与属主，Windows 使用继承 ACL 的 portable 边界，不将 Unix mode 视为 ACL 证明，拒绝符号链接、Windows junction/reparse point 与不确定文件状态。相同文件名但不同内容使用不同摘要目录，不原地覆盖旧 wheel。新安装或 PM admission 失败不改写旧代引用。

finish、rollback 和 uninstall 保守保留已归属外部 wheel：旧 PM generation 可能仍引用它们。此流程不扫描未知 PM 状态，也不提供自动垃圾回收；不要手工删除仍被代次使用的对象。保存的对象不是 HFC runtime producer，不会重复注册事件。

## PM 启动与配置写入

PM generation 的解释器与执行源码目录分别核验，工作目录本身不能保证 CLI 来自所选 checkout。启动前后检查准确的 main/init 源码与目录链；所选 runtime 的 site、`.pth` 与预加载模块仍属于既有可信运行时边界，不将这个入口称为任意代码隔离。配置写入只接纳准确受检 writer 的输出；writer family 名称是源码组合标识，与 Hermes tag 不保证一一对应。私有副本按准确受检的官方初始化条件决定是否执行配置迁移，再规划插件写入；已完成当前安装身份初始化时，恢复出的旧 schema 不会被规划器擅自升级。只读核对安装身份与初始化记录，无法检查、损坏或不安全的记录继续拒绝；不执行完整初始化，不手写或猜测 schema 版本戳。官方版本戳与用户配置字段分别核对，真正无关字段仍须一致，实际落盘字节仍须与准确规划相同。

未知源码、未登记的本地执行模块及不确定配置变化继续拒绝。诊断只公开固定拒绝类别与阶段，不透出原始配置、子进程错误输出或源地址。容器镜像应按准确 digest 与源码分别验收；Git fixture 通过不代表所有官方镜像已验证。

## 验收边界

单元测试覆盖身份迁移、无关配置保留、漂移拒绝、事务失败、同名不同 wheel、路径替换与旧 schema。完整矩阵、真实 PM lock/sync、普通 wheel 子进程与源码逐字恢复须分别记录证据。报告者原环境迁移、服务重启及飞书 Gateway/model 仍需现场复测；CI 通过不替代这些结论。
