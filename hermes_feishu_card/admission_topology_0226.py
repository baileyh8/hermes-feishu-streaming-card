"""Pinned 0.22.6 producer lifecycle provenance, never a lifecycle implementation."""
from __future__ import annotations

import ast
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager
import builtins
from hashlib import sha256
import sys
import inspect
from pathlib import Path
import stat
import threading
from types import CodeType, ModuleType
from typing import Any, cast

from .install.patcher import RENDER_CONTRACT_VERSION as _HFC_VERSION

SOURCE_HASHES = {
    "gateway.platforms.base": "ad064118a29bbe6d0d33424fbcde01484305a501aef4aa2584f5808c9b399a3d",
    "gateway.session_ingress": "12466f9c31f57877c6ae8ea6189d76123a278bdc99e5242c13c5c4ecff0f8ae9",
    "gateway.session_authority": "207708e4aeb0bf2d8e577f145110a414d80c12da1c4e69570f0402f7d7f6d1a6",
    "gateway.session_producer_lifecycle": "8c95c5c96bc0634c22cd698068194155dc932125a6cbefaea0039a1dfb8653a8",
    "gateway.platforms.base_post_delivery": "89471cd5ff6daf8001cab6d95321b1284a81b5db4943ca3e866fe21361d79206",
    "gateway.run_turn_followup_ack": "f45f7b93d091ecb57f30d80e18d08bca0aa7aae92a7bb796ae9ce4b499852283",
}
CORE_HASHES = {name: SOURCE_HASHES[name] for name in (
    "gateway.session_ingress", "gateway.session_authority")}
# v0.22.7 (b200f5bc60e31588ba2cb60e288029c4a54ba5fa) keeps the ingress and
# post-delivery sources unchanged but changes authority, producer lifecycle
# and Base. The admission extraction seam itself is byte-identical. Keep a
# separate whole-file cohort so an unknown upstream cannot be accepted by a
# relaxed per-file allowlist.
SOURCE_HASHES_0227 = {
    "gateway.platforms.base": "25bce7af7473b6eb1289dc6e83570062d04fe4338620832889a28d9f598f6229",
    "gateway.session_ingress": "12466f9c31f57877c6ae8ea6189d76123a278bdc99e5242c13c5c4ecff0f8ae9",
    "gateway.session_authority": "739d26a99bb786f0f2fc84f36b5d7fa92ca01b20692100429d845164d4b76379",
    "gateway.session_producer_lifecycle": "e948b9cb02aac2e169abee70c6b2ffc04dc0edeeb22f4b3b9748ee9890ee56e3",
    "gateway.platforms.base_post_delivery": "89471cd5ff6daf8001cab6d95321b1284a81b5db4943ca3e866fe21361d79206",
    "gateway.run_turn_followup_ack": "f45f7b93d091ecb57f30d80e18d08bca0aa7aae92a7bb796ae9ce4b499852283",
}
CORE_HASHES_0227 = {name: SOURCE_HASHES_0227[name] for name in (
    "gateway.session_ingress", "gateway.session_authority")}
