"""Provenance for known Hermes notice producers and native recall ownership.

Ordinary adapter.send calls, historical messages and unknown producer signatures
never acquire recall authority merely because their text resembles a notice.
"""
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256
import inspect
import re
import sys


ONLINE = "♻️ Gateway online — Hermes is back and ready."
RESTART = ("⚠️ Hermes is restarting — your current task will be interrupted. "
           "Send any message after the restart and I'll try to resume where you left off.")
SHUTDOWN = ("⚠️ Hermes is shutting down — your current task will be interrupted. "
            "When it is back online, send any message and I'll try to pick up where we left off.")
RESTARTED = "♻ Gateway restarted successfully. Your session continues."
EXPIRED_APPROVAL = "⌛ That approval had already expired — the command was not run (it timed out or was resolved elsewhere)."
DRAIN_TEXTS = frozenset(
    text for action in ('restarting', 'shutting down') for text in (
        f'⏳ Gateway {action} — queued for the next turn after it comes back.',
        f'⏳ Gateway is {action} and is not accepting another turn right now.',
        f'⏳ Gateway is {action} and is not accepting new work right now.',
    )
)
_BACKGROUND_SUCCESS = re.compile(r'✅ Background task finished(?: — `[^`\r\n]+`)?(?: \((?:\d+h \d+m|\d+m \d+s|\d+s)\))?')
_CONTEXT = ContextVar("hfc_owned_native_notice", default=None)
_DELIVERY = ContextVar("hfc_native_notice_delivery", default=None)
_REVIEW_SEND = ContextVar("hfc_background_review_send", default=None)

_REVIEW_SCHEDULE = "background_review_callback scheduling error"
_COLD_REVIEW_SCHEDULE = "cold /refine summary delivery scheduling failed"


def _at_call_site(frame, module_name, *names):
    module = sys.modules.get(module_name)
    return (frame is not None and module is not None
            and frame.f_globals is vars(module) and frame.f_code.co_name in names)


def _review_send(caller, label, coro, send_wrapper):
    # The scheduled coroutine is the exact adapter.send call made by the review
    # producer. Interim assistant text also uses _send_status_text, with a
    # different caller and label. Do not use _interim_send as provenance.
    if not inspect.iscoroutine(coro) or coro.cr_code is not send_wrapper.__code__:
        return None
    if label == _COLD_REVIEW_SCHEDULE and _at_call_site(caller, "gateway.session_cold_review", "deliver"):
        values = caller.f_locals
        adapter, source, message = values.get("adapter"), values.get("source"), values.get("message")
    elif label == _REVIEW_SCHEDULE:
        if _at_call_site(caller, "gateway.run_turn_progress", "_schedule"):
            sender = caller.f_back
            if not _at_call_site(sender, "gateway.run_turn_progress", "_send_status_text"):
                return None
            producer = sender.f_back
            if not _at_call_site(producer, "gateway.run_turn_runner", "deliver"):
                return None
            values = sender.f_locals
            message = values.get("text")
        elif _at_call_site(caller, "gateway.run", "_deliver_bg_review_message"):
            values = caller.f_locals
            message = values.get("message")
        else:
            return None
        ctx = values.get("ctx")
        adapter, source = getattr(ctx, "_status_adapter", None), getattr(ctx, "source", None)
    else:
        return None

    args = coro.cr_frame.f_locals if coro.cr_frame is not None else {}
    metadata = args.get("metadata")
    route_thread = str(getattr(source, "thread_id", "") or "")
    send_thread = str(metadata.get("thread_id") or "") if isinstance(metadata, dict) else ""
    if (adapter is None or args.get("self") is not adapter or _platform(getattr(source, "platform", None)) != "feishu"
            or args.get("chat_id") != getattr(source, "chat_id", None)
            or (route_thread and route_thread != send_thread)
            or not isinstance(message, str) or args.get("content") != message):
        return None
    return adapter, args["chat_id"], message, source


