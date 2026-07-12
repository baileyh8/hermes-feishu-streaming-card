# V4.0.1

V4.0.1 修复带图片或文件输出的完成卡片后仍重复发送一条原生正文的问题，同时保留 Hermes 原生媒体投递。

## 修复内容

- 修复 [issue #106](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/106)：卡片成功后，显式 `MEDIA:` 与本地输出路径只交给 Hermes 原生媒体通道，不再携带已经显示在卡片中的回答正文。
- 完成卡正文不再显示 `MEDIA:/opt/data/...` 等内部路径；附件摘要和原生图片/文件继续正常发送。
- 普通完成与 queued follow-up completion 使用同一媒体正文分流规则。

## 兼容性与回退

- 卡片投递失败时保留完整 Hermes 原生 response，避免吞掉回答或媒体。
- 非飞书平台不改写 response。
- 没有显式媒体路径的结构化媒体结果保持原有 native-delivery 行为。
- 安装器可识别并升级 V4.0.0 completion hook，不会把上一版合法 block 误判为 corrupt markers。

## 贡献

- 感谢 @ShakuOvO 报告 #106。
- 感谢 @blakejia 在 Hermes `0.18.2` 上独立确认问题。

## 验证

- 热区矩阵：`509 passed`。
- 全量测试：`1257 passed, 3 skipped`；`git diff --check` 通过。
- 本地发布包 smoke：sdist/wheel 构建成功，干净 venv 安装后导入版本为 `4.0.1`。
- Hermes `extract_media()` 数据流验证：媒体路径保留，可见原生正文为空。

## Release assets

- `hermes-feishu-card-v4.0.1-macos.tar.gz`
- `hermes-feishu-card-v4.0.1-linux.tar.gz`
- `hermes-feishu-card-v4.0.1-windows.zip`
- `hermes-feishu-card-v4.0.1-checksums.txt`
