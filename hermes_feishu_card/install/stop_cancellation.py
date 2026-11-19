"""Optional, exact Base /stop cancellation seam; no execution policy lives here."""
import ast


BEGIN = "# HERMES_FEISHU_CARD_BASE_STOP_CANCEL_PATCH_BEGIN"
END = "# HERMES_FEISHU_CARD_BASE_STOP_CANCEL_PATCH_END"
_CANCEL = "await self.cancel_session_processing(session_key, release_guard=False, discard_pending=False)"
_LEGACY_PREFIX = '''current_guard = self._active_sessions.get(session_key)
command_guard = asyncio.Event()
self._active_sessions[session_key] = command_guard
thread_meta = _thread_metadata_for_source(event.source, _reply_anchor_for_event(event))'''
_LEGACY_REPLY = '''response = await self._message_handler(event)
_text, _eph_ttl = self._unwrap_ephemeral(response)
if _text:
    logger.info("[%s] Sending command '/%s' response (%d chars) to %s",
                self.name, cmd, len(_text), event.source.chat_id)
    _r = await self._send_with_retry(
        chat_id=event.source.chat_id, content=_text,
        reply_to=_reply_anchor_for_event(event),
        metadata=_mark_notify_metadata(thread_meta))
    if _eph_ttl > 0 and _r.success and _r.message_id:
        self._schedule_ephemeral_delete(
            chat_id=event.source.chat_id, message_id=_r.message_id, ttl_seconds=_eph_ttl)'''


def _same(node, source):
    return ast.dump(node, include_attributes=False) == ast.dump(
        ast.parse(source).body[0], include_attributes=False)


def _fail():
    raise ValueError("unsafe stop cancellation contract")


def _same_body(nodes, source):
    expected = ast.parse(source).body
    return len(nodes) == len(expected) and all(
        ast.dump(a, include_attributes=False) == ast.dump(b, include_attributes=False)
        for a, b in zip(nodes, expected))


def _legacy_reply(dispatch, attempt):
    """Verified pre-extraction reply, including its session and topic scope."""
    prefix = dispatch.body[:dispatch.body.index(attempt)]
    if (prefix and isinstance(prefix[0], ast.Expr)
            and isinstance(prefix[0].value, ast.Constant)
            and isinstance(prefix[0].value.value, str)):
        prefix = prefix[1:]
    if prefix and _same(prefix[0], '''logger.debug(
        "[%s] Command '/%s' bypassing active-session guard for %s",
        self.name, cmd, session_key)'''):
        prefix = prefix[1:]
    return (_same_body(prefix, _LEGACY_PREFIX)
            and _same_body(attempt.body[:-1], _LEGACY_REPLY))


def _cancel_lines(indent, newline, multiline):
    lines = ("await self.cancel_session_processing(", "    session_key,",
             "    release_guard=False,", "    discard_pending=False,", ")") if multiline else (_CANCEL,)
    return [indent + line + newline for line in lines]


_DISPATCH_PEER = "gateway/platforms/base_session_command.py"
_VERIFIED_SESSION_COMMAND_SHA256 = (
    "d59c102b32fc1d3bdca74487496f96e7af6b52ff6ec05e4652aff714115bff33"
)
_DISPATCH_AWAIT = "await dispatch_active_session_command(self, event, session_key, cmd)"
_DISPATCH_IMPORT = "from gateway.platforms.base_session_command import dispatch_active_session_command"


