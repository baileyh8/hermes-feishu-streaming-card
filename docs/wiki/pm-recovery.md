# PM 运行环境迁移与 wheel 来源恢复

本页描述 v5.0.8 之后的 PM 恢复候选（#458/#459/#462）。管理 CLI、`integrity rebind-legacy` 和受控 restore 基线来自 v5.0.8；schema 1 flat wheel 接纳、精确旧声明校验和 ack 固定类别是本候选新增行为，已发布的 v5.0.8 发行说明未包含这些新增项。v5.0.7 不包含 `integrity rebind-legacy`。

## 先区分管理 CLI、runtime 与 member

重绑命令来自 v5.0.8 的 management CLI，被核验的 runtime 仍可以是旧版本。v5.0.8 CLI 会读取已归属 PM member 的 owner 版本，要求 runtime entry point、安装包资源、缓存 wheel 与该版本一致；普通 install 探针仍要求当前 HFC 版本。因此可以用 v5.0.8 CLI 核验 v5.0.7 的 owned runtime，再执行受控 restore 和升级。

不要先用 v5.0.7 CLI 尝试重绑：该版本没有这个子命令，`--help` 不会显示它。也不要在重绑前直接升级 member；member 记录钉着旧 wheel，升级后 runtime 资源与 owner 版本不一致，rebind 和 setup 会互相卡住。推荐顺序是：停止实际服务管理器与自动更新器 → 用 v5.0.8 management CLI 重绑旧 runtime → 受控 restore → fresh install 或升级 → 启动并验收。

从 `git+https://...` 安装的 distribution 不能证明旧 owned wheel 的来源。`direct_url.json` 里的 VCS 信息不等于 member 缓存 wheel 的归属；重绑要求安装来源指向已验证的 owned wheel 文件，而不是任意 Git checkout。VCS 安装、源码 checkout 和同一版本号都不是旧 wheel 归属的替代证据。

## 重绑参数与只读预览

当前 CLI 的两条示例都同时包含 `--hermes-dir` 和 `--hermes-home`。旧发布说明或第三方笔记只写一个参数，是文档遗漏，不是当前命令缺参。若旧二进制报缺少 `--hermes-home`，先用 v5.0.8 的 `--help` 确认实际入口。

```sh
hermes-feishu-card integrity rebind-legacy --hermes-dir /actual/hermes --hermes-home /actual/home
hermes-feishu-card integrity rebind-legacy --hermes-dir /actual/hermes --hermes-home /actual/home --yes
```

不带 `--yes` 时只读预览，不写 marker、journal 或配置。带 `--yes` 才在重新验证后提交；事务失败回滚，旧 preimage、journal、备份与迁移前证据保留。

重绑须验证受管源码、manifest、备份、observer 记录、配置 journal、同一 HOME、已归属 PM dependency member，以及当前 runtime 的普通 wheel 来源和包资源。新 CLI 可以核验已归属的旧版本 runtime：entry point 必须与已验证的 member/wheel 版本一致，探针后再次核对 owner。未知来源、证据损坏、不可检查文件和并发变化继续拒绝。仅支持已核验的 decomposed 安装和可用目录描述符安全事务的平台；Windows 的重绑当前明确拒绝，非 PM 或旧 single-file 安装的未知 venv 重建不在此接纳范围。

仅当插件配置分支仍等于已证明的官方写入结果时，才接纳无关配置编辑；恢复保留其它当前配置字节。插件分支修改、重复 YAML key、别名或其它无法证明归属的结构需要人工处理。重绑使用新 preimage/journal，并保留迁移前证据；失败保持可审计状态。重绑不恢复旧审批、任务执行或运行中 Gateway。

重绑成功后，若需要更换 HFC 安装来源或升级 wheel，先执行受控 restore，再 fresh install；更换来源可能先创建另一个 PM generation，直接 install 会再次触发旧 observer 的身份保护。restore 保留已归属的 PM dependency。源码恢复与配置重绑是不同步骤，重绑成功不能代替 hooks 恢复或服务启动验收。

