class GatewayTurnMixin:
    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):
        hook_ctx = {}
        session_entry = None
        session_key = _quick_key
        agent_result = await self._run_agent(event, source)
        _turn_seconds = 1.25
        agent_messages = []
        response = agent_result.get("response", "")
        _footer_line = ""
        _intentional_silence = False
        await self._hmwa_post_turn_hooks(hook_ctx, agent_result, response)
        return await self._hmwa_deliver_turn_response(
            event, source, session_entry, session_key, run_generation,
            agent_result, agent_messages, response, _footer_line, _intentional_silence,
        )

    async def _hmwa_post_turn_hooks(self, hook_ctx, agent_result, response):
        await self.hooks.emit("agent:end", {**hook_ctx, "response": response})

    async def _hmwa_deliver_turn_response(
        self, event, source, session_entry, session_key, run_generation,
        agent_result, agent_messages, response, _footer_line, _intentional_silence,
    ):
        if _intentional_silence:
            response = ""
        if agent_result.get("already_sent") and not agent_result.get("failed"):
            await self._deliver_media_from_response(response, event, None)
            return None
        return response

    async def _run_agent(self, event, source):
        return {"response": "answer"}

    def _reply_anchor_for_event(self, event):
        return event.reply_to_message_id
