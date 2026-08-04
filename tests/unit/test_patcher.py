import ast
from pathlib import Path

import pytest

from hermes_feishu_card.install import patcher


TURN_RUNNER_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "hermes_turn_runner.py"
)
HERMES_V020_BASE_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "hermes_exact_base_v020.py"
)


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


def test_apply_patch_013_plus_started_hook_uses_real_message_id_with_anchor_fallback():
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

    # The started hook must use the REAL incoming message id so every new user
    # message opens its own card session — even when the user replied to
    # (quoted) a previous message in a Feishu thread. The reply anchor is only
    # a fallback when no real message id is available.
    assert (
        "_hfc_started_message_id = getattr(event, \"message_id\", None) "
        "or self._reply_anchor_for_event(event)" in started_block
    )
    assert "_hfc_started_message_id = self._reply_anchor_for_event(event)" not in started_block
    assert '"message_id": _hfc_started_message_id' in started_block
    assert "handle_hfc_command_from_hermes_locals as _hfc_handle_command" in started_block
    assert (
        "if _hfc_handle_command({**locals(), \"message_id\": _hfc_started_message_id}):"
        in started_block
    )
    assert "return None" in started_block
    assert started_block.index("_hfc_handle_command") < started_block.index("_hfc_emit(")
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
    assert '"delivery_kind": "cron"' in patched
    assert '_hfc_resolve_targets = locals().get("_resolve_delivery_targets")' in patched
    assert "if callable(_hfc_resolve_targets):" in patched
    assert 'job["_hfc_resolved_targets"] = _hfc_resolve_targets(job)' in patched
    assert patcher.remove_patch(patched) == content


def test_cron_hook_keeps_native_media_delivery_after_card_success(monkeypatch):
    from hermes_feishu_card import hook_runtime

    content = (
        "class BasePlatformAdapter:\n"
        "    @staticmethod\n"
        "    def extract_media(content):\n"
        "        if 'MEDIA:' in content:\n"
        "            return [('/tmp/report.pdf', False)], '报告已生成'\n"
        "        return [], content\n"
        "\n"
        "    @staticmethod\n"
        "    def filter_media_delivery_paths(media_files):\n"
        "        return media_files\n"
        "\n"
        "deliveries = []\n"
        "\n"
        "def _resolve_delivery_targets(job):\n"
        "    return [{'platform': 'feishu', 'chat_id': 'oc_attachment'}]\n"
        "\n"
        "def _deliver_result(job: dict, content: str, adapters=None, loop=None):\n"
        "    targets = _resolve_delivery_targets(job)\n"
        "    delivery_content = content\n"
        "    media_files, cleaned_delivery_content = BasePlatformAdapter.extract_media(delivery_content)\n"
        "    media_files = BasePlatformAdapter.filter_media_delivery_paths(media_files)\n"
        "    deliveries.append((cleaned_delivery_content, media_files))\n"
        "    return deliveries[-1]\n"
    )
    monkeypatch.setattr(hook_runtime, "emit_cron_delivery", lambda local_vars: True)

    patched = patcher.apply_cron_patch(content)
    namespace = {}
    exec(patched, namespace)

    result = namespace["_deliver_result"](
        {"id": "job-attachment", "deliver": "origin"},
        "报告已生成 MEDIA:/tmp/report.pdf",
    )

    assert result == ("", [("/tmp/report.pdf", False)])
    assert patched.index("filter_media_delivery_paths(media_files)") < patched.index(
        patcher.CRON_PATCH_BEGIN
    )
    assert patcher.remove_patch(patched) == content


def test_apply_cron_patch_moves_v407_hook_after_media_extraction():
    legacy_hook = (
        "    # HERMES_FEISHU_CARD_CRON_PATCH_BEGIN\n"
        "    try:\n"
        "        from hermes_feishu_card.hook_runtime import emit_cron_delivery as _hfc_emit_cron\n"
        "        _hfc_cron_metadata = {\"delivery_kind\": \"cron\"}\n"
        "        # Pre-resolve targets so build_cron_event can discover feishu chat_id\n"
        "        _hfc_resolve_targets = locals().get(\"_resolve_delivery_targets\") or globals().get(\"_resolve_delivery_targets\")\n"
        "        if callable(_hfc_resolve_targets):\n"
        "            try:\n"
        "                job[\"_hfc_resolved_targets\"] = _hfc_resolve_targets(job)\n"
        "            except Exception:\n"
        "                pass\n"
        "        if _hfc_emit_cron(locals()):\n"
        "            return None\n"
        "    except Exception as _hfc_exc:\n"
        "        try:\n"
        "            import sys as _hfc_sys\n"
        "            print(\"[hermes-feishu-card] hook failed: \" + _hfc_exc.__class__.__name__ + \": \" + str(_hfc_exc), file=_hfc_sys.stderr)\n"
        "        except Exception:\n"
        "            pass\n"
        "    # HERMES_FEISHU_CARD_CRON_PATCH_END\n"
    )
    unpatched = (
        "def _deliver_result(job: dict, content: str, adapters=None, loop=None):\n"
        "    targets = _resolve_delivery_targets(job)\n"
        "    delivery_content = content\n"
        "    media_files, cleaned_delivery_content = BasePlatformAdapter.extract_media(delivery_content)\n"
        "    media_files = BasePlatformAdapter.filter_media_delivery_paths(media_files)\n"
        "    return cleaned_delivery_content, media_files\n"
    )
    v407_patched = unpatched.replace(
        "    targets = _resolve_delivery_targets(job)\n",
        legacy_hook + "    targets = _resolve_delivery_targets(job)\n",
    )

    upgraded = patcher.apply_cron_patch(v407_patched)

    assert upgraded.count(patcher.CRON_PATCH_BEGIN) == 1
    assert upgraded.index("filter_media_delivery_paths(media_files)") < upgraded.index(
        patcher.CRON_PATCH_BEGIN
    )
    assert patcher.remove_patch(upgraded) == unpatched


