# 安装安全

安装器的目标是只做可验证、可恢复的最小写入。任何版本、结构或校验不确定的情况都应 fail-closed。

## 安装前检查

安装前必须确认：

- Hermes 目录存在，且包含预期的 `gateway/run.py`。
- Hermes 版本和代码结构符合默认支持范围：`VERSION=v2026.4.23+` 或 Git tag `v2026.4.23+`，且 `gateway/run.py` 存在当前 hook 可识别的结构。
- `gateway/run.py` 中存在当前 hook 可识别的插入位置。
- 既有安装状态、备份和 manifest 没有互相矛盾。

检查失败时不写入 Hermes 文件。

安装前可先运行只读诊断：

```bash
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent
```

诊断输出会展示 Hermes 是否支持、Hermes root、`gateway/run.py` 路径、`run_py_exists`、`version_source`、`version`、`minimum_supported_version` 和 `reason`。`install` 在拒绝不支持的目录时也会输出同一组信息，便于用户判断是版本过低、版本来源未知、`gateway/run.py` 缺失，还是 hook 锚点结构不兼容。

## 备份与 manifest

安装会先保存 `gateway/run.py` 备份，再写入 manifest。manifest 至少记录：

- `run_py` 相对路径。
- 已安装后 `run.py` 的 hash。
- `backup` 相对路径。
- 备份文件 hash。

`restore` 和 `uninstall` 会使用 manifest 验证当前 `run.py` 与备份是否仍是安装器认识的状态。若发现用户或其他工具改动过相关文件，命令应拒绝覆盖。

## 原子写入

安装器写入 `run.py`、备份和 manifest 时使用临时文件替换，避免中途失败留下截断文件。若安装流程中任一步失败，应回滚已写入内容并清理半安装状态。

## 恢复和卸载

```bash
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli uninstall --hermes-dir ~/.hermes/hermes-agent --yes
```

`restore` 用于恢复安装前的 Hermes 文件；`uninstall` 当前同样移除本插件拥有的 hook 和安装状态。两者都不应覆盖无法校验的用户改动。

从 legacy/dual 历史安装迁移时，先阅读 `docs/migration.md`。历史 `installer_v2.py`、`gateway_run_patch.py`、`patch_feishu.py` 等入口写入的补丁不属于当前安装器 manifest 管理范围，不能假定当前 `restore` 能自动识别并清理。

## 降级行为

sidecar 不可用、超时或返回错误时，Hermes hook 应让 Hermes 继续原生文本回复。卡片不可用是插件故障，不应升级为 Agent 主流程故障。
