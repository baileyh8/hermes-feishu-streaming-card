# Exact Hermes a7198a8855ad98681114ff5138eb01fe132a62e7 run_adapters.py excerpts.
# Tests execute the upstream profile ingress handler, not synthetic HFC locals.
import contextlib
from contextlib import suppress

class HermesProfileIngress:
    @staticmethod
    def _scope_or_null(scope_factory, profile_home):
        """``scope_factory(profile_home)`` or a nullcontext when the profile home is unknown."""
        return scope_factory(profile_home) if profile_home is not None else contextlib.nullcontext()

    @staticmethod
    def _stamp_event_profile(event, profile_name: str) -> None:
        """Best-effort: stamp ``source.profile`` on an inbound event that has none yet."""
        with suppress(Exception):
            if getattr(event, "source", None) is not None and not event.source.profile:
                event.source.profile = profile_name

    def _make_profile_message_handler(self, profile_name: str):
        """Message handler that stamps source.profile, then delegates under the profile scope
        (auth runs BEFORE the agent-turn scope, so the profile's ``.env`` must be visible here)."""
        from gateway.run import _async_profile_runtime_scope
        profile_home = self._profile_home_or_none(profile_name)

        async def _handler(event):
            self._stamp_event_profile(event, profile_name)
            async with self._scope_or_null(_async_profile_runtime_scope, profile_home):
                return await self._handle_message(event)

        return _handler

