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


def wrapper_is_exact(wrapper: ast.AsyncFunctionDef) -> bool:
    """Validate the 0.22.5 wrapper as a narrow post-delivery usage hook."""
    args = wrapper.args
    positional = (*args.posonlyargs, *args.args)
    if (
        wrapper.decorator_list
        or args.kwonlyargs
        or args.vararg is not None
        or args.kwarg is not None
        or args.defaults
        or args.kw_defaults
        or tuple(arg.arg for arg in positional) != ("self", *WRAPPER_ARGS)
    ):
        return False

    body = list(wrapper.body)
    if body and _is_docstring_expr(body[0]):
        body = body[1:]
    if len(body) != 3:
        return False
    assigned, conditional, returned = body
    if (
        not isinstance(assigned, ast.Assign)
        or len(assigned.targets) != 1
        or not isinstance(assigned.targets[0], ast.Name)
        or assigned.targets[0].id != "delivered"
        or not isinstance(assigned.value, ast.Await)
        or not call_matches(assigned.value.value, DELIVERY, WRAPPER_ARGS)
    ):
        return False
    if (
        not isinstance(conditional, ast.If)
        or conditional.orelse
        or not _same_expression(
            conditional.test, 'isinstance(agent_result.get("usage_limit"), dict)'
        )
        or len(conditional.body) != 1
    ):
        return False
    schedule = conditional.body[0]
    if not isinstance(schedule, ast.Expr) or not isinstance(schedule.value, ast.Call):
        return False
    schedule_call = schedule.value
    if (
        not _same_expression(schedule_call.func, "self._schedule_usage_limit_offer")
        or len(schedule_call.args) != 2
        or not _same_expression(schedule_call.args[0], "event")
        or not _same_expression(schedule_call.args[1], 'agent_result["usage_limit"]')
        or tuple(
            (keyword.arg, ast.unparse(keyword.value))
            for keyword in schedule_call.keywords
        )
        != (("session_key", "session_key"), ("generation", "run_generation"))
    ):
        return False
    return (
        isinstance(returned, ast.Return)
        and isinstance(returned.value, ast.Name)
        and returned.value.id == "delivered"
    )
