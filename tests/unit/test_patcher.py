import ast

import pytest

from hermes_feishu_card.install import patcher


def test_apply_patch_accepts_explicit_legacy_strategy():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    response = await run_agent(message)\n"
        "    _response_time = 1\n"
        "    agent_result = {}\n"
        "    return response\n"
    )

    patched = patcher.apply_patch(content, strategy="legacy_gateway_run")

    assert patcher.PATCH_BEGIN in patched
    assert patcher.COMPLETE_PATCH_BEGIN in patched


def test_apply_patch_accepts_013_plus_strategy_and_marks_strategy():
    content = (
        "class GatewayRunner:\n"
        "    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "        response = 'ok'\n"
        "        _response_time = 1\n"
        "        agent_result = {}\n"
        "        return response\n"
        "\n"
        "    async def _run_agent(self, source, event_message_id=None):\n"
        "        _loop_for_step = None\n"
        "        def _run_still_current():\n"
        "            return True\n"
        "        def progress_callback(event_type: str, tool_name: str = None, preview: str = None, args: dict = None, **kwargs):\n"
        "            return None\n"
        "        def _stream_delta_cb(text: str) -> None:\n"
        "            return None\n"
        "        def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:\n"
        "            return None\n"
        "        return {}\n"
        "\n"
        "def _deliver_result(job: dict, content: str, adapters=None, loop=None):\n"
        "    return None\n"
    )

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    assert "# HERMES_FEISHU_CARD_STRATEGY gateway_run_013_plus" in patched
    assert patcher.PATCH_BEGIN in patched
    assert patcher.COMPLETE_PATCH_BEGIN in patched


def test_apply_patch_013_plus_started_hook_uses_reply_anchor_message_id():
    content = (
        "class GatewayRunner:\n"
        "    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "        event_message_id = self._reply_anchor_for_event(event)\n"
        "        response = 'ok'\n"
        "        _response_time = 1\n"
        "        agent_result = {}\n"
        "        return response\n"
        "\n"
        "    def _reply_anchor_for_event(self, event):\n"
        "        return getattr(event, 'reply_to_message_id', None) or event.message_id\n"
    )

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")
    started_block = patched[
        patched.index(patcher.PATCH_BEGIN) : patched.index(patcher.PATCH_END)
    ]

    assert "_hfc_started_message_id = self._reply_anchor_for_event(event)" in started_block
    assert '"message_id": _hfc_started_message_id' in started_block
    assert started_block.index("_hfc_started_message_id") < started_block.index(
        "_hfc_emit("
    )


def test_apply_patch_013_plus_inserts_cron_delivery_hook():
    content = (
        "def _deliver_result(job: dict, content: str, adapters=None, loop=None):\n"
        "    delivery_content = content\n"
        "    return adapter.send('chat', delivery_content)\n"
        "\n"
        "async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "    response = 'ok'\n"
        "    _response_time = 1\n"
        "    agent_result = {}\n"
        "    return response\n"
    )

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    assert patcher.CRON_PATCH_BEGIN in patched
    assert 'event_name="message.completed"' in patched
    assert '"delivery_kind": "cron"' in patched
    assert patcher.remove_patch(patched) == content


def test_cron_marker_block_in_other_function_is_not_owned():
    content = (
        "def other():\n"
        "    # HERMES_FEISHU_CARD_CRON_PATCH_BEGIN\n"
        "    try:\n"
        "        from hermes_feishu_card.hook_runtime import emit_cron_delivery as _hfc_emit_cron\n"
        "        _hfc_cron_metadata = {\"delivery_kind\": \"cron\"}\n"
        "        # event_name=\"message.completed\"\n"
        "        if _hfc_emit_cron(locals()):\n"
        "            return None\n"
        "    except Exception:\n"
        "        pass\n"
        "    # HERMES_FEISHU_CARD_CRON_PATCH_END\n"
        "    return None\n"
        "\n"
        "def _deliver_result(job: dict, content: str, adapters=None, loop=None):\n"
        "    return adapter.send('chat', content)\n"
        "\n"
        "async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "    response = 'ok'\n"
        "    _response_time = 1\n"
        "    agent_result = {}\n"
        "    return response\n"
    )

    with pytest.raises(ValueError, match="corrupt cron patch markers"):
        patcher.apply_patch(content, strategy="gateway_run_013_plus")

    with pytest.raises(ValueError, match="corrupt cron patch markers"):
        patcher.remove_patch(content)


