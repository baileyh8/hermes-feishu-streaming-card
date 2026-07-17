# Fail-open 边界

这份说明把项目中的容错分成两类：不影响 Hermes 主流程时“可继续”，涉及身份、网络暴露、状态所有权或不可验证写入时“必须失败”。它用于约束新增异常处理，避免把所有异常都机械地吞掉，也避免 sidecar 故障拖垮 Hermes Gateway。

## 裁决表

| 场景 | 裁决 | 当前行为与可观测性 |
|---|---|---|
| hook 无法构造、签名或投递一个尚未被 sidecar 接受的普通事件 | 可继续 | hook 返回未投递，让 Hermes 原生响应路径继续；hook 不输出 transport root。 |
| 未知事件、未知命令或当前 Hermes 版本不支持的交互结构 | 可继续 | 不接管、不改写 Hermes ownership；sidecar 对合法但无需处理的事件计入 `events_ignored`。 |
| sidecar 已成功接管 Feishu 卡片路径 | 可继续但抑制重复 | 只有确认卡片已投递后才抑制 Feishu 原生灰色文本；附件声明 `native_delivery=required` 时仍保留原生媒体投递。 |
| 非回环监听未显式设置 `server.allow_non_loopback: true` | 必须失败 | runner 拒绝启动，防止意外扩大攻击面。 |
| 非回环模式缺少 transport root，或 `/events` 签名缺失、错误、过期、重放 | 必须失败 | 启动或请求被拒绝；请求侧统一返回不含密钥细节的 401，并增加 `events_rejected` 与 `event_auth_rejections`。 |
| 事件 JSON、schema、bot route 或首次 Feishu send 无效 | 必须失败当前请求 | sidecar 返回 4xx/5xx 并增加拒绝/投递指标，不伪造成功状态。hook 可据未成功接管结果保留原生路径。 |
| card update、后台 command card 或诊断回调失败 | 可继续主进程 | 失败记录到日志或对应 metrics，后台任务不得终止 sidecar；操作卡显示失败结果，不报告成功。 |
| patch anchors、manifest、backup 或恢复所有权不可验证 | 必须失败 | installer/repair 拒绝覆盖，增加 `recovery_refusals`；只有已知安全状态才允许自动修复。 |
| Hermes 版本 metadata 缺失但 gateway anchors 完整可验证 | 可继续 | source-stripped 安装可使用 anchors 选择策略；metadata 可读但结构和 anchors 均不可验证时仍拒绝。 |
| 用户可验证修改与 HFC markers/manifest 不一致 | 必须失败 | 不覆盖用户内容；先人工确认修改来源，再选择 restore、重新安装或手工合并。 |

## 新代码检查清单

1. 先判断异常是否会改变身份、权限、网络暴露、安装所有权或持久状态；会改变时默认“必须失败”。
2. “可继续”必须回到明确的 Hermes 原生路径或受控降级路径，不能用假的成功结果掩盖失败。
3. 已接管卡片与尚未接管必须区分；只有 sidecar 明确成功后才能抑制 Feishu 原生文本。
4. 每个吞掉的异常至少应由返回值、受限日志、health/CLI metrics 或操作卡状态中的一种方式可观察。
5. 认证失败只暴露统一错误，不回显 timestamp、nonce、signature、transport root 或配置内容。

## 相关验证

- hook/runtime：`python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_hook_runtime_integration.py -q`
- server/event auth：`python -m pytest tests/unit/test_operations_transport.py tests/integration/test_server.py -q`
- installer/recovery：`python -m pytest tests/unit/test_patcher.py tests/integration/test_cli_install.py -q`
- release gate：`python -m pytest -q && git diff --check`
