import asyncio


class Source:
    def __init__(self, message):
        self.chat_id = getattr(message, "chat_id", "oc_fixture")
        self.conversation_id = getattr(message, "conversation_id", self.chat_id)
        self.message_id = getattr(message, "message_id", "msg_fixture")
        self.platform = "feishu"


async def _handle_message_with_agent(message, hooks):
    chat_id = getattr(message, "chat_id", "oc_fixture")
    message_id = getattr(message, "message_id", "msg_fixture")
    source = Source(message)
    await asyncio.sleep(0.05)
    agent_result = await _run_agent(source, event_message_id=message_id)
    text = agent_result["response"]
    response = text
    _response_time = agent_result["duration"]
    hooks.emit("agent:end", {"message": message})
    return response


async def _run_agent(source, event_message_id=None):
    _loop_for_step = asyncio.get_running_loop()

    def _run_still_current():
        return True

    def progress_callback(
        event_type: str,
        tool_name: str = None,
        preview: str = None,
        args: dict = None,
        **kwargs,
    ):
        return {
            "event_type": event_type,
            "tool_name": tool_name,
            "preview": preview,
            "args": args,
            "kwargs": kwargs,
        }

    def _stream_delta_cb(text: str) -> None:
        return None

    def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:
        return None

    _interim_assistant_cb("thinking fixture delta", already_streamed=False)
    await asyncio.sleep(0.05)
    progress_callback(
        "tool.started",
        tool_name="fixture_tool",
        preview="fixture tool preview",
        args={"query": "fixture"},
    )
    await asyncio.sleep(0.05)
    _stream_delta_cb("answer fixture delta")
    await asyncio.sleep(0.05)
    return {
        "response": "fixture answer",
        "duration": 0.25,
        "input_tokens": 7,
        "output_tokens": 11,
    }