def test_apply_patch_inserts_real_runtime_hook_call():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    return message\n"
    )

    patched = patcher.apply_patch(content)

    assert "from hermes_feishu_card.hook_runtime import emit_from_hermes_locals" in patched
    assert "_hfc_emit(locals())" in patched
    assert "        pass\n    except Exception:" not in patched


def test_apply_patch_inserts_completion_hook_before_response_return():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    response = await run_agent(message)\n"
        "    _response_time = 1.5\n"
        "    agent_result = {'input_tokens': 1, 'output_tokens': 2}\n"
        "    return response\n"
    )

    patched = patcher.apply_patch(content)

    assert patcher.COMPLETE_PATCH_BEGIN in patched
    assert 'event_name="message.completed"' in patched
    assert "should_suppress_native_response as _hfc_should_suppress" in patched
    assert "_hfc_card_delivered = await _hfc_emit_async(_hfc_completed_locals" in patched
    assert (
        '_hfc_completed_event = _hfc_build_event("message.completed", '
        "_hfc_completed_locals, preview=True)"
    ) in patched
    assert patched.index("_hfc_completed_event = _hfc_build_event") < patched.index(
        "_hfc_card_delivered = await _hfc_emit_async"
    )
    assert 'getattr(source.platform, "value", source.platform)' in patched
    assert "if _hfc_should_suppress(_hfc_platform, _hfc_card_delivered, _hfc_attachments):" in patched
    assert "        return None\n" in patched
    assert '"model": agent_result.get("model", ""),' in patched
    assert '"context": {' in patched
    assert '"used_tokens": agent_result.get("last_prompt_tokens", 0),' in patched
    assert '"max_tokens": agent_result.get("context_length", 0),' in patched
    assert patched.index(patcher.COMPLETE_PATCH_BEGIN) < patched.index("    return response\n")


def test_apply_patch_suppresses_queued_followup_native_resend():
    content = (
        "async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "    event_message_id = event.message_id\n"
        "    response = await self._run_agent(event, source)\n"
        "    return response\n"
        "\n"
        "async def _run_agent(self, event, source):\n"
        "    result = {'final_response': 'done'}\n"
        "    _already_streamed = False\n"
        "    first_response = result.get(\"final_response\", \"\")\n"
        "    if first_response and not _already_streamed:\n"
        "        await adapter.send(source.chat_id, first_response)\n"
    )

    patched = patcher.apply_patch(content)

    assert patcher.QUEUED_COMPLETE_PATCH_BEGIN in patched
    assert "_hfc_card_delivered = await _hfc_emit_async" in patched
    assert "_already_streamed = True" in patched
    assert patched.index(patcher.QUEUED_COMPLETE_PATCH_BEGIN) < patched.index(
        "    if first_response and not _already_streamed:\n"
    )
    assert patcher.remove_patch(patched) == content


def test_apply_patch_upgrades_legacy_completion_hook_block():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    response = await run_agent(message)\n"
        "    _response_time = 1.5\n"
        "    agent_result = {'input_tokens': 1, 'output_tokens': 2}\n"
        "    # HERMES_FEISHU_CARD_COMPLETE_PATCH_BEGIN\n"
        "    try:\n"
        "        from hermes_feishu_card.hook_runtime import emit_from_hermes_locals as _hfc_emit\n"
        "        _hfc_emit({\n"
        "            **locals(),\n"
        "            \"answer\": response,\n"
        "            \"duration\": _response_time,\n"
        "            \"tokens\": {\n"
        "                \"input_tokens\": agent_result.get(\"input_tokens\", 0),\n"
        "                \"output_tokens\": agent_result.get(\"output_tokens\", 0),\n"
        "            },\n"
        "        }, event_name=\"message.completed\")\n"
        "    except Exception:\n"
        "        pass\n"
        "    # HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n"
        "    return response\n"
    )

    upgraded = patcher.apply_patch(content)

    assert "emit_from_hermes_locals_async" in upgraded
    assert "should_suppress_native_response as _hfc_should_suppress" in upgraded
    assert upgraded.count("emit_from_hermes_locals as _hfc_emit") == 1


