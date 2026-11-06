"""Pure AST contracts for Hermes' inherited turn-completion seam.

The 0.22.5 revision wraps the verified delivery method with a usage-limit
offer.  These predicates keep the wrapper recognition narrow: they validate
the exact call, argument order, return value, conditional schedule and the
agent:end ordering without importing or executing Hermes source.
"""
from __future__ import annotations

import ast

from .ast_contracts import _is_docstring_expr, _same_expression


POST_HOOK = "_hmwa_post_turn_hooks"
DELIVERY = "_hmwa_deliver_turn_response"
WRAPPER = "_deliver_turn_response_then_limit_offer"
METHODS = frozenset({POST_HOOK, DELIVERY, WRAPPER})
BOT_REPLY_GUARD = '(delivered or agent_result.get("already_sent")) and not intentional_silence'
BOT_REPLY_CALL = "self._note_bot_reply(source)"
DELIVERY_ARGS = (
    "event",
    "source",
    "session_entry",
    "session_key",
    "run_generation",
    "agent_result",
    "agent_messages",
    "response",
    "_footer_line",
    "_intentional_silence",
)
WRAPPER_ARGS = (
    "event",
    "source",
    "session_entry",
    "session_key",
    "run_generation",
    "agent_result",
    "agent_messages",
    "response",
    "footer_line",
    "intentional_silence",
)


def call_matches(call: ast.AST, method_name: str, expected_args: tuple[str, ...]) -> bool:
    return (
        isinstance(call, ast.Call)
        and _same_expression(call.func, f"self.{method_name}")
        and not call.keywords
        and tuple(ast.unparse(arg) for arg in call.args) == tuple(expected_args)
    )


def return_matches(
    node: ast.AST, method_name: str, expected_args: tuple[str, ...]
) -> bool:
    return (
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Await)
        and call_matches(node.value.value, method_name, expected_args)
    )


def dispatch_return(handler: ast.AST) -> ast.Return | None:
    """Find the one exact return that hands the turn to final delivery."""
    candidates = [
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.Return)
        and (
            return_matches(node, DELIVERY, DELIVERY_ARGS)
            or return_matches(node, WRAPPER, DELIVERY_ARGS)
        )
    ]
    return candidates[0] if len(candidates) == 1 else None


def _wrapper_signature_is_exact(wrapper: ast.AsyncFunctionDef) -> bool:
    args = wrapper.args
    positional = (*args.posonlyargs, *args.args)
    return not (
        wrapper.decorator_list
        or args.kwonlyargs
        or args.vararg is not None
        or args.kwarg is not None
        or args.defaults
        or args.kw_defaults
        or tuple(arg.arg for arg in positional) != ("self", *WRAPPER_ARGS)
    )


def _wrapper_body(wrapper: ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = list(wrapper.body)
    if body and _is_docstring_expr(body[0]):
        body = body[1:]
    return body


def _delivery_assignment_is_exact(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "delivered"
        and isinstance(node.value, ast.Await)
        and call_matches(node.value.value, DELIVERY, WRAPPER_ARGS)
    )


def _usage_limit_offer_is_exact(node: ast.AST) -> bool:
    if (
        not isinstance(node, ast.If)
        or node.orelse
        or not _same_expression(
            node.test, 'isinstance(agent_result.get("usage_limit"), dict)'
        )
        or len(node.body) != 1
    ):
        return False
    schedule = node.body[0]
    if not isinstance(schedule, ast.Expr) or not isinstance(schedule.value, ast.Call):
        return False
    schedule_call = schedule.value
    return (
        _same_expression(schedule_call.func, "self._schedule_usage_limit_offer")
        and len(schedule_call.args) == 2
        and _same_expression(schedule_call.args[0], "event")
        and _same_expression(schedule_call.args[1], 'agent_result["usage_limit"]')
        and tuple(
            (keyword.arg, ast.unparse(keyword.value))
            for keyword in schedule_call.keywords
        )
        == (("session_key", "session_key"), ("generation", "run_generation"))
    )


def _returned_delivered_is_exact(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Name)
        and node.value.id == "delivered"
    )


def _bot_reply_guard_is_exact(node: ast.AST) -> bool:
    """Accept only v0.23.0's synchronous, conditional bot-loop bookkeeping."""
    if (
        not isinstance(node, ast.If)
        or node.orelse
        or len(node.body) != 1
        or not _same_expression(node.test, BOT_REPLY_GUARD)
    ):
        return False
    statement = node.body[0]
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return False
    call = statement.value
    return (
        _same_expression(call.func, "self._note_bot_reply")
        and len(call.args) == 1
        and _same_expression(call.args[0], "source")
        and not call.keywords
    )


def _is_v0225_wrapper(wrapper: ast.AsyncFunctionDef) -> bool:
    body = _wrapper_body(wrapper)
    if len(body) != 3:
        return False
    assigned, conditional, returned = body
    return (
        _wrapper_signature_is_exact(wrapper)
        and _delivery_assignment_is_exact(assigned)
        and _usage_limit_offer_is_exact(conditional)
        and _returned_delivered_is_exact(returned)
    )


def _is_v0230_wrapper(wrapper: ast.AsyncFunctionDef) -> bool:
    """Accept the one extra v0.23.0 guard; every original statement stays exact."""
    body = _wrapper_body(wrapper)
    if len(body) != 4:
        return False
    assigned, bot_reply, conditional, returned = body
    return (
        _wrapper_signature_is_exact(wrapper)
        and _delivery_assignment_is_exact(assigned)
        and _bot_reply_guard_is_exact(bot_reply)
        and _usage_limit_offer_is_exact(conditional)
        and _returned_delivered_is_exact(returned)
    )


def source_has_v0230_wrapper(source: str) -> bool:
    """Recognise the exact installed wrapper before enabling its bot-reply gate."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    wrappers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == WRAPPER
    ]
    return len(wrappers) == 1 and _is_v0230_wrapper(wrappers[0])


def wrapper_is_exact(wrapper: ast.AsyncFunctionDef) -> bool:
    """Validate the 0.22.5 or exact 0.23.0 post-delivery usage wrapper."""
    return _is_v0225_wrapper(wrapper) or _is_v0230_wrapper(wrapper)
