# V4.0.18

V4.0.18 修复 Hermes 升级后出现的隐蔽依赖错位：Gateway 进程仍在运行，但 Feishu WebSocket 因旧版 `lark-oapi` 不支持 `extra_ua_tags` 而持续连接失败。

## 修复内容

- 仅当 Hermes Feishu adapter 实际调用 `extra_ua_tags` 时，检查 Gateway venv 中 `lark_oapi.ws.Client` 的真实构造签名。
- `doctor --json/--explain` 新增 `feishu_sdk` 状态；不兼容时给出 `feishu_sdk_incompatible`，明确区分“Gateway 存活”和“飞书连接器在线”。
- `setup/install` 发现不兼容 SDK 后安装已验证的 `lark-oapi==1.6.8`，随后重新检查构造能力，通过后才继续安装 hook。
- 运维卡补充“飞书连接 SDK 不兼容”的中文诊断与恢复路径。

## 兼容性边界

- 不使用 `extra_ua_tags` 的旧 Hermes adapter 不触发 SDK 安装。
- 已支持该参数的更新 SDK 直接通过能力检查，不因版本号不同而被强制降级。
- `doctor` 保持只读；只有用户显式运行 `setup/install` 才会修改 Gateway venv。
- 不直接修改 Hermes `gateway/run.py` 或 Feishu adapter；依赖修复仍由项目安装流程执行。

## 验证

- 失败测试先复现 `lark-oapi 1.5.3` 缺少 `extra_ua_tags`，修复后验证安装到 `1.6.8` 并通过构造签名复检。
- 真实 Hermes v0.19.0 Gateway 恢复 `✓ feishu connected`，Gateway 与 sidecar 持续运行，运行环境 `214` 个包依赖检查兼容。
- 完整自动化通过：`1511 passed, 4 skipped`；`git diff --check` 通过。
- sdist/wheel、隔离 `site-packages` 导入、公开 tagged installer 和 Release assets 在发布流程中复核。

## Release assets

- `hermes-feishu-card-v4.0.18-macos.tar.gz`
- `hermes-feishu-card-v4.0.18-linux.tar.gz`
- `hermes-feishu-card-v4.0.18-windows.zip`
- `hermes-feishu-card-v4.0.18-checksums.txt`