def test_apply_cron_patch_is_a_noop_when_optional_anchor_is_absent():
    content = "def unrelated():\n    return None\n"

    assert patcher.apply_cron_patch(content) == content


def test_apply_patch_inserts_slash_confirm_card_hook():
    content = (
        "class GatewayRunner:\n"
        "    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "        response = 'ok'\n"
        "        _response_time = 1\n"
        "        agent_result = {}\n"
        "        return response\n"
        "\n"
        "    async def _request_slash_confirm(self, *, event, command, title, message, handler):\n"
        "        from tools import slash_confirm as _slash_confirm_mod\n"
        "        source = event.source\n"
        "        session_key = self._session_key_for_source(source)\n"
        "        confirm_id = 'confirm-1'\n"
        "        _slash_confirm_mod.register(session_key, confirm_id, command, handler)\n"
        "        adapter = self.adapters.get(source.platform)\n"
        "        metadata = self._thread_metadata_for_source(source, self._reply_anchor_for_event(event))\n"
        "        return message\n"
    )

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    assert "# HERMES_FEISHU_CARD_SLASH_CONFIRM_PATCH_BEGIN" in patched
    assert "request_slash_confirm_from_hermes_locals_async" in patched
    assert "complete_command_card_from_hermes_locals_async" in patched
    assert "await _hfc_request_slash_confirm(" in patched
    assert '"message_id": _hfc_slash_reply_to' in patched
    assert '_hfc_slash_interaction_id = "slash_"' in patched
    assert "interaction_id=_hfc_slash_interaction_id" in patched
    assert "_hfc_slash_result = await handler(_hfc_slash_choice)" in patched
    assert "if await _hfc_complete_command_card(" in patched
    assert "return None" in patched
    assert "return _hfc_slash_result" in patched
    assert patched.index("_slash_confirm_mod.register") < patched.index(
        "# HERMES_FEISHU_CARD_SLASH_CONFIRM_PATCH_BEGIN"
    )
    assert patcher.apply_patch(patched, strategy="gateway_run_013_plus") == patched
    assert patcher.remove_patch(patched) == content


def test_apply_patch_installs_feishu_command_card_adapter_methods():
    content = (
        "class GatewayRunner:\n"
        "    async def _handle_message(self, event):\n"
        "        source = event.source\n"
        "        command = event.get_command()\n"
        "        if command == 'model':\n"
        "            return await self._handle_model_command(event)\n"
        "        return None\n"
        "\n"
        "    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "        response = 'ok'\n"
        "        _response_time = 1\n"
        "        agent_result = {}\n"
        "        return response\n"
    )

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    assert "# HERMES_FEISHU_CARD_COMMAND_CARD_PATCH_BEGIN" in patched
    assert "install_feishu_command_card_adapter_methods" in patched
    assert "_hfc_install_command_cards(self, event=event)" in patched
    assert patched.index("source = event.source") < patched.index(
        "# HERMES_FEISHU_CARD_COMMAND_CARD_PATCH_BEGIN"
    )
    assert patcher.apply_patch(patched, strategy="gateway_run_013_plus") == patched
    legacy_patched = patched.replace(
        "_hfc_install_command_cards(self, event=event)",
        "_hfc_install_command_cards(self)",
    )
    assert patcher.apply_patch(legacy_patched, strategy="gateway_run_013_plus") == patched
    assert patcher.remove_patch(legacy_patched) == content
    assert patcher.remove_patch(patched) == content


def test_apply_patch_installs_command_card_adapter_before_recovered_watchers():
    content = (
        "class GatewayRunner:\n"
        "    async def start(self):\n"
        "        await self._finish_startup_restore()\n"
        "        try:\n"
        "            from tools.process_registry import process_registry\n"
        "            watchers = process_registry.pending_watchers\n"
        "            process_registry.pending_watchers = []\n"
        "            for watcher in watchers:\n"
        "                self._run_process_watcher(watcher)\n"
        "        except Exception:\n"
        "            pass\n"
        "\n"
        "    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "        response = 'ok'\n"
        "        _response_time = 1\n"
        "        agent_result = {}\n"
        "        return response\n"
    )

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    ast.parse(patched)
    assert patcher.COMMAND_CARD_STARTUP_PATCH_BEGIN in patched
    assert "_hfc_install_command_cards(self)" in patched
    assert patched.index(patcher.COMMAND_CARD_STARTUP_PATCH_BEGIN) < patched.index(
        "watchers = process_registry.pending_watchers"
    )
    assert patcher.apply_patch(patched, strategy="gateway_run_013_plus") == patched
    assert patcher.remove_patch(patched) == content


