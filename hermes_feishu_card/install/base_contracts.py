"""Verify monolithic, decomposed and split BasePlatformAdapter contracts.

Returns exact insertion coordinates only after checking executable call order.
Owned ledger fragments are supplied by the patcher's existing renderer; this
module does not choose or apply a patch and does not import the coordinator.
"""
from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Any

from .ast_contracts import (
    _call_function,
    _direct_child_position,
    _exact_deliver_attachments,
    _is_exact_assignment_call,
    _is_exact_compute_obligation_assignment,
    _is_exact_constant_assignment,
    _is_exact_final_send_assignment,
    _is_exact_ledger_call,
    _is_exact_local_filter_assignment,
    _is_exact_mark_failed_call,
    _is_exact_media_filter_assignment,
    _is_exact_name_assignment,
    _is_exact_positional_call,
    _is_split_compute_obligation_assignment,
    _is_split_final_send_assignment,
    _is_split_finalize_obligation_call,
    _is_split_record_obligation_call,
    _is_strip_assignment,
    _line_indent,
    _same_expression,
    _unique_exact_base_node,
)


def _parse_exact_base_content(content: str) -> ast.Module:
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise ValueError("could not find safe BasePlatformAdapter contract") from exc
    return tree


def find_base_patch_locations(
    tree: ast.Module, lines: list[str], *, owned_ledger_blocks: tuple[str, ...] = (),
    peer_sources: Mapping[str, str] | None = None,
) -> tuple[tuple[int, str], tuple[int, str]]:
    """Return the two insertion locations after validating Hermes' pipeline."""
    if _has_split_exact_base(tree):
        if _has_decomposed_base(tree):
            return _find_split_decomposed_base_patch_locations(
                tree, lines, owned_ledger_blocks=owned_ledger_blocks, peer_sources=peer_sources,
            )
        # Only the reported decomposed layout has a verified split contract.
        raise ValueError("could not find safe BasePlatformAdapter contract")
    if _has_decomposed_base(tree):
        return _find_decomposed_base_patch_locations(tree, lines)
    method = _find_exact_base_process_method(tree)
    method_nodes = list(ast.walk(method))

    extract_media = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("media_files", "response"),
            owner="self",
            function="extract_media",
            args=("response",),
        ),
    )
    filter_media = _unique_exact_base_node(
        method_nodes,
        _is_exact_media_filter_assignment,
    )
    extract_images = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("images", "text_content"),
            owner="self",
            function="extract_images",
            args=("response",),
        ),
    )
    strip_text = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_strip_assignment(
            node,
            target="text_content",
            source="text_content",
        ),
    )
    extract_local = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("local_files", "text_content"),
            owner="self",
            function="extract_local_files",
            args=("text_content",),
        ),
    )
    filter_local = _unique_exact_base_node(
        method_nodes,
        _is_exact_local_filter_assignment,
    )
    recovered = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_strip_assignment(
            node,
            target="_recovered",
            source="response",
        ),
    )
    restore_text = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_name_assignment(
            node,
            target="text_content",
            value="_recovered",
        ),
    )
    final_metadata = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("_final_thread_metadata",),
            owner=None,
            function="_mark_notify_metadata",
            args=("_thread_metadata",),
        ),
    )
    tts_caption_default = _unique_exact_base_node(
        method_nodes,
        lambda node: _is_exact_constant_assignment(
            node,
            target="_tts_caption_delivered",
            value=False,
        ),
    )
    text_guard = _unique_exact_base_node(
        method_nodes,
        lambda node: isinstance(node, ast.If)
        and _same_expression(
            node.test,
            "text_content and not _tts_caption_delivered",
        ),
    )

    guard_nodes = list(ast.walk(text_guard))
    delivery_adapter = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("delivery_adapter",),
            owner="self",
            function="_final_delivery_adapter",
            args=("event.source",),
        ),
    )
    reply_anchor = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_assignment_call(
            node,
            targets=("_reply_anchor",),
            owner=None,
            function="_reply_anchor_for_event",
            args=("event",),
        ),
    )
    compute_obligation = _unique_exact_base_node(
        guard_nodes,
        _is_exact_compute_obligation_assignment,
    )
    obligation_default = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_constant_assignment(
            node,
            target="_obligation_id",
            value=None,
        )
        and node.lineno < compute_obligation.lineno,
    )
    record_obligation = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_ledger_call(
            node,
            function="record_obligation",
            required_keywords={
                "obligation_id": "_obligation_id",
                "content": "text_content",
            },
        ),
    )
    mark_attempting = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_positional_call(
            node,
            function="mark_attempting",
            args=("_obligation_id",),
        ),
    )
    send = _unique_exact_base_node(guard_nodes, _is_exact_final_send_assignment)
    record_delivery = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_positional_call(
            node,
            function="_record_delivery",
            args=("result",),
        ),
    )
    mark_delivered = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_positional_call(
            node,
            function="mark_delivered",
            args=("_obligation_id",),
        ),
    )
    mark_failed = _unique_exact_base_node(
        guard_nodes,
        lambda node: _is_exact_mark_failed_call(node),
    )

    # The ledger operations must finish in one direct child statement before
    # the send. Merely checking line order would accept a source drift where
    # `_send_with_retry` moved inside the ledger try, reopening the exact crash
    # window this patch is meant to close.
    send_position = _direct_child_position(text_guard.body, send)
    ledger_positions = {
        _direct_child_position(text_guard.body, node)
        for node in (compute_obligation, record_obligation, mark_attempting)
    }
    if (
        send_position is None
        or None in ledger_positions
        or len(ledger_positions) != 1
        or next(iter(ledger_positions)) >= send_position
    ):
        raise ValueError("could not find safe BasePlatformAdapter contract")

    ordered = (
        extract_media,
        filter_media,
        extract_images,
        strip_text,
        extract_local,
        filter_local,
        recovered,
        restore_text,
        final_metadata,
        tts_caption_default,
        text_guard,
        delivery_adapter,
        reply_anchor,
        obligation_default,
        compute_obligation,
        record_obligation,
        mark_attempting,
        send,
        record_delivery,
        mark_delivered,
        mark_failed,
    )
    if any(getattr(node, "lineno", None) is None for node in ordered):
        raise ValueError("could not find safe BasePlatformAdapter contract")
    line_numbers = [node.lineno for node in ordered]
    if line_numbers != sorted(line_numbers) or len(set(line_numbers)) != len(line_numbers):
        raise ValueError("could not find safe BasePlatformAdapter contract")

    no_text_index = text_guard.lineno - 1
    send_index = send.lineno - 1
    return (
        (no_text_index, _line_indent(lines, no_text_index)),
        (send_index, _line_indent(lines, send_index)),
    )


