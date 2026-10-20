# CI 回归分级与时间预算

单轮完整 CI 的目标为 20 分钟内。这个目标包含 fixture 准备、测试和结果核对；GitHub runner 排队仍计入实际用时。设置 25 分钟的任务超时仅用于限制失控任务，超时不算通过。首次实测前不能宣称已达成目标。

## 选择回归范围

`tools/ci_plan.py` 对 PR 的 base SHA 到实际测试 HEAD 做完整路径比较，删除与新增分别计入。不会使用 GitHub API 的截断文件列表，也不会因为路径过滤而漏发 required check。

| 变更 | 自动选择 |
| --- | --- |
| 仅文档、README、AGENTS 等已登记文档 | Python 3.12 的 docs/metadata 检查 |
| 仅涉及已登记的 render、appearance、quota、config 低风险组 | Python 3.12 的相关测试，并带 docs/metadata |
| 事件、身份、交互、投递、安装、进程、依赖、测试或 CI 工具变更 | 完整矩阵 |
| 未知文件、映射缺失、删除的源码、无法比较 base | 完整矩阵或直接阻断；不能退化成空检查 |
| main push、发布 workflow 的 exact commit 输入 | 完整矩阵 |

影响范围取自 `tools/preflight_routes.json`。一个文件同时属于高、低风险组时运行完整矩阵。聚焦 PR 的摘要会记录 `docs` 或 `focused`，这些结果不能当成对应 Python 版本或 macOS 的全量通过证据。人工判断可以扩大范围；真实客户端、Gateway/model 和公开安装验收仍按原规则执行。

## 完整矩阵如何并行

保留 Linux Python 3.9–3.14 各版本的全部 pytest；macOS 3.12 仍运行全部 pytest。Windows portable、SDK、PowerShell、Docker 和 CodeQL 门禁保持原覆盖。

每个 Linux 版本分成八片，由两个独立 runner 各运行四个 pytest 进程；macOS 保持六片，两个 runner 各运行三个进程。各进程使用独立 Git clone、HOME、HFC state、TMPDIR 和 pytest basetemp；安装测试构建 wheel、写源码和元数据时不会竞争同一个 checkout。只借用 CI 准备的只读固定源码，以及预先构建的普通 wheel/runtime。

Hermes 3.12/3.13 兼容矩阵同样各拆八片。公开仓库的 Ubuntu runner 有 4 核、macOS arm64 有 3 核，进程数按[官方资源规格](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories)分配，不增加付费 runner。原来的逐源码安装恢复、官方 plugin 启用/配置字节、loader、rate 与 PM generation 检查分别进入用例集合，不再在单个进程中依次等待。固定上游 SHA、摘要校验、普通 wheel 和真实子进程边界保留。

`tools/ci_shard.py` 按收集到的完整 node ID 集合分配用例，使用 `tools/ci_durations.json` 中的历史耗时做均衡。耗时文件仅影响调度，不是测试白名单。新增和参数化用例自动进入集合，未登记耗时的用例也会运行。慢用例拆到不同进程，保留每个用例内部的完整安装、故障注入与恢复断言。

`full` 与 `upstream` 使用独立耗时数据。普通 full 中缺少上游 fixture 而跳过的用例，在 upstream 中会执行真实安装，不能沿用它的跳过耗时。尚无 upstream 数据时先均匀分配用例，再用该组实际 JUnit 耗时校准；不混用不同环境下同名用例的耗时。

共享可写 fixture 必须保持一个进程所有者。`official_runtime` 会向预建的普通 wheel 环境写入 sensor 和 `.pth`，其全部消费者作为一组分配到同一片，模块结束后仍由原 fixture 清理。其他用例继续并行；不会删除“拒绝覆盖调用者 customization”的保护断言来换取通过。

原有 `pytest (3.9)` 等 required check 名称保留，由结果核对任务产出。核对要求全部计划分片都完成并成功，collection hash 一致，selected 与 completed 相同，分片之间不重复，合起来覆盖完整集合，且每片 commit 与当前 checkout 一致。缺报告、早退、失败或混入另一提交的证据都会阻断。

这些既有 required check 也等待上游矩阵、quality 和 Python 3.14 SDK 检查成功，避免出现 Linux 全量通过、上游已经失败但 required check 仍为绿色的情况；不需要修改分支保护名称。

## 测量与调优

每片保留 JUnit、完整日志、实际耗时和最慢 20 个用例，作为保留三天的 Actions artifact。汇总结果输出最慢分片耗时；同时看 workflow 总用时和排队时间，不能只展示最快的 runner。

最初的耗时提示来自提交 `c78edde08e103d4efe74415d8e63d00ff6ecf664` 的本地完整回归，随后用 [CI 39388642766](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/39388642766) 的六片 macOS 实测数据校准 full 调度。六片覆盖 7,075 个已收集用例，最慢测试进程约 17 分 56 秒，整组从第一个 runner 开始到最后一个结束约 18 分 37 秒。完整 workflow 仍有排队，不能把这个数字写成整轮 CI 用时；这也不是调度校准后的最终实测。源码提交与各 JUnit SHA256 记录在耗时文件中。

若连续运行超过 20 分钟，先根据 artifact 判断是排队、单片偏重、单个用例过慢还是 runner 内 CPU/内存争用，再调整分配或优化重复准备步骤。不得通过删断言、缩短必要超时、跳过真实安装恢复或把失败标为允许失败来达标。

本地先跑受影响的聚焦验证；完整 CI 已在同一精确提交成功时，不为同一发布结论再重复一套无新增证据的本地全量。不同提交的结果不能直接复用。当前 tag workflow 仍执行完整矩阵；跨 workflow 的验证结果复用需另有可核对的 commit 与输入证据，不能因改为并行就跳过发布 gate。