def test_v019_startup_installs_uuid_wrapper_before_delivery_ledger_redelivery():
    content = (
        "class GatewayRunner:\n"
        "    async def start(self):\n"
        "        await self._redeliver_pending_obligations()\n"
        "        try:\n"
        "            from tools.process_registry import process_registry\n"
        "            watchers = process_registry.pending_watchers\n"
        "            for watcher in watchers:\n"
        "                self._run_process_watcher(watcher)\n"
        "        except Exception:\n"
        "            pass\n"
        "\n"
        "    async def _redeliver_pending_obligations(self):\n"
        "        for row in claimed:\n"
        "            adapter = self.adapters[row['platform']]\n"
        "            content = row['content']\n"
        "            result = await adapter.send(\n"
        "                chat_id=row['chat_id'],\n"
        "                content=content,\n"
        "                metadata=None,\n"
        "            )\n"
        "\n"
        "    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "        response = 'ok'\n"
        "        _response_time = 1\n"
        "        agent_result = {}\n"
        "        return response\n"
    )

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    ast.parse(patched)
    assert patched.index(patcher.COMMAND_CARD_STARTUP_PATCH_BEGIN) < patched.index(
        "await self._redeliver_pending_obligations()"
    )
    assert patched.index(patcher.NATIVE_REDELIVERY_PATCH_BEGIN) < patched.index(
        "result = await adapter.send("
    )
    assert "obligation_id=row.get(\"obligation_id\")" in patched
    assert "original_content=row.get(\"content\")" in patched
    assert patcher.apply_patch(patched, strategy="gateway_run_013_plus") == patched
    assert patcher.remove_patch(patched) == content


@pytest.mark.parametrize(
    "runner_name, watcher_call",
    [
        ("OtherRunner", "self._run_process_watcher(watcher)"),
        ("GatewayRunner", "self._record_recovered_watcher(watcher)"),
    ],
)
def test_command_card_startup_patch_requires_gateway_runner_recovered_watcher_drain(
    runner_name,
    watcher_call,
):
    content = (
        f"class {runner_name}:\n"
        "    async def start(self):\n"
        "        try:\n"
        "            from tools.process_registry import process_registry\n"
        "            watchers = process_registry.pending_watchers\n"
        "            for watcher in watchers:\n"
        f"                {watcher_call}\n"
        "        except Exception:\n"
        "            pass\n"
        "\n"
        "    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "        response = 'ok'\n"
        "        _response_time = 1\n"
        "        agent_result = {}\n"
        "        return response\n"
    )

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    assert patcher.COMMAND_CARD_STARTUP_PATCH_BEGIN not in patched
    assert patcher.remove_patch(patched) == content


def test_apply_patch_013_plus_intercepts_hfc_command_before_unknown_slash():
    content = (
        "class GatewayRunner:\n"
        "    async def _handle_message(self, event):\n"
        "        source = event.source\n"
        "        if not self._is_user_authorized(source):\n"
        "            return None\n"
        "        _quick_key = self._session_key_for_source(source)\n"
        "        command = event.get_command()\n"
        "        if command:\n"
        "            return f\"Unknown command `/{command}`. Type /commands.\"\n"
        "        return None\n"
        "\n"
        "    def _reply_anchor_for_event(self, event):\n"
        "        return getattr(event, 'reply_to_message_id', None) or event.message_id\n"
        "\n"
        "    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "        response = 'ok'\n"
        "        _response_time = 1\n"
        "        agent_result = {}\n"
        "        return response\n"
    )

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    assert patcher.HFC_COMMAND_PATCH_BEGIN in patched
    assert "handle_hfc_command_from_hermes_locals as _hfc_handle_command" in patched
    assert '"message_id": _hfc_command_message_id' in patched
    assert (
        patched.index(patcher.HFC_COMMAND_PATCH_BEGIN)
        < patched.index("Unknown command")
    )
    assert patcher.apply_patch(patched, strategy="gateway_run_013_plus") == patched
    assert patcher.remove_patch(patched) == content


def test_apply_patch_installs_platform_notice_card_hook():
    content = (
        "class GatewayRunner:\n"
        "    async def _handle_message(self, event):\n"
        "        source = event.source\n"
        "        return None\n"
        "\n"
        "    async def _deliver_platform_notice(self, source, content):\n"
        "        adapter = self.adapters.get(source.platform)\n"
        "        if not adapter:\n"
        "            return None\n"
        "        return await adapter.send(source.chat_id, content)\n"
        "\n"
        "    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "        response = 'ok'\n"
        "        _response_time = 1\n"
        "        agent_result = {}\n"
        "        return response\n"
    )

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    assert patcher.PLATFORM_NOTICE_PATCH_BEGIN in patched
    assert "handle_platform_notice_from_hermes" in patched
    assert (
        "if _hfc_handle_platform_notice(self, source, content):" in patched
    )
    assert patched.index(patcher.PLATFORM_NOTICE_PATCH_BEGIN) < patched.index(
        "adapter = self.adapters.get(source.platform)"
    )
    assert patcher.apply_patch(patched, strategy="gateway_run_013_plus") == patched
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
    assert "handle_hfc_command_from_hermes_locals as _hfc_handle_command" in patched
    assert "if _hfc_handle_command(locals()):" in patched
    assert patched.index("_hfc_handle_command") < patched.index("_hfc_emit(locals())")
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
    assert "native_media_only_response as _hfc_media_only" in patched
    assert "_hfc_card_delivered = await _hfc_emit_async(_hfc_completed_locals" in patched
    assert (
        '_hfc_completed_event = _hfc_build_event("message.completed", '
        "_hfc_completed_locals, preview=True)"
    ) in patched
    assert patched.index("_hfc_completed_event = _hfc_build_event") < patched.index(
        "_hfc_card_delivered = await _hfc_emit_async"
    )
    assert 'getattr(source.platform, "value", source.platform)' in patched
    assert '_hfc_native_delivery = "allowed"' in patched
    assert (
        '_hfc_native_delivery = _hfc_completed_data.get("native_delivery", '
        '"required" if _hfc_attachments else "allowed")'
    ) in patched
    assert (
        "if _hfc_should_suppress("
        "_hfc_platform, _hfc_card_delivered, _hfc_attachments, _hfc_native_delivery"
        "):"
    ) in patched
    assert (
        'if str(_hfc_platform).lower() == "feishu" and '
        '_hfc_card_delivered and _hfc_native_delivery == "required":'
    ) in patched
    assert "response = _hfc_media_only(response)" in patched
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
    assert (
        '_hfc_native_delivery = _hfc_completed_data.get("native_delivery", '
        '"required" if _hfc_attachments else "allowed")'
    ) in patched
    assert "_already_streamed = True" in patched
    assert "native_media_only_response as _hfc_media_only" in patched
    assert (
        'if str(_hfc_platform).lower() == "feishu" and '
        '_hfc_card_delivered and _hfc_native_delivery == "required":'
    ) in patched
    assert "first_response = _hfc_media_only(first_response)" in patched
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


