# 开发分支的模块边界与兼容入口

本页记录 2026-10-13 重构分支已经建立的边界，不代表全部计划已经完成，也不代表公开版本已包含这些变化。总进度见[执行计划](../plans/2026-10-13-refactor-upgrade.md)。

| 职责 | 实现所有者 | 调用方保留什么 |
| --- | --- | --- |
| Feishu/CardKit 传输错误、重试与不确定结果字段 | `delivery.FeishuAPIError` | client 与 CardKit 引用同一个错误 class；client 旧公开入口直接重导出，CardKit 不反向导入 client。重试顺序与发送逻辑仍由原 owner 持有 |
| 终态 PATCH、有限重试、一次补发的顺序 | `terminal_delivery`；结果类型在 `delivery` | server 冻结目标，注入 I/O，核对 generation 后提交回执/通知/检查点 |
| 事件路由、reply alias、独立通知归属及交互回复位置 | `event_rules` | server 在原锁域读取状态并写入 alias；交互状态、runtime reservation 与直接发卡复用一次 thread/anchor 决策，纯规则不发送消息 |
| Session Authority 跨 Task terminal display carrier | `admission_completion` | 仅借原 event/marker maps；完整源与 loaded code 绑定、单用 TTL、原 row/epoch 与 route 校验；Base ledger/执行 claim 不迁移 |
| native terminal admission、重复终态与持久 fence 衔接 | `native_admission` | 只依赖原始事件、owner 快照与四方法 store port；server 保留 policy、cache 提交、HTTP/指标与 repair 调度 |
| 原 Hermes resolver 的单次认证 HTTP 请求 | `interaction_resolver` | server 在原锁内验证 owner、锁外请求、重新入锁校验对象/token/descriptor，再应用结果；指标与 HTTP 回执仍由 server 管理 |
| 选择值与表单解析 | `interaction_input` | server/SDK 继续负责身份、过期、claim 和原 Hermes resolver |
| 交互容量预检、回调拒绝分类、双时限、文本回退请求资格、审批窗口及返回后原 owner 校验 | `interaction_rules` | server 在原锁内读取 owner、执行 expiry/状态提交；身份拒绝先于投递占位，模式解析保持短路，网络返回后重新加锁校验冻结身份；重展不执行 resolver |
| 辅助交互回执的只读投影 | `interaction_receipts` | server 选择已加载配置、实际 owner/dialect，负责保存和投递；纯投影保留原内容，剥离操作/token，超限拒绝而不截断授权范围 |
| task 状态投影、颜色语义 | `card_view` | `presentation` 修改已知显示字段；不改执行或授权状态 |
| 进程观察与卡片文本的共享脱敏 | `display_redaction` | `process_activity` 和 renderer 直接调用同一纯函数；共享 key/value、CLI flag、URL query 规则，不反向导入 session/render，调用方保留原规范化和裁切顺序 |
| 正文的完整来源与显示表示 | `content_plan`、`native_tables`、`text` | renderer 组合卡片并由共享 serializer 裁决最终容量 |
| 命令卡纯构造 | `command_cards` | hook_runtime 保留 producer、callback 注册、adapter identity 与命令执行 |
| turn-local observer 序号、terminal barrier 和有界 worker | `turn_events` | PluginRuntime 保留 producer 归属、registry/map 与锁顺序；允许的 producer 名称不是排他所有权证明 |
| 只读 CLI 格式化与本地诊断导出 | `cli_reports`、`support_report` | CLI 执行诊断并决定退出码；报告默认不上传 |
| 配置默认值、阅读预设、卡片合并/规范化与精确原生聊天校验 | `config_rules` | config 只加载文件/环境并调用规则；reading 只解释来源；delivery_policy 保留运行时加载/缓存。所有者不导入这三个消费者；旧公开值/函数入口以同对象重导出保留，内部调用直接导入规则 |
| 已加载阅读默认值与下次加载比较 | `config_snapshot` | process 验证现有受管进程，server 只读取 factory 已加载值 |
| 解释器路径身份 | `runtime_ownership.python_executable_identity` | server、CLI、runner、maintenance 共用同一算法 |
| Gateway stale-result 来源契约 | `install/gateway_contracts` | patcher 保留 handler 发现、marker 校验/移除与 owned-block render/apply；契约只返回已验证位置，未知绑定按原错误拒绝 |
| Cron 交付位置与通用函数正文位置 | `install/cron_contracts`、`install/ast_contracts` | patcher 选择原 marker/render/apply 路径；媒体过滤后的插入优先级、类/模块搜索顺序与 docstring/单行正文拒绝保持 |
| Base delivery 源码家族验证 | `install/base_contracts`、`install/ast_contracts` | patcher 传入原 renderer 生成的 owned blocks，再按原事务应用 |
| portable 安装文件事务与 evidence | `install/file_transaction` | CLI 与 decomposed installer 共用；自动恢复仍要求原有更强目录绑定 |
| POSIX 目录句柄、文件/stage 身份、bound replace/unlink、回滚内容验证及 cleanup | `install/posix_transaction` | CLI、Recovery、Integrity 共用物理 I/O；各入口保留原计划、锁、fingerprint/Git、ancestry 策略和事务顺序；底层不导入 planner/CLI |
| 会话、锁及 controller 清理 | `lifecycle.CleanupState` | server 注入清理所需实时 registry 引用；lifecycle 不导入 server、不持有 Application，原身份复核、顺序与上限保持 |
| 续答消息链接验证与可选按钮预算 | `message_navigation` | 只消费本次确认的 IM 回执链接或精确位置；不查历史、不猜位置、不执行任务；server 在送达与 owner 转移后使用 |
| 当前卡片替换资格、输入冻结与回执方言选择 | `owner_display_policy` | 原 owner/generation 先核验；failed 回执先渲染再选来源；模式按原分支惰性查询；实际 PATCH 和提交留 server |
| 维护排空证据与连续空闲心跳 | `maintenance_evidence` | 原维护编排保留轮询、时间、锁、lease 与停服；每 sender 数据仍由 runtime_ownership 管理 |
| 受理事件观测耗时 | `observations` | session 只记录已接受事件；checkpoint 不恢复计时；不推导模型速率 |