def _has_split_exact_base(tree: Any) -> bool:
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BasePlatformAdapter"
    ]
    return len(classes) == 1 and sum(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "send_final_ledgered"
        for node in classes[0].body
    ) == 1


def _validate_split_ledger_bracket(ledger: ast.AsyncFunctionDef, owned_ledger_blocks: tuple[str, ...]) -> None:
    """Require an executable, contiguous ledger bracket, not just ordered AST hits."""
    expected = ast.parse("""
delivery_adapter = self._final_delivery_adapter(event.source)
obligation_id = await self._record_delivery_obligation(event, session_key, text_content, delivery_adapter, is_ephemeral_response)
result = await delivery_adapter._send_with_retry(chat_id=event.source.chat_id, content=text_content, reply_to=reply_to, metadata=metadata)
if obligation_id is not None:
    await self._finalize_delivery_obligation(obligation_id, result, event, delivery_adapter)
return result, delivery_adapter
""").body
    body: list[ast.stmt] = []
    # Hermes may hand crash recovery from the turn marker to the ledger here.
    # Accept only this exact optional guard, once, after recording and before
    # sending. Keep it in generated source; never weaken the delivery bracket.
    release_guard = ast.parse("if obligation_id is not None:\n    await self._release_turn_marker(event)").body[0]
    release_seen = False
    # Hermes records reply latency after the actual send and before finalizing
    # the obligation. Preserve exactly this optional synchronous call, once.
    clock_stop = ast.parse("stop_reply_clock(delivery_adapter, event.source.chat_id, result)").body[0]
    clock_seen = False
    for index, node in enumerate(ledger.body):
        if len(body) == 3 and not clock_seen and ast.dump(node) == ast.dump(clock_stop):
            clock_seen = True
            continue
        if len(body) == 2 and not release_seen and ast.dump(node) == ast.dump(release_guard):
            release_seen = True
            continue
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        # The upstream informational log between adapter selection and recording
        # is the only non-contract statement accepted at this seam.
        if (len(body) == 1 and isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and _call_function(node.value) == ("logger", "info")):
            continue
        if len(body) == 2 and any(
            ast.dump(node) == ast.dump(ast.parse(block).body[0])
            for block in owned_ledger_blocks
        ):
            continue
        body.append(node)
    args = ledger.args
    if (args.vararg or args.kwarg or args.posonlyargs
            or [ast.dump(n) for n in body] != [ast.dump(n) for n in expected]):
        raise ValueError("could not find safe BasePlatformAdapter contract")