def test_apply_patch_upgrades_v400_completion_hook_with_media_text_split():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    response = await run_agent(message)\n"
        "    _response_time = 1.5\n"
        "    agent_result = {'input_tokens': 1, 'output_tokens': 2}\n"
        "    return response\n"
    )
    latest = patcher.apply_patch(content)
    v400 = latest.replace(
        "        from hermes_feishu_card.hook_runtime import native_media_only_response as _hfc_media_only\n",
        "",
    ).replace(
            '            if str(_hfc_platform).lower() == "feishu" and '
            '_hfc_card_delivered and _hfc_native_delivery == "required":\n'
            "                response = _hfc_media_only(response)\n",
        "",
    )

    upgraded = patcher.apply_patch(v400)

    assert "native_media_only_response as _hfc_media_only" in upgraded
    assert (
        'if str(_hfc_platform).lower() == "feishu" and '
        '_hfc_card_delivered and _hfc_native_delivery == "required":'
    ) in upgraded
    assert "response = _hfc_media_only(response)" in upgraded
    assert patcher.remove_patch(upgraded) == content


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


def test_answer_delta_hook_targets_native_text_stream_when_tts_fallback_exists():
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
        "    if _want_stream_deltas or _want_interim_consumer:\n"
        "        try:\n"
        "            if _adapter:\n"
        "                if _want_stream_deltas:\n"
        "                    def _stream_delta_cb(text: str) -> None:\n"
        "                        if _run_still_current():\n"
        "                            _stream_consumer.on_delta(text)\n"
        "        except Exception:\n"
        "            pass\n"
        "\n"
        "    if _stream_delta_cb is None and _stts_consumer_ref is not None:\n"
        "        def _stream_delta_cb(text: str) -> None:\n"
        "            if _run_still_current():\n"
        "                _stts_consumer_ref.on_delta(text)\n"
        "\n"
        "    def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:\n"
        "        status_queue.put(text)\n"
    )

    patched = patcher.apply_patch(content)

    native_stream_index = patched.index("_stream_consumer.on_delta(text)")
    tts_fallback_index = patched.index("_stts_consumer_ref.on_delta(text)")
    answer_hook_index = patched.index(patcher.ANSWER_DELTA_PATCH_BEGIN)
    assert answer_hook_index < native_stream_index < tts_fallback_index
    assert patched.count(patcher.ANSWER_DELTA_PATCH_BEGIN) == 1
    assert patcher.remove_patch(patched) == content

    patched_lines = patched.splitlines(keepends=True)
    begin = next(
        index
        for index, line in enumerate(patched_lines)
        if patcher.ANSWER_DELTA_PATCH_BEGIN in line
    )
    end = next(
        index
        for index, line in enumerate(patched_lines[begin:], start=begin)
        if patcher.ANSWER_DELTA_PATCH_END in line
    )
    stale = "".join(patched_lines[:begin] + patched_lines[end + 1 :])
    fallback_body = (
        "            if _run_still_current():\n"
        "                _stts_consumer_ref.on_delta(text)\n"
    )
    stale = stale.replace(
        fallback_body,
        "".join(patcher._render_answer_delta_hook_block("            ", "\n"))
        + fallback_body,
        1,
    )

    upgraded = patcher.apply_patch(stale)

    assert upgraded.index(patcher.ANSWER_DELTA_PATCH_BEGIN) < upgraded.index(
        "_stream_consumer.on_delta(text)"
    )
    assert upgraded.count(patcher.ANSWER_DELTA_PATCH_BEGIN) == 1
    assert patcher.remove_patch(upgraded) == content


def test_apply_patch_restores_hooks_after_turn_runner_refactor():
    content = TURN_RUNNER_FIXTURE.read_text(encoding="utf-8")

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    for marker in (
        patcher.STABLE_TOOL_PATCH_BEGIN,
        patcher.ANSWER_DELTA_PATCH_BEGIN,
        patcher.THINKING_DELTA_PATCH_BEGIN,
        patcher.CLARIFY_PATCH_BEGIN,
        patcher.APPROVAL_PATCH_BEGIN,
        patcher.STATUS_PATCH_BEGIN,
    ):
        assert marker in patched
    assert 'event_name="tool.updated"' in patched
    assert 'event_name="answer.delta"' in patched
    assert 'event_name="thinking.delta"' in patched
    assert "_hfc_turn_ctx = ctx" in patched
    assert '"source": _hfc_turn_ctx.source' in patched
    assert '"message_id": _hfc_turn_ctx.event_message_id' in patched
    assert '"_hfc_loop": _hfc_turn_ctx._loop_for_step' in patched
    assert '"chat_id": _hfc_turn_ctx._status_chat_id' in patched
    status_method = patched.index("def _status_callback_sync")
    status_context = patched.index("ctx = self._ctx", status_method)
    status_hook = patched.index(patcher.STATUS_PATCH_BEGIN, status_method)
    assert status_context < status_hook
    ast.parse(patched)
    assert patcher.apply_patch(patched, strategy="gateway_run_013_plus") == patched
    assert patcher.remove_patch(patched) == content


