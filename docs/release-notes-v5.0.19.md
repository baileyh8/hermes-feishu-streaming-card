# V5.0.19 — 自管理 PM sticky profile 与 Windows NTFS 安装修复

[English](release-notes-v5.0.19.en.md)

针对 [#507](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/507) 与 [#509](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/509)。未知源码继续拒绝；卡片样式与配置默认值不变。

## 范围

- sticky active profile：绑定阶段按官方 `_apply_profile_override` 同款规则派生实际写入的 config 目标；prepare/journal 记录该目标；探针与 enable 子进程显式 `-p <name>`；enable 的读取、校验、回滚与恢复全部作用于同一文件。profile home 不存在时在写任何配置前拒绝。postimage 字节门与无关改动保护不变。
- Windows NTFS：`st_ctime_ns` 在路径查询与句柄查询间存在恒定读差，所有文件身份签名改用 dev/ino/size/mtime 四元组；内容完整性由 sha256 摘要承担，ABA 恢复原字节的场景不再误拒，真实内容变化仍拒绝。

## 验收记录

（发布前填写：完整 CI、主线精确提交 CI、tag 门禁、普通 wheel 安装/恢复、公开 tag 安装与资产校验的链接与结果。）

## 贡献

感谢 [lvanwei180-lvan](https://github.com/lvanwei180-lvan)（#507 现场与 #497 的三连 blocker 现场报告）、[czh9101-lang](https://github.com/czh9101-lang)（#509 根因定位与最小复现）、[tidytouch](https://github.com/tidytouch)（#510/#511 观察）。问题证据与代码作者分开记录，历史贡献保留。