## 兼容名称与清退条件

以下是导入别名或窄接线，实际逻辑只有一份。新代码直接导入实现所有者；不要复制旧实现，或让业务模块反向导入入口文件。

| 入口 | 当前消费者与保留理由 | 清退条件 |
| --- | --- | --- |
| `cli._format_*`、`_doctor_exit_code` | CLI 现有 dispatch/格式化调用与针对公开输出的测试；不是新公共扩展接口 | 相关命令整体迁移时同步改调用方和故障注入点，不为改名单独增加转发层 |
| `server` 的旧 event-rule 名称 | 现有协调流程及 HTTP 回归定位；新纯规则不依赖 server | 调用方迁到真实所有者后逐个移除未用别名，不能绕过锁内写入 |
| `server._native_handoff_ack_capable` 与 identity/metadata 导入名 | 现有 handoff begin 调用和契约测试；窄 wrapper 只提取 verifier 是否可用 | 调用迁移时保留原认证入口，不能把 capability 布尔值当作签名通过证明 |
| `server.python_executable_identity` | 既有外部导入及 `test_server` 的直接消费者 | 暂保兼容 reexport；新内部调用已直达 runtime_ownership |
| `presentation` 的 task view 名称 | 既有导入保持可用；renderer/reading/server 已直达 card_view | 确认支持范围内无导入消费者并有迁移说明后移除；不得保留两份算法 |
| `hook_runtime` 的 command builder 名称 | 现有 adapter/命令调用；注入 hook 的稳定入口不改 | 完成 producer/callback 梳理后分开审核，不能根据私有前缀认定可删 |
| `hermes_plugin_runtime.ObserverEvent`、`TurnEventCoordinator` | 既有调用和测试的稳定 import；对象直接 reexport，无 subclass/wrapper | 新独立消费者从 turn_events 导入；已有导入路径在支持周期内保留 |
| `cli` 的 file_transaction helpers | 安装/恢复计划继续使用 snapshot/read/hash/evidence；portable writer 已共享 | 已清退没有产品或测试消费者的四个导入；其余随具体消费者迁移，不保留两份实现 |
| `patcher._find_exact_base_patch_locations` | 把两个现有 renderer 的精确 owned fragments 传给纯验证器 | 当前是必要接线，不是双实现；改变生成语义必须重新证明逐字恢复 |

迁移故障注入时必须证明异常仍触及真实 I/O，不能因为 patch 了一个无人调用的旧别名就宣布测试通过。已确认的迁移回归保留首次失败与修复记录。

POSIX writer、bound helpers 和 capability 检查的产品消费者已全部改为 `posix_transaction` 模块访问，避免固定导入别名让故障注入漏掉部分调用。旧 CLI 私有名称没有产品外部消费者，未新增兼容转发层；CLI 的公开 `main` 及命令参数保持原入口。

## 仍需继续整理