def test_apply_patch_upgrades_previous_async_completion_hook_without_platform_guard():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    response = await run_agent(message)\n"
        "    _response_time = 1.5\n"
        "    agent_result = {'input_tokens': 1, 'output_tokens': 2}\n"
        "    # HERMES_FEISHU_CARD_COMPLETE_PATCH_BEGIN\n"
        "    try:\n"
        "        from hermes_feishu_card.hook_runtime import emit_from_hermes_locals_async as _hfc_emit_async\n"
        "        _hfc_card_delivered = await _hfc_emit_async({\n"
        "            **locals(),\n"
        "            \"answer\": response,\n"
        "            \"duration\": _response_time,\n"
        "            \"tokens\": {\n"
        "                \"input_tokens\": agent_result.get(\"input_tokens\", 0),\n"
        "                \"output_tokens\": agent_result.get(\"output_tokens\", 0),\n"
        "            },\n"
        "        }, event_name=\"message.completed\")\n"
        "        if _hfc_card_delivered:\n"
        "            return None\n"
        "    except Exception:\n"
        "        pass\n"
        "    # HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n"
        "    return response\n"
    )

    upgraded = patcher.apply_patch(content)

    assert "should_suppress_native_response as _hfc_should_suppress" in upgraded
    assert "if _hfc_card_delivered:\n" not in upgraded


def test_remove_patch_lenient_removes_previous_async_completion_hook_block():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    response = await run_agent(message)\n"
        "    _response_time = 1.5\n"
        "    agent_result = {'input_tokens': 1, 'output_tokens': 2}\n"
        "    # HERMES_FEISHU_CARD_COMPLETE_PATCH_BEGIN\n"
        "    try:\n"
        "        from hermes_feishu_card.hook_runtime import emit_from_hermes_locals_async as _hfc_emit_async\n"
        "        _hfc_card_delivered = await _hfc_emit_async({\n"
        "            **locals(),\n"
        "            \"answer\": response,\n"
        "            \"duration\": _response_time,\n"
        "            \"tokens\": {\n"
        "                \"input_tokens\": agent_result.get(\"input_tokens\", 0),\n"
        "                \"output_tokens\": agent_result.get(\"output_tokens\", 0),\n"
        "            },\n"
        "        }, event_name=\"message.completed\")\n"
        "        if _hfc_card_delivered:\n"
        "            return None\n"
        "    except Exception:\n"
        "        pass\n"
        "    # HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n"
        "    return response\n"
    )

    restored = patcher.remove_patch_lenient(content)

    assert patcher.COMPLETE_PATCH_BEGIN not in restored
    assert "    return response\n" in restored


def test_remove_patch_removes_legacy_completion_hook_block():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    # HERMES_FEISHU_CARD_PATCH_BEGIN\n"
        "    try:\n"
        "        from hermes_feishu_card.hook_runtime import emit_from_hermes_locals as _hfc_emit\n"
        "        _hfc_emit(locals())\n"
        "    except Exception:\n"
        "        pass\n"
        "    # HERMES_FEISHU_CARD_PATCH_END\n"
        "    response = await run_agent(message)\n"
        "    _response_time = 1.5\n"
        "    agent_result = {'input_tokens': 1, 'output_tokens': 2}\n"
        "    # HERMES_FEISHU_CARD_COMPLETE_PATCH_BEGIN\n"
        "    try:\n"
        "        from hermes_feishu_card.hook_runtime import emit_from_hermes_locals as _hfc_emit\n"
        "        _hfc_emit({\n"
        "            **locals(),\n"
        "            \"answer\": response,\n"
        "            \"duration\": _response_time,\n"
        "            \"tokens\": {\n"
        "                \"input_tokens\": agent_result.get(\"input_tokens\", 0),\n"
        "                \"output_tokens\": agent_result.get(\"output_tokens\", 0),\n"
        "            },\n"
        "        }, event_name=\"message.completed\")\n"
        "    except Exception:\n"
        "        pass\n"
        "    # HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n"
        "    return response\n"
    )

    restored = patcher.remove_patch(content)

    assert patcher.PATCH_BEGIN not in restored
    assert patcher.COMPLETE_PATCH_BEGIN not in restored
    assert "    return response\n" in restored