def _dispatch_statements(dispatch):
    body = list(dispatch.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return body


def _is_extracted_session_command(dispatch):
    body = _dispatch_statements(dispatch)
    return (
        len(body) == 2
        and _same(body[0], _DISPATCH_IMPORT)
        and _same(body[1], _DISPATCH_AWAIT)
    )


def _validate_extracted_session_command(peer: str) -> None:
    """Reply first, then cancel only the accepted interrupt, same session_key."""
    from hashlib import sha256
    if sha256(peer.encode("utf-8")).hexdigest() != _VERIFIED_SESSION_COMMAND_SHA256:
        _fail()
    try:
        tree = ast.parse(peer)
    except SyntaxError:
        _fail()
    funcs = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
             and n.name == "dispatch_active_session_command"]
    if len(funcs) != 1:
        _fail()
    func = funcs[0]
    if [arg.arg for arg in (*func.args.posonlyargs, *func.args.args)] != [
        "adapter", "event", "session_key", "cmd"
    ] or func.args.kwonlyargs or func.args.vararg or func.args.kwarg:
        _fail()
    reply = ast.parse("await adapter._dispatch_inline_reply(event, log_cmd=cmd)").body[0]
    cancel = ast.parse(
        "await adapter.cancel_session_processing(session_key, release_guard=False, discard_pending=False)"
    ).body[0]
    replies = [n for n in ast.walk(func) if ast.dump(n, include_attributes=False)
               == ast.dump(reply, include_attributes=False)]
    cancels = [n for n in ast.walk(func) if ast.dump(n, include_attributes=False)
               == ast.dump(cancel, include_attributes=False)]
    if len(replies) != 1 or len(cancels) != 1 or replies[0].lineno >= cancels[0].lineno:
        _fail()
    # The cancel must stay behind the runner's accepted-interrupt verdict.
    guarded = [
        n for n in ast.walk(func)
        if isinstance(n, ast.If)
        and _same(
            n,
            "if adapter._authorization_check is None or getattr(event, \"_interrupt_accepted\", False) is True:\n"
            "    adapter._discard_text_debounce(session_key)\n"
            "    await adapter.cancel_session_processing(session_key, release_guard=False, discard_pending=False)\n"
            "    cancelled = True",
        )
    ]
    if len(guarded) != 1 or cancels[0] not in guarded[0].body:
        _fail()


def _anchor(content, peer_sources=None):
    tree = ast.parse(content)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "BasePlatformAdapter"]
    if len(classes) != 1:
        return None
    owner = classes[0]
    dispatches = [n for n in owner.body if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "_dispatch_active_session_command"]
    if not dispatches:
        return None  # Older Base has no controlled cancellation seam.
    cancels = [n for n in owner.body if isinstance(n, ast.AsyncFunctionDef)
               and n.name == "cancel_session_processing"]
    if len(dispatches) != 1 or len(cancels) != 1:
        _fail()
    dispatch, cancel = dispatches[0], cancels[0]
    if ([arg.arg for arg in dispatch.args.args] != ["self", "event", "session_key", "cmd"]
            or [arg.arg for arg in cancel.args.args] != ["self", "session_key"]
            or [arg.arg for arg in cancel.args.kwonlyargs] != ["release_guard", "discard_pending"]):
        _fail()
    extracted = _is_extracted_session_command(dispatch)
    if extracted:
        if not peer_sources or _DISPATCH_PEER not in peer_sources:
            _fail()
        _validate_extracted_session_command(peer_sources[_DISPATCH_PEER])
    else:
        # The reply must have returned before this single cancellation; no other
        # await or mutation may interleave the snapshot and the original call.
        attempts = [n for n in dispatch.body if isinstance(n, ast.Try)]
        if len(attempts) != 1 or not attempts[0].body:
            _fail()
        attempt = attempts[0]
        call = attempt.body[-1]
        if (not _same(call, _CANCEL)
                or not (_same_body(attempt.body[:-1], "await self._dispatch_inline_reply(event, log_cmd=cmd)")
                        or _legacy_reply(dispatch, attempt))):
            _fail()
    # Match the actual owner-pop -> expected-cancel -> task.cancel -> bounded
    # shielded wait. Merely finding a similarly named method is not evidence.
    pops = [n for n in cancel.body if _same(n, "task = self._session_tasks.pop(session_key, None)")]
    branches = [n for n in cancel.body if isinstance(n, ast.If)
                and ast.dump(n.test, include_attributes=False) == ast.dump(
                    ast.parse("task is not None and not task.done()", mode="eval").body,
                    include_attributes=False)]
    if len(pops) != 1 or len(branches) != 1 or cancel.body.index(branches[0]) != cancel.body.index(pops[0]) + 1:
        _fail()
    prefix = cancel.body[:cancel.body.index(pops[0])]
    if (prefix and isinstance(prefix[0], ast.Expr)
            and isinstance(prefix[0].value, ast.Constant)
            and isinstance(prefix[0].value.value, str)):
        prefix = prefix[1:]
    # Hermes 7533bd clears this session's requeue counter before popping its
    # owner. Only this exact synchronous expression is an inert prefix.
    if prefix and not (len(prefix) == 1
                       and _same(prefix[0], "self._requeue_counts.pop(session_key, None)")):
        _fail()
    body = branches[0].body
    # A leading debug log is inert; reject any other extra statement.
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Call):
        func = body[0].value.func
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == "logger" and func.attr == "debug"):
            body = body[1:]
    if (len(body) != 3
            or not _same(body[0], "self._expected_cancelled_tasks.add(task)")
            or not _same(body[1], "task.cancel()")
            or not isinstance(body[2], ast.Try) or len(body[2].body) != 1
            or not _same(body[2].body[0], "await asyncio.wait_for(asyncio.shield(task), timeout=5.0)")):
        _fail()
    # No hidden second call/cancel can substitute a different task or session.
    task_cancels = [n for n in ast.walk(cancel) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == "cancel"]
    if len(task_cancels) != 1:
        _fail()
    lines = content.splitlines(keepends=True)
    if extracted:
        dispatched = [n for n in _dispatch_statements(dispatch) if _same(n, _DISPATCH_AWAIT)]
        if len(dispatched) != 1:
            _fail()
        call = dispatched[0]
        kind = "dispatch"
        expected = _dispatch_lines
    else:
        calls = [n for n in ast.walk(dispatch) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "cancel_session_processing"]
        if len(calls) != 1:
            _fail()
        kind = "cancel"
        expected = _cancel_lines
    line = lines[call.lineno - 1]
    indent = line[:len(line) - len(line.lstrip())]
    newline = "\r\n" if line.endswith("\r\n") else "\n"
    multiline = call.end_lineno != call.lineno
    if lines[call.lineno - 1:call.end_lineno] != expected(indent, newline, multiline):
        _fail()
    return call.lineno - 1, call.end_lineno, indent, multiline, kind


