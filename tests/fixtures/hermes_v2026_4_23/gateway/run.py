async def _handle_message_with_agent(message, hooks):
    chat_id = getattr(message, "chat_id", "oc_fixture")
    message_id = getattr(message, "message_id", "msg_fixture")
    text = getattr(message, "text", "fixture answer")
    hooks.emit("agent:end", {"message": message})
    return text
