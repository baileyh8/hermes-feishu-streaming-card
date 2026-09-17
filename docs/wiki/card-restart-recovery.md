# 卡片连接恢复与执行恢复

HFC 只恢复展示和投递身份。Hermes 仍是执行任务、会话归属、启动续跑和授权的唯一负责人。

## 私有卡片检查点

独立 runner 在私有 state directory 下启用 `card-checkpoints-v1`。每个已发送的普通会话卡保存有界 JSON 检查点：原 Feishu message ID、profile/bot、应用身份摘要、会话正文和时间线、顺序号、回复位置。文件采用原子替换，目录 0700、文件 0600；拒绝符号链接和非当前用户文件，记录有摘要校验，最多 128 条、每条 1 MiB、保留 24 小时。包含对话正文，应按本地 Hermes 会话资料保护，不复制到公开日志或诊断附件。

不保存或恢复审批 token、运行中 waiter、callback、native admission 和执行栈。原来等待授权的卡片恢复为失效状态，用户必须重新发起请求。Native handoff 继续使用既有独立账本，不混入会话检查点。

重启加载只接受当前投递策略仍允许、profile/bot 绑定和 Feishu 应用身份未改变的记录。继续沿用原 message ID；同轮终局更新原卡，重复终局不另发卡。非终态卡先静态提示连接已重建、等待执行状态同步，避免在没有进程状态证据时永久显示动画或假称任务成功。收到原轮有效事件后继续正常更新。跨 chat/profile、未知身份和损坏记录不能用于恢复。

记录写入失败不改变已经发生的投递、不再发第二条消息；health diagnostics 的 `card_checkpoint_state` 标记 unavailable。它意味着该实例没有可靠的重启恢复能力，不能把发送成功等同于检查点成功。达到容量上限不驱逐尚在保留期的旧卡记录；新增记录停止保存并报告 unavailable。

这一机制不能追溯恢复升级前没有检查点的卡片，也不承诺平台发送成功与本地原子写之间崩溃窗口的永久 exactly-once。

## 启动恢复与传输重试

只对签名形状已知的 Hermes `_run_startup_resume_event(self, adapter, event, session_key)` 加有限 sidecar policy 可用性等待。未知签名不包装；非 Feishu、禁用插件、明确 native 策略和 HTTP 拒绝不强行等待卡片。原 Hermes handler 仍只调用一次；等待不能恢复授权或绕过会话 ownership。

terminal 传输的 75 秒预算包括请求和同轮发送锁等待，退避后重新检查剩余时间。重试复用原事件身份，HTTP 4xx 不重试；日志只保留重试次数和异常类，排除原始消息 ID、URL、异常正文。网络超时并不证明服务端未处理，所以服务端幂等和检查点同样必要。

## 验证

`tests/integration/test_combined_stability.py` 覆盖真实本机 HTTP 的重建 app、同卡终局、重复投递、错误 chat、旧审批失效、磁盘失败和启动等待；`tests/unit/test_session_store.py` 覆盖权限、损坏、保留时限和符号链接。模拟 Feishu 客户端不能替代真机视觉验收。
