# V5.0.19 — 自管理 PM sticky profile 与 Windows NTFS 安装修复

[English](release-notes-v5.0.19.en.md)

针对 [#507](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/507) 与 [#509](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/509)。未知源码继续拒绝；卡片样式与配置默认值不变。

## 范围

- sticky active profile：普通 Hermes home 的官方探针与 enable 子进程显式传入 `-p default`，阻止 sticky profile 将写入重定向到其他配置；`profiles/<name>` 形式的命名 profile home 保持官方环境绑定。配置读写、journal、所有权及恢复仍使用用户指定的 home，postimage 字节门与无关改动保护保留。
- Windows NTFS：`st_ctime_ns` 在路径查询与句柄查询间存在恒定读差，环境层、PM admission 和相关 legacy 证据签名改用 dev/ino/size/mtime 四元组；内容完整性由 sha256 摘要承担，ABA 恢复原字节的场景不再误拒，真实内容变化仍拒绝。

## 验收记录

- 本地文档与版本元数据：104 项通过；ruff 通过，mypy 检查的 60 个源文件通过。
- 安装、配置和恢复的相关回归：390 项通过、208 项跳过。跳过包含平台条件及未提供的特定历史/上游 fixture，不作为完整兼容验收。
- NTFS 读差注入及 CI 配置契约：23 项通过，无跳过；新回归纳入 Windows portable 套件。mtime 漂移显式注入，不依赖文件系统时钟粒度。
- 固定 Hermes v0.23.0 的隔离子进程对照确认：原始启动跟随 sticky profile；普通 home 显式 `-p default` 保持绑定根；命名 profile home 保持原环境绑定。
- 完整 CI、主线精确提交、annotated tag 门禁、普通 wheel 安装/恢复、公开安装与资产校验：发布前待完成，最终记录见 GitHub Release。
- 报告者原主机的安装及实际飞书回合仍待现场复测；本轮不声称已完成真实 Gateway 或客户端验收。

## 贡献

感谢 [lvanwei180-lvan](https://github.com/lvanwei180-lvan)（#507 现场与 #497 的三连 blocker 现场报告）、[czh9101-lang](https://github.com/czh9101-lang)（#509 根因定位与最小复现）、[tidytouch](https://github.com/tidytouch)（#510/#511 观察）。问题证据与代码作者分开记录，历史贡献保留。
