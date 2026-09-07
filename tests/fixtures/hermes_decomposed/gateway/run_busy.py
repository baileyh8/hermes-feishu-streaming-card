class GatewayBusyMixin:
    async def _request_slash_confirm(self, event, command, handler):
        source = event.source
        session_key = self._session_key_for_source(source)
        confirm_id = "fixture-confirm"
        _slash_confirm_mod.register(session_key, confirm_id, command, handler)
        return "confirm"