def _dispatch_lines(indent, newline, multiline=False):
    # The verified extracted wrapper is a single await; multiline is rejected.
    if multiline:
        _fail()
    return [indent + _DISPATCH_AWAIT + newline]


def _block(indent, newline, multiline=False, kind="cancel"):
    middle = (_dispatch_lines(indent, newline, multiline) if kind == "dispatch"
              else _cancel_lines(indent, newline, multiline))
    return [indent + line + newline for line in (
        BEGIN,
        "_hfc_stop_owner = None",
        "try:",
        "    from hermes_feishu_card.hook_runtime import capture_stop_cancellation as _hfc_capture_stop",
        "    _hfc_stop_owner = _hfc_capture_stop(self, event, session_key, cmd)",
        "except Exception:",
        "    pass",
    )] + middle + [indent + line + newline for line in (
        "try:",
        "    from hermes_feishu_card.hook_runtime import schedule_cancelled_stop_turn as _hfc_schedule_stop",
        "    _hfc_schedule_stop(self, _hfc_stop_owner)",
        "except Exception:",
        "    pass",
        END,
    )]


def remove(content, peer_sources=None):
    if BEGIN not in content and END not in content:
        return content
    if content.count(BEGIN) != 1 or content.count(END) != 1:
        _fail()
    lines = content.splitlines(keepends=True)
    begins = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        _fail()
    first, last = begins[0], ends[0]
    indent = lines[first][:len(lines[first]) - len(lines[first].lstrip())]
    newline = "\r\n" if lines[first].endswith("\r\n") else "\n"
    kind = None
    multiline = False
    for candidate, multi in (("dispatch", False), ("cancel", False), ("cancel", True)):
        if lines[first:last + 1] == _block(indent, newline, multi, candidate):
            kind = candidate
            multiline = multi
            break
    if kind is None:
        _fail()
    restored_lines = (_dispatch_lines if kind == "dispatch" else _cancel_lines)(indent, newline, multiline)
    lines[first:last + 1] = restored_lines
    restored = "".join(lines)
    if _anchor(restored, peer_sources=peer_sources) != (first, first + len(restored_lines), indent, multiline, kind):
        _fail()
    return restored


def apply(content, peer_sources=None):
    restored = remove(content, peer_sources=peer_sources)
    location = _anchor(restored, peer_sources=peer_sources)
    if location is None:
        return restored
    index, end, indent, multiline, kind = location
    lines = restored.splitlines(keepends=True)
    newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
    lines[index:end] = _block(indent, newline, multiline, kind)
    return "".join(lines)