def test_apply_patch_inserts_streaming_callback_hooks():
    content = (
        "async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "    return await self._run_agent(event_message_id=event.message_id)\n"
        "\n"
        "async def _run_agent(self, source, event_message_id=None):\n"
        "    _loop_for_step = asyncio.get_running_loop()\n"
        "    session_key = 'sess-1'\n"
        "    _status_chat_id = source.chat_id\n"
        "    _approval_session_key = session_key\n"
        "    def _run_still_current():\n"
        "        return True\n"
        "\n"
        "    def progress_callback(event_type: str, tool_name: str = None, preview: str = None, args: dict = None, **kwargs):\n"
        "        progress_queue.put(tool_name)\n"
        "\n"
        "    def _stream_delta_cb(text: str) -> None:\n"
        "        if _run_still_current():\n"
        "            _stream_consumer.on_delta(text)\n"
        "\n"
        "    def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:\n"
        "        if already_streamed:\n"
        "            return\n"
        "        status_queue.put(text)\n"
        "\n"
        "    def _clarify_callback_sync(question: str, choices):\n"
        "        return \"\"\n"
        "\n"
        "    def _approval_notify_sync(approval_data: dict) -> None:\n"
        "        return None\n"
    )

    patched = patcher.apply_patch(content)

    assert patcher.TOOL_PATCH_BEGIN in patched
    assert patcher.ANSWER_DELTA_PATCH_BEGIN in patched
    assert patcher.THINKING_DELTA_PATCH_BEGIN in patched
    assert patcher.CLARIFY_PATCH_BEGIN in patched
    assert patcher.APPROVAL_PATCH_BEGIN in patched
    assert 'event_name="tool.updated"' in patched
    assert 'event_name="answer.delta"' in patched
    assert 'event_name="thinking.delta"' in patched
    assert '"kind": "clarify"' in patched
    assert "resolve_gateway_approval" in patched
    assert (
        'if event_type in ("tool.started", "tool.completed") and _run_still_current():'
        in patched
    )
    assert '"mode": "append_block"' in patched
    assert '"_hfc_loop": locals().get("_loop_for_step")' in patched
    assert '}, event_name="tool.updated"):\n                    return\n' in patched
    assert "if text and _run_still_current():" in patched
    assert "if text and not already_streamed and _run_still_current():" in patched
    assert '}, event_name="answer.delta"):\n                    return\n' in patched
    assert '}, event_name="thinking.delta"):\n                    return\n' in patched
    assert '"_hfc_loop": _loop_for_step' in patched
    assert patcher.remove_patch(patched) == content


def test_apply_patch_inserts_streaming_hooks_into_run_agent_inner():
    content = (
        "async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "    return await self._run_agent(event_message_id=event.message_id)\n"
        "\n"
        "async def _run_agent(self, source, event_message_id=None):\n"
        "    return await self._run_agent_inner(source, event_message_id=event_message_id)\n"
        "\n"
        "async def _run_agent_inner(self, source, event_message_id=None):\n"
        "    _loop_for_step = asyncio.get_running_loop()\n"
        "    session_key = 'sess-1'\n"
        "    _status_chat_id = source.chat_id\n"
        "    _approval_session_key = session_key\n"
        "    def _run_still_current():\n"
        "        return True\n"
        "\n"
        "    def progress_callback(event_type: str, tool_name: str = None, preview: str = None, args: dict = None, **kwargs):\n"
        "        progress_queue.put(tool_name)\n"
        "\n"
        "    def _stream_delta_cb(text: str) -> None:\n"
        "        if _run_still_current():\n"
        "            _stream_consumer.on_delta(text)\n"
        "\n"
        "    def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:\n"
        "        if already_streamed:\n"
        "            return\n"
        "        status_queue.put(text)\n"
        "\n"
        "    def _clarify_callback_sync(question: str, choices):\n"
        "        return \"\"\n"
        "\n"
        "    def _approval_notify_sync(approval_data: dict) -> None:\n"
        "        return None\n"
    )

    patched = patcher.apply_patch(content)

    inner_index = patched.index("async def _run_agent_inner")
    assert patched.index(patcher.TOOL_PATCH_BEGIN) > inner_index
    assert patched.index(patcher.ANSWER_DELTA_PATCH_BEGIN) > inner_index
    assert patched.index(patcher.THINKING_DELTA_PATCH_BEGIN) > inner_index
    assert patched.index(patcher.CLARIFY_PATCH_BEGIN) > inner_index
    assert patched.index(patcher.APPROVAL_PATCH_BEGIN) > inner_index
    assert patcher.remove_patch(patched) == content