## 停止实际服务管理器

先确认哪个服务管理器或自动更新器真正启动 sidecar 和 Gateway，再停它。不要照抄文档里的硬编码 launchd label 或 systemd unit；用户安装的 label、plist 路径和 unit 名可能不同。用 `status`、`launchctl print`、`systemctl show` 或进程来源核对实际 owner 后，停止对应的服务。

macOS 上 `launchctl kill TERM` 不够：KeepAlive 会在进程退出后重新拉起。需要按实际 label 执行 `launchctl bootout gui/$(id -u)/<actual-label>` 或等价的实际服务卸载，确认 health 不可达且 PID 文件不存在；完成后用同一实际 plist 恢复。Linux 上停止并禁用实际 unit 与自动更新器，避免 systemd 或定时器重新启动。

`integrity acknowledge-review` 的双门槛是 health 与 PID 同时满足：sidecar `/health` 必须不可达，state 目录的 PID 文件必须不存在，并在提交前后各检查一次。KeepAlive、残留进程或陈旧 PID 文件都会让 ack 保持关闭。CLI 不会替你停止用户服务，也不会复制硬编码 service 来猜 owner。

## 旧 fence 的 target CAS

重绑会生成新的 target/plan binding，旧 `manual_review_required` fence 可能仍指向重绑前的 target。ack 不会自动清除这条 fence。只有当前 recovery/integrity plan 连续两次验证为 installed、sidecar 连续两次确认停止、显式 `--yes`、旧 target 精确匹配且 fence snapshot CAS 未变化时，才允许迁移。

如果 fence 属于不同 Hermes target，ack 会要求 `--rebind-target`：

```sh
hermes-feishu-card integrity acknowledge-review \
  --config /actual/config.yaml \
  --hermes-dir /actual/hermes \
  --state-dir /actual/state \
  --rebind-target <old-64-character-target_identity> \
  --yes
```

`<old-64-character-target_identity>` 必须从 pending fence 的现有记录读取，不能用猜测值或当前 target 代替。CLI 不打印旧 identity、原始异常、私有路径或 token；错误只给固定类别。不要手工编辑 `runtime-integrity-fence.json`，不要因为流式主链路正常就跳过 fence 校验。

固定诊断类别包括：

- `integrity_ack_plan_unverified`：当前 Hermes installed plan 无法验证。
- `integrity_ack_config_unverified`：配置或私有环境无法验证。
- `integrity_ack_fence_unreadable`：fence 无法安全读取。
- `integrity_ack_sidecar_running`：health 或 PID 双门槛未满足。
- `integrity_ack_binding_changed`：两次 plan binding 不一致。
- `integrity_ack_rebind_target_required`：pending fence 属于不同 target，需要显式 `--rebind-target`。
- `integrity_ack_rebind_target_mismatch`：显式旧 target 与 pending fence 不匹配。
- `integrity_ack_fence_changed`：提交前后 fence snapshot 变化。
- `integrity_ack_refused`：其他无法归类的安全拒绝。

## PM 外部 wheel 与 schema 1 旧格式

PM dependency member 的 `tool.uv.sources` 引用 HOME 内 workspace 之外的普通 wheel。v5.0.8 使用 `.hermes_feishu_card/wheel-store/<sha256>/<wheel-name>`，按内容摘要保存不可变对象；owner schema 2 同时绑定外部路径和摘要。

早期 PR458 分支的 schema 1 使用另一种准确格式：member 是 `home/plugins/hfc-runtime-dependency`，owner 只有 `schema`、`version`、`spec_sha256`、`selection_added`、`files` 五个键；`files` 绑定 `pyproject.toml` 和同名 wheel 的摘要；`pyproject.toml` 的 path 精确指向 `<home>/.hermes_feishu_card/wheel-store/<wheel-name>.whl`，没有摘要目录。安装后的 `direct_url.json` 常见 `archive_info: {}`，没有 `sha256`。

