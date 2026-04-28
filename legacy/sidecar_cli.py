#!/usr/bin/env /Users/bailey/.hermes/hermes-agent/venv/bin/python3
"""
Sidecar CLI - independent sidecar process manager
"""

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import yaml

logger = logging.getLogger("sidecar.cli")

PID_FILE = Path.home() / '.hermes' / 'feishu-sidecar.pid'
LOG_FILE = Path.home() / '.hermes' / 'logs' / 'feishu-sidecar.log'
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def setup_logging(debug=False):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(),
        ]
    )


def get_pid():
    if PID_FILE.exists():
        try:
            with open(PID_FILE) as f:
                data = json.load(f)
                return data.get('pid', 0)
        except Exception:
            pass
    return 0


def is_running():
    pid = get_pid()
    if pid:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            PID_FILE.unlink(missing_ok=True)
    return False


def save_pid(pid, config_path):
    with open(PID_FILE, 'w') as f:
        json.dump({'pid': pid, 'config': config_path, 'started_at': time.time()}, f)


def cmd_start(args):
    if is_running():
        print(f"Sidecar already running (PID {get_pid()})")
        return
    
    print("Starting Feishu Streaming Sidecar...")
    
    # Sidecar 目录
    sidecar_dir = Path.home() / '.hermes' / 'sidecar'
    if not sidecar_dir.exists():
        print(f"Error: Sidecar not installed at {sidecar_dir}")
        print("Run: hermes installer install")
        sys.exit(1)
    
    cmd = [
        sys.executable, '-m', 'sidecar.server',
        '--config', args.config,
        '--host', args.host,
        '--port', str(args.port),
    ]
    cwd = str(sidecar_dir.parent)  # ~/.hermes/ so -m sidecar works
    if args.debug:
        cmd.append('--debug')
    
    if not args.foreground:
        import pty
        cmd_str = ' '.join(cmd)
        full_cmd = f"cd {cwd} && nohup {cmd_str} > {LOG_FILE} 2>&1 & echo $!"
        result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
        pid_str = result.stdout.strip()
        
        if pid_str.isdigit():
            pid = int(pid_str)
            save_pid(pid, args.config)
            print(f"Sidecar started (PID {pid})")
            print(f"  Logs: {LOG_FILE}")
            print(f"  Health: http://{args.host}:{args.port}/health")
        else:
            print(f"Start failed: {result.stderr}")
            sys.exit(1)
    else:
        print("Running in foreground (Ctrl+C to stop)...")
        os.chdir(cwd)
        subprocess.run(cmd)


def cmd_stop(args):
    if not is_running():
        print("Sidecar not running")
        return
    
    pid = get_pid()
    print(f"Stopping sidecar (PID {pid})...")
    
    try:
        import signal
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            time.sleep(0.5)
            if not is_running():
                break
        else:
            print("Process did not exit, sending SIGKILL...")
            os.kill(pid, signal.SIGKILL)
        
        PID_FILE.unlink(missing_ok=True)
        print("Sidecar stopped")
    except Exception as e:
        print(f"Stop failed: {e}")
        sys.exit(1)


def cmd_restart(args):
    cmd_stop(args)
    time.sleep(1)
    # Re-use start's subprocess logic directly to avoid argument mismatch
    sidecar_dir = Path.home() / '.hermes' / 'sidecar'
    cmd = [
        sys.executable, '-m', 'sidecar.server',
        '--config', args.config,
        '--host', args.host,
        '--port', str(args.port),
    ]
    cwd = str(sidecar_dir.parent)
    cmd_str = ' '.join(cmd)
    full_cmd = f"cd {cwd} && nohup {cmd_str} > {LOG_FILE} 2>&1 & echo $!"
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    pid_str = result.stdout.strip()
    
    if pid_str.isdigit():
        pid = int(pid_str)
        save_pid(pid, args.config)
        print(f"Sidecar restarted (PID {pid})")
        print(f"  Logs: {LOG_FILE}")
        print(f"  Health: http://{args.host}:{args.port}/health")
    else:
        print(f"Restart failed: {result.stderr}")
        sys.exit(1)


def cmd_status(args):
    if is_running():
        pid = get_pid()
        print(f"Sidecar running (PID {pid})")
        try:
            with open(PID_FILE) as f:
                data = json.load(f)
            print(f"  Config: {data.get('config', 'unknown')}")
            print(f"  Started: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data.get('started_at', 0)))}")
        except Exception:
            pass
        
        try:
            import urllib.request
            resp = urllib.request.urlopen(f'http://{args.host}:{args.port}/health', timeout=2)
            data = json.loads(resp.read())
            print(f"  Health: {data.get('status')}")
            print(f"  Active cards: {data.get('active_cards', 0)}")
            print(f"  Uptime: {data.get('uptime', 0):.1f}s")
        except Exception as e:
            print(f"  Health check failed: {e}")
    else:
        print("Sidecar not running")