def _find_exact_base_process_method(tree: ast.Module) -> ast.AsyncFunctionDef:
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BasePlatformAdapter"
    ]
    if len(classes) != 1:
        raise ValueError("could not find safe BasePlatformAdapter contract")
    methods = [
        node
        for node in classes[0].body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_process_message_background"
    ]
    if len(methods) != 1:
        raise ValueError("could not find safe BasePlatformAdapter contract")
    return methods[0]


def _has_decomposed_base(tree: ast.Module) -> bool:
    return any(isinstance(c, ast.ClassDef) and c.name == "BasePlatformAdapter"
               and any(isinstance(n, ast.AsyncFunctionDef) and n.name == "_send_final_text" for n in c.body)
               for c in tree.body)


def _find_decomposed_base_patch_locations(tree: ast.Module, lines: list[str]) -> tuple[tuple[int, str], tuple[int, str]]:
    """Verify the extracted methods AND their exact call/argument boundaries."""
    error = "could not find safe BasePlatformAdapter contract"
    process = _find_exact_base_process_method(tree)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BasePlatformAdapter")
    def method(name: Any) -> Any:
        matches = [n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name]
        if len(matches) != 1:
            raise ValueError(error)
        return matches[0]
    extract, send, record, finish = (method(n) for n in (
        "_extract_response_content", "_send_final_text", "_record_delivery_obligation", "_finalize_delivery_obligation"))
    # Positional calls are only safe when the callee binds the same values.
    # Matching names somewhere in the body does not prove that boundary.
    for node, positional, keyword_only in (
        (process, ("self", "event", "session_key"), ()),
        (extract, ("self", "response", "event", "session_key"), ("is_ephemeral_response",)),
        (send, ("self", "event", "session_key", "text_content", "metadata",
                "is_ephemeral_response", "ephemeral_ttl", "record_delivery"), ()),
        (record, ("self", "event", "session_key", "text_content", "delivery_adapter",
                  "is_ephemeral_response"), ()),
        (finish, ("self", "obligation_id", "result", "event", "delivery_adapter"), ()),
    ):
        args = node.args
        if (tuple(a.arg for a in (*args.posonlyargs, *args.args)) != positional
                or tuple(a.arg for a in args.kwonlyargs) != keyword_only
                or args.vararg is not None or args.kwarg is not None):
            raise ValueError(error)
    def exact(scope: Any, source: Any) -> Any:
        expected = ast.parse(source).body[0]
        return _unique_exact_base_node(ast.walk(scope), lambda n: ast.dump(n) == ast.dump(expected))
    def ordered(nodes: Any) -> Any:
        if any(a.lineno >= b.lineno for a, b in zip(nodes, nodes[1:])):
            raise ValueError(error)
    extracted = exact(process, 'extracted = await self._extract_response_content(response, event, session_key, is_ephemeral_response=is_ephemeral_response)')
    assigned = exact(process, 'text_content, media_files = extracted.text_content, extracted.media_files')
    metadata = exact(process, '_final_thread_metadata = _mark_notify_metadata(_thread_metadata)')
    tts_default = exact(process, '_tts_caption_delivered = False')
    guard = _unique_exact_base_node(ast.walk(process), lambda n: isinstance(n, ast.If) and _same_expression(n.test, 'text_content and not _tts_caption_delivered'))
    delegated = exact(guard, 'await self._send_final_text(event, session_key, text_content, _final_thread_metadata, is_ephemeral_response, _ephemeral_ttl, _record_delivery)')
    if guard.body != [delegated]:
        raise ValueError(error)
    attachments = _exact_deliver_attachments(process)
    ordered([extracted, assigned, metadata, tts_default, guard, attachments])
    # All three stages must be siblings in the response branch.
    branches = [n for n in ast.walk(process) if isinstance(n, ast.If)
                and all(part in n.orelse for part in (extracted, assigned, metadata, tts_default, guard, attachments))]
    if len(branches) != 1 or not _same_expression(branches[0].test, 'not response'):
        raise ValueError(error)
    em = exact(extract, 'media_files, response = self.extract_media(response)')
    fm = _unique_exact_base_node(ast.walk(extract), _is_exact_media_filter_assignment)
    ei = exact(extract, 'images, text_content = self.extract_images(response)')
    strip = exact(extract, 'text_content = _strip_media_directives(text_content).strip()')
    el = exact(extract, 'local_files, text_content = self.extract_local_files(text_content)')
    fl = _unique_exact_base_node(ast.walk(extract), _is_exact_local_filter_assignment)
    recovered = exact(extract, '_recovered = _strip_media_directives(response).strip()')
    restored = exact(extract, 'text_content = _recovered')
    result = exact(extract, 'return _ExtractedResponse(text_content=text_content, images=images, media_files=media_files, local_files=local_files, force_document_attachments=force_document, pre_extract=pre_extract)')
    ordered([em, fm, ei, strip, el, fl, recovered, restored, result])
    adapter = exact(send, 'delivery_adapter = self._final_delivery_adapter(event.source)')
    ledger = exact(send, '_obligation_id = await self._record_delivery_obligation(event, session_key, text_content, delivery_adapter, is_ephemeral_response)')
    final_send = exact(send, 'result = await delivery_adapter._send_with_retry(chat_id=event.source.chat_id, content=text_content, reply_to=_reply_anchor_for_event(event), metadata=metadata)')
    delivered = exact(send, 'record_delivery(result)')
    finalized = exact(send, 'if _obligation_id is not None:\n    await self._finalize_delivery_obligation(_obligation_id, result, event, delivery_adapter)')
    if not all(n in send.body for n in (adapter, ledger, final_send, delivered, finalized)):
        raise ValueError(error)
    ordered([adapter, ledger, final_send, delivered, finalized])
    source = exact(record, 'source = event.source')
    computed = exact(record, 'obligation_id = compute_obligation_id(session_key, str(getattr(event, "message_id", "") or ""), text_content)')
    recorded = exact(record, 'await asyncio.to_thread(record_obligation, obligation_id=obligation_id, session_key=session_key, platform=str(getattr(source.platform, "value", source.platform)), chat_id=source.chat_id, thread_id=getattr(source, "thread_id", None), content=text_content, adapter_profile=getattr(delivery_adapter, "_owner_profile", None))')
    attempting = exact(record, 'await asyncio.to_thread(mark_attempting, obligation_id)')
    returned = exact(record, 'return obligation_id')
    ordered([source, computed, recorded, attempting, returned])
    tries = [n for n in record.body if isinstance(n, ast.Try) and all(x in n.body for x in (source, computed, recorded, attempting, returned))]
    if len(tries) != 1:
        raise ValueError(error)
    exact(finish, 'if getattr(result, "success", False):\n    await asyncio.to_thread(mark_delivered, obligation_id)\n    return')
    exact(finish, 'await asyncio.to_thread(mark_failed, obligation_id, error)')
    return ((guard.lineno - 1, _line_indent(lines, guard.lineno - 1)),
            (final_send.lineno - 1, _line_indent(lines, final_send.lineno - 1)))


