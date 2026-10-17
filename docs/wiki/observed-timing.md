# 观测耗时与 token 速率口径

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

[#392](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/392) 第一阶段提供可选页脚字段 `output_rate`，只用 Hermes `post_api_request` 对同一 `api_request_id` 报告的 `usage.output_tokens` 与该次调用的时间范围：有 `first_chunk_at` 时取 `ended_at - first_chunk_at`（`decode`），否则取 `api_duration`（`request`，包含 TTFT，页脚标注 `~` 与“含TTFT”）。各调用 token 相加、时间相加后相除，调用间的工具与等待不计入。任一调用缺少 usage/有效时间（非有限值、不大于 0 或不小于 3600 秒）、请求标识无效、MoA 聚合调用、输出少于 20 tokens 或总生成时间不足 0.3 秒时整轮不显示。速率保存在已持久化的 `tokens` 映射（`output_tps`、`rate_basis`）中，不新增检查点字段。上面的侧车事件计时依旧不是模型速率；运行中的实时估算（第二阶段）仍未实现，不会把字符估算冒充模型生成速度。