def install_review_notice_scheduler(runner_type, send_wrapper):
    # Both the pre-split Gateway and the current TurnRunner schedule the real
    # send after the turn reply. Cold /refine imports the same scheduler
    # directly. Bind only the existing in-process aliases, with no import hook.
    gateway = sys.modules.get("gateway.run")
    async_utils = sys.modules.get("agent.async_utils")
    if runner_type is not getattr(gateway, "GatewayRunner", None) or async_utils is None:
        return
    original = getattr(async_utils, "safe_schedule_threadsafe", None)
    if original is not getattr(gateway, "safe_schedule_threadsafe", None) or not inspect.isfunction(original):
        return
    if getattr(original, "_hfc_review_notice_scheduler", False):
        return
    try:
        parameters = inspect.signature(original).parameters.values()
    except (TypeError, ValueError):
        return
    positional, keyword = inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY
    if [(p.name, p.kind) for p in parameters] != [
        ("coro", positional), ("loop", positional), ("logger", keyword),
        ("log_message", keyword), ("log_level", keyword),
    ]:
        return

    @wraps(original)
    def schedule(coro, loop, **kwargs):
        label = kwargs.get("log_message")
        if label not in (_REVIEW_SCHEDULE, _COLD_REVIEW_SCHEDULE):
            return original(coro, loop, **kwargs)
        frame = inspect.currentframe()
        try:
            proof = _review_send(frame.f_back if frame is not None else None, label, coro, send_wrapper)
        except Exception:
            proof = None
        finally:
            del frame
        if proof is None:
            return original(coro, loop, **kwargs)
        # run_coroutine_threadsafe inherits the submitter's context. The queue,
        # generation check, returned Future and native scheduling stay upstream.
        token = _REVIEW_SEND.set(proof)
        try:
            return original(coro, loop, **kwargs)
        finally:
            _REVIEW_SEND.reset(token)

    schedule._hfc_review_notice_scheduler = True
    gateway.safe_schedule_threadsafe = async_utils.safe_schedule_threadsafe = schedule


def take_review_source(adapter, chat_id, content):
    proof = _REVIEW_SEND.get()
    if proof is not None and proof[0] is adapter and proof[1:3] == (chat_id, content):
        _REVIEW_SEND.set(None)
        return proof[3]
    return None


def adapter_card_notice_source(kind, caller):
    """Return the known producer's source, or None when only its egress is available."""
    if kind in {"background-process", "background-task"}:
        # The complete legacy wire envelopes have their own classifier.
        return True, None
    elif kind == "skill-loading":
        for module in ("gateway.run_turn_progress", "gateway.run"):
            if _at_call_site(caller, module, "_send_progress_text", "_edit_progress_message"):
                values = caller.f_locals
                ctx = values.get("ctx") or getattr(values.get("self"), "_ctx", None)
                return True, getattr(ctx, "source", None)
    elif kind in {"compression", "context-cap", "session-reset"}:
        if kind != "session-reset" and _at_call_site(caller, "gateway.run", "_send_or_update_status_coro"):
            return True, None
        if kind == "compression" and _at_call_site(caller, "gateway.platforms.base", "emit_warning"):
            caller = caller.f_back
            if _at_call_site(caller, "gateway.run_turn_hygiene", "_hmwa_hygiene_notify"):
                return True, caller.f_locals.get("source")
        legacy_notice = kind in {"compression", "session-reset"} and _at_call_site(
            caller, "gateway.run", "_handle_message_with_agent",
        )
        reset_notice = kind == "session-reset" and _at_call_site(
            caller, "gateway.run_turn_prepare", "_hmwa_deliver_auto_reset_notice",
        )
        if legacy_notice or reset_notice:
            return True, caller.f_locals.get("source")
    return False, None


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


def notice_for_send(adapter, chat, text, metadata):
    context = _CONTEXT.get()
    if context is None and isinstance(_DELIVERY.get(), dict):
        context = _DELIVERY.get().get('notice')
    metadata = metadata if isinstance(metadata, dict) else {}
    if isinstance(context, dict) and 'producer' in context:
        producer = context['producer']
        if producer == 'restart':
            allowed = text in {RESTARTED, RESTARTED.replace('♻', '♻️')}
        elif producer == 'drain':
            allowed = text in DRAIN_TEXTS and chat == context['chat']
        elif producer == 'watcher':
            allowed = (isinstance(text, str) and _BACKGROUND_SUCCESS.fullmatch(text) is not None
                       and text == context['text'] and chat == context['chat']
                       and str(metadata.get('thread_id') or '') == context['thread'])
        else:
            allowed = False
        if not allowed:
            return None
        context = _context(context['runner'], adapter, chat, str(metadata.get('thread_id') or ''), text)
        if context is None:
            return None
        context['family'] = 'restart' if producer in {'restart','drain'} else ''
        context['content'] = text.replace('♻ ', '♻️ ', 1) if producer == 'restart' else text
    if (not isinstance(context, dict) or context["adapter"] is not adapter
            or context["chat"] != chat or context["text"] != text
            or str(metadata.get("thread_id") or "") != context["thread"]):
        return None
    return {'route':dict(context['route']), 'family':context.get('family','restart'),
            'content':context.get('content',text)}


def notice_route_for_send(adapter, chat, text, metadata):
    notice = notice_for_send(adapter, chat, text, metadata)
    return notice['route'] if notice else None


