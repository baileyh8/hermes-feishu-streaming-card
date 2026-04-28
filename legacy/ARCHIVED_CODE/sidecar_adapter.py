
# adapter/sidecar_adapter.py
import aiohttp, json, logging
from .streaming_adapter import StreamingAdapter, CardContext

class SidecarAdapter(StreamingAdapter):
    def __init__(self, config):
        self.base_url = config['feishu_streaming_card']['sidecar']['url']
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def _ensure_session(self):
        if not self.session:
            self.session = aiohttp.ClientSession()
    
    async def create_card(self, ctx: CardContext) -> str:
        await self._ensure_session()
        payload = {
            "event": "message_received",
            "data": {
                "chat_id": ctx.chat_id,
                "greeting": ctx.greeting,
                "model": ctx.model,
                "text": ctx.user_input
            }
        }
        async with self.session.post(f"{self.base_url}/card", json=payload) as resp:
            data = await resp.json()
            return data['card_id']
    
    async def update_thinking(self, ctx: CardContext, delta: str, tools: list = None):
        await self._ensure_session()
        payload = {
            "event": "thinking",
            "data": {
                "chat_id": ctx.chat_id,
                "delta": delta,
                "tools": tools or []
            }
        }
        async with self.session.post(f"{self.base_url}/events", json=payload) as resp:
            return resp.status == 200
    
    async def finalize(self, ctx: CardContext, final_content: str, 
                      tokens: dict, duration: float):
        await self._ensure_session()
        payload = {
            "event": "finish",
            "data": {
                "chat_id": ctx.chat_id,
                "final_content": final_content,
                "tokens": tokens,
                "duration": duration,
                "thinking_start": ctx.thinking_start
            }
        }
        async with self.session.post(f"{self.base_url}/events", json=payload) as resp:
            return resp.status == 200
