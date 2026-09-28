"""Bounded display-only observations; never query or control a live process."""
from __future__ import annotations

import json
import math
import re
from typing import Any

from .text import normalize_stream_text

_PROCESS_ID = re.compile(r'proc_[0-9a-f]{12}')
_STATUSES = {'running', 'exited', 'timeout', 'interrupted', 'not_found', 'error'}
_ACTIONS = {'wait', 'poll', 'log'}


def _mapping(value: object) -> dict:
    if type(value) is str and len(value) <= 65536:
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    return value if type(value) is dict else {}


def _text(value: object, limit: int, *, tail: bool = False) -> str:
    if type(value) is not str or len(value) > 65536:
        return ''
    # Import lazily to share the established display redaction contract without
    # a session/render import cycle. Redact before clipping either end.
    from .render import _redact_tool_detail
    try:
        text = _redact_tool_detail(normalize_stream_text(value)).strip()
    except (ValueError, RecursionError):
        return ''
    return text[-limit:] if tail else text[:limit]


def _seconds(value: object) -> int | float | None:
    if type(value) in (int, float) and 0 <= value <= 10**9 and math.isfinite(value):
        return value
    return None


def process_activity(name: object, arguments: object, result: object = None) -> dict[str, Any]:
    if type(name) is not str or name not in {'terminal', 'process', 'process_manage'}:
        return {}
    args, response = _mapping(arguments), _mapping(result)
    if name == 'terminal' and response.get('error'):
        return {}
    action = args.get('action') if name != 'terminal' else 'spawn'
    if type(action) is not str or action not in _ACTIONS | {'spawn'}:
        return {}
    requested_id = args.get('session_id')
    returned_id = response.get('session_id')
    if name != 'terminal' and returned_id is not None and returned_id != requested_id:
        return {}  # Never attribute a result from another process to this wait.
    process_id = returned_id if name == 'terminal' else requested_id
    if type(process_id) is not str or not _PROCESS_ID.fullmatch(process_id):
        return {}
    info: dict[str, Any] = {'id': process_id, 'action': action}
    command = response.get('command') or (args.get('command') if name == 'terminal' else None)
    for key, value, tail in (('command', command, False),
                             ('output', response.get('output_preview') or response.get('output'), True)):
        text = _text(value, 600, tail=tail)
        if text:
            info[key] = text
    status = response.get('status')
    if type(status) is str and status in _STATUSES:
        info['status'] = status
    elif name == 'terminal':
        info['status'] = 'running'
    for key, value in (('timeout_seconds', args.get('timeout') if action == 'wait' else None),
                       ('uptime_seconds', response.get('uptime_seconds'))):
        seconds = _seconds(value)
        if seconds is not None:
            info[key] = seconds
    if type(response.get('exit_code')) is int:
        info['exit_code'] = response['exit_code']
    return info


def enrich_process_detail(data: dict[str, Any], observations: dict[str, dict], *, observe: bool = True) -> dict[str, Any]:
    """Cache at most 32 observations within one CardSession, never checkpointed."""
    raw = data.get('process_activity')
    if type(raw) is not dict:
        return data
    process_id, action = raw.get('id'), raw.get('action')
    if (type(process_id) is not str or not _PROCESS_ID.fullmatch(process_id)
            or type(action) is not str or action not in _ACTIONS | {'spawn'}):
        return data
    info = dict(observations.get(process_id, {}))
    if observe:
        for key in ('command', 'output'):
            value = _text(raw.get(key), 600, tail=key == 'output')
            if value:
                info[key] = value
        status = raw.get('status')
        if type(status) is str and status in _STATUSES:
            info['status'] = status
        uptime = _seconds(raw.get('uptime_seconds'))
        if uptime is not None:
            info['uptime_seconds'] = uptime
        if type(raw.get('exit_code')) is int:
            info['exit_code'] = raw['exit_code']
        observations.pop(process_id, None)
        while len(observations) >= 32:
            observations.pop(next(iter(observations)))
        observations[process_id] = info
    if action == 'spawn':
        return data
    verb = {'wait': '等待后台进程', 'poll': '查看后台进程', 'log': '读取后台进程输出'}[action]
    lines = [f"{verb}：{info.get('command') or '尚未观测到命令'}"]
    labels = {'running': '运行中', 'exited': '已退出', 'timeout': '等待上限已到（不代表进程结束）',
              'interrupted': '等待已中断（不代表进程结束）', 'not_found': '未找到进程', 'error': '查询失败'}
    if info.get('status') in labels:
        label = labels[info['status']]
        if info['status'] == 'exited' and 'exit_code' in info:
            label += f"，退出码 {info['exit_code']}"
        lines.append(f'上次观测：{label}')
    if 'uptime_seconds' in info:
        lines.append(f"已观测运行时长：{info['uptime_seconds']:g}s")
    timeout = _seconds(raw.get('timeout_seconds'))
    if action == 'wait' and timeout is not None:
        lines.append(f'请求等待上限：{timeout:g}s（非预计完成时间）')
    if info.get('output'):
        lines.append(f"最近输出（上次工具回报）：{info['output']}")
    return dict(data, detail='\n'.join(lines))
