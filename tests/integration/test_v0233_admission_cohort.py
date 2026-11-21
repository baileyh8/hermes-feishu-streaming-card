"""Exact Hermes v0.23.3 admission completion cohort contracts (#537).

The exact upstream checkout is supplied by CI. A local run can opt in with
'HFC_CONFIG_ADMISSION_0233_ROOT'; every negative control uses a private copy.
v0.23.3 (caafe5389a) deletes hermes_state_runtime, renames SessionAuthority's
store attribute (db -> persistence), and drifts 17 of the 33 closure files.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from hermes_feishu_card.admission_completion import (
    SOURCE_COHORTS,
    _admission_store_module,
    _authority_store,
)
from hermes_feishu_card.admission_topology_0226 import (
    CORE_HASHES_0233,
    EXPECTED_RENDERED_HASHES_0233,
    FUNCTIONS_0233,
    OPTIONAL_MODULES_0233,
    SOURCE_HASHES_0233,
    _read_023_set_sources,
    source_bytes_0233,
)
from hermes_feishu_card.install import admission_extraction
from hermes_feishu_card.install.detect import _detect_admission_completion


COMMIT = "caafe5389ae585ef77387a962aee05b1bcf17029"
ENV = "HFC_CONFIG_ADMISSION_0233_ROOT"
FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "hermes_v0233_admission_cohort.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _root() -> Path:
    configured = os.environ.get(ENV)
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("required v0.23.3 admission source missing")
        pytest.skip("v0.23.3 admission source not supplied")
    root = Path(configured)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    assert head == COMMIT, (head, COMMIT)
    return root


_PEER_SOURCES = (
    "gateway/run_turn_setup_guard.py",
    "gateway/run_turn_runner_approval_transport.py",
    "gateway/platforms/base_delivery_ledger.py",
    "gateway/platforms/base_session_command.py",
)


def _minimal_copy(root: Path, tmp_path: Path, extra: tuple[str, ...] = ()) -> Path:
    """Only the files the admission verification reads, in a private tree, so a
    negative control never touches the source and the copy stays cheap."""
    fixture = _fixture()
    wanted = sorted(
        name.replace(".", "/") + ".py" for name in fixture["closure"]
    ) + list(_PEER_SOURCES) + list(extra)
    destination = tmp_path / "hermes-0233-copy"
    for rel in wanted:
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / rel, target)
    return destination


def test_v0233_fixture_matches_registered_cohort() -> None:
    fixture = _fixture()
    assert fixture["commit"] == COMMIT
    assert fixture["closure"] == SOURCE_HASHES_0233
    assert fixture["rendered"] == EXPECTED_RENDERED_HASHES_0233
    assert CORE_HASHES_0233 == {
        name: SOURCE_HASHES_0233[name]
        for name in ("gateway.session_ingress", "gateway.session_authority")
    }
    assert "hermes_state_runtime" in fixture["removed_from_0232"]
    assert "hermes_state_runtime" not in SOURCE_HASHES_0233
    assert "hermes_state_runtime" not in FUNCTIONS_0233
    assert "hermes_state_runtime" not in OPTIONAL_MODULES_0233
    # close_discarded_turn left session_results for _admission_owner.py.
    assert "close_discarded_turn" not in FUNCTIONS_0233["gateway.session_results"]
    assert SOURCE_COHORTS[-1] is CORE_HASHES_0233


def test_v0233_admission_sources_are_exact(tmp_path: Path) -> None:
    root = _root()
    fixture = _fixture()
    for name, digest in fixture["closure"].items():
        rel = name.replace(".", "/") + ".py"
        raw = subprocess.run(
            ["git", "-C", str(root), "show", f"{COMMIT}:{rel}"],
            check=True, capture_output=True, timeout=10,
        ).stdout
        assert hashlib.sha256(raw).hexdigest() == digest, rel
    # The whole-file closure is verified as one exact set against the checkout.
    pair = _read_023_set_sources(root, SOURCE_HASHES_0233,
                                 (EXPECTED_RENDERED_HASHES_0233,))
    assert pair is not None
    originals, code_sources = pair
    assert set(originals) == set(fixture["closure"])


def test_v0233_every_closure_drift_is_rejected(tmp_path: Path) -> None:
    root = _root()
    fixture = _fixture()
    for name in sorted(fixture["closure"]):
        copy = _minimal_copy(root, tmp_path)
        rel = name.replace(".", "/") + ".py"
        path = copy / rel
        raw = path.read_bytes()
        path.write_bytes(raw + b"\n# drift\n")
        pair = _read_023_set_sources(copy, SOURCE_HASHES_0233,
                                     (EXPECTED_RENDERED_HASHES_0233,))
        assert pair is None, f"drift accepted: {rel}"
        shutil.rmtree(copy)


def test_v0233_seam_anchors_are_compatible(tmp_path: Path) -> None:
    """0.23.3 keeps the 0.23.2 _extract_response_content bytes: the footer_caption
    seam variant applies unchanged, so no new extraction anchor is needed."""
    root = _root()
    base_clean = (root / "gateway/platforms/base.py").read_text(encoding="utf-8")
    assert admission_extraction._anchor(base_clean) == "footer_caption"
    patched = admission_extraction.apply(base_clean)
    assert admission_extraction.BEGIN in patched
    restored = admission_extraction.remove(patched)
    assert restored == base_clean
    seam = _detect_admission_completion(root, base_clean)
    assert seam == (True, "verified-source-cohort")


def test_v0233_detect_accepts_the_pinned_cohort(tmp_path: Path) -> None:
    root = _root()
    base_clean = (root / "gateway/platforms/base.py").read_text(encoding="utf-8")
    # Negative control: one drifted closure file must fail the whole set.
    copy = _minimal_copy(root, tmp_path)
    authority = copy / "gateway/session_authority.py"
    authority.write_bytes(authority.read_bytes() + b"\n# drift\n")
    drifted = _detect_admission_completion(
        copy, (copy / "gateway/platforms/base.py").read_text(encoding="utf-8"))
    assert drifted == (False, "unsupported-source")
    # The exact cohort passes on the untouched checkout.
    assert _detect_admission_completion(root, base_clean) == (
        True, "verified-source-cohort")


def test_v0233_function_bindings_exist_in_source(tmp_path: Path) -> None:
    import ast
    root = _root()
    for name, qualified_names in FUNCTIONS_0233.items():
        rel = name.replace(".", "/") + ".py"
        tree = ast.parse((root / rel).read_bytes())
        found = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.add(node.name)
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        found.add(f"{node.name}.{sub.name}")
        for qualified in qualified_names:
            assert qualified in found, f"{name}: {qualified}"


def test_v0233_store_compatibility_probes() -> None:
    class Legacy:
        db = object()

    class Modern:
        persistence = object()

    class Bare:
        pass

    assert _authority_store(Legacy()) is Legacy.db
    assert _authority_store(Modern()) is Modern.persistence
    assert _authority_store(Bare()) is None
    # Without a Hermes installation neither home resolves; the seam must
    # degrade to None instead of importing application code blindly.
    probe = _admission_store_module()
    if probe is None:
        assert probe is None
    else:
        assert hasattr(probe, "get_session_admission")


def test_v0233_store_module_resolves_new_home(tmp_path: Path) -> None:
    """With a v0.23.3 checkout importable, the probe finds the moved store.
    Run in a subprocess: importing the persistence package pulls in its package
    init chain, which must never leak side effects into the test process."""
    root = _root()
    # The probe subprocess imports the real persistence package chain, which
    # needs Hermes' own runtime dependencies; a bare pytest env skips instead
    # of failing on unrelated missing modules.
    pytest.importorskip("ruamel.yaml")
    probe_source = (
        "import inspect, sys\n"
        "sys.path.insert(0, {hfc!r})\n"
        "from hermes_feishu_card.admission_completion import _admission_store_module\n"
        "store = _admission_store_module()\n"
        "assert store is not None, 'probe returned None'\n"
        "signature = inspect.signature(store.get_session_admission)\n"
        "assert list(signature.parameters) == ['holder', 'admission_id'], signature\n"
        "assert signature.parameters['admission_id'].kind is (\n"
        "    inspect.Parameter.KEYWORD_ONLY)\n"
    ).format(hfc=str(Path(__file__).parents[2]))
    result = subprocess.run(
        [sys_executable(), "-c", probe_source],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    assert result.returncode == 0, result.stderr


def sys_executable() -> str:
    import sys
    return sys.executable
