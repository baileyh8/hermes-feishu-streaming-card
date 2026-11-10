"""Loaded-topology verification on the exact Hermes v0.23.1 checkout (#520).

Mirrors the v0.23.0 harness against the 0231 whole-file set: installed rendered
sources, live module bindings, defaults/relocation/warm-drift rejection and the
settlement/producer live-binding gates. No Hermes import; synthetic modules only.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import FunctionType, ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_feishu_card import admission_completion as ac
from hermes_feishu_card import admission_topology_0226 as topology
from hermes_feishu_card.install import decomposed_ownership
from hermes_feishu_card.install.source_layout import DECOMPOSED_SOURCE_TARGETS
from hermes_feishu_card.admission_topology_0226 import (
    SOURCE_HASHES_0231,
    source_bytes,
    source_bytes_0231,
)

ENV = "HFC_UPSTREAM_0231_ROOT"
REQUIRE_ENV = "HFC_REQUIRE_0231_PIN"
MANIFEST = json.loads(
    (Path(__file__).parents[1] / "fixtures/hermes_v0231_sources.json").read_text()
)


def _pinned_root() -> Path:
    configured = os.environ.get(ENV)
    if not configured:
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail("required v0.23.1 pinned source missing")
        pytest.skip("v0.23.1 pinned source not supplied")
    root = Path(configured)
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    assert commit == MANIFEST["commit"], (commit, MANIFEST["commit"])
    assert subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain"], text=True
    ) == ""
    for name, digest in MANIFEST["files"].items():
        path = root / name
        assert path.is_file(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    return root


def _copy_sources(root: Path, target: Path, names=None) -> None:
    for name in names or MANIFEST["files"]:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, destination)


def _build_modules(root: Path, sources: dict[str, bytes]) -> dict[str, ModuleType]:
    """Compile exact pinned functions into synthetic globals; no Hermes import."""
    modules = {}
    for name in SOURCE_HASHES_0231:
        module = ModuleType(name)
        module.__file__ = str(root / (name.replace(".", "/") + ".py"))
        module._ANY_HEAD = object()
        modules[name] = module
    for name, names in topology.FUNCTIONS_0231.items():
        if name == "gateway.session_ingress":
            names = (
                *names,
                "_hand_over_turn_marker",
                "_move_marker",
                "deliver_response",
                "deliver_settled",
                "execute_admission",
                "dispatch_shared_busy",
            )
        elif name == "gateway.session_authority":
            names = (
                *names,
                "SessionAuthority._drain",
                "SessionAuthority._schedule",
                "SessionAuthority._resolve_settled_waiter",
                "SessionAuthority._execute_claimed",
                "SessionAuthority._stamp_claim",
            )
        module = modules[name]
        raw = sources[name]
        compiled = compile(raw, module.__file__, "exec", dont_inherit=True)
        for qualified in names:
            owner, _, function_name = qualified.rpartition(".")
            code = ac._compiled_code(compiled, function_name, owner or None)
            assert not code.co_freevars, (name, qualified)
            expected = topology._expected_function_defaults(
                raw, qualified, vars(module), module.__file__
            )
            assert expected is not None, (name, qualified)
            function = FunctionType(
                code, vars(module), function_name, expected[0] or None
            )
            function.__kwdefaults__ = expected[1] or None
            if (name, qualified) in topology.DECORATED_0231:
                function = contextlib.asynccontextmanager(function)
            elif (name, qualified) in topology.SYNC_DECORATED_0231:
                function = contextlib.contextmanager(function)
            if owner:
                if not hasattr(module, owner):
                    setattr(module, owner, type(owner, (), {}))
                setattr(getattr(module, owner), function_name, function)
            else:
                setattr(module, function_name, function)
    lifecycle = modules["gateway.session_producer_lifecycle"]
    ingress = modules["gateway.session_ingress"]
    authority = modules["gateway.session_authority"]
    base = modules["gateway.platforms.base"]
    event = modules["gateway.platforms.event"]
    contract = modules["gateway.session_contract"]
    event.ProcessingOutcome = type("ProcessingOutcome", (), {})
    ingress.ProcessingOutcome = lifecycle.ProcessingOutcome = event.ProcessingOutcome
    ingress.producer_turn = lifecycle.producer_turn
    ingress.close_producer_turn = lifecycle.close_producer_turn
    authority.mark_producer_admission = lifecycle.mark_producer_admission
    authority.drop_producer_turn = lifecycle.drop_producer_turn
    base.run_post_delivery_callback = modules[
        "gateway.platforms.base_post_delivery"
    ].run_post_delivery_callback
    base._POST_DELIVERY_CALLBACK_TIMEOUT_SECONDS = 30.0
    modules["gateway.run_turn_followup_ack"].BasePlatformAdapter = (
        base.BasePlatformAdapter
    )
    modules["gateway.session_settlement_recovery"].drop_producer_turn = (
        lifecycle.drop_producer_turn
    )
    contract.SessionActor = type("SessionActor", (), {})
    authority.SessionActor = contract.SessionActor
    return modules


def _installed_rendered(root: Path, tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    installed = tmp_path / "installed"
    _copy_sources(root, installed)
    originals = {
        name: (installed / name).read_bytes()
        for name in DECOMPOSED_SOURCE_TARGETS
    }
    rendered = decomposed_ownership.render(originals)
    assert all(
        decomposed_ownership._remove_target(name, raw) == originals[name]
        for name, raw in rendered.items()
    )
    for name, raw in rendered.items():
        (installed / name).write_bytes(raw)
    return installed, rendered


def test_v0231_installed_rendered_sources_are_verified(tmp_path, monkeypatch):
    root = _pinned_root()
    clean = tmp_path / "clean"
    _copy_sources(root, clean)
    assert source_bytes(clean, SOURCE_HASHES_0231) is not None
    assert source_bytes(clean, topology.SOURCE_HASHES_0230) is None
    installed, rendered = _installed_rendered(root, tmp_path)
    assert source_bytes(installed, SOURCE_HASHES_0231) is not None
    pair = source_bytes_0231(installed)
    assert pair is not None
    modules = _build_modules(installed, pair[1])
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def checked():
        return topology.verified(
            installed,
            modules["gateway.session_ingress"],
            modules["gateway.session_authority"],
            topology.SOURCE_HASHES_0231,
            functions=topology.FUNCTIONS_0231,
            decorated=topology.DECORATED_0231,
            optional_modules=topology.OPTIONAL_MODULES_0231,
        )

    topology._COMPILE_CACHE.clear()
    topology._FUNCTION_NODE_CACHE.clear()
    topology._AST_TREE_CACHE.clear()
    topology._OWNED_REMOVAL_CACHE.clear()
    cold_cpu_start = time.process_time()
    started = time.monotonic()
    assert checked()
    cold_cpu_seconds = time.process_time() - cold_cpu_start
    first_seconds = time.monotonic() - started
    warm_cpu_start = time.process_time()
    started = time.monotonic()
    for _ in range(4):
        assert ac._verified() is not None
    repeated_seconds = time.monotonic() - started
    warm_cpu_seconds = time.process_time() - warm_cpu_start
    print(
        json.dumps({
            "cold_seconds": round(first_seconds, 3),
            "cold_cpu_seconds": round(cold_cpu_seconds, 3),
            "warm_repeat_seconds": round(repeated_seconds, 3),
            "warm_cpu_seconds": round(warm_cpu_seconds, 3),
        }, sort_keys=True), flush=True,
    )

    checked_defaults = 0
    for module_name, qualified_names in topology.FUNCTIONS_0231.items():
        raw = pair[1][module_name]
        tree = topology._parsed_ast(raw)
        before = ast.dump(tree, include_attributes=True)
        for qualified in qualified_names:
            assert topology._expected_function_defaults(
                raw,
                qualified,
                vars(modules[module_name]),
                str(installed / (module_name.replace(".", "/") + ".py")),
            ) is not None
            checked_defaults += 1
        assert ast.dump(tree, include_attributes=True) == before, module_name
    assert checked_defaults == 81

    ingress_path = installed / "gateway/session_ingress.py"
    original = ingress_path.read_bytes()
    ingress_path.write_bytes(original + b"\n# warm disk mutation\n")
    assert not checked()
    ingress_path.write_bytes(original)
    assert checked()


def test_v0231_pinned_rendered_sources_reject_relocation_defaults_and_warm_drift(
    tmp_path, monkeypatch,
):
    root = _pinned_root()
    installed, rendered = _installed_rendered(root, tmp_path)
    assert source_bytes_0231(installed) is not None
    with patch.object(topology, "_HFC_VERSION", "5.0.21"):
        assert source_bytes_0231(installed) is None

    # Relocated helper block keeps owner removal equal but drifts rendered bytes.
    target = "gateway/run_turn_persistence.py"
    lines = rendered[target].decode().splitlines(keepends=True)
    start = next(
        i for i, line in enumerate(lines)
        if line.strip() == "# HERMES_FEISHU_CARD_COMPLETE_PATCH_BEGIN"
    )
    end = next(
        i for i, line in enumerate(lines)
        if line.strip() == "# HERMES_FEISHU_CARD_COMPLETE_PATCH_END"
    ) + 1
    block = lines[start:end]
    rest = lines[:start] + lines[end:]
    helper = next(
        node for node in ast.walk(ast.parse("".join(rest)))
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_hmwa_deliver_turn_response"
    )
    moved = "".join(
        rest[: helper.end_lineno] + block + rest[helper.end_lineno :]
    ).encode()
    assert decomposed_ownership._remove_target(target, moved) == (
        decomposed_ownership._remove_target(target, rendered[target])
    )
    (installed / target).write_bytes(moved)
    assert source_bytes_0231(installed) is None
    (installed / target).write_bytes(rendered[target])
    assert source_bytes_0231(installed) is not None

    pair = source_bytes_0231(installed)
    modules = _build_modules(installed, pair[1])
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def checked():
        return topology.verified(
            installed,
            modules["gateway.session_ingress"],
            modules["gateway.session_authority"],
            topology.SOURCE_HASHES_0231,
            functions=topology.FUNCTIONS_0231,
            decorated=topology.DECORATED_0231,
            optional_modules=topology.OPTIONAL_MODULES_0231,
        )

    assert checked()
    persist = modules["gateway.session_runtime_workers"].persist_work_count
    original_defaults = persist.__defaults__
    persist.__defaults__ = (object(),)
    assert not checked()
    persist.__defaults__ = original_defaults
    assert checked()
    claim = modules["hermes_state_runtime"].claim_session_input
    original_kwdefaults = claim.__kwdefaults__
    claim.__kwdefaults__ = {**original_kwdefaults, "head": object()}
    assert not checked()
    claim.__kwdefaults__ = original_kwdefaults
    assert checked()


def test_v0231_settlement_and_producer_live_bindings_are_enforced(
    tmp_path, monkeypatch,
):
    """Same-name replacement in settlement globals or producer identity fails."""
    root = _pinned_root()
    installed, _ = _installed_rendered(root, tmp_path)
    pair = source_bytes_0231(installed)
    modules = _build_modules(installed, pair[1])
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def checked():
        return topology.verified(
            installed,
            modules["gateway.session_ingress"],
            modules["gateway.session_authority"],
            topology.SOURCE_HASHES_0231,
            functions=topology.FUNCTIONS_0231,
            decorated=topology.DECORATED_0231,
            optional_modules=topology.OPTIONAL_MODULES_0231,
        )

    assert checked()

    # A same-named settlement helper bound from another module's globals must
    # fail settle_and_deliver's own-globals check.
    settlement = modules["gateway.session_settlement_recovery"]
    helper = settlement.settle_with_retry
    impostor = FunctionType(
        helper.__code__, dict(vars(modules["gateway.session_ingress"])),
        helper.__name__, helper.__defaults__,
    )
    impostor.__kwdefaults__ = helper.__kwdefaults__
    original = settlement.settle_with_retry
    settlement.settle_with_retry = impostor
    assert not checked()
    settlement.settle_with_retry = original
    assert checked()

    # A producer identity mismatch between lifecycle and authority must fail.
    lifecycle = modules["gateway.session_producer_lifecycle"]
    authority = modules["gateway.session_authority"]
    original_marker = authority.drop_producer_turn
    authority.drop_producer_turn = lambda *a, **k: None
    assert not checked()
    authority.drop_producer_turn = original_marker
    assert checked()