def test_apply_patch_skips_streaming_hooks_when_required_scope_is_missing():
    content = (
        "async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "    return await self._run_agent(event_message_id=event.message_id)\n"
        "\n"
        "async def _run_agent(self, event_message_id=None):\n"
        "    def progress_callback(event_type: str, tool_name: str = None, preview: str = None, args: dict = None, **kwargs):\n"
        "        progress_queue.put(tool_name)\n"
        "\n"
        "    def _stream_delta_cb(text: str) -> None:\n"
        "        _stream_consumer.on_delta(text)\n"
        "\n"
        "    def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:\n"
        "        status_queue.put(text)\n"
    )

    patched = patcher.apply_patch(content)

    assert patcher.TOOL_PATCH_BEGIN not in patched
    assert patcher.ANSWER_DELTA_PATCH_BEGIN not in patched
    assert patcher.THINKING_DELTA_PATCH_BEGIN not in patched


def test_apply_patch_upgrades_phase_one_placeholder_block():
    placeholder = (
        "async def _handle_message_with_agent(message):\n"
        "    # HERMES_FEISHU_CARD_PATCH_BEGIN\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        pass\n"
        "    # HERMES_FEISHU_CARD_PATCH_END\n"
        "    return message\n"
    )

    upgraded = patcher.apply_patch(placeholder)

    assert "emit_from_hermes_locals" in upgraded
    assert "        pass\n    except Exception:" not in upgraded
    assert upgraded.count(patcher.PATCH_BEGIN) == 1


def test_remove_patch_removes_phase_one_placeholder_block():
    placeholder = (
        "async def _handle_message_with_agent(message):\n"
        "    # HERMES_FEISHU_CARD_PATCH_BEGIN\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        pass\n"
        "    # HERMES_FEISHU_CARD_PATCH_END\n"
        "    return message\n"
    )

    restored = patcher.remove_patch(placeholder)

    assert patcher.PATCH_BEGIN not in restored
    assert patcher.PATCH_END not in restored
    assert "    return message\n" in restored


def test_apply_patch_upgrades_silent_existing_block_to_warning_block():
    content = """
async def _handle_message_with_agent(message):
    # HERMES_FEISHU_CARD_PATCH_BEGIN
    try:
        from hermes_feishu_card.hook_runtime import emit_from_hermes_locals as _hfc_emit
        _hfc_emit(locals())
    except Exception:
        pass
    # HERMES_FEISHU_CARD_PATCH_END
    return message
"""

    upgraded = patcher.apply_patch(content)

    assert "except Exception as _hfc_exc:" in upgraded
    assert "[hermes-feishu-card] hook failed" in upgraded
    assert patcher.apply_patch(upgraded) == upgraded


def test_remove_patch_removes_block_and_keeps_return_content():
    content = patcher.apply_patch(
        """
async def _handle_message_with_agent(message):
    return message
"""
    )

    result = patcher.remove_patch(content)

    assert patcher.PATCH_BEGIN not in result
    assert patcher.PATCH_END not in result
    assert "    return message\n" in result


