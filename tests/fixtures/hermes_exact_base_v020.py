import asyncio


class BasePlatformAdapter:
    async def _process_message_background(self, event, session_key):
        def _record_delivery(result):
            return None

        interrupt_event = self._active_sessions.get(session_key)
        try:
            response = await self._message_handler(event)
            is_ephemeral_response = isinstance(response, EphemeralReply)
            if response:
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
                                await asyncio.to_thread(
                                    record_obligation,
                                    obligation_id=_obligation_id,
                                    session_key=session_key,
                                    platform=str(event.source.platform.value),
                                    chat_id=event.source.chat_id,
                                    thread_id=getattr(event.source, "thread_id", None),
                                    content=text_content,
                                )
                                await asyncio.to_thread(
                                    mark_attempting,
                                    _obligation_id,
                                )
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
                                await asyncio.to_thread(mark_delivered, _obligation_id)
                            else:
                                await asyncio.to_thread(
                                    mark_failed,
                                    _obligation_id,
                                    str(result.error or ""),
                                )
                        except Exception:
                            pass
        finally:
            self._active_sessions.pop(session_key, None)