def _find_split_decomposed_base_patch_locations(
    tree: ast.Module, lines: list[str], *, owned_ledger_blocks: tuple[str, ...] = (),
    peer_sources: Mapping[str, str] | None = None,
) -> tuple[tuple[int, str], tuple[int, str]]:
    """Verify decomposed Hermes after its ledger helper was extracted."""
    error = "could not find safe BasePlatformAdapter contract"
    process = _find_exact_base_process_method(tree)
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BasePlatformAdapter"
    )

    def method(name: Any) -> Any:
        matches = [
            node
            for node in cls.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == name
        ]
        if len(matches) != 1:
            raise ValueError(error)
        return matches[0]

    extract, send, ledger, record, finish = (
        method(name)
        for name in (
            "_extract_response_content",
            "_send_final_text",
            "send_final_ledgered",
            "_record_delivery_obligation",
            "_finalize_delivery_obligation",
        )
    )
    delegated_ledger = _is_delegated_ledger_bracket(ledger)
    if delegated_ledger:
        _validate_delegated_ledger_contract(
            tree, ledger, record, finish, owned_ledger_blocks, peer_sources,
        )
    else:
        _validate_split_ledger_bracket(ledger, owned_ledger_blocks)
    signatures = (
        (process, ("self", "event", "session_key"), ()),
        (
            extract,
            ("self", "response", "event", "session_key"),
            ("is_ephemeral_response",),
        ),
        (
            send,
            ("self", "event", "session_key", "text_content", "metadata",
             "is_ephemeral_response", "ephemeral_ttl", "record_delivery"),
            (),
        ),
        (
            ledger,
            ("self", "event", "session_key", "text_content", "metadata"),
            ("reply_to", "is_ephemeral_response"),
        ),
        (
            record,
            ("self", "event", "session_key", "text_content", "delivery_adapter",
             "is_ephemeral_response"),
            (),
        ),
        (
            finish,
            ("self", "obligation_id", "result", "event", "delivery_adapter"),
            (),
        ),
    )
    for node, positional, keyword_only in signatures:
        args = node.args
        if (
            tuple(arg.arg for arg in (*args.posonlyargs, *args.args)) != positional
            or tuple(arg.arg for arg in args.kwonlyargs) != keyword_only
            or args.vararg is not None
            or args.kwarg is not None
        ):
            raise ValueError(error)

    def exact(scope: Any, source: Any) -> Any:
        expected = ast.parse(source).body[0]
        return _unique_exact_base_node(
            ast.walk(scope), lambda node: ast.dump(node) == ast.dump(expected)
        )

    def ordered(nodes: Any) -> Any:
        if any(a.lineno >= b.lineno for a, b in zip(nodes, nodes[1:])):
            raise ValueError(error)

    extracted = exact(
        process,
        "extracted = await self._extract_response_content(response, event, session_key, is_ephemeral_response=is_ephemeral_response)",
    )
    assigned = exact(
        process,
        "text_content, media_files = extracted.text_content, extracted.media_files",
    )
    metadata = exact(
        process,
        "_final_thread_metadata = _mark_notify_metadata(_thread_metadata)",
    )
    tts_default = exact(process, "_tts_caption_delivered = False")
    guard = _unique_exact_base_node(
        ast.walk(process),
        lambda node: isinstance(node, ast.If)
        and _same_expression(node.test, "text_content and not _tts_caption_delivered"),
    )
    delegated = exact(
        guard,
        "await self._send_final_text(event, session_key, text_content, _final_thread_metadata, is_ephemeral_response, _ephemeral_ttl, _record_delivery)",
    )
    if guard.body != [delegated]:
        raise ValueError(error)
    attachments = _exact_deliver_attachments(process)
    ordered([extracted, assigned, metadata, tts_default, guard, attachments])
    branches = [
        node
        for node in ast.walk(process)
        if isinstance(node, ast.If)
        and all(
            part in node.orelse
            for part in (extracted, assigned, metadata, tts_default, guard, attachments)
        )
    ]
    if len(branches) != 1 or not _same_expression(branches[0].test, "not response"):
        raise ValueError(error)

    em = exact(extract, "media_files, response = self.extract_media(response)")
    fm = _unique_exact_base_node(ast.walk(extract), _is_exact_media_filter_assignment)
    ei = exact(extract, "images, text_content = self.extract_images(response)")
    strip = exact(extract, "text_content = _strip_media_directives(text_content).strip()")
    el = exact(extract, "local_files, text_content = self.extract_local_files(text_content)")
    fl = _unique_exact_base_node(ast.walk(extract), _is_exact_local_filter_assignment)
    recovered = exact(extract, "_recovered = _strip_media_directives(response).strip()")
    restored = exact(extract, "text_content = _recovered")
    if delegated_ledger:
        _require_exact_import(
            tree,
            "gateway.runtime_footer",
            ("footer_onto_photo_caption",),
            error,
        )
        media_import = exact(
            extract,
            "from gateway.platforms.docker_media_paths import hand_back_unreadable_media",
        )
        handed = exact(
            extract,
            "await hand_back_unreadable_media([p for p, _ in media_files] + local_files)",
        )
        result = exact(
            extract,
            "return footer_onto_photo_caption(self, _ExtractedResponse(text_content=text_content, images=images, media_files=media_files, local_files=local_files, force_document_attachments=force_document, pre_extract=pre_extract), _IMAGE_EXTS)",
        )
        # hand_back must run after local-path filtering and before history
        # recovery / the footer-caption return, so unreadable Docker media
        # is settled on the same session-scoped lists later captured by HFC.
        if media_import.lineno <= fl.lineno or handed.lineno <= media_import.lineno:
            raise ValueError(error)
        if recovered.lineno <= handed.lineno:
            raise ValueError(error)
        ordered([em, fm, ei, strip, el, fl, media_import, handed, recovered, restored, result])
    else:
        result = exact(
            extract,
            "return _ExtractedResponse(text_content=text_content, images=images, media_files=media_files, local_files=local_files, force_document_attachments=force_document, pre_extract=pre_extract)",
        )
        ordered([em, fm, ei, strip, el, fl, recovered, restored, result])

    delegated_call = exact(
        send,
        "result, delivery_adapter = await self.send_final_ledgered(event, session_key, text_content, metadata, reply_to=_reply_anchor_for_event(event), is_ephemeral_response=is_ephemeral_response)",
    )
    delivered = exact(send, "record_delivery(result)")
    if delegated_call.lineno >= delivered.lineno:
        raise ValueError(error)
    if not delegated_ledger:
        record_source = exact(record, "source = event.source")
        ledger_id = exact(
            record, "_ledger_id = getattr(event, \"ledger_message_id\", None)"
        )
        ledger_fallback = exact(
            record,
            "if _ledger_id is None:\n    _ledger_id = getattr(event, \"message_id\", \"\")",
        )
        computed = _unique_exact_base_node(
            ast.walk(record),
            lambda node: _is_split_compute_obligation_assignment(node),
        )
        recorded = exact(
            record,
            "await asyncio.to_thread(record_obligation, obligation_id=obligation_id, session_key=session_key, platform=str(getattr(source.platform, \"value\", source.platform)), chat_id=source.chat_id, thread_id=getattr(source, \"thread_id\", None), content=text_content, adapter_profile=getattr(delivery_adapter, \"_owner_profile\", None))",
        )
        attempting = exact(record, "await asyncio.to_thread(mark_attempting, obligation_id)")
        returned = exact(record, "return obligation_id")
        ordered([record_source, ledger_id, ledger_fallback, computed, recorded, attempting, returned])
        tries = [
            node
            for node in record.body
            if isinstance(node, ast.Try)
            and all(x in node.body for x in (record_source, ledger_id, ledger_fallback,
                                              computed, recorded, attempting, returned))
        ]
        if len(tries) != 1:
            raise ValueError(error)

    if delegated_ledger:
        final_send = _delegated_final_send_assignment(ledger)
    else:
        ledger_nodes = list(ast.walk(method("send_final_ledgered")))
        ledger_record = _unique_exact_base_node(ledger_nodes, _is_split_record_obligation_call)
        final_send = _unique_exact_base_node(ledger_nodes, _is_split_final_send_assignment)
        finalized = _unique_exact_base_node(ledger_nodes, _is_split_finalize_obligation_call)
        ledger_return = exact(
            method("send_final_ledgered"), "return result, delivery_adapter"
        )
        ordered([ledger_record, final_send, finalized, ledger_return])
    return (
        (guard.lineno - 1, _line_indent(lines, guard.lineno - 1)),
        (final_send.lineno - 1, _line_indent(lines, final_send.lineno - 1)),
    )


