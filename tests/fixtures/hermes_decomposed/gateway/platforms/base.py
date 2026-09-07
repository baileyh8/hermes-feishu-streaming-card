from __future__ import annotations

# Contract excerpts from Hermes 79445a496c; no runtime installation needed.
class BasePlatformAdapter:

    async def _record_delivery_obligation(
        self, event: MessageEvent, session_key: str, text_content: str,
        delivery_adapter: "BasePlatformAdapter", is_ephemeral_response: bool) -> Optional[str]:
        """Ledger the final response BEFORE the send so a crash before platform ACK redelivers on
        next boot; best-effort, skips slash-command and ephemeral replies. Returns the obligation id
        or None."""
        if is_ephemeral_response or str(event.text or "").lstrip().startswith(
            ("/", self.typed_command_prefix or "!")):
            return None
        try:
            from gateway.delivery_ledger import (
                compute_obligation_id, ledger_enabled, mark_attempting, record_obligation)
            if not await asyncio.to_thread(ledger_enabled):
                return None
            source = event.source
            obligation_id = compute_obligation_id(
                session_key, str(getattr(event, "message_id", "") or ""), text_content)
            await asyncio.to_thread(
                record_obligation, obligation_id=obligation_id, session_key=session_key,
                platform=str(getattr(source.platform, "value", source.platform)),
                chat_id=source.chat_id, thread_id=getattr(source, "thread_id", None),
                content=text_content,
                adapter_profile=getattr(delivery_adapter, "_owner_profile", None))
            await asyncio.to_thread(mark_attempting, obligation_id)
            return obligation_id
        except Exception:
            logger.debug("delivery ledger record failed", exc_info=True)
            return None

    async def _finalize_delivery_obligation(
        self, obligation_id: str, result: Any, event: MessageEvent,
        delivery_adapter: "BasePlatformAdapter") -> None:
        """Mark the ledger row delivered/failed (best-effort). On ``send_path_degraded`` with a
        replacement adapter live, trigger another redelivery sweep (the watcher's may have run
        before this failure landed; atomic claiming keeps it idempotent)."""
        try:
            from gateway.delivery_ledger import mark_delivered, mark_failed
            if getattr(result, "success", False):
                await asyncio.to_thread(mark_delivered, obligation_id)
                return
            error = str(getattr(result, "error", "") or "")
            await asyncio.to_thread(mark_failed, obligation_id, error)
            if error == "send_path_degraded":
                redeliver = getattr(
                    self.gateway_runner, "_redeliver_failed_obligations_for_platform", None)
                live = self._final_delivery_adapter(event.source)
                if live is not delivery_adapter and callable(redeliver):
                    await redeliver(event.source.platform,
                                    profile=getattr(delivery_adapter, "_owner_profile", None))
        except Exception:
            logger.debug("delivery ledger update failed", exc_info=True)

    async def _send_final_text(
        self, event: MessageEvent, session_key: str, text_content: str, metadata: Dict[str, Any],
        is_ephemeral_response: bool, ephemeral_ttl: int, record_delivery: Callable) -> None:
        """Send the final text on the CURRENT transport (a reconnect may have replaced
        this adapter), ledger-bracketed; the message-id owner owns the ephemeral delete."""
        delivery_adapter = self._final_delivery_adapter(event.source)
        logger.info("[%s] Sending response (%d chars) to %s", delivery_adapter.name,
                    len(text_content), event.source.chat_id)
        _obligation_id = await self._record_delivery_obligation(
            event, session_key, text_content, delivery_adapter, is_ephemeral_response)
        result = await delivery_adapter._send_with_retry(
            chat_id=event.source.chat_id, content=text_content,
            reply_to=_reply_anchor_for_event(event), metadata=metadata)
        record_delivery(result)
        if _obligation_id is not None:
            await self._finalize_delivery_obligation(_obligation_id, result, event, delivery_adapter)
        if ephemeral_ttl and ephemeral_ttl > 0 and result.success and result.message_id:
            delivery_adapter._schedule_ephemeral_delete(
                event.source.chat_id, result.message_id, ephemeral_ttl)

    async def _extract_response_content(self, response: str, event: MessageEvent, session_key: str,
                                        *, is_ephemeral_response: bool) -> "_ExtractedResponse":
        """Split a handler response into deliverable text + attachments. Order matters: MEDIA tags →
        image URLs → residual directives → bare local paths (skipped for ephemeral notices so config
        paths stay text; unknown-extension MEDIA tags survive for the bare-path detector). History
        dedup is bare-path only, off-loop, fail-open. An emptied non-empty response is recovered."""
        # Captured before extract_media strips it: images then go via send_document (no recompression).
        force_document = "[[as_document]]" in response
        pre_extract = response
        # Pre-extract snapshot for the #29346 recovery/invariant below.
        media_files, response = self.extract_media(response)
        media_files = self.filter_media_delivery_paths(media_files, session_key=session_key)
        images, text_content = self.extract_images(response)
        # Strip any remaining internal directives from message body (fixes #1561). _strip_media_directives
        # shares MEDIA_TAG_CLEANUP_RE, so a MEDIA: tag with an unknown extension is intentionally left in
        # the body for extract_local_files below to pick up rather than silently dropped (#34517).
        text_content = _strip_media_directives(text_content).strip()
        if images:
            logger.info("[%s] extract_images found %d image(s) in response (%d chars)", self.name, len(images), len(response))
        local_files = []
        if not is_ephemeral_response:
            local_files, text_content = self.extract_local_files(text_content)
            local_files = self.filter_local_delivery_paths(local_files, session_key=session_key)
            history = (await self._bounded_history_media_paths_for_session(session_key)
                       if local_files else None)
            if history:
                suppressed = [p for p in local_files if p in history]
                if suppressed:
                    logger.info("[%s] Suppressing %d bare local file path(s) already delivered in "
                                "this session: %s", self.name, len(suppressed), suppressed)
                    local_files = [p for p in local_files if p not in history]
            if local_files:
                logger.info("[%s] extract_local_files found %d file(s) in response", self.name, len(local_files))
        # A2 (#29346): extraction can reduce a non-empty response to empty text with no attachment, and the
        # `if text_content` guard below then drops it silently. Recover on every platform (#33842 was
        # Discord-only); the guard avoids duplicating an attachment.
        if not (text_content or images or local_files or media_files):
            _recovered = _strip_media_directives(response).strip()
            if _recovered:
                logger.warning("[%s] response_delivery_recovered: extract pipeline "
                               "reduced a non-empty response (%d chars) to empty with "
                               "no attachment; delivering recovered original to %s", self.name,
                               len(pre_extract), event.source.chat_id)
                text_content = _recovered
        return _ExtractedResponse(
            text_content=text_content, images=images, media_files=media_files,
            local_files=local_files, force_document_attachments=force_document, pre_extract=pre_extract)


    async def _process_message_background(self, event, session_key):
        _record_delivery = self.record_delivery
        response = await self._message_handler(event)
        is_ephemeral_response = False
        _ephemeral_ttl = 0
        _thread_metadata = {}
        delivery_attempted = False
        if not response:
            pass
        else:
            extracted = await self._extract_response_content(
                response, event, session_key, is_ephemeral_response=is_ephemeral_response)
            text_content, media_files = extracted.text_content, extracted.media_files
            _final_thread_metadata = _mark_notify_metadata(_thread_metadata)
            _tts_caption_delivered = False
            if text_content and not _tts_caption_delivered:
                await self._send_final_text(
                    event, session_key, text_content, _final_thread_metadata,
                    is_ephemeral_response, _ephemeral_ttl, _record_delivery)
            await self._deliver_attachments(
                event, extracted, _final_thread_metadata,
                anything_sent=delivery_attempted or _tts_caption_delivered)
