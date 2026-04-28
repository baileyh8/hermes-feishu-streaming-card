#!/usr/bin/env python3
"""
Hermes Feishu Streaming Card — 环境检查脚本
============================================
"""

import json
import os
import subprocess
import sys
from pathlib import Path

def check(name: str, ok: bool, detail: str = "") -> bool:
    status = "✓" if ok else "✗"
    print(f"  [{status}] {name}")
    if detail:
        print(f"      {detail}")
    return ok

def main():
    print("=" * 60)
    print("Feishu Streaming Card — 环境检查")
    print("=" * 60)

    hermes_dir = Path.home() / '.hermes'
    all_ok = True

    # 1. lark-cli
    print("\n[1] lark-cli 检测")
    try:
        r = subprocess.run(['lark-cli', '--version'], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            ver = r.stdout.strip() or 'unknown'
            check("lark-cli 已安装", True, f"版本: {ver}")
        else:
            check("lark-cli 已安装", False, r.stderr[:100])
            all_ok = False
    except FileNotFoundError:
        check("lark-cli 已安装", False, "命令未找到")
        all_ok = False
    except subprocess.TimeoutExpired:
        check("lark-cli 已安装", False, "命令超时")
        all_ok = False

    # 2. lark-cli 认证
    print("\n[2] lark-cli 认证状态")
    try:
        r = subprocess.run(['lark-cli', 'auth', 'status'], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            check("lark-cli 已认证", True)
        else:
            check("lark-cli 已认证", False, "请运行: lark-cli auth login")
            all_ok = False
    except FileNotFoundError:
        check("lark-cli 已认证", False, "lark-cli 未安装")
        all_ok = False
    except subprocess.TimeoutExpired:
        check("lark-cli 已认证", False, "命令超时")
        all_ok = False

    # 3. CardKit API 连通性 — 用户已确认可用，跳过自动检测
    print("\n[3] CardKit API 连通性检测")
    print("  [--] CardKit: 用户已确认可用，跳过自动检测")

    # 4. Python 依赖
    print("\n[4] Python 依赖检测")
    for dep_name, import_name in [('aiohttp', 'aiohttp'), ('yaml', 'yaml')]:
        try:
            __import__(import_name)
            check(dep_name, True)
        except ImportError:
            check(dep_name, False, f"请安装: pip install {dep_name}")
            all_ok = False

    # 5. Hermes 配置
    print("\n[5] Hermes 配置检测")
    config_path = hermes_dir / 'config.yaml'
    if config_path.exists():
        check("config.yaml 存在", True)
        try:
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            feishu = config.get('platforms', {}).get('feishu', {})
            extra = feishu.get('extra', {})
            app_id = extra.get('app_id', '')
            app_secret = extra.get('app_secret', '')
            if app_id:
                check("Feishu app_id", True, f"{app_id[:12]}***")
            else:
                check("Feishu app_id", False, "platforms.feishu.extra.app_id 未设置")
                all_ok = False
            if app_secret:
                check("Feishu app_secret", True, "已设置")
            else:
                check("Feishu app_secret", False, "未设置")
                all_ok = False
        except Exception as e:
            check("config.yaml 解析", False, str(e))
            all_ok = False
    else:
        check("config.yaml 存在", False, str(config_path))
        all_ok = False

    # 6. Sidecar 状态
    print("\n[6] Sidecar 状态检测")
    sidecar_dir = hermes_dir / 'feishu-sidecar'
    check("Sidecar 目录已安装", sidecar_dir.exists(), "未安装" if not sidecar_dir.exists() else "")

    pid_file = hermes_dir / 'feishu-sidecar.pid'
    if pid_file.exists():
        try:
            with open(pid_file) as f:
                pid_data = json.load(f)
            pid = pid_data.get('pid')
            if pid:
                os.kill(pid, 0)
                check("Sidecar 进程运行中", True, f"PID {pid}")
        except ProcessLookupError:
            check("Sidecar 进程运行中", False, "进程已退出（PID 文件残留）")
            all_ok = False
        except PermissionError:
            check("Sidecar 进程运行中", True, f"PID {pid} (无权限检查)")
        except Exception as e:
            check("Sidecar 进程运行中", False, str(e))
            all_ok = False
    else:
        check("Sidecar 进程运行中", False, "PID 文件不存在")

    # 7. Hermes Gateway 状态
    print("\n[7] Hermes Gateway 状态检测")
    pid_file = hermes_dir / 'gateway.pid'
    if pid_file.exists():
        try:
            with open(pid_file) as f:
                pid_data = json.load(f)
            pid = pid_data.get('pid')
            if pid:
                os.kill(pid, 0)
                check("Gateway 进程运行中", True, f"PID {pid}")
        except ProcessLookupError:
            check("Gateway 进程运行中", False, "PID 已退出")
            all_ok = False
        except PermissionError:
            check("Gateway 进程运行中", True, f"PID {pid} (无权限检查)")
        except Exception as e:
            check("Gateway 进程运行中", False, str(e))
            all_ok = False
    else:
        check("Gateway 进程运行中", False, "gateway.pid 不存在")

    # 8. Streaming Card 安装状态
    print("\n[8] Streaming Card 安装状态")
    feishu_py = hermes_dir / 'hermes-agent' / 'gateway' / 'platforms' / 'feishu.py'
    if feishu_py.exists():
        with open(feishu_py) as f:
            content = f.read()
        if '_sidecar_forwarder' in content or 'Feishu Streaming Card' in content:
            check("feishu.py 有注入", True, "legacy 或 sidecar 模式")
        else:
            check("feishu.py 有注入", False, "未安装")
    else:
        check("feishu.py 存在", False, "文件不存在")

    # 总结
    print("\n" + "=" * 60)
    if all_ok:
        print("✓ 所有检查通过，环境就绪！")
    else:
        print("✗ 部分检查失败")
    return 0 if all_ok else 1

if __name__ == '__main__':
    sys.exit(main())
