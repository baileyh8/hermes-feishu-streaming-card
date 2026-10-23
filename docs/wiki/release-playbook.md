# 发布手册

## 何时发版本

适合发补丁版本：

- 修复明确 issue 或真实 Feishu bug。
- 增加 Hermes 兼容性。
- 修复 installer/doctor/Docker 影响安装的问题。
- README/文档和安装包需要同步公开。

维护者能够通过聚焦回归、完整自动化、目标平台 CI、精确 merge SHA、包构建与公开安装验证覆盖的修复，自测通过即可发布，不把 issue 报告者复测作为默认前置门禁。外部复测是补充证据；只有真实平台边界无法由现有自动化/CI 覆盖，或现有证据发生冲突时，才应暂停发布并明确记录未验证项。

适合攒到小版本：

- 多个相关 UX 改进。
- 新诊断命令或新安装流程。
- 涉及截图、README 首页和 release assets 的体验升级。

## 发版前

1. 确认版本号
   - `pyproject.toml`
   - `hermes_feishu_card/__init__.py`
   - `tests/unit/test_package_metadata.py`
2. 更新文档
   - `CHANGELOG.md`
   - `docs/release-notes-vX.Y.Z.md`
   - `README.md`
   - `README.en.md`
   - `TODO.md`
   - 受影响的 `docs/wiki/` 页面
3. 更新安装包默认值
   - `docker-compose.example.yml`
   - `README-install.md`
   - 必要时 `install.sh` / `install.ps1`
4. 真实 Feishu 验收
   - 按 [真实飞书验收清单](feishu-acceptance.md) 选择相关 smoke。

V4.1.0 还必须完成：card → native → card、native 工具/命令/notice、`/hfc` 卡片、7 表格 compact、fenced fake table、>28,000 byte terminal handoff；strict Hermes upgrade simulation 必须证明 notify、safe repair、`sidecar.restart_required`、不自动重启 Gateway与后续 `runtime.hello` ready。Linux 覆盖四种 `service.manager`，Docker Compose 使用普通非 privileged 容器且不运行 systemd。未完成的真实环境项必须明确写“未验证”，不能由单元测试代替。

## 必跑验证

```bash
python -m pytest -q
git diff --check
```

发布候选 PR 与 tag gate 还必须等待以下仓库门禁全部通过。普通 PR 按[CI 回归分级](regression-ci.md)选择范围；聚焦结果不能替代发布的完整矩阵：

- Ubuntu 上 Python 3.9、3.10、3.11、3.12、3.13、3.14 全量 pytest；SDK 专项通过不能代替对应解释器的完整运行回归。
- macOS 3.12 全量 pytest；Windows 3.12 固定 portable runtime/server 套件。
- Windows PowerShell installer 与 manifestless/portable migration 契约；POSIX-only 安全事务、mode bit、systemd 与 bash 测试由 Ubuntu/macOS 门禁承担。
- Feishu SDK compatibility、PowerShell installer 与 Docker Compose runtime smoke。
- CodeQL Python analysis。

完整 pytest 和上游兼容矩阵按用例耗时并行：Linux 与上游各八片，macOS 十片，保留全部测试与既有 required check 名称。结果核对任务确认每个用例完成一次，并核对实际 commit；缺片或混入其他提交的报告会阻断。单轮 CI 目标为 20 分钟内，以 GitHub 实测为准。

本地先完成失败复现和受影响的聚焦检查。同一精确提交已有完整 CI 证据时，不强制再跑一套重复的本地全量；真实平台、普通安装包与公开安装验收仍独立执行。

官方 GitHub Actions 必须固定到核验过的 40 位 commit SHA，并在旁注保留对应 release tag；升级前读取官方 `action.yml` 确认 runtime，不凭 major tag 推断 Node 版本。Dependabot 的 pip 与 GitHub Actions weekly PR 是维护输入，不替代上述 release gate。

如果使用 `uv run --extra test pytest -q`，测试后删除临时 `uv.lock`，除非项目明确决定开始提交 lockfile。

## 提交和 tag

