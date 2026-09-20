"""Task-local provenance for known native home/startup/shutdown notice producers.

Ordinary adapter.send calls, historical messages and unknown producer signatures
never acquire recall authority merely because their text resembles a notice.
"""
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256
import inspect
import re
import weakref


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
            allowed = producer == 'approval' and context['adapter'] is adapter and text == EXPIRED_APPROVAL
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
    """Bind the native expired-approval correction to its actual registered bot."""
    _install_delivery_envelopes(type(adapter))
    original = getattr(type(adapter), '_resolve_approval', None)
    try:
        adapter._hfc_notice_runner_ref = weakref.ref(runner)
        if getattr(original, '_hfc_notice_producer', False) or not inspect.iscoroutinefunction(original):
            return
        parameters=list(inspect.signature(original).parameters.values())
        if ([p.name for p in parameters] != ['self','approval_id','choice','user_name','open_id','chat_id']
                or [p.kind for p in parameters] != [inspect.Parameter.POSITIONAL_OR_KEYWORD]*4 + [inspect.Parameter.KEYWORD_ONLY]*2):
            return
    except (TypeError, AttributeError, ValueError):
        return
    @wraps(original)
    async def wrapped(self, *args, **kwargs):
        ref=getattr(self,'_hfc_notice_runner_ref',None)
        owner=ref() if callable(ref) else None
        token=_CONTEXT.set(dict(producer='approval',runner=owner,adapter=self) if owner is not None else None)
        try:
            return await original(self,*args,**kwargs)
        finally:
            _CONTEXT.reset(token)
    wrapped._hfc_notice_producer=True
    setattr(type(adapter),'_resolve_approval',wrapped)


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
