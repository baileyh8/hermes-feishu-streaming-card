class GatewayNotificationsMixin:
    async def _deliver_platform_notice(self, source, content):
        adapter = self._adapter_for_source(source)
        return await adapter.send(chat_id=source.chat_id, content=content)