def test_turn_id_patcher_seam_keeps_one_source_object_across_all_callbacks():
    content = TURN_RUNNER_FIXTURE.read_text(encoding="utf-8")
    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")
    tree = ast.parse(patched)
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_handle_message_with_agent"
    )
    assert [argument.arg for argument in handler.args.args][-3:] == [
        "source",
        "_quick_key",
        "run_generation",
    ]
    bindings = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute) and target.attr == "source"
            for target in node.targets
        )
    ]
    assert len(bindings) == 1
    assert isinstance(bindings[0].value, ast.Name)
    assert bindings[0].value.id == "source"

    started = patched[
        patched.index(patcher.PATCH_BEGIN) : patched.index(patcher.PATCH_END)
    ]
    completed = patched[
        patched.index(patcher.COMPLETE_PATCH_BEGIN) : patched.index(
            patcher.COMPLETE_PATCH_END
        )
    ]
    assert "**locals()" in started
    assert "**locals()" in completed
    for marker in (
        patcher.STABLE_TOOL_PATCH_BEGIN,
        patcher.ANSWER_DELTA_PATCH_BEGIN,
        patcher.THINKING_DELTA_PATCH_BEGIN,
    ):
        begin = patched.index(marker)
        end = patched.index("PATCH_END", begin)
        assert '"source": _hfc_turn_ctx.source' in patched[begin:end]
    assert patcher.remove_patch(patched) == content


def test_apply_patch_uses_stable_tool_call_ids_when_gateway_exposes_callbacks():
    content = (
        "async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "    return await self._run_agent(source, event_message_id=event.message_id)\n"
        "\n"
        "async def _run_agent(self, source, event_message_id=None):\n"
        "    _loop_for_step = asyncio.get_running_loop()\n"
        "    agent = self.agent\n"
        "    def _run_still_current():\n"
        "        return True\n"
        "    def progress_callback(event_type: str, tool_name: str = None, preview: str = None, args: dict = None, **kwargs):\n"
        "        return None\n"
        "    agent.tool_progress_callback = progress_callback\n"
        "    agent.tool_start_callback = voice_ack_callback if voice_enabled else None\n"
        "    return agent\n"
    )

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    assert patcher.STABLE_TOOL_PATCH_BEGIN in patched
    assert '"tool_id": str(call_id or tool_name or "tool")' in patched
    assert "agent.tool_start_callback = _hfc_tool_start_callback" in patched
    assert "agent.tool_complete_callback = _hfc_tool_complete_callback" in patched
    assert "_hfc_pending_tool_previews" in patched
    assert 'kwargs.get("_hfc_force_tool_progress_fallback")' in patched
    assert (
        'getattr(agent.tool_start_callback, "_hfc_stable_wrapper", False)'
        in patched
    )
    assert (
        'getattr(agent.tool_complete_callback, "_hfc_stable_wrapper", False)'
        in patched
    )
    assert "_hfc_original_tool_progress_callback = getattr(" in patched
    assert "def _hfc_tool_progress_callback(" in patched
    assert "agent.tool_progress_callback = _hfc_tool_progress_callback" in patched
    assert "_hfc_tool_progress_callback._hfc_stable_wrapper = True" in patched
    assert (
        "_hfc_original_tool_progress_callback("
        '"tool.started", tool_name, _hfc_tool_preview, args,'
        in patched
    )
    assert patched.count("_hfc_force_tool_progress_fallback=True") == 2
    ast.parse(patched)
    assert patcher.apply_patch(patched, strategy="gateway_run_013_plus") == patched
    assert patcher.remove_patch(patched) == content


def _status_callback_fixture() -> str:
    return (
        "async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "    return await self._run_agent(source, event_message_id=event.message_id)\n"
        "\n"
        "async def _run_agent(self, source, event_message_id=None):\n"
        "    _loop_for_step = asyncio.get_running_loop()\n"
        "    _status_chat_id = source.chat_id\n"
        "    def _run_still_current():\n"
        "        return True\n"
        "\n"
        "    def _status_callback_sync(event_type: str, message: str) -> None:\n"
        "        prepared_message = _prepare_gateway_status_message(message)\n"
        "        status_queue.put(prepared_message)\n"
    )


def test_apply_patch_inserts_removable_status_callback_hook():
    content = _status_callback_fixture()

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    assert patcher.STATUS_PATCH_BEGIN in patched
    assert patched.index(patcher.STATUS_PATCH_BEGIN) < patched.index(
        "prepared_message = _prepare_gateway_status_message("
    )
    assert "handle_status_from_hermes_locals as _hfc_handle_status" in patched
    assert patched.count(patcher.STATUS_PATCH_BEGIN) == 1
    assert patcher.apply_patch(patched, strategy="gateway_run_013_plus") == patched
    assert patcher.remove_patch(patched) == content