def cmd_logs(args):
    if not LOG_FILE.exists():
        print("Log file not found")
        return
    
    if args.tail:
        print(f"Tailing log: {LOG_FILE} (Ctrl+C to exit)")
        try:
            with open(LOG_FILE, 'r') as f:
                f.seek(0, 2)
                import select
                while True:
                    rlist, _, _ = select.select([f], [], [], 1)
                    if rlist:
                        line = f.readline()
                        if line:
                            print(line, end='')
        except KeyboardInterrupt:
            print("Stopped")
    else:
        try:
            result = subprocess.run(['tail', f'-{args.lines}', str(LOG_FILE)],
                                  capture_output=True, text=True)
            print(result.stdout)
        except Exception as e:
            print(f"Read log failed: {e}")


def cmd_test(args):
    print("Testing Sidecar connectivity...")
    
    if not is_running():
        print("Sidecar not running, start it first")
        sys.exit(1)
    
    try:
        import urllib.request
        resp = urllib.request.urlopen(f'http://{args.host}:{args.port}/health', timeout=5)
        data = json.loads(resp.read())
        print(f"Health check: {data.get('status')}")
        print(f"Active cards: {data.get('active_cards', 0)}")
    except Exception as e:
        print(f"Health check failed: {e}")
        sys.exit(1)
    
    print("Testing event sending...")
    try:
        payload = {
            'schema_version': '1.0',
            'event': 'message_received',
            'data': {
                'chat_id': 'test_chat_123',
                'message_id': 'test_msg_456',
                'user_id': 'test_user',
                'greeting': 'Test',
                'model': 'test-model',
                'text': 'This is a test',
            }
        }
        req = urllib.request.Request(
            f'http://{args.host}:{args.port}/events',
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        resp = urllib.request.urlopen(req, timeout=5)
        result = json.loads(resp.read())
        print(f"Event sent: {result}")
    except Exception as e:
        print(f"Event send failed: {e}")
        sys.exit(1)
    
    print("Sidecar test passed!")


def main():
    parser = argparse.ArgumentParser(
        description='Hermes Feishu Streaming Sidecar CLI',
        epilog='''
Examples:
  %(prog)s start                  # start sidecar (background)
  %(prog)s start --foreground     # foreground
  %(prog)s status                 # check status
  %(prog)s logs --tail            # tail logs
  %(prog)s test                   # test connection
        '''
    )
    
    subparsers = parser.add_subparsers(dest='command', help='sub-command')
    
    p_start = subparsers.add_parser('start', help='Start sidecar')
    p_start.add_argument('--config', '-c', default='~/.hermes/feishu-sidecar.yaml')
    p_start.add_argument('--host', default='localhost')
    p_start.add_argument('--port', type=int, default=8765)
    p_start.add_argument('--foreground', action='store_true')
    p_start.add_argument('--debug', action='store_true')
    p_start.set_defaults(func=cmd_start)
    
    p_stop = subparsers.add_parser('stop', help='Stop sidecar')
    p_stop.set_defaults(func=cmd_stop)
    
    p_restart = subparsers.add_parser('restart', help='Restart sidecar')
    p_restart.add_argument('--config', '-c', default='~/.hermes/feishu-sidecar.yaml')
    p_restart.add_argument('--host', default='localhost')
    p_restart.add_argument('--port', type=int, default=8765)
    p_restart.add_argument('--debug', action='store_true')
    p_restart.set_defaults(func=cmd_restart)
    
    p_status = subparsers.add_parser('status', help='Show status')
    p_status.add_argument('--host', default='localhost')
    p_status.add_argument('--port', type=int, default=8765)
    p_status.set_defaults(func=cmd_status)
    
    p_logs = subparsers.add_parser('logs', help='View logs')
    p_logs.add_argument('--tail', '-f', action='store_true')
    p_logs.add_argument('--lines', '-n', type=int, default=50)
    p_logs.set_defaults(func=cmd_logs)
    
    p_test = subparsers.add_parser('test', help='Test connection')
    p_test.add_argument('--host', default='localhost')
    p_test.add_argument('--port', type=int, default=8765)
    p_test.set_defaults(func=cmd_test)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    setup_logging(getattr(args, 'debug', False))
    args.func(args)


if __name__ == '__main__':
    main()