# v0.23.0 (2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19) moves settlement and
# delivery from SessionAuthority._drain into settle_and_deliver, tracked by
# session_runtime_workers.track_mutation and awaited through asyncio.shield.
# The exact source cohort therefore includes the producer, settlement, worker,
# bot-loop and delivery dependencies that those loaded bindings resolve.
SOURCE_HASHES_0230 = {
    "gateway.platforms.base": "8f73b0de236491c147f0d90d1ee7569df9f463809315c9df024412380d419c35",
    "gateway.platforms.event": "3030bcc12551de4fa1ac1a8ebb5e82426556a8ea2171341e29cab51305cf24c4",
    "gateway.session_ingress": "12466f9c31f57877c6ae8ea6189d76123a278bdc99e5242c13c5c4ecff0f8ae9",
    "gateway.session_authority": "7902f0cdca329c229a41328bec2c84103d3c5adc01bea7020b7bc76e4b26404d",
    "gateway.session_producer_lifecycle": "e948b9cb02aac2e169abee70c6b2ffc04dc0edeeb22f4b3b9748ee9890ee56e3",
    "gateway.platforms.base_post_delivery": "89471cd5ff6daf8001cab6d95321b1284a81b5db4943ca3e866fe21361d79206",
    "gateway.run_turn_followup_ack": "f45f7b93d091ecb57f30d80e18d08bca0aa7aae92a7bb796ae9ce4b499852283",
    "gateway.session_settlement_recovery": "f33174eafa49c4ec5fe7dcf18fa70c8ed9fb8e232de86a1fb3ae66c36f1bf312",
    "gateway.session_runtime_workers": "dd10aadcce77376a04ddb03ff1587b61c9cc0910ccb04cd0e25bbaa89f32e1b4",
    "gateway.session_results": "0c00cb67eba6322b34c801604c29bab360832dfded5c15b3c0d69bc592145bb2",
    "gateway.session_queued_controls": "0c4e6a7054a891c5a625d4ce4940c2257555d75ac217ab8db6ce2302052ef903",
    "gateway.session_ingress_media": "860e3cf09dcbc7732430778037d0ad6ad68ec0ca6eabb88f1b67f483f08653e2",
    "gateway.session_retirement": "84eab1445ae3d0bd24c45325ea44f141434aa5e8d951c3ed7a6b870a7c948658",
    "gateway.session_contract": "624f45de8dcf2537266614ac14eaf98150ad6ed82ac3167f30f6f463733e7a41",
    "gateway.bot_loop_guard": "bef2b671488f0b6a26713647ae832026d2fae4fdb4d689e2968839e3fb15e621",
    "gateway.bot_peer_notices": "671e75da3ff3c6fb8af43f9cfa4b4ec8c67f8e3fe605f6b4b7b888481897b9d6",
    "gateway.authz_mixin": "bb8af52a222ee560beacdaccde18b24d4b2bba75793d527be7c9081633b5d94a",
    "gateway.run_turn_persistence": "93500872620e0dbc3eeed5f58206907cf689c7bf5315c940e3c2b120f828d6b6",
    "gateway.run_turn": "851dea07d3177ca1a274a5b94e6d490a3b22aee46f5fb57d27fb8a363a03879a",
    "hermes_state_runtime": "6bbcc688799d3ea784545b5227d20e07f8b5ce8f26fcf545825bc8a60462ee3f",
    "gateway.run": "f2eabc2972822df232375a4e32b22e79daafcec9bc2f66a6cfb305c255cf11d3",
    "gateway.run_background": "7702a2b46dd13acfe82c2bbc0fecff63761498ba0ca257fcddcf21cea0288f54",
    "gateway.run_turn_prepare": "71512a46ac5f361eb75ff3c455ada7f8486e2f28a12fec30fe8e8f7f90f44461",
    "gateway.run_turn_hygiene": "ce9d47df93e786da4547b2bf019e608bf553f893c877a6d2d22c72a05da4cf64",
    "gateway.run_turn_runner": "c20474c99ae171882f5524646b60cf5d877e596b6f753051134ce1b8840edbb6",
    "gateway.run_inbound": "2336674a0167ec46f56a0fe1f71965507ba996eddaf55eb99bbf8e11a958c645",
    "gateway.run_busy": "becd774be4e06133ef773a32cd60f5503db05cd7259b69002427d5c781f6d709",
    "gateway.run_startup": "ab836c1a752b84b008c3826d32304ec4537839900e0b7cd2a76eee34b970e70e",
    "gateway.run_notifications": "09566558ee1f967c90f62871c992db2e5922c062bdb534167e55257bdf65b003",
    "cron.scheduler": "b5956a54f05a8d360b9652149cae0f603e92bc52bb4a5af2b911fbd457f98470",
    "cron.scheduler_delivery": "a47babe7b6e1be929a5af3c8699f490f4f957c8391c62878666a8d516dbd85e2",
}
CORE_HASHES_0230 = {name: SOURCE_HASHES_0230[name] for name in (
    "gateway.session_ingress", "gateway.session_authority")}
