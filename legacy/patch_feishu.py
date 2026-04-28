#!/usr/bin/env python3
"""
Patch feishu.py to add Sidecar event forwarding.
在 FeishuAdapter.send() 方法中注入事件转发逻辑。
"""
import re
from pathlib import Path

FEISHU_PY = Path.home() / ".hermes" / "hermes-agent" / "gateway" / "platforms" / "feishu.py"

if not FEISHU_PY.exists():
    print(f"ERROR: {FEISHU_PY} not found")
    exit(1)

text = FEISHU_PY.read_text()

# 1. 在 __init__ 末尾添加 _event_emitter 初始化
init_pattern = r'(        self\._load_seen_message_ids\(\))'
init_replacement = r'\1\n\n        # 初始化 Sidecar 事件发射器\n        try:\n            from gateway.platforms.feishu_forward import get_emitter\n            self._event_emitter = get_emitter(self, mode="sidecar")\n        except Exception as e:\n            logger.warning(f"[Feishu] Sidecar emitter init failed: {e}")\n            self._event_emitter = None'

if re.search(init_pattern, text):
    text = re.sub(init_pattern, init_replacement, text, count=1)
    print("✅ Injected _event_emitter initialization into __init__")
else:
    print("⚠️  Pattern not found in __init__, searching alternative...")
    # 尝试匹配
    if "self._load_seen_message_ids()" in text:
        print("Found _load_seen_message_ids(), but regex failed")

# 2. 在 send 方法开头注入事件发送
send_pattern = r'(    async def send\([\s\S]*?"""Send a Feishu message\.\""")\n(        if not self\._client:)'

send_injection = r'''\1

        # ── Feishu Streaming Card: 发送事件到 Sidecar ────────────────────────
        # 检测 thinking 内容并发送 message_received / thinking 事件
        try:
            if self._event_emitter is not None:
                import asyncio as _asyncio
                import time as _time
                import re as _re

                # 提取 thinking 内容（如果存在）
                _thinking_match = _re.search(r'<think>(.*?)</think>', content, _re.DOTALL | _re.IGNORECASE)
                _thinking_content = _thinking_match.group(1).strip() if _thinking_match else None

                # 首次发送（新消息）→ message_received 事件
                if reply_to is None:
                    _asyncio.create_task(self._event_emitter.emit('message_received', {
                        'chat_id': chat_id,
                        'message_id': None,  # 待创建后填充
                        'user_id': getattr(self, '_current_user_id', None),
                        'user_name': getattr(self, '_current_user_name', None),
                        'text': content[:500],
                        'model': getattr(self, '_current_model', 'unknown'),
                        'greeting': 'Thinking...',
                        'timestamp': _time.time(),
                    }))

                # 如果有 thinking 内容 → thinking 事件
                if _thinking_content:
                    _asyncio.create_task(self._event_emitter.emit('thinking', {
                        'chat_id': chat_id,
                        'delta': _thinking_content,
                        'tools': [],  # 工具调用信息由 on_tool_call 单独发送
                        'timestamp': _time.time(),
                    }))

                # 如果 content 不含 thinking（最终回答）→ finish 事件
                # 注意：finish 事件参数需包含 tokens/duration，暂时留空，由 run.py patch 补充
                if not _thinking_content and reply_to is not None:
                    # 可能为最终回答（编辑消息完成），发送 finish 信号
                    pass
        except Exception as _e:
            logger.debug(f"[Feishu] Sidecar event emit error: {_e}")
        # ───────────────────────────────────────────────────────────────────────

        if not self._client:'''

if re.search(send_pattern, text, re.DOTALL):
    text = re.sub(send_pattern, send_replacement, text, count=1, flags=re.DOTALL)
    print("✅ Injected event forwarding into send() method")
else:
    print("⚠️  Pattern not found in send(), skipping")

# 保存文件
FEISHU_PY.write_text(text)
print(f"\n✅ Patched {FEISHU_PY}")