def install_notice_producers(runner_type):
    for name, names in (
        ("_send_home_channel_message", ["self","platform","home","transport","message","failure_fmt"]),
        ("_send_shutdown_notice", ["self","adapter","chat_id","msg","kind","platform_str","send_kwargs"]),
        ("_send_restart_notification", ["self"]),
        ("_send_watcher_message", ["self","platform_name","chat_id","thread_id","message_text","watcher"]),
        ("_send_busy_drain_notice", ["self","event","session_key","effective_mode"]),
        ("_hm_handle_running_session_message", ["self","event","source","_quick_key"]),
        ("_hm_dispatch_quick_and_plugin_commands", ["self","event","source","command"]),
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
            if name == '_send_restart_notification':
                context = dict(producer='restart',runner=runner)
            elif name == '_send_watcher_message' and bound['platform_name']=='feishu':
                context = dict(producer='watcher',runner=runner,chat=bound['chat_id'],
                               thread=str(bound['thread_id'] or ''),text=bound['message_text'])
            elif name == '_send_busy_drain_notice':
                source=bound['event'].source
                if _platform(source.platform)=='feishu':
                    context = dict(producer='drain',runner=runner,chat=source.chat_id)
            elif name == "_send_home_channel_message":
                transport = bound["transport"]
                home = bound["home"]
                if (_platform(bound["platform"]) == "feishu" and bound["message"] == ONLINE
                        and getattr(transport, "is_relay", True) is False):
                    context = _context(runner, transport.adapter, str(home.chat_id), str(home.thread_id or ""), ONLINE)
            elif (name == '_send_shutdown_notice' and bound["platform_str"] == "feishu" and bound["kind"] in {"home channel", "active chat"}
                  and bound["msg"] in {RESTART, SHUTDOWN}):
                metadata = bound.get("send_kwargs", {}).get("metadata") or {}
                context = _context(runner, bound["adapter"], bound["chat_id"],
                                   str(metadata.get("thread_id") or ""), bound["msg"])
        except (AttributeError, TypeError, ValueError, KeyError):
            pass
        token = _CONTEXT.set(context)
        try:
            result = await original(*args, **kwargs)
            # Returned drain replies are sent later by Base. Carry provenance only
            # within the exact Base delivery invocation, never in a global text cache.
            if name.startswith('_hm_'):
                delivery=_DELIVERY.get()
                text=result[1] if name=='_hm_dispatch_quick_and_plugin_commands' and isinstance(result,tuple) and len(result)==3 and result[0] is True else result
                if (isinstance(delivery,dict) and delivery['event'] is bound['event']
                        and getattr(runner,'_draining',False) is True
                        and isinstance(text,str) and text in DRAIN_TEXTS
                        and _platform(getattr(bound.get('source'),'platform',None))=='feishu'):
                    delivery['notice']=dict(producer='drain',runner=runner,chat=getattr(bound['source'],'chat_id',''))
            return result
        finally:
            _CONTEXT.reset(token)
    wrapped._hfc_notice_producer = True
    return wrapped


def install_adapter_notice_producers(adapter, runner):
    """Wrap deferred sends, without granting native approval corrections a TTL.

    Native _resolve_approval may discover expiry after its callback has already
    displayed Approved. Its correction can be the only record that no command
    ran; a known producer alone is not evidence of another complete receipt.
    """
    _install_delivery_envelopes(type(adapter))


def _install_delivery_envelopes(adapter_type):
    for name,names,kinds in (
        ('_process_message_background',['self','event','session_key'],[inspect.Parameter.POSITIONAL_OR_KEYWORD]*3),
        ('_dispatch_inline_reply',['self','event','log_cmd'],[inspect.Parameter.POSITIONAL_OR_KEYWORD]*2+[inspect.Parameter.KEYWORD_ONLY]),
    ):
        original=getattr(adapter_type,name,None)
        if getattr(original,'_hfc_notice_delivery',False) or not inspect.iscoroutinefunction(original):continue
        try:parameters=list(inspect.signature(original).parameters.values())
        except (TypeError,ValueError):continue
        if [p.name for p in parameters]!=names or [p.kind for p in parameters]!=kinds:continue
        setattr(adapter_type,name,_delivery_envelope(original))


def _delivery_envelope(original):
    @wraps(original)
    async def wrapped(self,event,*args,**kwargs):
        token=_DELIVERY.set({'event':event})
        try:
            return await original(self,event,*args,**kwargs)
        finally:
            _DELIVERY.reset(token)
    wrapped._hfc_notice_delivery=True
    return wrapped
