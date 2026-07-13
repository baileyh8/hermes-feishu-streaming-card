# V4.0.5

V4.0.5 修复 #115：一键升级虽然更新了 CLI 与 sidecar，但 Hermes Gateway venv 中仍可能保留并加载旧版插件，导致新版的原生消息抑制逻辑没有真正进入 Gateway 进程。

## Gateway runtime 版本同步

- `install` / `setup` 现在会在 Hermes runtime Python 中读取 `hermes_feishu_card.__version__` 与模块路径。
- 仅当 Gateway runtime 版本与当前 CLI 包一致时跳过安装；“旧版仍可 import”不再被视为升级成功。
- 版本落后时沿用一键安装器传入的 `HFC_INSTALL_SPEC` 自动执行 runtime pip 升级。
- 安装完成后再次检查版本与加载路径；版本仍不一致或 metadata 无法读取时明确失败，不再输出误导性的成功状态。
- 已同步的同版本安装保持幂等，不产生额外 pip 操作。

## Issue 与贡献

- 感谢 @blakejia 在 #115 提供完整升级步骤、sidecar 健康指标和重复消息截图。
- @blakejia 此前在 #106 贴出的 Gateway venv 检查结果显示 runtime 实际仍为 `3.6.3`，这条证据帮助确认问题位于 CLI/sidecar 与 Gateway runtime 的版本分裂，而不是 Card 投递失败。
- 修复由 PR #116 合并；issue 暂时保留 open，等待升级后的真实飞书确认。

## 升级后验证

重新运行一键安装后，可确认 Gateway runtime 的版本和加载路径：

```bash
~/.hermes/hermes-agent/venv/bin/python -c 'import hermes_feishu_card; print(hermes_feishu_card.__version__, hermes_feishu_card.__file__)'
```

输出应显示 `4.0.5`，且路径位于 Hermes Gateway venv 可访问的安装位置。

## 验证

- installer / patcher 热区：`139 passed`。
- 全量测试：`1278 passed, 3 skipped`；`git diff --check` 通过。
- 回归覆盖旧版 `3.6.3` 可正常 import 但必须升级，以及同版本 runtime 跳过 pip 的幂等路径。

## Release assets

- `hermes-feishu-card-v4.0.5-macos.tar.gz`
- `hermes-feishu-card-v4.0.5-linux.tar.gz`
- `hermes-feishu-card-v4.0.5-windows.zip`
- `hermes-feishu-card-v4.0.5-checksums.txt`
