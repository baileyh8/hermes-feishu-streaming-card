"""Exercise real root-build/non-root-runtime ownership inside the smoke container."""
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--uid', type=int, default=65532)
parser.add_argument('--expected', choices=('installed', 'refused'), required=True)
args = parser.parse_args()
if os.getuid() != 0:
    raise SystemExit('ownership probe must begin as container root')
os.setgroups([])
os.setgid(args.uid)
os.setuid(args.uid)

from hermes_feishu_card import cli
from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card.install.integrity import plan_integrity_repair

root = Path('/opt/hermes')
try:
    detection = detect_hermes(root)
    status = cli._diagnose_install_state(detection)['status']
    reason = plan_integrity_repair(detection).reason
except (PermissionError, ValueError, OSError) as exc:
    status, reason = 'refused', type(exc).__name__
ready = status == 'installed' and reason == 'recovery_not_required'
print(json.dumps({'uid': os.getuid(), 'status': status, 'integrity': reason, 'ready': ready}))
assert ready == (args.expected == 'installed')