_DELEGATED_LEDGER_PEER = "gateway/platforms/base_delivery_ledger.py"
_VERIFIED_DELIVERY_LEDGER_SHA256 = (
    "8c8aefe86a6ec3bc05f77136f26bfb2b3d6f82cf7d533777e9ea5da26e6bdee0"
)


def _is_delegated_ledger_bracket(ledger: ast.AsyncFunctionDef) -> bool:
    """True only when send_final_ledgered returns the extracted ledger_bracket call."""
    statements = _executable_statements(ledger)
    if len(statements) != 2:
        return False
    nested, ret = statements
    return (
        isinstance(nested, ast.AsyncFunctionDef)
        and nested.name == "_send"
        and _is_exact_ledger_bracket_return(ret)
    )


def _executable_statements(func: ast.AST) -> list[ast.AST]:
    body = getattr(func, "body", [])
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return list(body[1:])
    return list(body)


def _is_exact_ledger_bracket_return(node: ast.AST) -> bool:
    expected = ast.parse(
        "return await ledger_bracket(self, event, session_key, text_content, _send, "
        "is_ephemeral_response=is_ephemeral_response)"
    ).body[0]
    return ast.dump(node) == ast.dump(expected)


def _require_exact_import(tree: ast.Module, module: str, names: tuple[str, ...], error: str) -> None:
    found: list[str] = []
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == module
            and node.level == 0
        ):
            found.extend(alias.name for alias in node.names if alias.asname is None)
    if not set(names) <= set(found):
        raise ValueError(error)