def test_apply_patch_uses_class_method_body_indentation():
    content = """
class Gateway:
    async def _handle_message_with_agent(self, message):
        return message
"""

    result = patcher.apply_patch(content)

    assert f"        {patcher.PATCH_BEGIN}\n" in result
    assert (
        "            from hermes_feishu_card.hook_runtime "
        "import emit_from_hermes_locals as _hfc_emit\n"
    ) in result
    assert "            _hfc_emit(locals())\n" in result
    assert f"        {patcher.PATCH_END}\n" in result


def test_apply_patch_does_not_use_commented_handler_name():
    content = """
# async def _handle_message_with_agent(message):
def unrelated():
    return None
"""

    with pytest.raises(ValueError, match="safe handler"):
        patcher.apply_patch(content)


@pytest.mark.parametrize(
    "content",
    [
        """
async def _handle_message_with_agent(message):
    note = "# HERMES_FEISHU_CARD_PATCH_BEGIN"
    return message
""",
        """
# # HERMES_FEISHU_CARD_PATCH_BEGIN
async def _handle_message_with_agent(message):
    return message
""",
    ],
)
def test_marker_text_in_string_or_comment_fails_closed(content):
    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


@pytest.mark.parametrize(
    "content",
    [
        '''
"""
# HERMES_FEISHU_CARD_PATCH_BEGIN
try:
    pass
except Exception:
    pass
# HERMES_FEISHU_CARD_PATCH_END
"""
async def _handle_message_with_agent(message):
    return message
''',
        '''
# HERMES_FEISHU_CARD_PATCH_BEGIN
# try:
#     pass
# except Exception:
#     pass
# HERMES_FEISHU_CARD_PATCH_END
async def _handle_message_with_agent(message):
    return message
''',
    ],
)
def test_complete_marker_shape_outside_handler_fails_closed(content):
    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_multiple_unrelated_markers_raise():
    content = f"""
async def _handle_message_with_agent(message):
    {patcher.PATCH_BEGIN}
    return message

async def other(message):
    {patcher.PATCH_END}
    return message
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_module_level_triple_quoted_handler_name_is_not_patched():
    content = '''
"""
async def _handle_message_with_agent(message):
    return message
"""
def unrelated():
    return None
'''

    with pytest.raises(ValueError, match="safe handler"):
        patcher.apply_patch(content)


@pytest.mark.parametrize(
    "content",
    [
        """
def outer():
    async def _handle_message_with_agent(message):
        return message
""",
        """
def outer():
    class Gateway:
        async def _handle_message_with_agent(self, message):
            return message
