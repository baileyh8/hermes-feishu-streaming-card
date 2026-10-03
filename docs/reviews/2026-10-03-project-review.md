# 项目代码审查 · 2026-10-03

## 结论

HFC 已有较完整的投递、授权、安装恢复和回归测试基础；当前最需要改善的是核心代码集中度、变更反馈速度及配置的可解释性。建议保留 Python sidecar 架构，在交付用户功能时逐步拆出明确边界，下一阶段以[任务体验升级](../roadmap-v5.md)为主线。

这是一轮基于代码、测试、CI、依赖告警和现有使用流程的工程审查，不是完整安全审计，也没有用户研究样本。下文区分已确认事实、工程判断和待验证事项；不以测试数量或文件行数生成虚假的“健康分”。

## 范围与证据

- 基线：HFC **v4.6.13**，提交 [b0db6a55e8f5](https://github.com/baileyh8/hermes-feishu-streaming-card/tree/b0db6a55e8f5fda36e14127c6b0ef76b047aa63c)；指标均从这个基线计算，不包含本次新增文档。此次交付范围仅为审查、规则与版本建议。
- 方法：包结构与 AST 盘点、热点代码/对应测试定向阅读、Git 变更频率、配置解释器验证、preflight 纯函数行为检查、GitHub CI/issue/PR/告警只读核对。
- 今天重新确认该基线的 tests、CodeQL、Dependency Graph CI 成功。2026-10-02 的已有发布验收记录为 **4368 passed / 12 skipped**，并记录了普通安装包与真实 macOS 子进程验证；这是历史基线证据，不冒充本次重新执行。
- 今天观察到 Hermes stable 为 **v2026.9.24 / 0.21.5**，main 为 [c8301ea6c9b7](https://github.com/NousResearch/hermes-agent/commit/c8301ea6c9b797184df16a9c5dd462400b264ff4)。HFC 已验证的 main fixture 仍为 **7817bf522af3**；观察到更新不等于新提交兼容测试通过。
- 未升级或修补本机生产 Hermes；未进行真实 Gateway/model 执行或飞书桌面、Android、iOS 视觉验收。已有 macOS 进程测试不能代替 Termux 或移动端证据。

## 代码健康度

### 1. 核心协调器集中，是后续开发的首要风险

| 基线指标 | 结果 |
| --- | ---: |
| 包内 Python 文件 | 88 |
| 排除 provenance 后的 active Python 文件 | 63 |
| active Python 物理行数 | 71,651 |
| 测试 Python 文件 / 物理行数 | 148 / 93,198 |
| docs 下 Markdown 文件 | 300 |
| 最大四个 active 文件合计 | 32,318 行，占 45.1% |

物理行数包含注释与空行。provenance 是固定上游证据，不计入维护业务代码；测试行数不表示分支覆盖率。

| 文件 | 物理行数 | 2026-09-01 至基线的变更次数 | 主要压力 |
| --- | ---: | ---: | --- |
| `hook_runtime.py` | 11,961 | 42 | 事件抽取、adapter 包装、命令、交互与多版兼容共存 |
| `server.py` | 8,748 | 37 | 状态、身份、渲染、投递、恢复集中 |
| `cli.py` | 6,438 | 10 | 安装、诊断、进程和恢复等控制路径 |
| `install/patcher.py` | 5,171 | 25 | 多源码布局的精确识别、修改和撤销 |

变更次数按非 merge commit 中的文件出现次数统计，包含 10 月初，不是严格的“9 月数据”。

尤其是 [server.py 的 `_apply_event_locked_inner`](https://github.com/baileyh8/hermes-feishu-streaming-card/blob/b0db6a55e8f5fda36e14127c6b0ef76b047aa63c/hermes_feishu_card/server.py#L4813)，单个函数 1,219 行，连接身份决策、终态处理与锁外投递任务。代码已明确锁内状态与锁外 I/O 的要求；拆分必须保留这条契约。

**判断：** 大文件与高频改动会提高审阅和回归成本，但不足以证明存在性能问题。优先按事件适配、状态决策、展示模型和投递拆出可测试边界；不要以削减行数为目标开展全仓重写。

### 2. 回归基础强，反馈与证据边界仍需改进

现有 CI 包含 Linux Python 3.9–3.12、macOS 3.12、Windows 选定可移植用例，以及独立上游源码、Feishu SDK、Docker、PowerShell、CodeQL 检查。上游源码任务使用 3.12/3.13，不能据此称全部功能已支持 3.13。

基线 tests workflow 从 00:39:07Z 到 01:07:40Z，约 28 分 33 秒；这是完整 workflow 耗时，不是某个单元测试性能。当前没有配置 formatter、linter、typechecker、coverage 门禁或可重复的性能基准。

[preflight](https://github.com/baileyh8/hermes-feishu-streaming-card/blob/b0db6a55e8f5fda36e14127c6b0ef76b047aa63c/tools/preflight.py#L131) 有两项确认的限制：

- `AGENTS.md` 及 63 个 active Python 文件中的 40 个没有自动分组选项，例如 CardKit、runner、notice producer；工具会停止并要求明确选组，**不会悄悄通过**。应完善路由并保持未知文件拒绝猜测。
- `child_environment()` 清理全部 `HFC_*`，只恢复固定 tag fixture，因而清掉 `HFC_UPSTREAM_*`。本地 preflight full 不能单独证明 latest stable/main 用例运行；需使用独立 pinned CI matrix 或受控隔离入口。

今天已把以上限制和正确使用方式写入 AGENTS 与开发规则。改进工具本身、建立性能基线和增量静态检查仍属后续工作。

文档测试有 3,529 行，承担链接、版本、安全协议和贡献记录等有效约束，也存在大量文本断言。后续应保留语义契约、减少对宣传措辞的冻结；本轮没有通过删除断言来绕开文档验证。

### 3. Python 支持范围与依赖基线需要收敛

当前 [pyproject.toml](https://github.com/baileyh8/hermes-feishu-streaming-card/blob/b0db6a55e8f5fda36e14127c6b0ef76b047aa63c/pyproject.toml) 仍为 Python `>=3.9`，依赖 `aiohttp>=3.9`、`PyYAML>=6.0`。Python 3.9 已于 2025-10-31 结束支持，3.10 于 2026-10-01 结束支持。[Python 官方生命周期](https://devguide.python.org/versions/)

今天的 [Hermes main 依赖声明](https://github.com/NousResearch/hermes-agent/blob/c8301ea6c9b797184df16a9c5dd462400b264ff4/pyproject.toml) 为 Python `>=3.11,<3.15`，Feishu extra 使用 `lark-oapi==1.6.8`。继续为旧 Python 扩展新功能会增加与 Hermes 联调及安全依赖升级的成本。

**建议：** V5 评估 Python floor 3.11、默认开发环境 3.12；先测支持矩阵和迁移，再更改声明。依赖 lower bound 依据漏洞修复、兼容性与最低版本测试重新确定；本轮未改 floor、依赖版本或 CI 矩阵。

### 4. 告警集中在归档清单，不能混算运行时风险

2026-10-03 GitHub 查询结果：

- 13 条 open Dependabot 告警，全部为 low、全部是 `aiohttp`、全部来自 `legacy/sidecar/requirements.txt`；该归档清单锁定 3.9.5。
- 0 条 open CodeQL 告警。它只描述该扫描入口的当前结果，不证明不存在漏洞。
- 本次检查所复用的 v4.6.13 普通 wheel 测试环境安装的是 aiohttp 3.14.3、PyYAML 6.0.3；这不是所有用户环境或生产环境的版本证明。

归档告警与 active 依赖的宽松下限应分别处理。建议为 active 安装建立最低安全版本策略和更新验证；归档处理单独评审。本次没有修改 `legacy/`、dismiss 告警或扩大生产变更。

### 5. 配置与当前文档有可确认的理解成本

示例配置含 20 个顶层 card 字段，而 [reading explainer](https://github.com/baileyh8/hermes-feishu-streaming-card/blob/b0db6a55e8f5fda36e14127c6b0ef76b047aa63c/hermes_feishu_card/reading.py#L38) 解释 13 个阅读字段，其中不含 `width_mode`。这是有效配置解释覆盖范围有限，不能称为“全部配置失效”。V5 应统一外观配置的有效值、来源和实际加载状态。

本轮已修正文档漂移：

- 架构图补齐 native plugin + 精确兼容 hook 的 Hybrid 路径，历史 fixed-tag 数量不再代表所有 Hermes 版本。
- 区分普通卡片的展示检查点与执行恢复；明确 CardKit 实体仍是进程内状态。
- 明确普通 create/reply 的 UUID/三次尝试，以及 terminal 专用传输重试；不再笼统写所有 `/events` 都不重试。
- 将 wiki 的 V4.6.4 入口标明历史资料，并链接当前开发规则、功能规则和本报告。
- 个人记忆/外部 wiki 的同步改为明确要求后进行，不作为项目工作的隐含步骤。

### 6. 用户补充：非 GPT 模型误显 Codex 周额度

基线 [`_populate_subscription_usage()`](https://github.com/baileyh8/hermes-feishu-streaming-card/blob/b0db6a55e8f5fda36e14127c6b0ef76b047aa63c/hermes_feishu_card/server.py#L7483) 只检查完成态与页脚开关，没有检查模型归属；renderer 也直接使用额度字符串。因此 DeepSeek、MiniMax 等卡片会显示另一个 Codex 账户的 `5h / weekly` 额度。

建议的验收规则是：只有明确归属对应订阅渠道的 GPT 轮次才查询和显示；其他 provider、未知来源以及晚到的旧模型结果不能沿用额度。裸 GPT 名称不足以区分 API key 与订阅。实现前需核对各 Hermes 路径提供的模型/渠道证据，避免误隐藏合法使用场景。

在模拟固定账户额度的隔离复现中，11 个断言确认了非相关模型仍显示额度，包括本机 HTTP 完成卡与 renderer 缓存场景。步骤是启用 `footer_fields: [model, subscription_usage]`，发送非 GPT 模型的完成事件，再检查最终卡片与额度查询行为；未访问真实账户。按用户要求，本轮只登记问题与准入规则，工作区不保留运行时或测试修改；不得将该问题标记为已修复或已发布。

## 功能与开发规则的落地

根目录 [AGENTS.md](../../AGENTS.md) 保持简短入口，详细约束放在[开发规则](../wiki/development-rules.md)和[功能准入规则](../wiki/feature-rules.md)，避免把每次审查的数据不断塞进长期指令。

必须长期保留的边界是：Hermes 负责执行与授权；同一事件/卡片有唯一 owner；未知投递路径 fail-open，安装与授权 mutation fail-closed；最终答案和授权范围完整；所有状态与重试有界；源码、普通包、真实平台、客户端视觉证据分别记录。

| 顺序 | 后续工作 | 完成证据 |
| --- | --- | --- |
| 后续修复候选 | 额度归属修复；#344 审批正文缺失的复现 | 卡片结果回归；#344 未复现前不关闭 |
| 先做 | 核验新 Hermes main，保持 stable/main 精确 SHA 矩阵 | 固定源码安装、重复安装、拒绝漂移及精确恢复通过 |
| V5 前置 | 完善 preflight 分组/上游 fixture 入口，明确 Python 迁移 | 新增文件不会漏选；明确 skips；最低及目标版本验证 |
| 随功能推进 | 提取展示模型、状态决策和 I/O 边界，增加有限静态检查 | 同一事件轨迹结果不变，失败/乱序/晚到回归通过 |
| 产品主线 | 任务可读性、交互反馈、有效配置预览 | 见 V5 的真实场景与多端验收门禁 |
| 单独决策 | legacy 告警处置、较大 PR 拆分或重品牌 | 范围和收益明确后另行处理 |

GitHub 尚未关闭的问题不等于均未修复：#367 等已有版本修复和回复，仍需对应环境反馈；#344 仍缺少可确认的复现。宽范围 PR #357 不宜与体验版本整包合并。问题/PR 状态为当天快照，执行前应刷新。

推荐的下一版本范围、技术取舍和可衡量验收见 [V5 路线建议](../roadmap-v5.md)。

## 本次文档验收

- 隔离 preflight 的 docs/metadata 组：**102 passed、0 skipped**；固定 Hermes fixture 的 9 个文件及 Git 身份核验通过。
- 本次 9 份 Markdown 的 69 个本地链接目标存在；`git diff --check` 通过。
- active runtime、tests、配置、版本和用户手册与审查基线无差异。本次未重跑完整运行时套件；未修改生产安装，也没有发布版本。
