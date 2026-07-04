# 发布手册

## 何时发版本

适合发补丁版本：

- 修复明确 issue 或真实 Feishu bug。
- 增加 Hermes 兼容性。
- 修复 installer/doctor/Docker 影响安装的问题。
- README/文档和安装包需要同步公开。

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

## 必跑验证

```bash
python -m pytest -q
git diff --check
```

如果使用 `uv run --extra test pytest -q`，测试后删除临时 `uv.lock`，除非项目明确决定开始提交 lockfile。

## 提交和 tag

```bash
git status --short
git add <release files>
git commit -m "Release vX.Y.Z <summary>"
git tag -a vX.Y.Z -m "Release vX.Y.Z <summary>"
git push origin main
git push origin vX.Y.Z
```

`vX.Y.Z` 必须是 annotated tag。

## GitHub Release

如果 tag push 触发 `.github/workflows/release-assets.yml`，workflow 会创建 release 并上传：

- `hermes-feishu-card-vX.Y.Z-macos.tar.gz`
- `hermes-feishu-card-vX.Y.Z-linux.tar.gz`
- `hermes-feishu-card-vX.Y.Z-windows.zip`
- `hermes-feishu-card-vX.Y.Z-checksums.txt`

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