本候选只接纳能证明的上述历史格式，证明条件是：

- owner 与 member 文件摘要一致，`pyproject.toml` 逐字等于 PR458 的声明文本。
- flat store 路径精确等于声明路径；wheel 是普通文件、属主正确、无符号链接或 reparse point、非 group/world 可写，内容摘要等于 owner 记录的 member wheel 摘要。
- installed distribution 版本等于 owner 版本；`direct_url.json` 是精确 file URL，指向同一 flat store 文件，不是 editable，`archive_info` 可以为空；已安装包资源逐项匹配 member wheel。
- observer marker、manifest、preimage、journal、源码指纹和 runtime binding 在提交前后保持稳定。

相同包资源、同名字符串、任意存在的 `.whl`、另一个路径上的同字节文件，或 VCS 安装记录都不构成来源证明。缺少 owner、声明、store 文件、摘要、资源、hooks/manifest/preimage/journal 或稳定身份时继续拒绝，分别给出 `legacy_rebind_schema1_source_unverified` 或 `legacy_rebind_wheel_resources_unverified`，并保留现场证据。不要把拒绝当成“删掉 marker 再装”的许可。

新安装与迁移使用 schema 2 摘要目录；旧 flat store 可能仍被旧 PM generation 引用，finish、rollback 和 uninstall 保守保留它。此流程不扫描未知 PM 状态，也不提供自动垃圾回收；不要手工删除仍被代次使用的对象。保存的对象不是 HFC runtime producer，不会重复注册事件。

创建与读取校验目录身份、普通文件和摘要；POSIX 校验属主与权限，Windows 使用继承 ACL 的 portable 边界，不将 Unix mode 视为 ACL 证明，拒绝符号链接、Windows junction/reparse point 与不确定文件状态。共享 `wheel-store` 父目录只有在仍包含 PR458 flat wheel 时才允许保留 0755，且不得 group/world 可写；新建 schema 2 store、没有 flat wheel 证据的父目录，以及每个 schema 2 摘要子目录仍要求 0700。

## PM 启动与配置写入

PM generation 的解释器与执行源码目录分别核验，工作目录本身不能保证 CLI 来自所选 checkout。启动前后检查准确的 main/init 源码与目录链；所选 runtime 的 site、`.pth` 与预加载模块仍属于既有可信运行时边界，不将这个入口称为任意代码隔离。配置写入只接纳准确受检 writer 的输出；writer family 名称是源码组合标识，与 Hermes tag 不保证一一对应。私有副本按准确受检的官方初始化条件决定是否执行配置迁移，再规划插件写入；已完成当前安装身份初始化时，恢复出的旧 schema 不会被规划器擅自升级。只读核对安装身份与初始化记录，无法检查、损坏或不安全的记录继续拒绝；不执行完整初始化，不手写或猜测 schema 版本戳。历史安装的 postimage 回放与当前启动条件分开：仅重放受检官方“迁移／不迁移”两个分支，结果必须逐字节匹配原 journal 的准确摘要，并保留无关字段语义检查；当前初始化记录不能冒充历史记录。官方版本戳与用户配置字段分别核对，真正无关字段仍须一致，实际落盘字节仍须与准确规划相同。

未知源码、未登记的本地执行模块及不确定配置变化继续拒绝。诊断只公开固定拒绝类别与阶段，不透出原始配置、子进程错误输出或源地址。容器镜像应按准确 digest 与源码分别验收；Git fixture 通过不代表所有官方镜像已验证。

## 验收边界

单元测试覆盖身份迁移、无关配置保留、漂移拒绝、事务失败、同名不同 wheel、路径替换、旧 schema 1 flat store、空 `archive_info`、错误 file URL、错误版本、资源漂移与权限/链接拒绝。完整矩阵、真实 PM lock/sync、普通 wheel 子进程与源码逐字恢复须分别记录证据。报告者原环境迁移、服务重启及飞书 Gateway/model 仍需现场复测；CI 通过不替代这些结论。
