# 观测耗时与 token 速率口径

## Legacy 安装的数据源（开发分支）

Hermes 0.22.x 的 legacy completion 原先只有 token 数、没有逐调用计时，因此选择 `output_rate` 也不显示。安装器先核验 v0.22.2 `post_api_request` helper 的 AST 指纹，确认逐调用 usage、时间窗口、身份和参数绑定，以及 response intake 在 provider projection 之后的唯一直接调用。核验通过后，再通过官方 plugin enable 流程写入独立的 `hfc-legacy-observer-v1` ownership 记录。只有记录与当前 source/HOME、配置 preimage/journal 匹配时才进入 observe-only。确定没有该记录的 Hybrid 路径保持原行为；marker 的 metadata 权限、I/O 或目录错误进入 disabled，不把不可检查当作不存在。

观察模式不创建 ingress binding，不启动 runtime hello/heartbeat 或 interaction listener，也不发送任何侧车事件。只有 legacy producer 发送 `message.completed`。缓存按 `(session_id, turn_id)` 区分，最多 256 项，使用 monotonic clock 保留 3600 秒；消费后保留有界去重占位，满时优先回收占位；全部为未消费观测时拒绝新项并使同 session 旧项失效，过期删除。只从 pre-LLM 开始的 turn 接受 API 观测；相同 session 存在多个未消费 turn 时全部隐藏，不猜测所属轮次。

completion preview 只检查正文和附件，不读取或消费速率。正式终局 payload 构建时用 `agent_result.session_id` 一次性取回速率和原始输出 token 合计；本轮 `agent_result.api_calls` 必须是有效正整数，零调用或未知时丢弃旧样本并隐藏速率，避免压缩前失败借用同 session 上轮观测。正向速率仅依据逐调用 usage 完整、精确输出 token 达到既有阈值、单模型/provider 和有效窗口采用。v0.22.2 缓存 Agent 的 completion token 可为会话累计值，因此不与页脚 token 比较；现有页脚计数和估算规则保持不变。页脚 `output_tokens` 可能仍显示 legacy 的会话累计值，而 `output_rate` 仅反映本轮逐调用观测，两者统计范围可能不同。跨模型/provider、usage/时序缺失、session 不匹配、插件未加载或 host 隔离均隐藏；异常不阻断正文投递。默认配置、持久化 schema 和已有速率阈值不变。

正式 payload 沿现有 exact Base stage 和终局传输重试复用；重试不再次取样，迟到或重复观测不能恢复已消费 turn。此边界不增加 metrics registry 或执行 owner。

验收覆盖独立缓存、legacy completion 到 footer、零插件事件、官方 PluginManager 加载和配置逐字恢复。真实上游加载测试通过 `HFC_LEGACY_PLUGIN_SOURCE_ROOT` 指定只读 Hermes v0.22.2 checkout；未设置时显式 skip，需要该源码的 Python 依赖。测试使用私有 HOME，不执行模型或真实飞书调用；这些测试不代表桌面/Android/iOS 验收。

开发分支在 `/health` 的 `timing` 中提供侧车受理事件的耗时统计，本地 `doctor --support-report` 保留同一组安全字段。默认卡片不增加统计行，也不为计时新增 PATCH。

| 字段 | 范围与条件 |
| --- | --- |
| `first_answer_total_ms` / `first_answer_samples` | 接受本轮 `message.started` 至首个确实增加可见回答的 `answer.delta`；每轮最多一个样本 |
| `observed_turn_total_ms` / `completed_samples` | 同一开始点至接受 `message.completed` 或 `message.failed`；包括工具、等待与调度 |
| `started_samples` | 当前仍保留在侧车会话内存中、有有效开始证据的回合数 |
| `window` | 固定为 `retained_session_memory`，清理旧会话或重启后会改变，不是累计全站指标 |
| `token_rate_status` | 保留会话中有完成卡片带精确逐调用速率时为 `available`，原因 `per_invocation`，并给出 `token_rate_samples`；否则为 `unavailable`，原因 `model_invocation_scope_missing` |

缺少开始事件、开始之前已经观察到其他活动、时钟倒退或时钟异常时不构造时长。没有流式答案的终局可以有总观测耗时，但没有首字样本。重复、乱序和终态后事件先遵守现有会话拒绝规则，不能重复记样本。计时使用 monotonic clock；显示恢复检查点不保存或恢复这些计时数据，重启不伪造开始点。

这不是模型 TTFT，也不代表客户端首字可见时间。它包含 HFC 受理事件之间的时间，无法替代模型、Gateway 和飞书 API 各自的耗时。Feishu 更新耗时和 terminal drain 指标仍沿用原来的独立口径。

[#392](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/392) 第一阶段提供可选页脚字段 `output_rate`，默认关闭。只使用 Hermes `post_api_request` 对同一 `api_request_id` 报告的 `usage.output_tokens` 和时间范围。数据来自逐调用观测，不使用正文字符数或整轮耗时估算。

- 有 `first_chunk_at` 时，取 `ended_at - first_chunk_at`，页脚显示 `~N tok/s (流式)`。协议值 `decode` 为兼容性保留；这段时间是流式窗口，首个事件可能只是 role、reasoning 或 Responses lifecycle 帧，不能解释为纯答案解码速度，也不能推出首个可见 token 时间。usage 是否包含 reasoning tokens 由 provider 的输出 token 统计口径决定。
- 没有首 chunk 时间时，取 `api_duration`，显示 `~N tok/s (含TTFT)`。Hermes 请求耗时可能包含失败重试及其等待，因此这也是观测窗口的平均速率。
- 多次调用将输出 token 相加、对应时间相加后相除；调用间的工具与等待不计入。重复请求标识只记一次，模型或 provider 切换时隐藏整轮速率，避免将混合路线的速率归到最后一个模型。
- 任一调用缺少 usage/有效时间（非有限值、时序矛盾、不大于 0 或不小于 3600 秒）、请求标识无效、MoA 聚合调用、输出少于 20 tokens 或总窗口不足 0.3 秒时整轮不显示。非法或超出显示上限的速率也隐藏，不影响正文。

速率保存在既有持久化 `tokens` 映射（`output_tps`、`rate_basis`）中，不新增检查点字段；旧检查点仍不显示速率，恢复数据在渲染和健康统计前再次校验。上面的侧车事件计时依旧不是模型速率；运行中的实时估算（第二阶段）未实现。固定 Hermes stable/main 的原始 hook 调用、每次请求的首 chunk 重置，以及首事件计时语义由 CI 源码契约检查覆盖；这些测试不代表真实模型或飞书客户端验收。
