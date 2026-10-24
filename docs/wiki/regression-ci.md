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

每个 Linux 版本分成八片，由两个独立 runner 各运行四个 pytest 进程；macOS 分为十片，五个 runner 各运行两个进程。各进程使用独立 Git clone、HOME、HFC state、TMPDIR 和 pytest basetemp；安装测试构建 wheel、写源码和元数据时不会竞争同一个 checkout。只借用 CI 准备的只读固定源码、普通 wheel 和用于创建独立环境的 Python。上游 worker 在自己的私有目录新建 venv，以 `--no-index --no-deps` 安装同一 wheel，不复制旧 prefix 的 launchers，也不写回借用环境。`--system-site-packages` 复用 setup-python base 中已安装的测试依赖；它不继承任意外层 venv 的独有依赖。

Hermes 3.12/3.13 兼容矩阵各拆十二片，由三个 runner 各运行四个进程。公开仓库的 Ubuntu runner 有 4 核、macOS arm64 有 3 核，硬件基准见[官方资源规格](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories)；macOS 每台限制两个测试进程，为重复源码解析和安装测试留出 CPU 余量，不增加付费 runner。原来的逐源码安装恢复、官方 plugin 启用/配置字节、loader、rate 与 PM generation 检查分别进入用例集合，不再在单个进程中依次等待。固定上游 SHA、摘要校验、普通 wheel 和真实子进程边界保留。

`tools/ci_shard.py` 按收集到的完整 node ID 集合分配用例，使用 `tools/ci_durations.json` 中的历史耗时做均衡。耗时文件仅影响调度，不是测试白名单。新增和参数化用例自动进入集合，未登记耗时的用例也会运行。慢用例拆到不同进程，保留每个用例内部的完整安装、故障注入与恢复断言。

`full` 与 `upstream` 使用独立耗时数据。普通 full 中缺少上游 fixture 而跳过的用例，在 upstream 中会执行真实安装，不能沿用它的跳过耗时。upstream 已按 [CI 39729191204](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/39729191204) 的 Linux Python 3.12 JUnit 校准；缺失的 33 例使用同集合已完成的 Python 3.13 耗时乘 1.7 估计，并在耗时文件中单列来源、node ID 和摘要。这些估计只影响调度，不能视为测试通过。

共享可写 fixture 必须保持一个进程所有者。`official_runtime` 会写入 sensor 和 `.pth`：只有 `ci_run` 成功为每个 worker 创建独立普通 wheel prefix 后，才解除消费者的整组分配。内部标志不接受调用者转发；缺少 wheel 或环境创建失败会阻断该 worker，不以共享 runtime 或另一种分片方式继续。未建立独立环境时保留原 bundle。模块结束后仍由原 fixture 清理，普通 wheel 的逐资源字节与来源核验、拒绝覆盖 customization 的断言保持不变。

原有 `pytest (3.9)` 等 required check 名称保留，由结果核对任务产出。核对要求全部计划分片都完成并成功，collection hash 一致，selected 与 completed 相同，分片之间不重复，合起来覆盖完整集合，且每片 commit 与当前 checkout 一致。缺报告、早退、失败或混入另一提交的证据都会阻断。

这些既有 required check 也等待上游矩阵、quality 和 Python 3.14 SDK 检查成功，避免出现 Linux 全量通过、上游已经失败但 required check 仍为绿色的情况；不需要修改分支保护名称。

## 测量与调优

每片保留 JUnit、完整日志、实际耗时和最慢 20 个用例，作为保留三天的 Actions artifact。汇总结果输出最慢分片耗时；同时看 workflow 总用时和排队时间，不能只展示最快的 runner。

最初的耗时提示来自提交 `c78edde08e103d4efe74415d8e63d00ff6ecf664` 的本地完整回归，随后用 [CI 39388642766](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/39388642766) 的六片 macOS 实测数据校准 full 调度。六片覆盖 7,075 个已收集用例，最慢测试进程约 17 分 56 秒，整组从第一个 runner 开始到最后一个结束约 18 分 37 秒。完整 workflow 仍有排队，不能把这个数字写成整轮 CI 用时；这也不是调度校准后的最终实测。源码提交与各 JUnit SHA256 记录在耗时文件中。

full 耗时现采用 [CI 39741242119](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/39741242119) 六片及 [CI 39745645764](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/39745645764) 已完成三片 JUnit 的逐用例最大值，补齐新增 admission/source 用例的成本。后一轮 macOS slice 1 的三个进程均在 25 分钟限制时未完成；日志只能界定最后标记附近的用例，不能据此认定具体死锁。改为五个 runner 各两进程，保留十片完整并集、原 required check 和 25 分钟限制。本机 10 核环境的同一安装/doctor 用例在三进程并发时约 49.8 秒、两进程并发时约 48.0 秒，只改善约 3.7%，不支持耗时直接按 2/3 缩放。hosted runner 的改善和十片调度仍须以新一轮 CI 实测确认，不能当作已达到 20 分钟目标。

若连续运行超过 20 分钟，先根据 artifact 判断是排队、单片偏重、单个用例过慢还是 runner 内 CPU/内存争用，再调整分配或优化重复准备步骤。不得通过删断言、缩短必要超时、跳过真实安装恢复或把失败标为允许失败来达标。

本地先跑受影响的聚焦验证；完整 CI 已在同一精确提交成功时，不为同一发布结论再重复一套无新增证据的本地全量。不同提交的结果不能直接复用。当前 tag workflow 仍执行完整矩阵；跨 workflow 的验证结果复用需另有可核对的 commit 与输入证据，不能因改为并行就跳过发布 gate。

V5.0.7 新增固定 v0.22.5 安装、writer 和 PM 用例，上游改为三个 runner 各四进程（12 片），保留逐用例完整并集与既有 required checks。首次运行前，新用例沿用相应 v0.22.4 的耗时作为调度估计；不是 v0.22.5 的实测或通过证据。