def _delegated_final_send_assignment(ledger: ast.AsyncFunctionDef) -> ast.stmt:
    error = "could not find safe BasePlatformAdapter contract"
    statements = _executable_statements(ledger)
    if len(statements) != 2 or not isinstance(statements[0], ast.AsyncFunctionDef):
        raise ValueError(error)
    node = _unique_exact_base_node(statements[0].body, _is_split_final_send_assignment)
    if not isinstance(node, ast.stmt):
        raise ValueError(error)
    return node


def _validate_delegated_ledger_contract(
    tree: ast.Module,
    ledger: ast.AsyncFunctionDef,
    record: ast.AsyncFunctionDef,
    finish: ast.AsyncFunctionDef,
    owned_ledger_blocks: tuple[str, ...],
    peer_sources: Mapping[str, str] | None,
) -> None:
    """Accept only the verified v0.23.2 ledger_bracket extraction.

    The adapter methods remain the subclass opt-out seam: they must forward the
    same session_key, event, text and delivery_adapter to the extracted
    helpers. The peer module must record before send and finalize from the
    result. Missing or drifted peers fail closed.
    """
    error = "could not find safe BasePlatformAdapter contract"
    _require_exact_import(
        tree,
        "gateway.platforms.base_delivery_ledger",
        ("finalize_final_obligation", "ledger_attempted", "ledger_bracket",
         "record_final_obligation"),
        error,
    )
    if not peer_sources or _DELEGATED_LEDGER_PEER not in peer_sources:
        raise ValueError(error)
    peer = peer_sources[_DELEGATED_LEDGER_PEER]
    from hashlib import sha256
    if sha256(peer.encode("utf-8")).hexdigest() != _VERIFIED_DELIVERY_LEDGER_SHA256:
        raise ValueError(error)
    try:
        peer_tree = ast.parse(peer)
    except SyntaxError as exc:
        raise ValueError(error) from exc
    helpers = {
        node.name: node
        for node in peer_tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name in {
            "ledger_bracket", "record_final_obligation", "finalize_final_obligation",
        }
    }
    if set(helpers) != {"ledger_bracket", "record_final_obligation", "finalize_final_obligation"}:
        raise ValueError(error)
    _validate_verified_ledger_bracket(helpers["ledger_bracket"])
    _validate_verified_record_final_obligation(helpers["record_final_obligation"])
    _validate_verified_finalize_final_obligation(helpers["finalize_final_obligation"])
    _validate_delegated_send_callback(ledger, owned_ledger_blocks)
    _validate_exact_forward(
        record,
        "return await record_final_obligation(self, event, session_key, text_content, delivery_adapter, is_ephemeral_response)",
    )
    _validate_exact_forward(
        finish,
        "await finalize_final_obligation(self, obligation_id, result, event, delivery_adapter)",
    )