@pytest.mark.parametrize(
    "content",
    [
        _status_callback_fixture().replace(
            "def _status_callback_sync(", "def _renamed_status_callback_sync("
        ),
        _status_callback_fixture().replace(
            "event_type: str, message: str", "event_type: str"
        ),
        _status_callback_fixture().replace(
            "    _status_chat_id = source.chat_id\n", ""
        ),
    ],
)
def test_apply_patch_skips_status_callback_hook_when_anchor_is_incompatible(content):
    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")

    assert patcher.PATCH_BEGIN in patched
    assert patcher.STATUS_PATCH_BEGIN not in patched


def test_remove_patch_lenient_removes_previous_status_callback_block():
    content = _status_callback_fixture()
    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")
    previous = patched.replace(
        "handle_status_from_hermes_locals as _hfc_handle_status",
        "old_status_handler as _hfc_handle_status",
    )

    assert patcher.remove_patch_lenient(previous) == content


def test_remove_patch_rejects_corrupt_status_callback_block():
    patched = patcher.apply_patch(
        _status_callback_fixture(), strategy="gateway_run_013_plus"
    )
    corrupt = patched.replace(
        patcher.STATUS_PATCH_END,
        patcher.STATUS_PATCH_BEGIN + "\n        " + patcher.STATUS_PATCH_END,
        1,
    )

    with pytest.raises(ValueError, match="status callback patch markers"):
        patcher.remove_patch(corrupt)


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
    assert "can_stage_exact_base_completion as _hfc_can_stage_exact" in block
    assert "stage_message_completed_from_hermes_locals_async as _hfc_stage_exact" in block
    assert "if not _hfc_exact_staged:" in block
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


_ALREADY_SENT_HANDLER = (
    "class GatewayRunner:\n"
    "    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
    "        response = 'ok'\n"
    "        _response_time = 1\n"
    "        agent_result = {}\n"
    "        if agent_result.get(\"already_sent\") and not agent_result.get(\"failed\"):\n"
    "            if response:\n"
    "                pass\n"
    "            return None\n"
    "        return response\n"
    "\n"
    "    def _reply_anchor_for_event(self, event):\n"
    "        return getattr(event, 'reply_to_message_id', None) or event.message_id\n"
)


def test_complete_hook_inserted_before_already_sent_early_return():
    """Hermes 0.18.x: streamed turns return None from the already_sent branch
    before the final `return response`, so the completion hook must run first."""
    patched = patcher.apply_patch(_ALREADY_SENT_HANDLER, strategy="gateway_run_013_plus")

    assert patcher.COMPLETE_PATCH_BEGIN in patched
    assert patched.index(patcher.COMPLETE_PATCH_BEGIN) < patched.index(
        'if agent_result.get("already_sent")'
    )
    ast.parse(patched)


def test_apply_patch_migrates_owned_pre_exact_completion_block():
    old_block = "".join(
        patcher._render_pre_exact_complete_hook_block_with_reply_anchor(
            "        ",
            "\n",
        )
    )
    source = _ALREADY_SENT_HANDLER.replace(
        '        if agent_result.get("already_sent")',
        old_block + '        if agent_result.get("already_sent")',
    )

    upgraded = patcher.apply_patch(source, strategy="gateway_run_013_plus")

    assert "stage_message_completed_from_hermes_locals_async" in upgraded
    assert upgraded != source
    assert patcher.apply_patch(upgraded, strategy="gateway_run_013_plus") == upgraded
    ast.parse(upgraded)


def test_complete_hook_keeps_final_return_location_without_already_sent_branch():
    content = (
        "class GatewayRunner:\n"
        "    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "        response = 'ok'\n"
        "        _response_time = 1\n"
        "        agent_result = {}\n"
        "        return response\n"
    )

    patched = patcher.apply_patch(content, strategy="gateway_run_013_plus")
    lines = patched.splitlines()
    marker_line = next(
        index for index, line in enumerate(lines) if patcher.COMPLETE_PATCH_END in line
    )

    assert lines[marker_line + 1].strip() == "return response"


def test_013_plus_complete_hook_derives_reply_anchor_message_id():
    patched = patcher.apply_patch(_ALREADY_SENT_HANDLER, strategy="gateway_run_013_plus")
    complete_block = patched[
        patched.index(patcher.COMPLETE_PATCH_BEGIN) : patched.index(
            patcher.COMPLETE_PATCH_END
        )
    ]

    assert (
        "_hfc_completed_message_id = self._reply_anchor_for_event(event)"
        in complete_block
    )
    assert '"message_id": _hfc_completed_message_id' in complete_block


def test_legacy_complete_hook_does_not_reference_reply_anchor():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    response = await run_agent(message)\n"
        "    _response_time = 1\n"
        "    agent_result = {}\n"
        "    return response\n"
    )

    patched = patcher.apply_patch(content, strategy="legacy_gateway_run")
    complete_block = patched[
        patched.index(patcher.COMPLETE_PATCH_BEGIN) : patched.index(
            patcher.COMPLETE_PATCH_END
        )
    ]

    assert "_reply_anchor_for_event" not in complete_block


def test_apply_complete_patch_migrates_stale_block_before_already_sent_branch():
    """A block installed by an older version after the already_sent branch is
    dead code on Hermes 0.18.x; re-applying must move it before the branch."""
    from hermes_feishu_card.install.patcher import _render_complete_hook_block

    stale_block = "".join(_render_complete_hook_block("        ", "\n"))
    content = (
        "class GatewayRunner:\n"
        "    async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "        response = 'ok'\n"
        "        _response_time = 1\n"
        "        agent_result = {}\n"
        "        if agent_result.get(\"already_sent\") and not agent_result.get(\"failed\"):\n"
        "            return None\n"
        + stale_block
        + "        return response\n"
    )

    patched = patcher._apply_complete_patch(content, strategy="gateway_run_013_plus")

    assert patched.count(patcher.COMPLETE_PATCH_BEGIN) == 1
    assert patched.index(patcher.COMPLETE_PATCH_BEGIN) < patched.index(
        'if agent_result.get("already_sent")'
    )
    ast.parse(patched)