# Exact rendered bytes produced by decomposed_ownership.render for the pinned
# clean v0.23.0 sources and HFC 5.0.17. A full installer render is too costly
# on a terminal admission request; the project test proves these hashes are
# byte-for-byte equivalent to that render and rejects mixed/drifted files.
RENDER_VERSION_0230 = "completion-native-fallthrough-v1"
EXPECTED_RENDERED_HASHES_0230 = {
    "gateway/run.py": "f2eabc2972822df232375a4e32b22e79daafcec9bc2f66a6cfb305c255cf11d3",
    "gateway/run_background.py": "7702a2b46dd13acfe82c2bbc0fecff63761498ba0ca257fcddcf21cea0288f54",
    "gateway/run_turn.py": "1dabfdfca3011509df7a7cc5d36a5ef6f05cce848982d7bc295fe795f0cfe731",
    "gateway/run_turn_persistence.py": "060d37dd63e4577ac6835bb51ebdbe688566460f872294f59185afd42021d553",
    "gateway/run_turn_prepare.py": "71512a46ac5f361eb75ff3c455ada7f8486e2f28a12fec30fe8e8f7f90f44461",
    "gateway/run_turn_hygiene.py": "ce9d47df93e786da4547b2bf019e608bf553f893c877a6d2d22c72a05da4cf64",
    "gateway/run_turn_runner.py": "e26c9d13c04c5821730aafb2583b8d36acbaa8e149d768817b89a1d7235d21f2",
    "gateway/run_inbound.py": "974d23c4a4dc4dc07e532b06b75a130ac8edbf56a6a2239a0754ad591051c93f",
    "gateway/run_busy.py": "a5ea46067ed52619821a8e84101cc2ab49d44085abcd40556819e2d61b8dcc29",
    "gateway/run_startup.py": "68f549a53eebfd578002b748e6454a21229a312495bc5405cae0a633eb88c17d",
    "gateway/run_notifications.py": "e156bced1c28743477f6c405aa833f40b9ef5836ac258a7b1705ec6b7a056c71",
    "cron/scheduler.py": "b5956a54f05a8d360b9652149cae0f603e92bc52bb4a5af2b911fbd457f98470",
    "cron/scheduler_delivery.py": "09ed28e78e0230c87070c3159f669155a13050405e162d9817a312196c0ad4ce",
    "gateway/platforms/base.py": "fbec459568bc66d231514ea172fe10f127a5861197043760769d2cf9552cb9e3",
}
TOPOLOGY_HASHES = (SOURCE_HASHES, SOURCE_HASHES_0227, SOURCE_HASHES_0230)
_FUNCTIONS = {
    "gateway.platforms.base": ("_split_post_delivery_entry",
                               "BasePlatformAdapter.send_typing", "BasePlatformAdapter.on_processing_start"),
    "gateway.session_ingress": (
        "_admission_scope", "_execution_scope", "_launch_body_if_launch_owner",
        "_take_post_delivery", "_hand_back_post_delivery", "fire_post_delivery", "_registry"),
    "gateway.session_authority": ("SessionAuthority._admitted", "SessionAuthority._settle_cancelled"),
    "gateway.session_producer_lifecycle": (
        "_turns", "mark_producer_admission", "_open", "_executed_outcome",
        "producer_turn", "close_producer_turn", "drop_producer_turn",
        "_Bracket.__init__", "_Bracket.stop_typing", "_Bracket.close"),
    "gateway.platforms.base_post_delivery": ("run_post_delivery_callback",),
    "gateway.run_turn_followup_ack": ("_followup_processing_hooks_apply",),
}
_DECORATED = {("gateway.session_ingress", "_admission_scope"),
              ("gateway.session_producer_lifecycle", "producer_turn")}
