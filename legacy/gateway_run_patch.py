#!/usr/bin/env python3
"""
gateway_run_patch.py — Universal Adaptive Injector for Feishu Streaming Card

Automatically detects Hermes code structure and adapts injection points.
Works across v0.4 through v0.10+ and future versions.

Strategy:
1. Find _handle_message_with_agent function
2. Look for existing timing variable (_msg_start_time or similar)
3. If found → inject event forwarding after it (NO duplicate timing)
4. If not found → inject timing variable + event at function start
5. Find agent:end hook and inject finish event after it
6. Always adapt indentation to match surrounding code
"""

import re
import ast
from pathlib import Path


# Template for when timing variable already exists (v0.4-v0.6 style)
# Injects AFTER the existing _msg_start_time line
MSG_INJECTION_WITH_TIMING = """
        # ═══ Feishu Streaming Card Sidecar ══════════════════════════════════════
        try:
            _fsc_mode = None
            try:
                import yaml as _yaml
                with open(str(self._hermes_dir / "config.yaml")) as _f:
                    _cfg = _yaml.safe_load(_f) or {}
                _fsc_mode = (_cfg.get("feishu_streaming_card", {}) or {}).get("mode", "disabled")
            except Exception:
                pass

            if _fsc_mode == "sidecar" and source.platform.value == "feishu":
                from gateway.platforms.feishu_forward import get_emitter
                import asyncio

                _emitter = getattr(self, '_feishu_sidecar_emitter', None)
                if _emitter is None:
                    _emitter = get_emitter(None, mode="sidecar")
                    self._feishu_sidecar_emitter = _emitter

                _payload = {
                    'chat_id': source.chat_id,
                    'user_id': source.user_id,
                    'user_name': getattr(source, 'user_name', '') or '',
                    'text': (event.text or "")[:500],
                    'timestamp': time.time(),
                }
                asyncio.create_task(_emitter.emit('message_received', _payload))
        except Exception:
            pass
        # ═════════════════════════════════════════════════════════════════════════"""


# Template for when NO timing variable exists (v0.7+ style)
# Injects timing declaration + event forwarding at function start
MSG_INJECTION_NEW_TIMING = """
        # ═══ Feishu Streaming Card Sidecar ══════════════════════════════════════
        _msg_start_time = time.time()
        try:
            _fsc_mode = None
            try:
                import yaml as _yaml
                with open(str(self._hermes_dir / "config.yaml")) as _f:
                    _cfg = _yaml.safe_load(_f) or {}
                _fsc_mode = (_cfg.get("feishu_streaming_card", {}) or {}).get("mode", "disabled")
            except Exception:
                pass

            if _fsc_mode == "sidecar" and source.platform.value == "feishu":
                from gateway.platforms.feishu_forward import get_emitter
                import asyncio

                _emitter = getattr(self, '_feishu_sidecar_emitter', None)
                if _emitter is None:
                    _emitter = get_emitter(None, mode="sidecar")
                    self._feishu_sidecar_emitter = _emitter

                _payload = {
                    'chat_id': source.chat_id,
                    'user_id': source.user_id,
                    'user_name': getattr(source, 'user_name', '') or '',
                    'text': (event.text or "")[:500],
                    'timestamp': time.time(),
                }
                asyncio.create_task(_emitter.emit('message_received', _payload))
        except Exception:
            pass
        # ═════════════════════════════════════════════════════════════════════════"""


# Finish injection - inserted after agent:end emit's closing brace
# Must match the indentation of the surrounding code (typically 12 for try blocks)
FINISH_INJECTION = """
            # ═══ Feishu Streaming Card Sidecar Finish ══════════════════════════
            try:
                _fsc_mode = None
                try:
                    import yaml as _yaml
                    with open(str(self._hermes_dir / "config.yaml")) as _f:
                        _cfg = _yaml.safe_load(_f) or {}
                    _fsc_mode = (_cfg.get("feishu_streaming_card", {}) or {}).get("mode", "disabled")
                except Exception:
                    pass

                if _fsc_mode == "sidecar" and source.platform.value == "feishu":
                    _emitter = getattr(self, '_feishu_sidecar_emitter', None)
                    if _emitter is not None:
                        _duration = time.time() - _msg_start_time
                        _tokens = {
                            'input_tokens': agent_result.get('input_tokens', 0),
                            'output_tokens': agent_result.get('output_tokens', 0),
                            'cache_read_tokens': agent_result.get('cache_read_tokens', 0),
                            'api_calls': agent_result.get('api_calls', 0),
                            'last_prompt_tokens': agent_result.get('last_prompt_tokens', 0) or agent_result.get('input_tokens', 0),
                        }
                        _finish_payload = {
                            'chat_id': source.chat_id,
                            'final_content': (response or '')[:1000],
                            'tokens': _tokens,
                            'duration': _duration,
                            'thinking_start': _msg_start_time,
                        }
                        asyncio.create_task(_emitter.emit('finish', _finish_payload))
            except Exception:
                pass
            # ═════════════════════════════════════════════════════════════════════════"""