def test_queued_complete_patch_tolerates_interleaved_stream_confirmation():
    """Newer Hermes interleaves a multi-line _stream_confirmed_final_delivery
    call between the first_response assignment and the anchor line; the patch
    must not be silently skipped."""
    content = (
        "async def _drain_queue(self):\n"
        "    result = {}\n"
        "    _previewed = bool(result.get('response_previewed'))\n"
        "    first_response = result.get('final_response', '')\n"
        "    _already_streamed = _stream_confirmed_final_delivery(\n"
        "        _sc,\n"
        "        first_response,\n"
        "        previewed=_previewed,\n"
        "    )\n"
        "    if first_response and not _already_streamed:\n"
        "        await adapter.send('chat', first_response)\n"
    )

    patched = patcher._apply_queued_complete_patch(content)

    assert patcher.QUEUED_COMPLETE_PATCH_BEGIN in patched
    lines = patched.splitlines()
    marker_line = next(
        index
        for index, line in enumerate(lines)
        if patcher.QUEUED_COMPLETE_PATCH_BEGIN in line
    )
    anchor_line = next(
        index
        for index, line in enumerate(lines)
        if line.strip() == "if first_response and not _already_streamed:"
    )
    assert marker_line < anchor_line
    ast.parse(patched)


def test_hfc_command_patch_enforces_maintenance_admission_before_commands():
    block = "".join(patcher._render_hfc_command_hook_block("    ", "\n"))

    admission = block.index("maintenance_admission_from_hermes_locals")
    command = block.index("handle_hfc_command_from_hermes_locals")
    assert admission < command
    assert "if await _hfc_enforce_maintenance_admission(locals()):" in block
    ast.parse("async def patched(self, event):\n" + block)


_EXACT_BASE_SOURCE = '''class BasePlatformAdapter:
    async def _process_message_background(self, event, session_key):
        delivery_attempted = False
        delivery_succeeded = False

        def _record_delivery(result):
            return None

        interrupt_event = self._active_sessions.get(session_key)
        try:
            response = await self._message_handler(event)
            is_ephemeral_response = isinstance(response, EphemeralReply)
            response, _ephemeral_ttl = self._unwrap_ephemeral(response)
            if response and interrupt_event.is_set() and session_key in self._pending_messages:
                response = None
            if not response:
                logger.debug("empty response")
            if response:
                force_document_attachments = "[[as_document]]" in response
                _response_pre_extract = response
                media_files, response = self.extract_media(response)
                media_files = self.filter_media_delivery_paths(media_files)
                images, text_content = self.extract_images(response)
                text_content = _strip_media_directives(text_content).strip()
                local_files = []
                if not is_ephemeral_response:
                    local_files, text_content = self.extract_local_files(text_content)
                    local_files = self.filter_local_delivery_paths(local_files)
                if not (text_content or images or local_files or media_files):
                    _recovered = _strip_media_directives(response).strip()
                    if _recovered:
                        text_content = _recovered
                _final_thread_metadata = _mark_notify_metadata(_thread_metadata)
                _tts_path = None
                if text_content and not media_files:
                    _tts_path = await make_tts(text_content)
                _tts_caption_delivered = False
                if _tts_path:
                    telegram_tts_caption = text_content
                    tts_result = await self.play_tts(
                        chat_id=event.source.chat_id,
                        audio_path=_tts_path,
                        caption=telegram_tts_caption,
                        metadata=_final_thread_metadata,
                    )
                    _tts_caption_delivered = bool(
                        telegram_tts_caption and getattr(tts_result, "success", False)
                    )
                # Send the text portion.
                if text_content and not _tts_caption_delivered:
                    delivery_adapter = self._final_delivery_adapter(event.source)
                    _reply_anchor = _reply_anchor_for_event(event)
                    _obligation_id = None
                    if not is_ephemeral_response:
                        try:
                            from gateway.delivery_ledger import (
                                compute_obligation_id,
                                ledger_enabled,
                                mark_attempting,
                                record_obligation,
                            )
                            if ledger_enabled():
                                _obligation_id = compute_obligation_id(
                                    session_key,
                                    str(getattr(event, "message_id", "") or ""),
                                    text_content,
                                )
                                record_obligation(
                                    obligation_id=_obligation_id,
                                    session_key=session_key,
                                    platform=str(event.source.platform.value),
                                    chat_id=event.source.chat_id,
                                    thread_id=getattr(event.source, "thread_id", None),
                                    content=text_content,
                                )
                                mark_attempting(_obligation_id)
                        except Exception:
                            _obligation_id = None
                    result = await delivery_adapter._send_with_retry(
                        chat_id=event.source.chat_id,
                        content=text_content,
                        reply_to=_reply_anchor,
                        metadata=_final_thread_metadata,
                    )
                    _record_delivery(result)
                    if _obligation_id is not None:
                        try:
                            from gateway.delivery_ledger import mark_delivered, mark_failed
                            if getattr(result, "success", False):
                                mark_delivered(_obligation_id)
                            else:
                                mark_failed(_obligation_id, str(result.error or ""))
                        except Exception:
                            pass
        finally:
            self._active_sessions.pop(session_key, None)
'''


