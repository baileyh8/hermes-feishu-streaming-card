
# ~/.hermes/hermes-agent/gateway/platforms/feishu.py
# 需要注入的代码片段（最小侵入）

# 在 __init__ 末尾添加：
from gateway.platforms.feishu_forward import get_emitter
self._event_emitter = get_emitter(self)  # SidecarEventEmitter 或 LegacyEmitter

# 在 _handle_message_with_agent 开头添加（agent 处理前）：
if self._event_emitter:
    asyncio.create_task(self._event_emitter.emit('message_received', {
        'chat_id': chat_id,
        'message_id': message_id,
        'user_id': user_id,
        'greeting': greeting,
        'model': model,
        'text': user_input,
    }))

# 在 send 方法中，每次流式输出后：
if self._event_emitter:
    asyncio.create_task(self._event_emitter.emit('thinking', {
        'chat_id': chat_id,
        'delta': delta_text,
        'tools': current_tools,  # 工具调用状态数组
    }))

# 在 after_agent_hooks 中（完成时）：
if self._event_emitter:
    asyncio.create_task(self._event_emitter.emit('finish', {
        'chat_id': chat_id,
        'final_content': final_text,
        'tokens': token_counts,
        'duration': duration_seconds,
        'thinking_start': thinking_start_time,
    }))