def get_indent(line: str) -> int:
    """Get leading whitespace count."""
    return len(line) - len(line.lstrip())


def find_function(content: str, patterns: list) -> tuple:
    """Find function start line and its indentation level."""
    lines = content.split('\n')
    for i, line in enumerate(lines):
        for pat in patterns:
            if re.search(pat, line):
                return i, get_indent(line)
    return None, None


def find_in_function(content: str, func_start: int, func_end: int, patterns: list) -> int:
    """Find a pattern within function bounds."""
    lines = content.split('\n')
    for i in range(func_start, min(func_end, len(lines))):
        for pat in patterns:
            if re.search(pat, lines[i]):
                return i
    return None


def find_matching_brace(lines, start: int) -> int:
    """Find matching closing brace, handling strings and nested calls."""
    depth = 0
    in_string = False
    string_char = None
    
    for i in range(start, len(lines)):
        for c in lines[i]:
            if not in_string and c in '"\'':
                in_string = True
                string_char = c
            elif in_string:
                if c == '\\':
                    continue
                if c == string_char:
                    in_string = False
            elif c in '({':
                depth += 1
            elif c in ')}':
                depth -= 1
                if depth == 0:
                    return i
    return None


def reindent_block(block: str, target_indent: int) -> str:
    """Reindent a code block to target indentation level."""
    lines = block.split('\n')
    result = []
    
    # Find minimum indent (ignoring completely empty lines)
    non_empty = [l for l in lines if l.strip()]
    if non_empty:
        min_indent = min(len(l) - len(l.lstrip()) for l in non_empty)
    else:
        min_indent = 0
    
    offset = target_indent - min_indent
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append('')
        else:
            current_indent = len(line) - len(line.lstrip())
            new_indent = max(0, current_indent + offset)
            result.append(' ' * new_indent + stripped)
    
    return '\n'.join(result)


def analyze_structure(content: str) -> dict:
    """Analyze code structure to determine injection strategy."""
    lines = content.split('\n')
    
    result = {
        'func_start': None,
        'func_indent': None,
        'timing_line': None,
        'agent_end_start': None,
        'agent_end_end': None,
        'agent_end_indent': None,
        'needs_timing_var': False,
    }
    
    # Find _handle_message_with_agent
    func_start, func_indent = find_function(content, [
        r'async def _handle_message_with_agent',
        r'def _handle_message_with_agent'
    ])
    
    if func_start is None:
        return result
    
    result['func_start'] = func_start
    result['func_indent'] = func_indent
    
    # Find function end
    func_end = None
    for i in range(func_start + 1, len(lines)):
        if lines[i].strip() and get_indent(lines[i]) <= func_indent:
            func_end = i
            break
    if func_end is None:
        func_end = len(lines)
    
    # Look for existing timing variable
    timing_patterns = [
        r'_msg_start_time\s*=\s*time\.time\(\)',
        r'_msg_start_time\s*=\s*.*time.*time\(\)',
    ]
    
    timing_line = find_in_function(content, func_start, func_end, timing_patterns)
    
    if timing_line is not None:
        result['timing_line'] = timing_line
        result['needs_timing_var'] = False
    else:
        # No timing found - need to find first REAL code line after function def
        # Skip: empty lines, comments, and docstrings
        inject_line = func_start + 1
        in_docstring = False
        docstring_char = None
        
        while inject_line < func_end and inject_line < len(lines):
            line = lines[inject_line]
            stripped = line.strip()
            
            if not stripped:  # Empty line
                inject_line += 1
                continue
            
            if in_docstring:
                # Inside docstring, look for closing
                if docstring_char in stripped:
                    in_docstring = False
                inject_line += 1
                continue
            
            # Check for docstring start
            if '"""' in stripped or "'''" in stripped:
                docstring_char = '"""' if '"""' in stripped else "'''"
                # Check if single-line docstring
                if stripped.count(docstring_char) >= 2:
                    in_docstring = False
                else:
                    in_docstring = True
                inject_line += 1
                continue
            
            if stripped.startswith('#'):  # Comment
                inject_line += 1
                continue
            
            # This is the first real code line
            break
        
        result['timing_line'] = inject_line
        result['needs_timing_var'] = True
    
    # Find agent:end hook
    agent_end_start = find_in_function(content, func_start, func_end, [
        r'await self\.hooks\.emit\(["\']agent:end["\']'
    ])
    
    if agent_end_start is not None:
        result['agent_end_start'] = agent_end_start
        result['agent_end_indent'] = get_indent(lines[agent_end_start])
        result['agent_end_end'] = find_matching_brace(lines, agent_end_start)
    
    return result