def _validate_exact_forward(func: ast.AsyncFunctionDef, source: str) -> None:
    error = "could not find safe BasePlatformAdapter contract"
    statements = _executable_statements(func)
    expected = ast.parse(source).body[0]
    if len(statements) != 1 or ast.dump(statements[0]) != ast.dump(expected):
        raise ValueError(error)


def _validate_delegated_send_callback(
    ledger: ast.AsyncFunctionDef, owned_ledger_blocks: tuple[str, ...],
) -> None:
    error = "could not find safe BasePlatformAdapter contract"
    statements = _executable_statements(ledger)
    if len(statements) != 2 or not _is_exact_ledger_bracket_return(statements[1]):
        raise ValueError(error)
    callback = statements[0]
    if not isinstance(callback, ast.AsyncFunctionDef) or callback.name != "_send":
        raise ValueError(error)
    args = callback.args
    if (
        tuple(arg.arg for arg in (*args.posonlyargs, *args.args)) != ("delivery_adapter", "obligation_id")
        or args.kwonlyargs
        or args.vararg is not None
        or args.kwarg is not None
    ):
        raise ValueError(error)
    release_guard = ast.parse(
        "if obligation_id is not None:\n    await self._release_turn_marker(event)"
    ).body[0]
    clock_stop = ast.parse(
        "stop_reply_clock(delivery_adapter, event.source.chat_id, result)"
    ).body[0]
    log_ok = False
    release_seen = False
    body: list[ast.AST] = []
    for node in callback.body:
        if (
            not log_ok
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and _call_function(node.value) == ("logger", "info")
        ):
            log_ok = True
            continue
        if not release_seen and ast.dump(node) == ast.dump(release_guard):
            release_seen = True
            continue
        if any(
            ast.dump(node) == ast.dump(ast.parse(block).body[0])
            for block in owned_ledger_blocks
        ):
            continue
        body.append(node)
    expected = [
        ast.parse(
            "result = await delivery_adapter._send_with_retry("
            "chat_id=event.source.chat_id, content=text_content, reply_to=reply_to, metadata=metadata)"
        ).body[0],
        clock_stop,
        ast.parse("return result").body[0],
    ]
    if not log_ok or not release_seen or [ast.dump(n) for n in body] != [ast.dump(n) for n in expected]:
        raise ValueError(error)


