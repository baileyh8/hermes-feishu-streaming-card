class GatewayStartupMixin:
    async def _start_finish_wiring(self, connected_count):
        await self._start_post_connect_services(connected_count)
        await self._await_startup_boot_sends(planned_restart_notification_pending=False)

    async def _redeliver_claimed_obligations(self, claimed):
        for row in claimed:
            adapter = await self._obligation_adapter(row)
            content = row["content"]
            metadata = {"thread_id": row["thread_id"]} if row.get("thread_id") else None
            result = await adapter.send(chat_id=row["chat_id"], content=content, metadata=metadata)
        return result