def patch_run_py(hermes_dir=None):
    if hermes_dir is None:
        hermes_dir = Path.home() / ".hermes" / "hermes-agent"
    else:
        hermes_dir = Path(hermes_dir)
    RUN_PY_PATH = hermes_dir / "gateway" / "run.py"
    
    if not RUN_PY_PATH.exists():
        print(f"ERROR: {RUN_PY_PATH} not found")
        return False
    
    original = RUN_PY_PATH.read_text(encoding='utf-8')
    
    # Check if already patched
    if 'Feishu Streaming Card Sidecar' in original and 'message_received' in original:
        print("INFO: Already patched")
        return True
    
    print("Analyzing Hermes run.py structure...")
    info = analyze_structure(original)
    
    if info['func_start'] is None:
        print("ERROR: Could not find _handle_message_with_agent function")
        print("  This Hermes version may be too old or incompatible.")
        print("  Please report at: https://github.com/baileyh8/hermes-feishu-streaming-card/issues")
        return False
    
    lines = original.split('\n')
    
    print(f"  Function: line {info['func_start']+1}, indent {info['func_indent']}")
    
    if info['agent_end_start'] is None:
        print("ERROR: Could not find agent:end hook")
        return False
    
    print(f"  agent:end: lines {info['agent_end_start']+1}-{info['agent_end_end']+1}, indent {info['agent_end_indent']}")
    
    # Prepare message injection
    # For needs_timing_var=True: timing_line is the index of first real code line AFTER function def
    # We want to insert BEFORE it, so use timing_line (not +1)
    # Also use func_indent + 4 (function body indent = 8 for 4-space indent functions)
    
    if info['needs_timing_var']:
        print(f"  Timing: will inject NEW variable at line {info['timing_line']+1}")
        body_indent = info['func_indent'] + 4  # Function body indent
        msg_block = reindent_block(MSG_INJECTION_NEW_TIMING, body_indent)
        inject_at = info['timing_line']  # Insert BEFORE first real code line
    else:
        print(f"  Timing: existing at line {info['timing_line']+1}, will inject event after it")
        # Get indent of existing timing line
        timing_indent = get_indent(lines[info['timing_line']])
        msg_block = reindent_block(MSG_INJECTION_WITH_TIMING, timing_indent)
        inject_at = info['timing_line'] + 1  # Insert AFTER timing line
    
    # Prepare finish injection - match agent:end indent
    finish_block = reindent_block(FINISH_INJECTION, info['agent_end_indent'])
    
    # Insert injections (in reverse order to preserve line numbers)
    lines.insert(info['agent_end_end'] + 1, finish_block)
    lines.insert(inject_at, msg_block)
    
    # Validate syntax
    try:
        result = '\n'.join(lines)
        ast.parse(result)
    except SyntaxError as e:
        print(f"\nERROR: Syntax error after patch: {e}")
        print("This is a bug. Please report at:")
        print("  https://github.com/baileyh8/hermes-feishu-streaming-card/issues")
        return False
    
    # Backup and write
    backup = RUN_PY_PATH.with_suffix('.py.backup')
    if not backup.exists():
        backup.write_text(original, encoding='utf-8')
        print(f"\nBackup saved: {backup}")
    
    RUN_PY_PATH.write_text(result, encoding='utf-8')
    print(f"Successfully patched run.py")
    return True


if __name__ == '__main__':
    import sys
    import argparse
    parser = argparse.ArgumentParser(description='Patch Hermes gateway/run.py')
    parser.add_argument('--hermes-dir', help='Path to Hermes agent directory')
    args = parser.parse_args()
    sys.exit(0 if patch_run_py(args.hermes_dir) else 1)
