# V4.6.10：跟进新版 Hermes，保留每一轮答案

## 更新

- 接受 Hermes 0.21.x 中精确的、受 `obligation_id is not None` 保护的 turn-marker 释放步骤；保持 ledger 的 record → send → finalize 顺序及未知契约拒绝行为（#352/#353，PR #355）。
- 按新版 `_delivery_adapter_for` 解析回复归属，兼容旧 resolver；新版明确拒绝时不回退到其他 bot（#354）。
- 排队与 idle 内部通知使用独立、绑定 source 的 turn 身份，不把合成 ID 当作飞书回复锚点。兼容旧 hook 模板的卸载与重装（PR #355）。
- 相同 key 的已结束会话重开时使用新的 create UUID；飞书幂等去重不会将跟进短回执折叠回原答案卡片。创建失败时保留上一轮只读显示，不恢复旧授权，并遵守原有回收时限（#359）。
- 安装器和 CLI 使用 Hermes PM 已提交的运行 venv；外部 venv 不再误用 `pip --user`（PR #358）。Hermes 升级后仍须重新安装插件，不宣称能抵抗任意依赖同步清理。

## Hermes 验证范围

- 最新稳定版 `v2026.9.24` / 0.21.5：`f97608f178d1ffeca59860195ab7da295f7c8e5f`。
- main 固定快照：`6f7a7991bb069db07ae74a479823ce8310f8c7e0`。该快照的静态版本读取为 unknown，按已验证源码锚点识别；这不是对未来 main 的无限兼容承诺。
- 两份真实源码均执行安装、重复安装、编译、完整性诊断和逐字节卸载还原；固定文件 SHA-256 与对应 CI 已加入仓库，保留既有历史兼容矩阵。
- 完整回归、普通 wheel、精确提交 CI、发布资产与公开安装结果以最终 Release 记录为准。真实本机升级、飞书客户端与手机视觉必须分别登记，不以源码测试代替。

## 贡献

[Nevoker](https://github.com/Nevoker) (PR #355), [shichenshuo-star](https://github.com/shichenshuo-star) (PR #358); [leavrcn](https://github.com/leavrcn) (#359), [lanx214](https://github.com/lanx214) (#352), [kite40](https://github.com/kite40) (#353), [ywarmy](https://github.com/ywarmy) and [mslchy](https://github.com/mslchy) (#354), [Love4yzp](https://github.com/Love4yzp) (PR #355 deployment evidence).
保留全部历史贡献与原始代码作者。