server 保留原锁内的交互校验、送达后身份复核及状态提交，理由和验收映射见[交互协议](interaction-protocol.md)。这些协调步骤不因仍位于 server 就需要另建服务。hook producer/SDK 接线、CLI 生命周期与 Gateway/cron 来源家族按实际职责和来源证明继续审查；不以拆完所有函数作为目标。Android 与桌面真机、Bailey 本人上手和完整候选验收仍分别留在计划中；按 2026-10-14 的授权调整，不要求 iOS 与五位新用户试用，本页结构说明不能替代其余验收。

native admission 的有 generation fence 与终态去重仍先于 policy；无 generation 的 legacy lifecycle 准备仍在 policy 之后。返回值分开保存 restore 时的 cache 更新和 expire 后的 selected record，后续拒绝也不能丢弃已发生的 cache 更新。四次 store 操作沿用各自的原子机制，不被描述为一个新的跨调用事务。

已验证的恢复证据删除也由 `posix_transaction` 持有目录句柄：CLI 只选择有序路径、保留原无 identity 的 legacy 分支。执行器先绑定并核验全部目标，再逐项删除，finally 关闭所有句柄；不把多文件删除称为原子事务。旧 `_safe_unlink_restore_evidence` 无外部消费者，随实现迁入实际所有者，不新增 CLI 转发别名。

交互 resolver 只返回受限结果，不接收 app/session、不重试、不更新授权。取消原样传播，由 server 记录取消指标；网络成功仍须通过原身份复核，不可据此直接写入新一轮交互。

resolver 按原 2 秒预算读取完整响应，最多缓存 513 字节；未到 EOF 的合法 JSON 前缀不作为成功回执。失败统计由调用方每次请求记录一次，连接清理异常不会重复计数。

交互容量预检由 `interaction_rules.interaction_preflight_rejection` 持有：仅对交互请求复制原会话或创建未注册的预演会话，按原 sequence 规则 apply，再调用注入的展示函数。重复事件不渲染；非 card 结果拒绝接管。server 保留配置读取、原锁、拒绝指标、HTTP 响应及后续实际 apply，预演对象从不成为真实 owner。

`DrainEvidence` 只保存已验证的 owner、sequence 和活跃数量，`DrainProof` 只保留上一个有效空闲样本；无效或忙碌样本清空证明，同 owner 严格递增才能放行。重复或倒序样本沿用原先“替换前一样本”的语义，不增加最大序号策略。多个 live Gateway 的 conflict 仍拒绝维护，不代表已实现跨 Gateway 协同更新。

已清退仓库内无产品或测试消费者的五个私有导入别名：`server._session_alias_keys_for_event`、`cli._assert_restore_directory_ancestry_unchanged`、`hook_runtime._model_picker_provider_options`、`_model_picker_model_options`、`_model_picker_current_provider_slug`。实现仍分别归 event_rules、install.file_transaction、command_cards；有消费者或明确兼容合同的 reexport 保留。

清理 view 只借用现有 registry 对象，单次同步遍历不创建第二套缓存或持久化状态。server 在各调用入口从当前 AppKey 组装 view，lifecycle 继续复核 session/controller/lock 对象身份并保留活跃发送和锁用户；不把整个 Application 作为通用上下文传递。

## Recovery / Integrity 的共同物理 I/O

`BoundTarget`、`BoundStage` 与读取、staging、replace/unlink、cleanup primitive 的唯一实现位于 `install/posix_transaction.py`。Recovery/Integrity 的私有 `_bind_target`、`_stage_text`、snapshot adapters 只选择原 refusal 类型和上下文；Recovery `_atomic_replace` 保留既有故障注入消费者的薄委托，staged.target_binding 绑定原 destination，不是授权证明。没有消费者的 `_open_staging_parent`、`_controlled_ancestry_paths`、`_read_directory_identity` 私有副本已移除。新的物理故障注入指向实际 I/O owner。

两种恢复入口仍严格拒绝不支持 secure dirfd 的平台，CLI 原 portable fallback 仍由 `file_transaction` 持有。恢复阶段先完成原 ancestry/prewrite checks 再读取目标；CLI bind 时读取 initial bytes/mode 的原时点保留。恢复 text stage 持有 dup fd，保留 write/flush/fsync/fchmod 顺序；CLI bytes stage 借用 binding fd，保留 write/fchmod/close。原 bytes/newline、mode、非 UTF-8 拒绝、absence 与不可检查 metadata 的区分不变。CLI 允许原本不存在的 ancestry 时点没有放宽给恢复 planner。

Recovery 的 fingerprint 复算、写入优先于删除、final sweep 与逆序回滚，以及 Integrity 的 Git/provenance、pre_commit_validate/post validate 和错误包装仍在各执行器。只在本次写入 identity/digest 尚匹配时回滚；替换目录或并发编辑的文件不能被当前 lexical path 覆盖，未知 inode 的 stage 不清除。底层收敛不等于三个恢复计划或 portable/POSIX 策略合并。