def _validate_verified_ledger_bracket(func: ast.AsyncFunctionDef) -> None:
    error = "could not find safe BasePlatformAdapter contract"
    args = func.args
    if (
        tuple(arg.arg for arg in (*args.posonlyargs, *args.args))
        != ("adapter", "event", "session_key", "text_content", "send")
        or tuple(arg.arg for arg in args.kwonlyargs) != ("is_ephemeral_response",)
        or args.vararg is not None
        or args.kwarg is not None
    ):
        raise ValueError(error)
    expected = [
        ast.parse("delivery_adapter = adapter._final_delivery_adapter(event.source)").body[0],
        ast.parse(
            "obligation_id = await adapter._record_delivery_obligation("
            "event, session_key, text_content, delivery_adapter, is_ephemeral_response)"
        ).body[0],
        ast.parse("result = await send(delivery_adapter, obligation_id)").body[0],
        ast.parse(
            "if obligation_id is not None:\n"
            "    await adapter._finalize_delivery_obligation(obligation_id, result, event, delivery_adapter)"
        ).body[0],
        ast.parse("return result, delivery_adapter").body[0],
    ]
    statements = _executable_statements(func)
    if [ast.dump(n) for n in statements] != [ast.dump(n) for n in expected]:
        raise ValueError(error)


def _validate_verified_record_final_obligation(func: ast.AsyncFunctionDef) -> None:
    error = "could not find safe BasePlatformAdapter contract"
    args = func.args
    if (
        tuple(arg.arg for arg in (*args.posonlyargs, *args.args))
        != ("adapter", "event", "session_key", "text_content", "delivery_adapter", "is_ephemeral_response")
        or args.kwonlyargs
        or args.vararg is not None
        or args.kwarg is not None
    ):
        raise ValueError(error)

    def exact(scope: ast.AST, source: str) -> ast.stmt:
        expected = ast.parse(source).body[0]
        node = _unique_exact_base_node(
            ast.walk(scope), lambda candidate: ast.dump(candidate) == ast.dump(expected)
        )
        if not isinstance(node, ast.stmt):
            raise ValueError(error)
        return node

    skip = exact(
        func,
        "if is_ephemeral_response or str(event.text or \"\").lstrip().startswith("
        "(\"/\", adapter.typed_command_prefix or \"!\")):\n    return None",
    )
    source = exact(func, "source = event.source")
    ledger_id = exact(func, "_ledger_id = getattr(event, \"ledger_message_id\", None)")
    ledger_fallback = exact(
        func,
        "if _ledger_id is None:\n    _ledger_id = getattr(event, \"message_id\", \"\")",
    )
    computed = _unique_exact_base_node(ast.walk(func), _is_split_compute_obligation_assignment)
    recorded = exact(
        func,
        "await asyncio.to_thread(record_obligation, obligation_id=obligation_id, session_key=session_key, "
        "platform=str(getattr(source.platform, \"value\", source.platform)), chat_id=source.chat_id, "
        "thread_id=getattr(source, \"thread_id\", None), content=text_content, "
        "adapter_profile=getattr(delivery_adapter, \"_owner_profile\", None))",
    )
    attempting = exact(func, "await asyncio.to_thread(mark_attempting, obligation_id)")
    returned = exact(func, "return obligation_id")
    ordered = [skip, source, ledger_id, ledger_fallback, computed, recorded, attempting, returned]
    if any(a.lineno >= b.lineno for a, b in zip(ordered, ordered[1:])):
        raise ValueError(error)
    tries = [
        node
        for node in func.body
        if isinstance(node, ast.Try)
        and all(
            item in node.body
            for item in (source, ledger_id, ledger_fallback, computed, recorded, attempting, returned)
        )
    ]
    if len(tries) != 1:
        raise ValueError(error)


def _validate_verified_finalize_final_obligation(func: ast.AsyncFunctionDef) -> None:
    error = "could not find safe BasePlatformAdapter contract"
    args = func.args
    if (
        tuple(arg.arg for arg in (*args.posonlyargs, *args.args))
        != ("adapter", "obligation_id", "result", "event", "delivery_adapter")
        or args.kwonlyargs
        or args.vararg is not None
        or args.kwarg is not None
    ):
        raise ValueError(error)
    delivered = ast.parse(
        "if getattr(result, \"success\", False):\n"
        "    await asyncio.to_thread(mark_delivered, obligation_id)\n"
        "    return"
    ).body[0]
    failed = ast.parse("await asyncio.to_thread(mark_failed, obligation_id, error)").body[0]
    if not any(ast.dump(node) == ast.dump(delivered) for node in ast.walk(func)):
        raise ValueError(error)
    if not any(ast.dump(node) == ast.dump(failed) for node in ast.walk(func)):
        raise ValueError(error)