# v0.23.0 adds the exact settlement/delivery and bot-loop closure. Each loaded
# function is checked against its complete pinned source, not only its name.
_FUNCTIONS_0230 = {
    **_FUNCTIONS,
    "gateway.platforms.event": (),
    "gateway.session_settlement_recovery": (
        "_mark_unknown", "_transfers", "recover_failed_settlement",
        "completion_payload", "_publish_completion", "owner_stop_notice",
        "gateway_refusal_boundary", "_release_media", "commit_and_publish",
        "settle_with_retry", "publish_terminal", "settle_and_deliver",
        "hand_collection_to_drain", "release_media_after_delivery",
        "_collect_queued_media",
    ),
    "gateway.session_runtime_workers": (
        "executing_sessions", "uncounted_runtime_work", "persist_work_count",
        "mutation_tasks", "track_mutation", "tracked_write", "start_turn_worker",
        "track_turn_worker", "physical_workers", "stop_authority_work",
        "join_authority_work",
    ),
    "gateway.session_results": (
        "retain_result", "prepare_result", "finish_result", "admission_result",
        "close_discarded_turn",
    ),
    "gateway.session_queued_controls": ("steer_transfers", "publish_transfers"),
    "gateway.session_ingress_media": (
        "capture_lease", "drop_capture_lease", "release_admission_media",
        "sweep_settled_media", "restore_native_media",
    ),
    "gateway.session_retirement": ("restore_retired", "touch_restored"),
    "gateway.session_contract": (),
    "gateway.bot_loop_guard": (
        "configured_bot_user", "BotLoopGuard.chain_admit",
        "BotLoopGuard.note_reply",
    ),
    "gateway.bot_peer_notices": ("automatic_notices_allowed",),
    "gateway.authz_mixin": (
        "GatewayAuthorizationMixin._admit_bot_chain",
        "GatewayAuthorizationMixin._note_bot_reply",
    ),
    "gateway.run_turn_persistence": (
        "GatewayTurnPersistenceMixin._deliver_turn_response_then_limit_offer",
        "GatewayTurnPersistenceMixin._hmwa_deliver_turn_response",
    ),
    "gateway.run_turn": ("GatewayTurnMixin._handle_message_with_agent",),
    "hermes_state_runtime": (
        "_epoch", "_admission", "get_session_admission", "claim_session_input",
        "settle_session_input", "cancel_session_input",
        "settle_steer_transfers", "resolve_unknown_session_input",
    ),
    "gateway.run": (),
    "gateway.run_background": (),
    "gateway.run_turn_prepare": (),
    "gateway.run_turn_hygiene": (),
    "gateway.run_turn_runner": (),
    "gateway.run_inbound": (),
    "gateway.run_busy": (),
    "gateway.run_startup": (),
    "gateway.run_notifications": (),
    "cron.scheduler": (),
    "cron.scheduler_delivery": (),
}
FUNCTIONS_0230 = _FUNCTIONS_0230
DECORATED_0230 = _DECORATED
# Only this pinned helper uses the synchronous contextlib wrapper. Never unwrap
# arbitrary __wrapped__ chains, or treat every generator as a context manager.
SYNC_DECORATED_0230 = frozenset({
    ("gateway.session_ingress_media", "capture_lease"),
})
# These modules are imported lazily by the pinned settlement path. Their source
# bytes are always checked; loaded bindings are checked as soon as they exist.
OPTIONAL_MODULES_0230 = frozenset({
    "gateway.session_settlement_recovery",
    "gateway.session_runtime_workers",
    "gateway.session_results",
    "gateway.session_queued_controls",
    "gateway.session_ingress_media",
    "gateway.session_retirement",
    "gateway.session_contract",
    "gateway.bot_loop_guard",
    "gateway.bot_peer_notices",
    "gateway.authz_mixin",
    "gateway.run_turn_persistence",
    "gateway.run_turn",
    "hermes_state_runtime",
    "gateway.run",
    "gateway.run_background",
    "gateway.run_turn_prepare",
    "gateway.run_turn_hygiene",
    "gateway.run_turn_runner",
    "gateway.run_inbound",
    "gateway.run_busy",
    "gateway.run_startup",
    "gateway.run_notifications",
    "cron.scheduler",
    "cron.scheduler_delivery",
})


_MISSING_DEFAULT = object()
_COMPILE_CACHE_LIMIT = 32
_COMPILE_CACHE: OrderedDict[tuple[str, str], CodeType] = OrderedDict()
_COMPILE_CACHE_LOCK = threading.Lock()
_FUNCTION_NODE_CACHE_LIMIT = 256
_FUNCTION_NODE_CACHE: OrderedDict[tuple[str, str], object] = OrderedDict()
_FUNCTION_NODE_CACHE_LOCK = threading.Lock()
_AST_TREE_CACHE_LIMIT = 64
_AST_TREE_CACHE: OrderedDict[str, ast.Module] = OrderedDict()
_AST_TREE_CACHE_LOCK = threading.Lock()
_OWNED_REMOVAL_CACHE_LIMIT = 32
_OWNED_REMOVAL_CACHE: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_OWNED_REMOVAL_CACHE_LOCK = threading.Lock()