""",
    ],
)
def test_nested_handler_locations_are_not_patched(content):
    with pytest.raises(ValueError, match="safe handler"):
        patcher.apply_patch(content)


def test_non_async_and_prefixed_handler_names_are_not_patched():
    for content in (
        "def _handle_message_with_agent(message):\n    return message\n",
        "async def prefix_handle_message_with_agent(message):\n    return message\n",
    ):
        with pytest.raises(ValueError, match="safe handler"):
            patcher.apply_patch(content)


def test_crlf_patch_does_not_insert_bare_lf():
    content = (
        "async def _handle_message_with_agent(message):\r\n"
        "    return message\r\n"
    )

    result = patcher.apply_patch(content)

    assert "\n" in result
    assert "\n" not in result.replace("\r\n", "")


def test_apply_remove_round_trip_preserves_parseable_body():
    content = (
        "VALUE = 1\n\n"
        "async def _handle_message_with_agent(message):\n"
        "    original = message\n"
        "    return original\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    ast.parse(patched)
    ast.parse(restored)
    assert restored == content


def test_apply_remove_round_trip_preserves_missing_final_newline():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    return message"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    assert restored == content


def test_apply_patch_handles_module_level_multiline_signature():
    content = (
        "async def _handle_message_with_agent(\n"
        "    message,\n"
        "):\n"
        "    return message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    ast.parse(patched)
    assert "):\n    # HERMES_FEISHU_CARD_PATCH_BEGIN\n" in patched
    assert "    # HERMES_FEISHU_CARD_PATCH_END\n    return message\n" in patched
    assert restored == content


def test_apply_patch_handles_class_method_multiline_signature():
    content = (
        "class Gateway:\n"
        "    async def _handle_message_with_agent(\n"
        "        self,\n"
        "        message,\n"
        "    ):\n"
        "        return message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    ast.parse(patched)
    assert "    ):\n        # HERMES_FEISHU_CARD_PATCH_BEGIN\n" in patched
    assert "        # HERMES_FEISHU_CARD_PATCH_END\n        return message\n" in patched
    assert restored == content


def test_apply_patch_preserves_module_level_handler_docstring():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    \"\"\"Keep this docstring.\"\"\"\n"
        "    return message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)
    handler = ast.parse(patched).body[0]

    assert ast.get_docstring(handler) == "Keep this docstring."
    assert "\"\"\"Keep this docstring.\"\"\"\n    # HERMES_FEISHU_CARD_PATCH_BEGIN\n" in patched
    assert "    # HERMES_FEISHU_CARD_PATCH_END\n    return message\n" in patched
    assert patcher.apply_patch(patched) == patched
    assert restored == content


def test_apply_patch_preserves_class_method_docstring():
    content = (
        "class Gateway:\n"
        "    async def _handle_message_with_agent(self, message):\n"
        "        \"\"\"Keep method docstring.\"\"\"\n"
        "        return message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)
    method = ast.parse(patched).body[0].body[0]

    assert ast.get_docstring(method) == "Keep method docstring."
    assert "\"\"\"Keep method docstring.\"\"\"\n        # HERMES_FEISHU_CARD_PATCH_BEGIN\n" in patched
    assert "        # HERMES_FEISHU_CARD_PATCH_END\n        return message\n" in patched
    assert patcher.apply_patch(patched) == patched
    assert restored == content


def test_apply_patch_preserves_tab_indented_module_handler_prefix():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "\treturn message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    ast.parse(patched)
    assert f"\t{patcher.PATCH_BEGIN}\n" in patched
    assert "\ttry:\n" in patched
    assert (
        "\t\tfrom hermes_feishu_card.hook_runtime "
        "import emit_from_hermes_locals as _hfc_emit\n"
    ) in patched
    assert "\t\t_hfc_emit(locals())\n" in patched
    assert "    # HERMES_FEISHU_CARD_PATCH_BEGIN" not in patched
    assert restored == content


def test_apply_patch_preserves_tab_indented_class_method_prefix():
    content = (
        "class Gateway:\n"
        "\tasync def _handle_message_with_agent(self, message):\n"
        "\t\treturn message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    ast.parse(patched)
    assert f"\t\t{patcher.PATCH_BEGIN}\n" in patched
    assert "\t\ttry:\n" in patched
    assert (
        "\t\t\tfrom hermes_feishu_card.hook_runtime "
        "import emit_from_hermes_locals as _hfc_emit\n"
    ) in patched
    assert "\t\t\t_hfc_emit(locals())\n" in patched
    assert restored == content


def test_apply_patch_handles_docstring_only_handler():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    \"\"\"Only documentation.\"\"\"\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)
    handler = ast.parse(patched).body[0]

    assert ast.get_docstring(handler) == "Only documentation."
    assert "\"\"\"Only documentation.\"\"\"\n    # HERMES_FEISHU_CARD_PATCH_BEGIN\n" in patched
    assert patcher.apply_patch(patched) == patched
    assert restored == content


def test_apply_patch_handles_docstring_only_handler_without_final_newline():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    \"\"\"Only documentation.\"\"\""
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)
    handler = ast.parse(patched).body[0]

    assert ast.get_docstring(handler) == "Only documentation."
    assert "\"\"\"Only documentation.\"\"\"\n    # HERMES_FEISHU_CARD_NO_FINAL_NEWLINE\n" in patched
    assert patcher.apply_patch(patched) == patched
    assert restored == content


def test_apply_remove_preserves_docstring_blank_line_before_return():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    \"\"\"Keep this docstring.\"\"\"\n"
        "\n"
        "    return message\n"
    )

    patched = patcher.apply_patch(content)
    restored = patcher.remove_patch(patched)

    assert ast.get_docstring(ast.parse(patched).body[0]) == "Keep this docstring."
    assert restored == content


def test_apply_patch_rejects_module_level_one_line_handler():
    content = "async def _handle_message_with_agent(message): pass\n"

    with pytest.raises(ValueError, match="safe handler"):
        patcher.apply_patch(content)


def test_apply_patch_rejects_class_method_one_line_handler():
    content = (
        "class Gateway:\n"
        "    async def _handle_message_with_agent(self, message): pass\n"
    )

    with pytest.raises(ValueError, match="safe handler"):
        patcher.apply_patch(content)


def test_user_sentinel_before_valid_looking_hook_is_not_owned():
    content = f"""
