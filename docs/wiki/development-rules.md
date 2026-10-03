# 开发规则与技术栈

本页是日常开发约束；详细领域不变量见[维护指南](maintenance-guide.md)。[V5 建议](../roadmap-v5.md)中的任务体验已获准进入开发分支，当前实现与验收边界见[任务卡片](../task-cards.md)；这不授权更改其他默认值或 Python 支持范围。

## 技术栈与采用原则

| 层 | 当前实现 | 开发规则 |
| --- | --- | --- |
| Runtime | Python，asyncio，aiohttp；Python floor 以 `pyproject.toml` 为准 | 复用 Hermes 的实际解释器；不要以另一个 venv 的成功代替目标环境 |
| 配置与协议 | PyYAML；dataclass 与显式输入验证 | 在边界拒绝非法类型/字段；保留 schema 兼容与用户显式配置 |
| Hermes 集成 | 原生 plugin + 精确补丁的 Hybrid 方式 | 先验证能力，再选择 producer；同一事件不能 native/legacy 双发 |
| Feishu | HTTP client、可选 CardKit；Hermes 提供 SDK 环境 | 不在轮询诊断中安装 SDK；保留权限、限流、重试及方言边界 |
| 状态 | 内存 session + 私有有界文件检查点/账本 | 明确保留时间、容量、原子写与损坏处理；展示恢复不恢复执行 |
| 构建与检查 | setuptools；pytest；GitHub Actions/CodeQL/Dependabot | 普通 wheel 与公开安装独立验收；不把扫描无告警解释为无漏洞 |

当前未配置 formatter、linter、typechecker、coverage 门禁或性能基准。
新增工具先用独立 PR 在有限范围验证收益；禁止借工具引入全仓格式改写。
Ruff/类型检查属于后续建议，未接入前不要把它们写成已经执行的门禁。
不因“下一大版本”改用另一语言、Web 框架、数据库或消息队列；新增依赖必须说明用户收益、版本兼容、维护成本、安全更新和回退方式。

## 模块职责

优先保持五个方向：上游事件适配 → 标准事件/状态决策 → 展示模型 → 渲染/容量检查 → 外部投递。安装、配置和服务管理走独立控制路径。

- 适配器只转换可验证的事实；不用卡片文案推断执行成功。
- 状态层负责身份、幂等、终态和授权关系；renderer 不修改执行状态。
- renderer 尽量为纯函数；动画时钟可注入，快照比较不能偶然跨帧。
- Feishu I/O 层负责实际投递结果；发送成功不等于检查点成功。
- coordinator 连接这些边界，不再吸收与原职责无关的新功能。

对 `hook_runtime.py`、`server.py`、`cli.py`、`install/patcher.py` 的较大改动，先画清受影响的调用链和状态归属。新增独立职责提取成小模块；提取时先用已有事件轨迹固定结果，不同时改默认值、协议和投递语义。行数是审查信号，不是机械拆文件的目标。

## 修改分级

| 改动 | 最小验证 | 升级条件 |
| --- | --- | --- |
| 纯文档、AGENTS、路线建议 | docs/metadata、链接、关键约束及建议状态检查 | 若改动实际协议描述，核对实现与对应测试；不为文档自动发版 |
| 展示、配置 | config/render/session 与相关 HTTP 用例 | 新交互/布局要做桌面和移动端验收；默认值变化需要迁移方案 |
| 事件、投递、身份、交互 | 原始失败复现、正反路径、重复/乱序/晚到及真实回环 HTTP | 完整回归；实际客户端或 Gateway 证据分别登记 |
| 安装、进程、恢复、安全 | patcher/install/process 矩阵与安全失败路径 | 普通包实际进程、受支持平台 CI、精确恢复与完整回归 |
| 依赖、协议、持久化格式 | 兼容矩阵、旧数据/配置读写、升级降级 | 有界迁移、回退和完整发布门禁 |

不为可逆的小文档修改新增只重复实现的测试。已有安全协议、源码指纹、权限和恢复契约不能为了通过测试而弱化。

## 测试环境和已知工具限制

先执行 `python tools/preflight.py --check-only`。它只读，不启动服务，`ready` 不代表 pytest 已运行。

```bash
python tools/preflight.py --suite focused --module docs
python tools/preflight.py --suite focused --module process
python tools/preflight.py --suite focused --module runtime --module render
```

只选本次需要的组。自动映射不完整时会拒绝猜测；`AGENTS.md` 需显式选 `docs`。
组选择是起点，不保证涵盖 runner、credential file、CardKit 等所有关联测试；按[维护矩阵](maintenance-guide.md)补齐。

全量需要 `HFC_FIXED_TAG_SOURCE_ROOT` 指向 provenance 指定提交、摘要一致、工作区干净的 Git checkout。缺 fixture 的 docs 聚焦运行可以通过，但总报告可能是 `partial`；须分别记录 `pytest.status` 与 fixture 状态。

现有 preflight 会移除 `HFC_UPSTREAM_*`。因此 full 通过不能单独证明 latest stable/main 已验证；这些源码检查必须按 CI 中的固定 SHA 和环境变量单独执行，或使用已记录、隔离 HOME/state 且只转发已核验 fixture 变量的等价入口。不得用未隔离的生产 HOME 规避环境清理。将此能力正式纳入 preflight 是后续工具改进项。

开发态可使用已有 editable 环境；安装/发布验收必须验证普通 `site-packages` 的版本、实际导入位置和源码一致性。不要在同一结论中混用两个解释器的结果。

## 依赖与安全

- 依赖清单、实际安装版本、扫描告警和可达运行路径分别记录；`legacy/` 告警不能直接计入 active runtime。
- 默认不自动 dismiss 告警，不修改 archive，不升级本机生产。根据授权为 active 依赖修复开独立 PR。
- 跨 Python floor、主要 API、配置/持久化 schema 的变更需要兼容与迁移记录。安全 lower bound 依据上游公告与实测，不能只扩大为无约束 `latest`。
- secrets、控制 token、原始路由 ID、checkpoint 正文不能进入日志、诊断包、公开 fixture 或截图。
- 文件 mode 不等于 Windows ACL；本机同用户信任也不等于跨用户隔离。

## 文档和交付

代码/测试是实际行为证据，wiki 是维护入口，README 是用户入口，release notes 是历史记录。
修改行为同步当前文档；不要把历史版本说明重新描述为最新状态。
文档测试优先验证链接、配置名称、安全含义与贡献记录；避免冻结整段宣传文案或文档行数。
每个 PR 列出最重要的验收证据和未覆盖边界。保留首轮失败证据；只有明确归因后才重跑，不能用反复重跑替代定位。

每批验证结束后检查本任务创建的临时目录、复制源码、候选安装包与服务。确认没有进程或后续验收依赖后直接回收；保留失败日志、结果报告、来源哈希及必要回滚材料。最终验收结束再清理不再复用的隔离环境和任务专用依赖，托管工作树使用托管工具归档。记录准确路径与实测占用，不把归档会话当作释放磁盘。