本职责包的产品实现需独立验证 Recovery、Integrity 和真实 CLI 三个原入口；既有 CLI writer 绿色证据不能代替另两入口。源码、普通 wheel 和平台验收仍各自登记，不由本页声明通过。

异常 cleanup 的资源释放是单独的行为修复：`cleanup_transaction_resources` 在某个 stage cleanup 或 binding close 失败时，继续处理其余本次已取得资源，最后传播第一原异常，不改异常类型/原因。未知 inode 的临时文件继续保留，拒绝/rollback/授权门禁不变；CLI 的 borrowed binding 只由原所有者关闭。独立测试应在前一个纯收敛提交复现“后续 fd 未关闭”，再在修复提交证明红转绿。

## callback 目标的唯一性

开发候选的 callback patcher 对 legacy 与 TurnRunner 共用唯一候选选择：先保留已有 required-call 优先级，再要求最终只有一个目标。唯一候选的兼容 fallback 保持；多个同等候选时不猜测第一个。owned marker 只能证明块归属，不能证明 callback 仍然接线，因此每次 apply 都移除原块并重新验证目标，包括没有 required calls 的 progress/interim 路径。

无法确认的 optional seam 保持未注入，required seam 仍由已有安装门禁判定。marker/render/apply/remove 继续由 patcher 负责，没有新增 producer 或执行状态。该行为修复独立于前面的结构提取，完成情况以精确候选的独立歧义、旧 marker、TTS 优先级、实际 callback 执行及幂等恢复测试为准。


## 安装来源证据与事务执行

`install/source_layout.py` 持有已知 decomposed targets、布局与 V4 标识；通用安装 manifest 当前版本仍是 V3。V4 的完整结构验证在 `manifest.validate_decomposed_manifest`，不依赖安装或恢复入口。

`decomposed_ownership` 持有只读 snapshot、指纹、backup/source 校验、可逆渲染与来源证明。它不导入 detect、recovery 或 integrity，不持有文件锁或执行写入。detect 与 ownership 共同消费 patcher 的完整 marker 描述。Git 查询由 `git_evidence` 执行，保留原来的五秒预算、精确 root/HEAD/blob 检查和错误语义；`errors.IntegrityRepairRefused` 在 integrity 直接重导出，仍是同一个 class。

`recovery` 是两个来源家族的计划与事务执行所有者。原 decomposed 的完整 plan/install/restore 实现迁入该模块；锁内 detection、两次 fingerprint、全部 staging 后复核、提交顺序和回滚位置保持。显式 portable 安装与强目录绑定的自动恢复/卸载继续使用不同准入条件。

`decomposed` 仅直接重导出既有名称，供已有安装测试与导入消费者兼容；产品内部直接调用 ownership 或 recovery。后续确认支持周期内无旧导入消费者后才能清退，不能恢复一份 wrapper 算法。故障注入应指向 `decomposed_ownership.render` 与 `git_evidence._run_git`，物理 I/O 与 Recovery 的原 fault seams 不迁移。源码、普通 wheel、实际安装恢复和平台验证分别登记，不由结构说明推定通过。

安装包入口保留原 `__all__`，通过按需解析返回原实现对象；导入只读叶模块不再预先加载 detect/recovery。包级名称、对象身份和 `dir` 可发现性保持，未知名称仍抛 AttributeError。


## 既有 legacy owner 的展示组合

`existing_owner_projection.project_existing_owner` 持有完整方言与容量选择：普通卡原样返回、优先保留安全的实时输入卡、使用原正文投影拼接 legacy 回执、超限时保留完整原回执并返回 native/deferred-native 结果。原始超限 inspection 与原因继续随结果返回，不把安全占位卡冒称完整答案已送达。

server 只适配本轮真实 owner message、session 与配置。实时卡和标题按原分支惰性获取；无 legacy/schema2 组合时不读取两者，安全实时卡不查询 fallback 标题。projection 不持有 app/session/注册表，不执行 I/O、确认授权或提交 owner 变更；原锁、投递与检查点顺序保留。未知或旧交互不能由静态回执恢复执行。

## Canonical turn carrier

`canonical_turn.py` owns the existing thread/task ContextVar, bounded registry, opaque tokens and weak Agent metrics reference. `hook_runtime` keeps direct compatibility exports for installed hook call sites; the plugin reads the same carrier directly, without importing the hook adapter. Publication, copied-context restrictions, reset revocation and clear order are unchanged. There is no second registry or authorization path. The private registry-limit fault injection belongs to this implementation module.
