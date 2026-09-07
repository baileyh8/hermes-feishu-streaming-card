class GatewayInboundMixin:
    async def _handle_message(self, event):
        _admitted = await self._hm_admit_event(event)
        if _admitted is None:
            return None
        event, source, is_internal = _admitted
        _quick_key = self._session_key_for_source(source)
        return await self._handle_message_with_agent(event, source, _quick_key, 1)