async def _handle_message_with_agent(message):
    # HERMES_FEISHU_CARD_NO_FINAL_NEWLINE
    {patcher.PATCH_BEGIN}
    try:
        pass
    except Exception:
        pass
    {patcher.PATCH_END}
    return message
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_user_comment_before_sentinel_is_not_owned():
    content = f"""
async def _handle_message_with_agent(message):
    \"\"\"Only documentation.\"\"\"
    # user comment
    # HERMES_FEISHU_CARD_NO_FINAL_NEWLINE
    {patcher.PATCH_BEGIN}
    try:
        pass
    except Exception:
        pass
    {patcher.PATCH_END}
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_user_comment_between_sentinel_and_marker_is_not_owned():
    content = f"""
async def _handle_message_with_agent(message):
    \"\"\"Only documentation.\"\"\"
    # HERMES_FEISHU_CARD_NO_FINAL_NEWLINE
    # user comment
    {patcher.PATCH_BEGIN}
    try:
        pass
    except Exception:
        pass
    {patcher.PATCH_END}
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_isolated_no_final_newline_sentinel_is_rejected():
    content = """
async def _handle_message_with_agent(message):
    # HERMES_FEISHU_CARD_NO_FINAL_NEWLINE
    return message
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_remove_rejects_marker_block_with_wrong_shape():
    content = f"""
async def _handle_message_with_agent(message):
    {patcher.PATCH_BEGIN}
    print("not owned")
    {patcher.PATCH_END}
    return message
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_half_marker_raises_for_apply_and_remove():
    content = f"""
async def _handle_message_with_agent(message):
    {patcher.PATCH_BEGIN}
    return message
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.apply_patch(content)

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_remove_patch_raises_when_markers_are_reversed():
    content = f"""
async def _handle_message_with_agent(message):
    {patcher.PATCH_END}
    return message
    {patcher.PATCH_BEGIN}
"""

    with pytest.raises(ValueError, match="corrupt patch markers"):
        patcher.remove_patch(content)


def test_apply_patch_raises_when_no_handler_found():
    with pytest.raises(ValueError, match="safe handler"):
        patcher.apply_patch("def handle(message):\n    return message\n")


def test_complete_hook_block_contains_suppression_guard():
    """_render_complete_hook_block 生成的代码包含 native response suppression guard"""
    from hermes_feishu_card.install.patcher import _render_complete_hook_block
    block = "".join(_render_complete_hook_block("    ", "\n"))
    assert "should_suppress_native_response as _hfc_should_suppress" in block
    assert "getattr(source.platform, \"value\", source.platform)" in block
    assert "_hfc_attachments" in block
    assert "return None" in block


def test_previous_async_complete_hook_block_contains_platform_check():
    """_render_previous_async_complete_hook_block 生成的代码包含平台判断"""
    from hermes_feishu_card.install.patcher import _render_previous_async_complete_hook_block
    block = "".join(_render_previous_async_complete_hook_block("    ", "\n"))
    assert "source.platform.value == \"feishu\"" in block
    assert "return None" in block


def test_legacy_complete_hook_block_has_no_return_none():
    """_render_legacy_complete_hook_block 没有 return None（fire-and-forget）"""
    from hermes_feishu_card.install.patcher import _render_legacy_complete_hook_block
    block = "".join(_render_legacy_complete_hook_block("    ", "\n"))
    assert "return None" not in block
