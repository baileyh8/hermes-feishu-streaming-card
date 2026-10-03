"""Optional, exact Base /stop cancellation seam; no execution policy lives here."""
import ast


BEGIN = "# HERMES_FEISHU_CARD_BASE_STOP_CANCEL_PATCH_BEGIN"
END = "# HERMES_FEISHU_CARD_BASE_STOP_CANCEL_PATCH_END"
_CANCEL = "await self.cancel_session_processing(session_key, release_guard=False, discard_pending=False)"


def _same(node, source):
    return ast.dump(node, include_attributes=False) == ast.dump(
        ast.parse(source).body[0], include_attributes=False)


def _fail():
    raise ValueError("unsafe stop cancellation contract")


def _anchor(content):
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
    # The reply must have returned before this single cancellation; no other
    # await or mutation may interleave the snapshot and the original call.
    attempts = [n for n in dispatch.body if isinstance(n, ast.Try)]
    if len(attempts) != 1 or len(attempts[0].body) != 2:
        _fail()
    reply, call = attempts[0].body
    if (not _same(reply, "await self._dispatch_inline_reply(event, log_cmd=cmd)")
            or not _same(call, _CANCEL)):
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
    calls = [n for n in ast.walk(dispatch) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "cancel_session_processing"]
    task_cancels = [n for n in ast.walk(cancel) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == "cancel"]
    if len(calls) != 1 or len(task_cancels) != 1:
        _fail()
    lines = content.splitlines(keepends=True)
    line = lines[call.lineno - 1]
    indent = line[:len(line) - len(line.lstrip())]
    if call.end_lineno != call.lineno or line.strip() != _CANCEL:
        _fail()
    return call.lineno - 1, indent


def _block(indent, newline):
    return [indent + line + newline for line in (
        BEGIN,
        "_hfc_stop_owner = None",
        "try:",
        "    from hermes_feishu_card.hook_runtime import capture_stop_cancellation as _hfc_capture_stop",
        "    _hfc_stop_owner = _hfc_capture_stop(self, event, session_key, cmd)",
        "except Exception:",
        "    pass",
        _CANCEL,
        "try:",
        "    from hermes_feishu_card.hook_runtime import schedule_cancelled_stop_turn as _hfc_schedule_stop",
        "    _hfc_schedule_stop(self, _hfc_stop_owner)",
        "except Exception:",
        "    pass",
        END,
    )]


def remove(content):
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
    if lines[first:last + 1] != _block(indent, newline):
        _fail()
    lines[first:last + 1] = [indent + _CANCEL + newline]
    restored = "".join(lines)
    if _anchor(restored) != (first, indent):
        _fail()
    return restored


def apply(content):
    restored = remove(content)
    location = _anchor(restored)
    if location is None:
        return restored
    index, indent = location
    lines = restored.splitlines(keepends=True)
    newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
    lines[index:index + 1] = _block(indent, newline)
    return "".join(lines)