```bash
git status --short
git add <release files>
git commit -m "Release vX.Y.Z <summary>"
git tag -a vX.Y.Z -m "Release vX.Y.Z <summary>"
git push origin refs/tags/vX.Y.Z
```

`vX.Y.Z` 必须是 annotated tag，并且只在合并 SHA 的精确测试和 provenance 验证完成后创建。发布审批只创建并推送 tag，绝不由 release gate 推送 main。

Release Assets 只接受完整的 `refs/tags/vMAJOR.MINOR.PATCH` 输入。resolver 会把 annotated tag peel 到一个精确 commit，reusable 跨平台 tests 全部 checkout 该 exact commit；package job 在构建前重新 fetch 并执行 full verification，上传前再完整复验同一 tag/commit。任何 lightweight tag、tag 移动、metadata 不一致、非 `origin/main` 祖先或测试失败都会在资产上传前终止。

main 应启用 PR-only branch protection，并要求 tests 与 CodeQL 的实际 check contexts；先让新 workflow 在 main/PR 上真实出现，再设置 required checks，不能预填尚未产生的 context。Dependabot vulnerability alerts/security updates 与 CodeQL 可在发布后启用；Secret Scanning 告警必须按维护者的单独裁决处理，不得在普通发版流程中自动 dismiss、rotate 或改写。

## GitHub Release

如果 tag push 触发 `.github/workflows/release-assets.yml`，workflow 会创建 release 并上传：

- `hermes-feishu-card-vX.Y.Z-macos.tar.gz`
- `hermes-feishu-card-vX.Y.Z-linux.tar.gz`
- `hermes-feishu-card-vX.Y.Z-windows.zip`
- `hermes-feishu-card-vX.Y.Z-checksums.txt`

自 v5.0.6 起，每个平台资产必须仅含一个与资产名匹配的顶层目录，例如 `hermes-feishu-card-v5.0.6-linux/`；macOS 与 Windows 分别使用 `macos` 和 `windows`。目录内仍是九个发行文件。tar uid/gid 归一为 0，目录和 shell 安装器为 0755，其余普通文件为 0644；ZIP 使用相同目录和 mode 约定。禁止 `.` / 绝对或上级路径、链接、特殊文件与重复条目。构建和验收复用共享脚本，不再通过 `tar ... .` 打包解压目标目录本身。

发行前核验 Linux GNU tar 非 root/root 和 macOS 的实际解压，分别在独占 scratch 内设 0700/1777 目标，检查 inode/uid/gid/mode、哨兵、文件字节与 executable mode；旧布局负向控制须能抓到改写。root 检查只在核验归属的 CI 专属树内运行，不操作共享 `/tmp`。公开下载后复核 checksums、完整清单和实际解压元数据，不能只看归档文件字节。

用户解压后须先 `cd hermes-feishu-card-vX.Y.Z-<platform>` 再调用安装器。同步 README-install、双语 README 与发行说明，明确旧 flat 布局到新目录的变化。保留已发表的 v5.0.5 及之前 tag/资产，不替换旧文件。发布 workflow 先保持 draft，精确 tag 门禁、资产及普通包验收完成后再公开；待验不能提前写为通过。

随后用自定义 notes 覆盖自动 body：

```bash
gh release edit vX.Y.Z \
  --repo baileyh8/hermes-feishu-streaming-card \
  --title "vX.Y.Z" \
  --notes-file docs/release-notes-vX.Y.Z.md
```

最后确认：

```bash
gh release view vX.Y.Z --repo baileyh8/hermes-feishu-streaming-card
gh run list --workflow release-assets.yml --limit 3
```

## Issue 回复

能确认解决的 issue，回复应包含：

- 修复版本和 release 链接。
- 修复范围。
- 测试命令。
- 如果需要用户再验证，列出最小验证步骤。

不要把未验证的问题写成已解决。

## 稳定性候选的额外证据

按 [稳定性测试规则](stability-test-policy.md) 给出涉及场景、自动化结果和真实边界验收。缺 fixture 或环境相关失败必须解决或明确阻断；局部绿灯不能替代完整 CI，也不能关闭尚未复现的用户 Issue。
