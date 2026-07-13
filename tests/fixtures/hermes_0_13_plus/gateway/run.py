class GatewayRunner:
    async def start(self):
        await self._finish_startup_restore()

        # Drain any recovered process watchers from the crash-recovery checkpoint.
        try:
            from tools.process_registry import process_registry

            watchers = process_registry.pending_watchers
            process_registry.pending_watchers = []
            for watcher in watchers:
                self._run_process_watcher(watcher)
        except Exception:
            pass

    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):
        _msg_start_time = 0.0
        agent_result = {
            "model": "test-model",
            "input_tokens": 1,
            "output_tokens": 2,
            "last_prompt_tokens": 1,
            "context_length": 100,
        }
        response = "final response"
        _response_time = 1.25
        await self.hooks.emit("agent:end", {"response": response})
        return response

    async def _run_agent(self, source, event_message_id=None):
        _loop_for_step = None

        def _run_still_current():
            return True

        def progress_callback(event_type: str, tool_name: str = None, preview: str = None, args: dict = None, **kwargs):
            return None

        def _stream_delta_cb(text: str) -> None:
            return None

        def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:
            return None

        return {}


def _deliver_result(job: dict, content: str, adapters=None, loop=None):
    media_files = []
    return None