def _module_path(name: str) -> str:
    return name.replace(".", "/") + ".py"


def _compiled_source(code_raw: bytes, path: Path) -> CodeType:
    key = (sha256(code_raw).hexdigest(), str(path))
    with _COMPILE_CACHE_LOCK:
        cached = _COMPILE_CACHE.get(key)
        if cached is not None:
            _COMPILE_CACHE.move_to_end(key)
            return cached
    compiled = compile(code_raw, str(path), "exec", dont_inherit=True)
    with _COMPILE_CACHE_LOCK:
        _COMPILE_CACHE[key] = compiled
        while len(_COMPILE_CACHE) > _COMPILE_CACHE_LIMIT:
            _COMPILE_CACHE.popitem(last=False)
    return compiled


def _parsed_ast(source: bytes | str) -> ast.Module:
    """Private read-only AST cache; callers must never mutate or expose it."""
    text = source.decode("utf-8") if isinstance(source, bytes) else source
    key = sha256(text.encode("utf-8")).hexdigest()
    with _AST_TREE_CACHE_LOCK:
        cached = _AST_TREE_CACHE.get(key)
        if cached is not None:
            _AST_TREE_CACHE.move_to_end(key)
            return cached
    tree = ast.parse(text)
    with _AST_TREE_CACHE_LOCK:
        _AST_TREE_CACHE[key] = tree
        while len(_AST_TREE_CACHE) > _AST_TREE_CACHE_LIMIT:
            _AST_TREE_CACHE.popitem(last=False)
    return tree


def _owned_removal_key(target: str, raw: bytes) -> tuple[str, str]:
    return (target, sha256(raw).hexdigest())


def _cached_owned_removal(key: tuple[str, str]) -> bytes | None:
    """Reuse a pure normalization result; never cache disk or loaded facts."""
    with _OWNED_REMOVAL_CACHE_LOCK:
        cached = _OWNED_REMOVAL_CACHE.get(key)
        if cached is not None:
            _OWNED_REMOVAL_CACHE.move_to_end(key)
            return cached
    return None


def _remember_owned_removal(key: tuple[str, str], original: bytes) -> None:
    with _OWNED_REMOVAL_CACHE_LOCK:
        _OWNED_REMOVAL_CACHE[key] = original
        while len(_OWNED_REMOVAL_CACHE) > _OWNED_REMOVAL_CACHE_LIMIT:
            _OWNED_REMOVAL_CACHE.popitem(last=False)


def _read_0230_sources(root: Path) -> tuple[dict[str, bytes], dict[str, bytes]] | None:
    """Verify clean or strictly owned rendered sources, and return both forms.

    Installed v0.23.0 Gateway files carry HFC-owned hook blocks. Their exact
    owner removal must reproduce the pinned clean bytes, while their expected
    rendered bytes are the only source that can describe loaded function code.
    """
    from .install import decomposed_ownership
    from .install.source_layout import DECOMPOSED_SOURCE_TARGETS

    originals: dict[str, bytes] = {}
    installed: dict[str, bytes] = {}
    owned = {
        name: _module_path(name)
        for name in SOURCE_HASHES_0230
        if _module_path(name) in DECOMPOSED_SOURCE_TARGETS
    }
    try:
        for name, digest in SOURCE_HASHES_0230.items():
            path = root / _module_path(name)
            before = path.lstat()
            limit = (
                1024 * 1024
                if _module_path(name) in DECOMPOSED_SOURCE_TARGETS
                else 256 * 1024
            )
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                return None
            raw = path.read_bytes()
            after = path.lstat()
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
                return None
            key = None
            if name in owned:
                key = _owned_removal_key(owned[name], raw)
                original = _cached_owned_removal(key)
                if original is None:
                    original = decomposed_ownership._remove_target(
                        owned[name], raw
                    )
            else:
                original = raw
            if original is None or sha256(original).hexdigest() != digest:
                return None
            if key is not None:
                _remember_owned_removal(key, original)
            originals[name] = original
            installed[name] = raw
        if any(installed[name] != originals[name] for name in SOURCE_HASHES_0230
               if name not in owned):
            return None
        if all(installed[name] == originals[name] for name in owned):
            return originals, originals
        if (
            _HFC_VERSION != RENDER_VERSION_0230
            or set(owned.values()) != set(EXPECTED_RENDERED_HASHES_0230)
        ):
            return None
        if not all(
            sha256(installed[name]).hexdigest()
            == EXPECTED_RENDERED_HASHES_0230[owned[name]]
            for name in owned
        ):
            return None
        code_source = {
            name: (installed[name] if name in owned
                   else originals[name])
            for name in SOURCE_HASHES_0230
        }
        return originals, code_source
    except (OSError, UnicodeError, ValueError, SyntaxError, TypeError):
        return None


