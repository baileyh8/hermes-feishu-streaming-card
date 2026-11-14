"""Bounded Chinese hints for Hermes-native approval pattern keys.

HFC does not parse commands, infer paths or effects, or recommend choices.
The producer passes the NATIVE pattern keys Hermes already decided on
(approval_data["pattern_keys"]); this module only maps keys from the
upstream frozen rule table to short Chinese labels and collapses anything
else to a single "rule name unknown" phrase. Unknown keys are never echoed
verbatim: they can carry plugin/execute_code internals or injection text.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Final


MAX_RULE_KEYS: Final = 8
MAX_KEY_CHARS: Final = 120
_UNKNOWN_LABEL: Final = "Hermes 原生规则命中（规则名未知）"
_HINT_PREFIX: Final = "Hermes 原生审批规则提示（仅规则命中类别，非完整影响判断）："

_RULE_LABELS: Final[dict[str, str]] = {
    "recursive delete": "递归删除文件",
    "recursive delete of root filesystem": "删除根目录内容",
    "recursive delete of system directory": "删除系统目录内容",
    "recursive delete of home directory": "删除用户主目录内容",
    "delete in root path": "删除根路径文件",
    "recursive delete (long flag)": "递归删除文件",
    "recursive delete (flags after operands)": "递归删除文件",
    "Windows cmd destructive delete": "Windows 删除命令",
    "Windows PowerShell destructive delete": "Windows PowerShell 删除命令",
    "PowerShell destructive delete (Remove-Item)": "Windows PowerShell 删除命令",
    "Windows destructive delete (recursive/quiet switch)": "Windows 递归删除",
    "PowerShell encoded command execution": "PowerShell 编码命令执行",
    "format filesystem": "格式化文件系统",
    "format filesystem (mkfs)": "格式化文件系统",
    "format filesystem (Format-Volume)": "格式化磁盘",
    "format drive (format.com)": "格式化磁盘",
    "disk copy": "整盘复制",
    "dd to raw block device": "写入原始磁盘设备",
    "write to block device": "写入原始磁盘设备",
    "redirect to raw block device": "写入原始磁盘设备",
    "wipe disk (Clear-Disk)": "清空磁盘",
    "wipe free space (cipher /w)": "擦除磁盘空闲空间",
    "disk partitioning (diskpart)": "磁盘分区操作",
    "SQL DROP": "执行 SQL DROP 语句",
    "SQL DELETE without WHERE": "无条件删除数据库记录",
    "SQL TRUNCATE": "清空数据库表",
    "overwrite system config": "覆盖系统配置",
    "stop/restart system service": "停止/重启系统服务",
    "kill all processes": "终止所有进程",
    "force kill processes": "强制终止进程",
    "force kill processes (taskkill /F)": "强制终止进程",
    "force kill processes (Stop-Process -Force)": "强制终止进程",
    "force kill processes (killall -KILL)": "强制终止进程",
    "kill processes by regex (killall -r)": "按名称批量终止进程",
    "fork bomb": "进程爆炸脚本",
    "system shutdown/reboot": "关机/重启系统",
    "init 0/6 (shutdown/reboot)": "关机/重启系统",
    "systemctl poweroff/reboot": "关机/重启系统",
    "stop/restart hermes gateway (kills running agents)": "停止/重启 Hermes 网关（会中断运行中的会话）",
    "hermes update (restarts gateway, kills running agents)": "Hermes 更新（会重启网关、中断运行中的会话）",
    "stop/restart hermes launchd service (kills running agents)": "停止/重启 Hermes 服务（会中断运行中的会话）",
    "stop/restart hermes gateway via shell-spliced verb (kills running agents)": "停止/重启 Hermes 网关（会中断运行中的会话）",
    "pipe remote content to shell": "把远程内容直接交给 Shell 执行",
    "pipe remote content to PowerShell (iwr | iex)": "把远程内容直接交给 PowerShell 执行",
    "execute remote content via Invoke-Expression": "执行远程内容",
    "execute remote script via process substitution": "执行远程脚本",
    "execute remote content via command substitution": "执行远程内容",
    "pipe decoded content to shell (possible command obfuscation)": "解码后交给 Shell 执行（疑似混淆）",
    "pipe xxd-decoded content to shell (possible command obfuscation)": "解码后交给 Shell 执行（疑似混淆）",
    "pipe tr-transformed output to shell (possible command obfuscation)": "变换后交给 Shell 执行（疑似混淆）",
    "pipe openssl-decoded content to shell (possible command obfuscation)": "解码后交给 Shell 执行（疑似混淆）",
    "script execution via -e/-c flag": "通过解释器执行代码",
    "script execution via heredoc": "通过内嵌脚本执行代码",
    "shell execution via heredoc": "通过内嵌脚本执行代码",
    "shell command via -c/-lc flag": "通过 Shell -c 执行命令",
    "upload a secret or credential file via curl/wget (possible exfiltration)": "上传疑似凭据文件到网络（可能外泄）",
    "pipe a credential file into a curl/wget upload (possible exfiltration)": "把疑似凭据文件通过管道上传（可能外泄）",
    "invisible or bidirectional Unicode control character (possible obfuscation)": "包含不可见/双向 Unicode 字符（疑似混淆）",
    "cloud metadata endpoint access (instance credentials)": "访问云主机元数据接口（可能获取实例凭据）",
    "overwrite system file via tee": "覆盖系统文件",
    "overwrite system file via redirection": "覆盖系统文件",
    "overwrite project env/config via tee": "覆盖项目环境/配置文件",
    "overwrite project env/config via redirection": "覆盖项目环境/配置文件",
    "overwrite project env/config file": "覆盖项目环境/配置文件",
    "copy/move file into system config path": "写入系统配置目录",
    "copy/move file into sensitive credential/SSH/shell-rc path": "写入凭据/SSH/Shell 配置路径",
    "in-place edit of sensitive credential/SSH/shell-rc path": "就地修改凭据/SSH/Shell 配置文件",
    "in-place edit of sensitive credential/SSH/shell-rc path (long flag)": "就地修改凭据/SSH/Shell 配置文件",
    "in-place edit of sensitive credential/SSH/shell-rc path (perl/ruby)": "就地修改凭据/SSH/Shell 配置文件",
    "in-place edit of system config": "就地修改系统配置",
    "in-place edit of system config (long flag)": "就地修改系统配置",
    "in-place edit of Hermes config/env": "就地修改 Hermes 配置/环境文件",
    "in-place edit of Hermes config/env (long flag)": "就地修改 Hermes 配置/环境文件",
    "in-place edit of Hermes config/env (perl/ruby)": "就地修改 Hermes 配置/环境文件",
    "xargs with rm": "批量删除文件",
    "find -exec/-execdir rm": "批量删除文件",
    "find -delete": "批量删除文件",
    "find dynamic shell word may expand to destructive flag": "find 参数可能动态展开为删除操作",
    "dynamic shell word may expand to arbitrary program execution flag": "参数可能动态展开为执行任意程序",
    "git reset --hard (destroys uncommitted changes)": "丢弃未提交的 Git 改动",
    "git force push (rewrites remote history)": "Git 强制推送（重写远端历史）",
    "git force push short flag (rewrites remote history)": "Git 强制推送（重写远端历史）",
    "git clean with force (deletes untracked files)": "Git 强制清理未跟踪文件",
    "git branch force delete": "Git 强制删除分支",
    "git branch force delete (long flags)": "Git 强制删除分支",
    "git branch force delete (long flags, force-first)": "Git 强制删除分支",
    "chmod +x followed by immediate execution": "赋予执行权限后立即运行脚本",
    "world/other-writable permissions": "开放全员写权限",
    "recursive world/other-writable (long flag)": "递归开放全员写权限",
    "recursive chown to root": "递归改属主为 root",
    "recursive chown to root (long flag)": "递归改属主为 root",
    "sudo with privilege flag (stdin/askpass/shell/list)": "sudo 使用特权参数（stdin/askpass/shell/list）",
    "sudo with combined-flag privilege escalation": "sudo 使用组合特权参数",
    "package manager uninstall": "卸载软件包",
    "docker restart/stop/kill (container lifecycle)": "重启/停止/强杀容器",
    "docker compose restart/stop/kill/down (container lifecycle)": "重启/停止/强杀容器或 compose 栈",
    "docker with remote daemon redirect (-H/--host)": "docker 连接到远端守护进程",
    "docker with daemon redirect (--context: alternate daemon)": "docker 切换守护进程上下文",
    "docker context use (switches default daemon for future commands)": "切换 docker 默认上下文",
    "podman with remote daemon redirect (--url/--connection/--identity)": "podman 连接到远端守护进程",
    "podman remote mode (-r/--remote: remote daemon)": "podman 连接到远端守护进程",
    "docker/podman daemon redirect via environment (DOCKER_HOST/CONTAINER_HOST)": "容器命令连接到远端守护进程",
    "kill hermes/gateway process (self-termination)": "终止 Hermes/网关进程",
    "kill process via pgrep/pidof expansion (self-termination)": "终止 Hermes/网关进程",
    "kill process via backtick pgrep/pidof expansion (self-termination)": "终止 Hermes/网关进程",
    "access to SSH keys (Windows path)": "访问 SSH 密钥（Windows 路径）",
    "access to Hermes secrets (Windows path)": "访问 Hermes 凭据文件（Windows 路径）",
    "grant Everyone access (icacls)": "开放 Everyone 访问权限",
    "reset ACLs recursively (icacls /reset)": "递归重置访问控制列表",
    "delete volume shadow copies (vssadmin)": "删除卷影副本（备份）",
    "delete backups (wbadmin)": "删除备份",
    "modify boot configuration (bcdedit /set)": "修改启动配置",
    "registry delete (reg delete)": "删除注册表项",
    "registry value delete (Remove-ItemProperty -Force)": "删除注册表值",
    "force stop service (Stop-Service -Force)": "强制停止服务",
    "stop/delete service (sc)": "停止/删除服务",
    "command parser limit exceeded": "命令过长或过复杂，Hermes 解析受限",
    "command parser limit or malformed executable payload": "命令载荷无法解析",
}



def bounded_native_rule_keys(raw: object) -> list[str]:
    """Bound and normalize producer-supplied native keys.

    Producer contract (from approval_data): a list of short native rule
    strings. Non-strings, empty/oversized entries and extra entries beyond
    the cap are dropped; the result is order-preserving and deduplicated.
    """
    if isinstance(raw, (str, bytes, dict)) or not isinstance(raw, Iterable):
        return []
    bounded = []
    for item in raw:
        if not isinstance(item, str):
            continue
        key = item.strip()
        if not key or len(key) > MAX_KEY_CHARS:
            continue
        if key not in bounded:
            bounded.append(key)
        if len(bounded) >= MAX_RULE_KEYS:
            break
    return bounded


def approval_rule_hint_line(keys: Iterable[object] | None) -> str | None:
    """One bounded Chinese hint line, or None when empty.

    Input is re-bounded here: old or hand-built InteractionState values may
    carry non-string entries, so every item is type-checked before lookup
    and a lookup failure can never raise into the card. Known canonical
    keys render joined by Chinese enumeration commas; every unknown key
    collapses into one shared unknown phrase (never echoed, never a
    per-key rendering). Labels are internal constants, so the line carries
    no Markdown-control characters.
    """
    bounded = bounded_native_rule_keys(keys)
    if not bounded:
        return None
    labels = []
    unknown = False
    for key in bounded:
        label = _RULE_LABELS.get(key)
        if label is None:
            unknown = True
        elif label not in labels:
            labels.append(label)
    if unknown:
        labels.append(_UNKNOWN_LABEL)
    if not labels:
        return None
    return _HINT_PREFIX + "、".join(labels)