def test_apply_base_patch_inserts_exact_hooks_at_semantic_boundaries():
    patched = patcher.apply_base_patch(_EXACT_BASE_SOURCE)

    no_text = patched.index(patcher.EXACT_BASE_NO_TEXT_PATCH_BEGIN)
    send_guard = patched.index("if text_content and not _tts_caption_delivered:")
    ledger_attempt = patched.index("mark_attempting(_obligation_id)")
    final = patched.index(patcher.EXACT_BASE_FINAL_DELIVERY_PATCH_BEGIN)
    send = patched.index("result = await delivery_adapter._send_with_retry(")
    assert no_text < send_guard < ledger_attempt < final < send
    assert "if not text_content or _tts_caption_delivered:" in patched
    assert "await _hfc_finalize_exact_base_no_text({" in patched
    assert '"source": event.source' in patched
    assert (
        "delivery_adapter, text_content, _reply_anchor, _final_thread_metadata = "
        "await _hfc_prepare_exact_base_final_delivery({" in patched
    )
    assert '"content": text_content' in patched
    assert '"obligation_id": _obligation_id' in patched
    assert '"reply_to": _reply_anchor' in patched
    assert '"metadata": _final_thread_metadata' in patched
    ast.parse(patched)
    assert patcher.apply_base_patch(patched) == patched
    assert patcher.remove_base_patch(patched) == _EXACT_BASE_SOURCE


def test_apply_base_patch_accepts_hermes_v020_async_ledger_contract():
    content = HERMES_V020_BASE_FIXTURE.read_text(encoding="utf-8")

    patched = patcher.apply_base_patch(content)

    assert patched.index(patcher.EXACT_BASE_NO_TEXT_PATCH_BEGIN) < patched.index(
        "if text_content and not _tts_caption_delivered:"
    )
    assert patched.index(
        "await asyncio.to_thread(\n                                    mark_attempting,"
    ) < patched.index(
        "result = await delivery_adapter._send_with_retry("
    )
    assert patched.index(patcher.EXACT_BASE_FINAL_DELIVERY_PATCH_BEGIN) < patched.index(
        "result = await delivery_adapter._send_with_retry("
    )
    ast.parse(patched)
    assert patcher.apply_base_patch(patched) == patched
    assert patcher.remove_base_patch(patched) == content


@pytest.mark.parametrize(
    "content",
    [
        _EXACT_BASE_SOURCE.replace("\n", "\r\n"),
        _EXACT_BASE_SOURCE.rstrip("\n"),
        _EXACT_BASE_SOURCE.replace("\n", "\r\n").rstrip("\r\n"),
    ],
)
def test_base_patch_round_trip_is_byte_identical_for_newlines(content):
    patched = patcher.apply_base_patch(content)

    assert patcher.remove_base_patch(patched) == content
    assert patcher.remove_base_patch_lenient(patched) == content


@pytest.mark.parametrize(
    "content",
    [
        _EXACT_BASE_SOURCE.replace(
            "media_files = self.filter_media_delivery_paths(media_files)",
            "media_files = list(media_files)",
        ),
        _EXACT_BASE_SOURCE.replace(
            "text_content = _strip_media_directives(text_content).strip()",
            "text_content = text_content.strip()",
        ),
        _EXACT_BASE_SOURCE.replace(
            "content=text_content,",
            "content=response,",
        ),
        _EXACT_BASE_SOURCE.replace(
            "reply_to=_reply_anchor,",
            "reply_to=None,",
        ),
        _EXACT_BASE_SOURCE.replace(
            "mark_attempting(_obligation_id)",
            "mark_attempting('different')",
        ),
        _EXACT_BASE_SOURCE.replace(
            "if text_content and not _tts_caption_delivered:",
            "if text_content:",
        ),
        _EXACT_BASE_SOURCE
        + "\nclass BasePlatformAdapter:\n"
        + "    async def _process_message_background(self, event, session_key):\n"
        + "        pass\n",
    ],
)
def test_apply_base_patch_fails_closed_for_inexact_or_ambiguous_contract(content):
    with pytest.raises(ValueError, match="safe BasePlatformAdapter contract"):
        patcher.apply_base_patch(content)


def test_remove_base_patch_strict_rejects_changed_owned_body_but_lenient_recovers():
    patched = patcher.apply_base_patch(_EXACT_BASE_SOURCE)
    corrupt = patched.replace(
        "await _hfc_finalize_exact_base_no_text({",
        "await _hfc_finalize_exact_base_no_text_changed({",
        1,
    )

    with pytest.raises(ValueError, match="corrupt exact base patch markers"):
        patcher.remove_base_patch(corrupt)
    assert patcher.remove_base_patch_lenient(corrupt) == _EXACT_BASE_SOURCE


def test_base_patch_rejects_partial_or_ambiguous_owned_markers():
    patched = patcher.apply_base_patch(_EXACT_BASE_SOURCE)
    partial = patched.replace(patcher.EXACT_BASE_FINAL_DELIVERY_PATCH_END, "", 1)
    duplicate = patched.replace(
        patcher.EXACT_BASE_NO_TEXT_PATCH_BEGIN,
        patcher.EXACT_BASE_NO_TEXT_PATCH_BEGIN
        + "\n                "
        + patcher.EXACT_BASE_NO_TEXT_PATCH_BEGIN,
        1,
    )

    for content in (partial, duplicate):
        with pytest.raises(ValueError, match="corrupt exact base patch markers"):
            patcher.apply_base_patch(content)
        with pytest.raises(ValueError, match="corrupt exact base patch markers"):
            patcher.remove_base_patch_lenient(content)