def source_bytes_0230(
    root: Path,
) -> tuple[dict[str, bytes], dict[str, bytes]] | None:
    return _read_0230_sources(root)


def _function_node(source: bytes | str, qualified: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    text = source.decode("utf-8") if isinstance(source, bytes) else source
    key = (sha256(text.encode("utf-8")).hexdigest(), qualified)
    with _FUNCTION_NODE_CACHE_LOCK:
        cached = _FUNCTION_NODE_CACHE.get(key)
        if cached is not None:
            _FUNCTION_NODE_CACHE.move_to_end(key)
            return cached  # type: ignore[return-value]
        if key in _FUNCTION_NODE_CACHE:
            return None
    tree = _parsed_ast(text)
    owner, _, name = qualified.rpartition(".")
    scopes = (
        [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner]
        if owner else [tree]
    )
    if len(scopes) != 1:
        return None
    nodes = [
        n for n in ast.walk(scopes[0])
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    ]
    node = nodes[0] if len(nodes) == 1 else None
    with _FUNCTION_NODE_CACHE_LOCK:
        _FUNCTION_NODE_CACHE[key] = node
        while len(_FUNCTION_NODE_CACHE) > _FUNCTION_NODE_CACHE_LIMIT:
            _FUNCTION_NODE_CACHE.popitem(last=False)
    return node


def _default_value(
    node: ast.AST, namespace: dict[str, Any], filename: str
) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        pass
    scope = dict(namespace)
    scope.setdefault("__builtins__", builtins.__dict__)
    try:
        expression = ast.Expression(body=cast(ast.expr, node))
        return eval(compile(expression, filename, "eval"), scope)
    except Exception:
        return _MISSING_DEFAULT


def _expected_function_defaults(
    source: bytes | str, qualified: str,
    namespace: dict[str, Any], filename: str
) -> tuple[tuple[object, ...], dict[str, object]] | None:
    node = _function_node(source, qualified)
    if node is None:
        return None
    defaults = tuple(
        _default_value(value, namespace, filename) for value in node.args.defaults
    )
    kwdefaults = {
        arg.arg: _default_value(value, namespace, filename)
        for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults)
        if value is not None
    }
    if _MISSING_DEFAULT in defaults or any(
        value is _MISSING_DEFAULT for value in kwdefaults.values()
    ):
        return None
    return defaults, kwdefaults


def _same_default(actual: object, expected: object) -> bool:
    if actual is expected:
        return True
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, (bool, int, float, str, bytes, type(None))):
        return actual == expected
    if isinstance(expected, tuple):
        return isinstance(actual, tuple) and len(actual) == len(expected) and all(
            _same_default(left, right) for left, right in zip(actual, expected)
        )
    return False


def _defaults_match(
    function: object,
    expected: tuple[tuple[object, ...], dict[str, object]],
) -> bool:
    expected_defaults, expected_kwdefaults = expected
    actual_defaults = getattr(function, "__defaults__", None) or ()
    actual_kwdefaults = getattr(function, "__kwdefaults__", None) or {}
    return (
        len(actual_defaults) == len(expected_defaults)
        and all(_same_default(left, right)
                for left, right in zip(actual_defaults, expected_defaults))
        and set(actual_kwdefaults) == set(expected_kwdefaults)
        and all(_same_default(actual_kwdefaults[key], expected_kwdefaults[key])
                for key in expected_kwdefaults)
    )


