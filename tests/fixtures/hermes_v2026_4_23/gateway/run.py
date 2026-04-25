def _handle_message_with_agent(message, hooks):
    hooks.emit("agent:end", {"message": message})
