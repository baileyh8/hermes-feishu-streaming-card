# V5.0.16 — 安装诊断与 #462 可定位失败

[English](release-notes-v5.0.16.en.md)

本版处理 #462 在 Hermes v0.22.6 PM 布局上的安装失败可诊断性：安装失败不再只给一句 `official plugin enable failed`，PM 布局下会在写配置前先做插件可见性探针并 fail-closed 拒绝。本版不改变 PM 的 sys.path 切换本身，也不能保证拒绝前不创建 generation；完整关闭以报告者在真实环境复测为准。

## 变更范围

- **#462 官方 enable 失败诊断**：`_run_official_enable` 子进程失败（非零退出、超时、OSError）时，把 stdout/stderr 尾部脱敏后并入拒绝文案（`official plugin enable failed (...)`）。报告者场景的真实错误文本在 stdout（官方 Rich console），此前被 PIPE 后丢弃；现在可带出例如 `No plugin named 'hermes-feishu-card'.`。两个来源都为空时保持原固定文案。子进程统一 `encoding="utf-8", errors="replace"`，非 UTF-8 输出不再抛 `UnicodeDecodeError` 逃过分类。
- **#462 enable 前置可见性探针**：PM 布局（checkout 根存在 `pm/environments.py`）下，在写任何配置之前，用与 enable 相同的子进程启动契约运行官方 `plugins list` 路径，确认 HFC entry point 在真实启动路径（含 PM 的 sys.path 切换）可解析。不可解析时按固定分类拒绝（`official plugin enable visibility probe failed (plugin not resolvable in the selected runtime)` 等），避免进入"每次重试新建一个 generation"的循环；探针异常、非 JSON、横幅多行等情形均 fail-closed。普通 `.venv` 布局不运行探针，行为不变。
- **install 层有界文案放行**：fixed-tag install 的拒绝 reason 白名单按同一套规则放行有界、可打印的 enable/probe 诊断（≤700 字符、`) 结尾），控制字符或超长的未知异常仍折叠为固定文案。
- **诊断脱敏增强**：新增 `display_redaction.redact_diagnostic_text()`，在旧遮罩之前完整处理 key=value 赋值、Authorization Bearer/Negotiate/Basic、单双引号值（含空格与反斜杠转义）、quoted CLI flag 与 URL userinfo，再折叠/截断。`redact_tool_detail()` 等既有函数契约不变。

## 验收

自动化证据：PR #496 完整矩阵 59 项检查全部通过（含 Python 3.10–3.14 全量、macOS 十片、Windows、官方镜像 v0.22.1–v0.22.5 preserved/cleared、上游固定检出分片、CodeQL、SDK 兼容与 Docker Compose smoke），merge commit `e607efc8e04eea229ec0be4733f43a4be4a04443`。本地与独立验证：focused install 全量回归 2444 tests / 0 failures / 0 errors / 85 skipped（独立 venv + 固定 Hermes fixture）；真实 `enable_plugin → install` 拒绝链上四类凭证 canary（api_key 赋值、Bearer、带空格引号值、转义引号、quoted flag）全部遮蔽；0xff 真实子进程输出归入正常分类；好布局 `install --yes` 仍成功，坏布局出现 visibility probe 拒绝文案。

已知未验证项：报告者的 macOS 受管现场升级、真实 Hermes Gateway/model 执行与真实飞书客户端视觉/点击不在本轮范围；本版不修复 PM `sys.path` 重写本身，探针只把失败提前并给出原因；报告者复测若仍失败，请附脱敏的完整 stderr/stdout 文案（新版本会自动带出）。

## 发布验收记录

- main 精确合并提交 `e607efc8e04eea229ec0be4733f43a4be4a04443`：PR #496 CI 59/59 通过。
- tag 门禁（release-assets）、资产校验和、公开匿名下载与普通包安装验收：tag 推送后由 CI 执行并在 GitHub Release 记录。