def source_bytes(
    root: Path, expected: dict[str, str] | None = None
) -> dict[str, bytes] | None:
    if expected == SOURCE_HASHES_0230:
        pair = _read_0230_sources(root)
        return None if pair is None else pair[0]
    try:
        result = {}
        for name, digest in (expected or SOURCE_HASHES).items():
            path = root / (name.replace(".", "/") + ".py")
            before = path.lstat()
            limit = 1024 * 1024 if name == "gateway.platforms.base" else 256 * 1024
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                return None
            raw = path.read_bytes()
            after = path.lstat()
            if name == "gateway.platforms.base":
                from .install.patcher import remove_base_patch
                raw = remove_base_patch(raw.decode("utf-8")).encode("utf-8")
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    or sha256(raw).hexdigest() != digest):
                return None
            result[name] = raw
        return result
    except (OSError, UnicodeError, ValueError, SyntaxError):
        return None


def _verified_contextmanager(function: object, *, synchronous: bool) -> object | None:
    """Return only the single generator held by an exact contextlib wrapper."""
    if not inspect.isfunction(function):
        return None
    wrapped = getattr(function, "__wrapped__", None)
    expected_wrapper: Any
    if synchronous:
        if not inspect.isgeneratorfunction(wrapped):
            return None
        expected_wrapper = contextmanager(wrapped)
    else:
        if not inspect.isasyncgenfunction(wrapped):
            return None
        expected_wrapper = asynccontextmanager(wrapped)
    if (function.__code__ is not expected_wrapper.__code__
            or function.__globals__ is not expected_wrapper.__globals__
            or function.__defaults__ is not expected_wrapper.__defaults__
            or function.__kwdefaults__ is not expected_wrapper.__kwdefaults__
            or function.__closure__ is None
            or len(function.__closure__) != 1
            or function.__closure__[0].cell_contents is not wrapped):
        return None
    return wrapped


