"""Narrow, instance-local repair for the SDK's verified CARD-frame drop path.

No SDK files or global Client class are changed. Unknown implementations remain
untouched; the ordinary SDK controls fragmentation, response encoding and ACKs.
"""
from __future__ import annotations

import ast
from functools import lru_cache
import hashlib
import inspect
import textwrap
from types import MethodType
from typing import Any

# Body AST of lark-oapi 1.6.8 Client._handle_data_frame. Ignore source formatting
# and the outer FunctionDef's Python-version-specific fields, not behavior.
_DROP_BODY_SHA256 = "78ec17eb4a9538e7e3f93fba28c48133303c86df64eb772114d13df68355b858"


@lru_cache(maxsize=8)
def _repaired_function(original: Any) -> Any:
    if (not inspect.iscoroutinefunction(original)
            or original.__module__ != "lark_oapi.ws.client"
            or original.__qualname__ != "Client._handle_data_frame"
            or original.__closure__):
        return None
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(original)))
        function = tree.body[0]
        if (not isinstance(function, ast.AsyncFunctionDef)
                or ast.dump(function.args) != ast.dump(ast.parse(
                    "async def f(self, frame: Frame): pass").body[0].args)):
            return None
        body = "\n".join(ast.dump(n, include_attributes=False) for n in function.body)
        if hashlib.sha256(body.encode()).hexdigest() != _DROP_BODY_SHA256:
            return None
        matches = [n for n in ast.walk(function) if isinstance(n, ast.If)
                   and ast.dump(n.test) == ast.dump(ast.parse(
                       "message_type == MessageType.CARD", mode="eval").body)]
        if len(matches) != 1:
            return None
        matches[0].body = ast.parse(
            "result = self._event_handler._do_without_validation(pl)"
        ).body
        ast.fix_missing_locations(tree)
        namespace = dict(original.__globals__)
        exec(compile(tree, "<hfc-feishu-card-frame-compat>", "exec"), namespace)
        repaired = namespace[function.name]
        repaired._hfc_card_frame_repair = True
        return repaired
    except (OSError, TypeError, ValueError, SyntaxError):
        return None


def card_frame_dispatch_needs_repair(client: Any) -> bool:
    method = getattr(client, "_handle_data_frame", None)
    original = getattr(method, "__func__", None)
    return bool(original is not None and _repaired_function(original) is not None)


def repair_card_frame_dispatch(client: Any) -> bool:
    method = getattr(client, "_handle_data_frame", None)
    original = getattr(method, "__func__", None)
    repaired = _repaired_function(original) if original is not None else None
    if repaired is None:
        return False
    client._handle_data_frame = MethodType(repaired, client)
    return True
