"""Task-local provenance for known native home/startup/shutdown notice producers.

Ordinary adapter.send calls, historical messages and unknown producer signatures
never acquire recall authority merely because their text resembles a notice.
"""
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256
import inspect


ONLINE = "♻️ Gateway online — Hermes is back and ready."
RESTART = ("⚠️ Hermes is restarting — your current task will be interrupted. "
           "Send any message after the restart and I'll try to resume where you left off.")
SHUTDOWN = ("⚠️ Hermes is shutting down — your current task will be interrupted. "
            "When it is back online, send any message and I'll try to pick up where we left off.")
_CONTEXT = ContextVar("hfc_owned_native_notice", default=None)


def _platform(value):
    return str(getattr(value, "value", value) or "").lower()


def _context(runner, adapter, chat, thread, text):
    profiles = set()
    primary = str(getattr(runner, "_primary_profile_name", "default") or "default")
    registries = [(primary, getattr(runner, "adapters", None))]
    others = getattr(runner, "_profile_adapters", None)
    if isinstance(others, dict):
        registries.extend(others.items())
    for profile, mapping in registries:
        if isinstance(mapping, dict) and any(_platform(key) == "feishu" and value is adapter for key, value in mapping.items()):
            profiles.add(profile)
    app_id = getattr(adapter, "_app_id", None)
    if len(profiles) != 1 or not isinstance(app_id, str) or not app_id or len(app_id) > 256:
        return None
    profile = next(iter(profiles))
    if not all(isinstance(value, str) and len(value) <= 256 for value in (profile, chat, thread)) or not chat:
        return None
    return dict(adapter=adapter, text=text, chat=chat, thread=thread,
                route={"profile_id":profile, "chat_id":chat, "conversation_id":thread,
                       "app_id_hash":sha256(app_id.encode()).hexdigest()})


def notice_route_for_send(adapter, chat, text, metadata):
    context = _CONTEXT.get()
    metadata = metadata if isinstance(metadata, dict) else {}
    if (not isinstance(context, dict) or context["adapter"] is not adapter
            or context["chat"] != chat or context["text"] != text
            or str(metadata.get("thread_id") or "") != context["thread"]):
        return None
    return dict(context["route"])


def install_notice_producers(runner_type):
    for name, names in (
        ("_send_home_channel_message", ["self","platform","home","transport","message","failure_fmt"]),
        ("_send_shutdown_notice", ["self","adapter","chat_id","msg","kind","platform_str","send_kwargs"]),
    ):
        original = getattr(runner_type, name, None)
        if getattr(original, "_hfc_notice_producer", False) or not inspect.iscoroutinefunction(original):
            continue
        try:
            parameters = list(inspect.signature(original).parameters.values())
        except (TypeError, ValueError):
            continue
        expected_kinds = [inspect.Parameter.POSITIONAL_OR_KEYWORD] * len(names)
        if name == "_send_shutdown_notice":
            expected_kinds[-1] = inspect.Parameter.VAR_KEYWORD
        if [p.name for p in parameters] != names or [p.kind for p in parameters] != expected_kinds:
            continue
        setattr(runner_type, name, _wrap(original, name))


def _wrap(original, name):
    signature = inspect.signature(original)
    @wraps(original)
    async def wrapped(*args, **kwargs):
        context = None
        try:
            bound = signature.bind(*args, **kwargs).arguments
            runner = bound["self"]
            if name == "_send_home_channel_message":
                transport = bound["transport"]
                home = bound["home"]
                if (_platform(bound["platform"]) == "feishu" and bound["message"] == ONLINE
                        and getattr(transport, "is_relay", True) is False):
                    context = _context(runner, transport.adapter, str(home.chat_id), str(home.thread_id or ""), ONLINE)
            elif (bound["platform_str"] == "feishu" and bound["kind"] in {"home channel", "active chat"}
                  and bound["msg"] in {RESTART, SHUTDOWN}):
                metadata = bound.get("send_kwargs", {}).get("metadata") or {}
                context = _context(runner, bound["adapter"], bound["chat_id"],
                                   str(metadata.get("thread_id") or ""), bound["msg"])
        except (AttributeError, TypeError, ValueError):
            pass
        token = _CONTEXT.set(context)
        try:
            return await original(*args, **kwargs)
        finally:
            _CONTEXT.reset(token)
    wrapped._hfc_notice_producer = True
    return wrapped