def verified(
    root: Path, ingress: ModuleType, authority: ModuleType,
    expected: dict[str, str] | None = None,
    functions: dict[str, tuple[str, ...]] | None = None,
    decorated: set[tuple[str, str]] | None = None,
    optional_modules: frozenset[str] = frozenset(),
) -> bool:
    """Bind loaded helpers and their explicitly registered contextlib wrappers."""
    from .admission_completion import _claim_frame, _compiled_code, _shape

    if expected == SOURCE_HASHES_0230:
        pair = source_bytes_0230(root)
        if pair is None:
            return False
        sources, code_sources = pair
    else:
        legacy_sources = source_bytes(root, expected)
        if legacy_sources is None:
            return False
        sources = legacy_sources
        code_sources = legacy_sources
    functions = _FUNCTIONS if functions is None else functions
    decorated = _DECORATED if decorated is None else decorated
    sync_decorated = (
        SYNC_DECORATED_0230 if expected == SOURCE_HASHES_0230 else frozenset()
    )
    modules = {"gateway.session_ingress": ingress, "gateway.session_authority": authority}
    try:
        for name, raw in sources.items():
            module = modules.get(name) or sys.modules.get(name)
            if module is None and name in optional_modules:
                continue
            # The native producer path imports this helper before entering the
            # admitted handler. Ordinary turns need not load it at all. Check
            # its pinned source now and its live binding whenever present;
            # provenance inspection must never execute an unknown loader.
            if module is None and name == "gateway.run_turn_followup_ack":
                claim = _claim_frame(ingress)
                if claim is not None:
                    turns = getattr(claim.get("authority"), "producer_turns", {})
                    row = claim.get("row")
                    if (isinstance(turns, dict) and isinstance(row, dict)
                            and row.get("admission_id") in turns):
                        return False
                continue
            if not isinstance(module, ModuleType):
                return False
            modules[name] = module
            path = root / (name.replace(".", "/") + ".py")
            filename = getattr(module, "__file__", None)
            if not isinstance(filename, str) or Path(filename).resolve() != path.resolve():
                return False
            code_raw = code_sources.get(name, raw)
            compiled = _compiled_source(code_raw, path)
            for qualified in functions[name]:
                owner, _, function_name = qualified.rpartition(".")
                function = getattr(getattr(module, owner) if owner else module, function_name)
                key = (name, qualified)
                if key in decorated or key in sync_decorated:
                    wrapped = _verified_contextmanager(
                        function, synchronous=key in sync_decorated
                    )
                    if wrapped is None:
                        return False
                    function = wrapped
                expected_defaults = _expected_function_defaults(
                    code_raw, qualified, vars(module), str(path)
                )
                if (not inspect.isfunction(function)
                        or expected_defaults is None
                        or not _defaults_match(function, expected_defaults)
                        or function.__globals__ is not vars(module)
                        or Path(function.__code__.co_filename).resolve() != path.resolve()
                        or _shape(function.__code__) != _shape(
                            _compiled_code(compiled, function_name, owner or None))):
                    return False
        lifecycle = modules["gateway.session_producer_lifecycle"]
        if expected == SOURCE_HASHES_0230:
            settlement = modules.get("gateway.session_settlement_recovery")
            workers = modules.get("gateway.session_runtime_workers")
            contract = modules.get("gateway.session_contract")
            ingress_media = modules.get("gateway.session_ingress_media")
            if settlement is not None:
                # settle_and_deliver resolves these helpers through its own
                # module globals. A same-named replacement must not inherit
                # the pinned source hash of the file that defined the original.
                for helper in (
                    "settle_with_retry", "recover_failed_settlement",
                    "publish_terminal", "release_media_after_delivery",
                    "commit_and_publish", "_publish_completion",
                    "hand_collection_to_drain", "_collect_queued_media",
                    "_release_media",
                ):
                    bound = getattr(settlement, helper, None)
                    if (not inspect.isfunction(bound)
                            or bound.__globals__ is not vars(settlement)
                            or settlement.settle_and_deliver.__globals__[helper]
                            is not bound):
                        return False
                if (getattr(settlement, "drop_producer_turn", None)
                        is not lifecycle.drop_producer_turn):
                    return False
            if workers is not None and settlement is not None:
                track = getattr(workers, "track_mutation", None)
                if (not inspect.isfunction(track)
                        or track.__globals__ is not vars(workers)
                        or not {"track_mutation", "settle_and_deliver"}.issubset(
                            set(authority.SessionAuthority._drain.__code__.co_names))):
                    return False
            if contract is not None and (
                not hasattr(contract, "SessionActor")
                or hasattr(contract, "Principal")
                or getattr(authority, "SessionActor", None) is not contract.SessionActor
            ):
                return False
            if ingress_media is not None and settlement is not None:
                release = getattr(ingress_media, "release_admission_media", None)
                if (not inspect.isfunction(release)
                        or release.__globals__ is not vars(ingress_media)
                        or "release_admission_media" not in
                        settlement._release_media.__code__.co_names):
                    return False
        if any(getattr(ingress, n, None) is not getattr(lifecycle, n)
               for n in ("producer_turn", "close_producer_turn")):
            return False
        if any(getattr(authority, n, None) is not getattr(lifecycle, n)
               for n in ("mark_producer_admission", "drop_producer_turn")):
            return False
        base = modules["gateway.platforms.base"]
        event = modules.get("gateway.platforms.event") or sys.modules.get("gateway.platforms.event")
        followup = modules.get("gateway.run_turn_followup_ack")
        if (event is None or ingress.ProcessingOutcome is not event.ProcessingOutcome
                or lifecycle.ProcessingOutcome is not event.ProcessingOutcome
                or (followup is not None and followup.BasePlatformAdapter
                    is not base.BasePlatformAdapter)):
            return False
        if (type(base._POST_DELIVERY_CALLBACK_TIMEOUT_SECONDS) not in (int, float)
                or base._POST_DELIVERY_CALLBACK_TIMEOUT_SECONDS != 30.0):
            return False
        if base.run_post_delivery_callback is not modules[
                "gateway.platforms.base_post_delivery"].run_post_delivery_callback:
            return False
        return True
    except (OSError, AttributeError, ImportError, TypeError, ValueError, SyntaxError, StopIteration):
        return False
