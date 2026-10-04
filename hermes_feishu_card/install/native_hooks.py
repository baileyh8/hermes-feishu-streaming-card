from __future__ import annotations

import ast
import configparser
import csv
from dataclasses import dataclass
from email.parser import BytesParser
import hashlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.resources
import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import time
from typing import Iterable

from ..integration import KNOWN_NATIVE_CAPABILITIES, NativeHookCapabilities


FIXED_TAG_COMMIT = "3c27eb6234bf91b8ceee9e9071591b31e9b148cb"
_PROVENANCE_RESOURCE_PACKAGE = (
    "hermes_feishu_card.install._native_hook_provenance"
)
FIXED_TAG_PROVENANCE_PATH = Path(os.fspath(
    importlib.resources.files(_PROVENANCE_RESOURCE_PACKAGE).joinpath(
        "provenance.json"
    )
))
FIXED_TAG_PROVENANCE_SHA256 = (
    "sha256:90a873bb3742155ba3cd3c006394f3487a3a509b4223d142df93da34f2c63f09"
)
FIXED_TAG_PROVENANCE_ANCHOR_COUNT = 24

HFC_REGISTERED_HOOKS = frozenset({
    "pre_llm_call",
    "post_llm_call",
    "on_session_end",
    "on_session_reset",
    "on_session_finalize",
    "pre_tool_call",
    "post_tool_call",
    "pre_approval_request",
    "post_approval_response",
    "subagent_start",
    "subagent_stop",
})

_EXPECTED_TARGETS = frozenset({
    "plugin_manager",
    "turn_context",
    "turn_finalizer",
    "tool_hooks",
    "approval",
    "subagent",
    "gateway",
    "cron",
    "base",
})
_RELATIVE_PATHS = {
    "plugin_manager": "hermes_cli/plugins.py",
    "turn_context": "agent/turn_context.py",
    "turn_finalizer": "agent/turn_finalizer.py",
    "tool_hooks": "model_tools.py",
    "approval": "tools/approval.py",
    "subagent": "tools/delegate_tool.py",
    "gateway": "gateway/run.py",
    "cron": "cron/scheduler.py",
    "base": "gateway/platforms/base.py",
}
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
_PACKAGE_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_OFFICIAL_HFC_VCS_URL = (
    "https://github.com/baileyh8/hermes-feishu-streaming-card.git"
)
_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_SLICE_BYTES = 128 * 1024
_MAX_SUBPROCESS_BYTES = 64 * 1024
_PLUGIN_PROBE_TIMEOUT_SECONDS = 15.0
_TRUSTED_GIT = Path("/usr/bin/git")
_MAX_ARCHIVE_MEMBER_BYTES = 32 * 1024 * 1024
_MAX_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024
_DESCRIPTOR_ROOT_MODE = "openat+fchdir"
_DESCRIPTOR_SOURCE_ROOT = "fd-source://snapshot"
_DESCRIPTOR_LOADER_NAME = (
    "hermes_feishu_card.install.native_hooks._DescriptorSourceLoader"
)
_REASON_CODES = frozenset({
    "verified",
    "expected_commit_invalid",
    "expected_commit_unsupported",
    "provenance_commit_mismatch",
    "provenance_invalid",
    "hermes_root_invalid",
    "source_commit_mismatch",
    "source_dirty",
    "plugin_evidence_missing",
    "plugin_evidence_invalid",
    "plugin_source_commit_mismatch",
    "plugin_attestation_unverified",
    "plugin_runtime_unverified",
    "entrypoint_ambiguous",
    "entrypoint_identity_mismatch",
    "plugin_not_enabled",
    "registration_incomplete",
    "source_missing",
    "source_not_regular",
    "source_digest_mismatch",
    "source_anchor_mismatch",
    "source_ast_invalid",
    "callsite_contract_mismatch",
    "authenticated_ingress_missing",
    "answer_delta_missing",
    "thinking_delta_missing",
    "interaction_resolver_missing",
    "terminal_consumer_missing",
    "command_platform_notice_missing",
    "cron_hook_missing",
    "exact_native_delivery_missing",
})


@dataclass(frozen=True)
class NativeHookAnchorProvenance:
    name: str
    line_start: int
    line_end: int
    slice_path: str
    slice_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or not self.name
            or not re.fullmatch(r"[a-z0-9_]+", self.name)
        ):
            raise ValueError("invalid anchor name")
        if (
            type(self.line_start) is not int
            or type(self.line_end) is not int
            or self.line_start < 1
            or self.line_end < self.line_start
            or self.line_end > _MAX_SOURCE_BYTES
        ):
            raise ValueError("invalid anchor lines")
        _validate_relative_path(self.slice_path)
        _validate_digest(self.slice_sha256)


@dataclass(frozen=True)
class NativeHookSourceProvenance:
    target: str
    relative_path: str
    sha256: str
    anchors: tuple[NativeHookAnchorProvenance, ...] = ()

    def __post_init__(self) -> None:
        if type(self.target) is not str or self.target not in _EXPECTED_TARGETS:
            raise ValueError("unknown provenance target")
        _validate_relative_path(self.relative_path)
        if self.relative_path != _RELATIVE_PATHS[self.target]:
            raise ValueError("provenance target path mismatch")
        _validate_digest(self.sha256)
        if type(self.anchors) is not tuple or not all(
            type(anchor) is NativeHookAnchorProvenance for anchor in self.anchors
        ):
            raise ValueError("invalid provenance anchors")
        names = [anchor.name for anchor in self.anchors]
        if len(names) != len(set(names)):
            raise ValueError("duplicate anchor names")


@dataclass(frozen=True)
class FixedTagNativeHookProvenance:
    commit: str
    sources: tuple[NativeHookSourceProvenance, ...]

    def __post_init__(self) -> None:
        if type(self.commit) is not str or _COMMIT_RE.fullmatch(self.commit) is None:
            raise ValueError("invalid provenance commit")
        if type(self.sources) is not tuple or not all(
            type(source) is NativeHookSourceProvenance for source in self.sources
        ):
            raise ValueError("invalid provenance sources")
        targets = [source.target for source in self.sources]
        if len(targets) != len(set(targets)) or set(targets) != _EXPECTED_TARGETS:
            raise ValueError("provenance targets must be exact and unique")


@dataclass(frozen=True)
class _PluginManagerEvidence:
    attestation_sha256: str

    def __post_init__(self) -> None:
        _validate_digest(self.attestation_sha256)


@dataclass
class _RuntimeBinding:
    path: Path
    descriptor: int
    identity: tuple[int, int]
    prefix: Path
    base_prefix: Path
    purelib: Path
    platlib: Path
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return None
        self._closed = True
        os.close(self.descriptor)
        return None


@dataclass
class _SourceSnapshot:
    root: Path
    descriptor: int
    identity: tuple[int, int]
    container: Path
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return None
        self._closed = True
        try:
            _remove_source_snapshot(self)
        except (OSError, ValueError):
            # A rebound path must be retained, not treated as our old snapshot.
            logging.getLogger(__name__).warning("native source snapshot cleanup incomplete")
        finally:
            os.close(self.descriptor)
        return None


def _empty_snapshot_directory(descriptor: int, device: int) -> None:
    info = os.fstat(descriptor)
    if info.st_dev != device or info.st_uid != os.geteuid():
        raise ValueError("snapshot directory ownership changed")
    # Unlink needs writable directories, not writable files. In particular,
    # never chmod a file that could have been replaced by an external hardlink.
    os.fchmod(descriptor, stat.S_IMODE(info.st_mode) | 0o700)
    with os.scandir(descriptor) as iterator:
        entries = list(iterator)
    for entry in entries:
        before = entry.stat(follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode):
            os.unlink(entry.name, dir_fd=descriptor)
            continue
        child = os.open(entry.name, _directory_flags(), dir_fd=descriptor)
        try:
            bound = os.fstat(child)
            if (bound.st_dev, bound.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("snapshot child identity changed")
            _empty_snapshot_directory(child, device)
            current = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (bound.st_dev, bound.st_ino):
                raise ValueError("snapshot child identity changed")
            os.rmdir(entry.name, dir_fd=descriptor)
        finally:
            os.close(child)


def _remove_source_snapshot(snapshot: _SourceSnapshot) -> None:
    container = _open_absolute_directory(snapshot.container)
    try:
        current = os.stat("source", dir_fd=container, follow_symlinks=False)
        bound = os.fstat(snapshot.descriptor)
        if (
            snapshot.root != snapshot.container / "source"
            or (current.st_dev, current.st_ino) != snapshot.identity
            or (bound.st_dev, bound.st_ino) != snapshot.identity
        ):
            raise ValueError("snapshot root identity changed")
        _empty_snapshot_directory(snapshot.descriptor, bound.st_dev)
        current = os.stat("source", dir_fd=container, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != snapshot.identity:
            raise ValueError("snapshot root identity changed")
        os.rmdir("source", dir_fd=container)
        try:
            os.unlink("source.tar", dir_fd=container)
        except FileNotFoundError:
            pass
        if not _bound_directory_unchanged(snapshot.container, container):
            raise ValueError("snapshot container identity changed")
        os.rmdir(snapshot.container)
    finally:
        os.close(container)


class _DescriptorSourceLoader(importlib.abc.Loader):
    def __init__(
        self,
        *,
        fullname: str,
        relative_path: str,
        source: bytes,
        is_package: bool,
    ) -> None:
        self.fullname = fullname
        self.relative_path = relative_path
        self.source = source
        self.is_package = is_package

    @property
    def origin(self) -> str:
        return f"{_DESCRIPTOR_SOURCE_ROOT}/{self.relative_path}"

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        module.__file__ = self.origin
        module.__cached__ = None
        if self.is_package:
            module.__path__ = [self.origin.rsplit("/__init__.py", 1)[0]]
        code = compile(self.source, self.origin, "exec", dont_inherit=True)
        exec(code, module.__dict__)


class _DescriptorSourceFinder(importlib.abc.MetaPathFinder):
    def __init__(self, root_descriptor: int) -> None:
        self.root_descriptor = root_descriptor

    def find_spec(self, fullname, path=None, target=None):
        if type(fullname) is not str or not fullname:
            return None
        base = fullname.replace(".", "/")
        for relative_path, is_package in (
            (f"{base}/__init__.py", True),
            (f"{base}.py", False),
        ):
            try:
                source = _read_bound_relative_file(
                    self.root_descriptor, relative_path, _MAX_SOURCE_BYTES
                )
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                raise ImportError(
                    f"descriptor source unavailable: {fullname}"
                ) from exc
            loader = _DescriptorSourceLoader(
                fullname=fullname,
                relative_path=relative_path,
                source=source,
                is_package=is_package,
            )
            spec = importlib.machinery.ModuleSpec(
                fullname,
                loader,
                origin=loader.origin,
                is_package=is_package,
            )
            if is_package:
                spec.submodule_search_locations = [
                    loader.origin.rsplit("/__init__.py", 1)[0]
                ]
            return spec
        return None


@dataclass(frozen=True)
class NativeCapabilityStatus:
    name: str
    available: bool
    reason_code: str
    callsite_signature: str

    def __post_init__(self) -> None:
        if type(self.name) is not str or self.name not in KNOWN_NATIVE_CAPABILITIES:
            raise ValueError("unknown capability status")
        if type(self.available) is not bool:
            raise ValueError("capability availability must be boolean")
        if type(self.reason_code) is not str or self.reason_code not in _REASON_CODES:
            raise ValueError("invalid capability reason")
        _validate_digest(self.callsite_signature)


@dataclass(frozen=True)
class NativeHookSourceDigest:
    target: str
    sha256: str

    def __post_init__(self) -> None:
        if type(self.target) is not str or self.target not in _EXPECTED_TARGETS:
            raise ValueError("unknown source digest target")
        _validate_digest(self.sha256)


@dataclass(frozen=True)
class NativeHookCapabilityProbe:
    capabilities: NativeHookCapabilities
    statuses: tuple[NativeCapabilityStatus, ...]
    source_commit: str
    source_digests: tuple[NativeHookSourceDigest, ...]
    plugin_evidence_sha256: str
    reason_code: str

    def __post_init__(self) -> None:
        if type(self.capabilities) is not NativeHookCapabilities:
            raise ValueError("invalid capability primitive")
        if type(self.statuses) is not tuple or not all(
            type(status) is NativeCapabilityStatus for status in self.statuses
        ):
            raise ValueError("invalid capability statuses")
        names = [status.name for status in self.statuses]
        if len(names) != len(set(names)) or set(names) != KNOWN_NATIVE_CAPABILITIES:
            raise ValueError("capability statuses must be exact and unique")
        available = frozenset(
            status.name for status in self.statuses if status.available
        )
        if available != self.capabilities.available:
            raise ValueError("capability statuses do not match available set")
        if type(self.source_commit) is not str:
            raise ValueError("invalid source commit")
        if type(self.source_digests) is not tuple or not all(
            type(item) is NativeHookSourceDigest for item in self.source_digests
        ):
            raise ValueError("invalid source digests")
        targets = [item.target for item in self.source_digests]
        if len(targets) != len(set(targets)) or not set(targets) <= _EXPECTED_TARGETS:
            raise ValueError("source digests must be unique")
        if self.source_commit and _COMMIT_RE.fullmatch(self.source_commit) is None:
            raise ValueError("invalid source commit")
        if type(self.plugin_evidence_sha256) is not str:
            raise ValueError("invalid plugin evidence primitive")
        if self.plugin_evidence_sha256:
            _validate_digest(self.plugin_evidence_sha256)
        if type(self.reason_code) is not str or self.reason_code not in _REASON_CODES:
            raise ValueError("invalid probe reason")
        if self.reason_code == "verified" and (
            self.source_commit != FIXED_TAG_COMMIT
            or set(targets) != _EXPECTED_TARGETS
            or len(targets) != len(_EXPECTED_TARGETS)
            or not self.plugin_evidence_sha256
        ):
            raise ValueError("verified probe evidence must be exact and complete")


@dataclass(frozen=True)
class _ContractCheck:
    available: bool
    reason_code: str
    signature: str


def load_fixed_tag_native_hook_provenance(
    path: str | Path,
) -> FixedTagNativeHookProvenance:
    try:
        raw = _read_absolute_regular_file(
            _coerce_absolute_path(path), _MAX_MANIFEST_BYTES
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("invalid provenance manifest") from exc
    return _parse_fixed_tag_native_hook_provenance(raw)


def _parse_fixed_tag_native_hook_provenance(
    raw: bytes,
) -> FixedTagNativeHookProvenance:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_MANIFEST_BYTES:
        raise ValueError("invalid provenance manifest")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid provenance manifest") from exc
    if type(payload) is not dict or set(payload) != {"commit", "files"}:
        raise ValueError("invalid provenance manifest shape")
    files = payload.get("files")
    if type(files) is not list:
        raise ValueError("invalid provenance file list")
    try:
        sources: list[NativeHookSourceProvenance] = []
        for item in files:
            if type(item) is not dict or set(item) != {
                "target", "relative_path", "sha256", "anchors"
            }:
                raise ValueError("invalid provenance file shape")
            anchor_items = item["anchors"]
            if type(anchor_items) is not list:
                raise ValueError("invalid provenance anchors")
            anchors = []
            for anchor in anchor_items:
                if type(anchor) is not dict or set(anchor) != {
                    "name", "line_start", "line_end", "slice_path", "slice_sha256"
                }:
                    raise ValueError("invalid provenance anchor shape")
                anchors.append(
                    NativeHookAnchorProvenance(
                        name=anchor["name"],
                        line_start=anchor["line_start"],
                        line_end=anchor["line_end"],
                        slice_path=anchor["slice_path"],
                        slice_sha256=anchor["slice_sha256"],
                    )
                )
            sources.append(
                NativeHookSourceProvenance(
                    target=item["target"],
                    relative_path=item["relative_path"],
                    sha256=item["sha256"],
                    anchors=tuple(anchors),
                )
            )
        return FixedTagNativeHookProvenance(
            commit=payload["commit"],
            sources=tuple(sources),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid provenance manifest") from exc


def _load_canonical_fixed_tag_provenance() -> FixedTagNativeHookProvenance:
    raw = _read_absolute_regular_file(
        _coerce_absolute_path(FIXED_TAG_PROVENANCE_PATH), _MAX_MANIFEST_BYTES
    )
    if _sha256(raw) != FIXED_TAG_PROVENANCE_SHA256:
        raise ValueError("canonical provenance digest mismatch")
    provenance = _parse_fixed_tag_native_hook_provenance(raw)
    anchors = tuple(
        anchor for source in provenance.sources for anchor in source.anchors
    )
    if (
        provenance.commit != FIXED_TAG_COMMIT
        or len(provenance.sources) != len(_EXPECTED_TARGETS)
        or len(anchors) != FIXED_TAG_PROVENANCE_ANCHOR_COUNT
        or any(not source.anchors for source in provenance.sources)
        or len({anchor.slice_path for anchor in anchors}) != len(anchors)
    ):
        raise ValueError("canonical provenance contract mismatch")
    return provenance


def verify_provenance_slices(
    provenance: FixedTagNativeHookProvenance,
    *,
    fixture_root: str | Path,
) -> bool:
    try:
        root = _coerce_absolute_path(fixture_root)
        root_descriptor = _open_absolute_directory(root)
    except (OSError, TypeError, ValueError):
        return False
    seen_paths: set[str] = set()
    try:
        if (
            type(provenance) is not FixedTagNativeHookProvenance
            or not all(source.anchors for source in provenance.sources)
        ):
            return False
        for source in provenance.sources:
            for anchor in source.anchors:
                if anchor.slice_path in seen_paths:
                    return False
                seen_paths.add(anchor.slice_path)
                try:
                    data = _read_bound_relative_file(
                        root_descriptor, anchor.slice_path, _MAX_SLICE_BYTES
                    )
                except (OSError, ValueError):
                    return False
                if not data or _sha256(data) != anchor.slice_sha256:
                    return False
        return _bound_directory_unchanged(root, root_descriptor)
    finally:
        os.close(root_descriptor)


def probe_native_hook_capabilities(
    hermes_root: str | Path,
    *,
    expected_commit: str,
    runtime_python: str | Path,
) -> NativeHookCapabilityProbe:
    if type(expected_commit) is not str or _COMMIT_RE.fullmatch(expected_commit) is None:
        return _closed_probe("expected_commit_invalid")
    if expected_commit != FIXED_TAG_COMMIT:
        return _closed_probe("expected_commit_unsupported")
    try:
        provenance = _load_canonical_fixed_tag_provenance()
    except (OSError, TypeError, ValueError):
        return _closed_probe("provenance_invalid")
    if expected_commit != provenance.commit:
        return _closed_probe("provenance_commit_mismatch")
    try:
        root = _coerce_absolute_path(hermes_root)
        root_descriptor = _open_absolute_directory(root)
    except (OSError, TypeError, ValueError):
        return _closed_probe("hermes_root_invalid")
    snapshot: _SourceSnapshot | None = None
    source_text: dict[str, str] = {}
    source_reason: dict[str, str] = {}
    source_digests: list[NativeHookSourceDigest] = []
    try:
        actual_commit, source_clean = _git_source_state(root)
        if actual_commit != expected_commit:
            return _closed_probe(
                "source_commit_mismatch", observed_commit=actual_commit
            )
        if not source_clean:
            return _closed_probe("source_dirty", observed_commit=actual_commit)
        if (
            not _bound_directory_unchanged(root, root_descriptor)
            or not verify_provenance_slices(
                provenance, fixture_root=FIXED_TAG_PROVENANCE_PATH.parent
            )
        ):
            return _closed_probe(
                "provenance_invalid", observed_commit=actual_commit
            )
        try:
            snapshot = _build_trusted_source_snapshot(
                root, expected_commit, provenance
            )
        except (OSError, subprocess.SubprocessError, tarfile.TarError, ValueError):
            return _closed_probe(
                "source_commit_mismatch", observed_commit=actual_commit
            )
        if not _bound_directory_unchanged(root, root_descriptor):
            return _closed_probe("source_dirty", observed_commit=actual_commit)
        for source in provenance.sources:
            try:
                data = _read_bound_relative_file(
                    snapshot.descriptor, source.relative_path, _MAX_SOURCE_BYTES
                )
            except FileNotFoundError:
                source_reason[source.target] = "source_missing"
                continue
            except (OSError, ValueError):
                source_reason[source.target] = "source_not_regular"
                continue
            digest = _sha256(data)
            source_digests.append(
                NativeHookSourceDigest(target=source.target, sha256=digest)
            )
            if digest != source.sha256:
                source_reason[source.target] = "source_digest_mismatch"
                continue
            if not _anchors_match_source(data, source.anchors):
                source_reason[source.target] = "source_anchor_mismatch"
                continue
            try:
                text = data.decode("utf-8")
                ast.parse(text)
            except (UnicodeDecodeError, SyntaxError):
                source_reason[source.target] = "source_ast_invalid"
                continue
            source_text[source.target] = text
        if source_reason or len(source_digests) != len(_EXPECTED_TARGETS):
            return _closed_probe(
                next(iter(source_reason.values()), "source_digest_mismatch"),
                observed_commit=actual_commit,
            )
        if not _bound_directory_unchanged(snapshot.root, snapshot.descriptor):
            return _closed_probe("source_dirty", observed_commit=actual_commit)
        plugin_evidence, plugin_reason = _produce_plugin_manager_evidence(
            snapshot, runtime_python
        )
        if plugin_evidence is None:
            return _closed_probe(plugin_reason, observed_commit=actual_commit)
        final_commit, final_clean = _git_source_state(root)
        if final_commit != expected_commit:
            return _closed_probe(
                "source_commit_mismatch", observed_commit=final_commit
            )
        if (
            not final_clean
            or not _bound_directory_unchanged(root, root_descriptor)
            or not _bound_directory_unchanged(snapshot.root, snapshot.descriptor)
        ):
            return _closed_probe("source_dirty", observed_commit=final_commit)
    finally:
        if snapshot is not None:
            snapshot.close()
        os.close(root_descriptor)

    contracts = {
        "authenticated_ingress": _probe_authenticated_ingress,
        "turn_start": _probe_turn_start,
        "turn_terminal_result": _probe_turn_terminal,
        "stable_tool_lifecycle": _probe_tool_lifecycle,
        "approval_observe": _probe_approval_observe,
        "subagent_lifecycle": _probe_subagent_lifecycle,
        "answer_delta": _probe_answer_delta,
        "thinking_delta": _probe_thinking_delta,
        "interaction_round_trip": _probe_interaction_round_trip,
        "final_delivery_disposition": _probe_terminal_disposition,
        "command_platform_notice": _probe_command_platform_notice,
        "cron_delivery": _probe_cron_delivery,
        "exact_native_delivery": _probe_exact_native_delivery,
    }
    dependencies = {
        "authenticated_ingress": ("plugin_manager", "gateway"),
        "turn_start": ("plugin_manager", "turn_context"),
        "turn_terminal_result": ("plugin_manager", "turn_finalizer"),
        "stable_tool_lifecycle": ("plugin_manager", "tool_hooks"),
        "approval_observe": ("plugin_manager", "tool_hooks", "approval"),
        "subagent_lifecycle": ("plugin_manager", "subagent"),
        "answer_delta": ("plugin_manager", "gateway"),
        "thinking_delta": ("plugin_manager", "gateway"),
        "interaction_round_trip": ("plugin_manager", "approval", "gateway"),
        "final_delivery_disposition": (
            "plugin_manager", "turn_finalizer", "gateway"
        ),
        "command_platform_notice": ("plugin_manager", "gateway"),
        "cron_delivery": ("plugin_manager", "cron"),
        "exact_native_delivery": ("plugin_manager", "base"),
    }
    statuses: list[NativeCapabilityStatus] = []
    for name in sorted(KNOWN_NATIVE_CAPABILITIES):
        targets = dependencies[name]
        drift = next(
            (source_reason[target] for target in targets if target in source_reason),
            None,
        )
        if drift is not None:
            statuses.append(_status(name, False, drift, targets))
            continue
        check = contracts[name](source_text)
        statuses.append(
            _status(
                name,
                check.available,
                check.reason_code,
                targets,
                signature=check.signature,
            )
        )
    available_names = frozenset(
        status.name for status in statuses if status.available
    )
    return NativeHookCapabilityProbe(
        capabilities=NativeHookCapabilities.from_names(available_names),
        statuses=tuple(statuses),
        source_commit=actual_commit,
        source_digests=tuple(sorted(source_digests, key=lambda item: item.target)),
        plugin_evidence_sha256=plugin_evidence.attestation_sha256,
        reason_code="verified",
    )


def _probe_authenticated_ingress(sources: dict[str, str]) -> _ContractCheck:
    manager = ast.parse(sources["plugin_manager"])
    gateway = ast.parse(sources["gateway"])
    call = _single_hook_call(gateway, "pre_gateway_dispatch")
    auth_calls = _named_calls(gateway, "_is_user_authorized") + _named_calls(
        gateway, "authenticated"
    )
    nodes: list[ast.AST] = [manager]
    if call is not None:
        nodes.append(call)
    nodes.extend(auth_calls)
    available = (
        _plugin_manager_contract(manager)
        and call is not None
        and {"event", "gateway", "session_store"} <= _keyword_names(call)
        and bool(auth_calls)
        and call.lineno > max(item.lineno for item in auth_calls)
    )
    return _check(
        "authenticated_ingress",
        available,
        "verified" if available else "authenticated_ingress_missing",
        nodes,
    )


def _probe_turn_start(sources: dict[str, str]) -> _ContractCheck:
    manager = ast.parse(sources["plugin_manager"])
    turn = ast.parse(sources["turn_context"])
    call = _single_hook_call(turn, "pre_llm_call")
    required = {
        "session_id", "task_id", "turn_id", "user_message",
        "conversation_history", "is_first_turn", "model", "platform",
    }
    assignment = _turn_id_assignment_before(turn, call)
    available = (
        _plugin_manager_contract(manager)
        and call is not None
        and required <= _keyword_names(call)
        and _call_enclosed_by_function(
            turn, call, "build_turn_context", "prepare_turn"
        )
        and assignment is not None
        and _call_result_is_consumed(turn, call)
    )
    return _check(
        "turn_start",
        available,
        "verified" if available else "callsite_contract_mismatch",
        [manager, call, assignment],
    )


def _probe_turn_terminal(sources: dict[str, str]) -> _ContractCheck:
    manager = ast.parse(sources["plugin_manager"])
    tree = ast.parse(sources["turn_finalizer"])
    post = _single_hook_call(tree, "post_llm_call")
    end = _single_hook_call(tree, "on_session_end")
    post_required = {
        "session_id", "task_id", "turn_id", "assistant_response", "platform"
    }
    end_required = {
        "session_id", "task_id", "turn_id", "completed", "failed",
        "interrupted", "turn_exit_reason", "platform",
    }
    result_assignment = _named_assignment_between(tree, "result", post, end)
    available = (
        _plugin_manager_contract(manager)
        and post is not None
        and end is not None
        and post_required <= _keyword_names(post)
        and end_required <= _keyword_names(end)
        and _call_is_guarded_by(
            tree, post, required_names={"final_response", "interrupted"}
        )
        and post.lineno < end.lineno
        and result_assignment is not None
        and _call_precedes_return_in_same_function(tree, end, "result")
    )
    return _check(
        "turn_terminal_result",
        available,
        "verified" if available else "callsite_contract_mismatch",
        [manager, post, result_assignment, end],
    )


def _probe_tool_lifecycle(sources: dict[str, str]) -> _ContractCheck:
    manager = ast.parse(sources["plugin_manager"])
    tools = ast.parse(sources["tool_hooks"])
    pre = _single_named_call(tools, "resolve_pre_tool_block")
    post = _single_hook_call(tools, "post_tool_call")
    required = {
        "task_id", "session_id", "tool_call_id", "turn_id", "api_request_id"
    }
    normal_post = _normal_post_tool_call(tools)
    available = (
        _plugin_manager_contract(manager)
        and _manager_pre_tool_contract(manager)
        and pre is not None
        and required <= _keyword_names(pre)
        and post is not None
        and required | {"duration_ms", "status"} <= _keyword_names(post)
        and _call_precedes_dispatch(tools, pre)
        and normal_post is not None
    )
    return _check(
        "stable_tool_lifecycle",
        available,
        "verified" if available else "callsite_contract_mismatch",
        [manager, pre, post, normal_post],
    )


def _probe_approval_observe(sources: dict[str, str]) -> _ContractCheck:
    manager = ast.parse(sources["plugin_manager"])
    tools = ast.parse(sources["tool_hooks"])
    approval = ast.parse(sources["approval"])
    set_context = _single_named_call(tools, "set_current_observability_context")
    reset_context = _single_named_call(tools, "reset_current_observability_context")
    await_fn = _function(approval, "_await_gateway_decision")
    pre = _single_hook_call(await_fn, "pre_approval_request") if await_fn else None
    post = _single_hook_call(await_fn, "post_approval_response") if await_fn else None
    fire_fn = _function(approval, "_fire_approval_hook")
    invoke = _single_named_call(fire_fn, "invoke_hook") if fire_fn else None
    available = (
        _plugin_manager_contract(manager)
        and set_context is not None
        and {"turn_id", "tool_call_id"} <= _keyword_names(set_context)
        and reset_context is not None
        and _approval_contextvars_present(approval)
        and invoke is not None
        and any(keyword.arg is None for keyword in invoke.keywords)
        and pre is not None
        and post is not None
        and _approval_enqueue_pre_notify_wait_order(await_fn, pre, post)
    )
    return _check(
        "approval_observe",
        available,
        "verified" if available else "callsite_contract_mismatch",
        [manager, set_context, reset_context, fire_fn, pre, post],
    )


def _probe_subagent_lifecycle(sources: dict[str, str]) -> _ContractCheck:
    manager = ast.parse(sources["plugin_manager"])
    tree = ast.parse(sources["subagent"])
    start = _single_hook_call(tree, "subagent_start")
    stop = _single_hook_call(tree, "subagent_stop")
    start_required = {
        "parent_session_id", "parent_turn_id", "child_session_id",
        "child_subagent_id", "child_role", "child_goal",
    }
    stop_required = {
        "parent_session_id", "parent_turn_id", "child_session_id",
        "child_role", "child_summary", "child_status", "duration_ms",
    }
    start_child = _keyword_value(start, "child_subagent_id")
    start_parent = _keyword_value(start, "parent_turn_id")
    stop_parent = _keyword_value(stop, "parent_turn_id")
    available = (
        _plugin_manager_contract(manager)
        and start is not None
        and stop is not None
        and start_required <= _keyword_names(start)
        and stop_required <= _keyword_names(stop)
        and _nonempty_expression(start_child)
        and _immutable_parent_turn_reference(start_parent)
        and _immutable_parent_turn_reference(stop_parent)
        and _call_precedes_return_in_same_function(tree, start, "child")
    )
    return _check(
        "subagent_lifecycle",
        available,
        "verified" if available else "callsite_contract_mismatch",
        [manager, start, stop],
    )


def _probe_answer_delta(sources: dict[str, str]) -> _ContractCheck:
    return _missing_hook_contract(
        "answer_delta", "answer_delta_missing", sources, ("plugin_manager", "gateway")
    )


def _probe_thinking_delta(sources: dict[str, str]) -> _ContractCheck:
    return _missing_hook_contract(
        "thinking_delta", "thinking_delta_missing", sources,
        ("plugin_manager", "gateway"),
    )


def _probe_interaction_round_trip(sources: dict[str, str]) -> _ContractCheck:
    manager = ast.parse(sources["plugin_manager"])
    approval = ast.parse(sources["approval"])
    gateway = ast.parse(sources["gateway"])
    resolver_calls = [
        node for tree in (approval, gateway) for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_name(node) in {
            "resolve_approval_choice", "resolve_clarify_choice",
            "resolve_slash_confirmation",
        }
    ]
    available = _plugin_manager_contract(manager) and len(resolver_calls) >= 3
    return _check(
        "interaction_round_trip", available,
        "verified" if available else "interaction_resolver_missing",
        [manager, approval, gateway, *resolver_calls],
    )


def _probe_terminal_disposition(sources: dict[str, str]) -> _ContractCheck:
    manager = ast.parse(sources["plugin_manager"])
    finalizer = ast.parse(sources["turn_finalizer"])
    gateway = ast.parse(sources["gateway"])
    consumers = [
        node for node in ast.walk(gateway)
        if isinstance(node, ast.Call)
        and _call_name(node) in {
            "take_terminal_disposition", "consume_terminal_disposition"
        }
    ]
    end = _single_hook_call(finalizer, "on_session_end")
    available = (
        _plugin_manager_contract(manager)
        and end is not None
        and bool(consumers)
        and any(_call_result_is_consumed(gateway, node) for node in consumers)
    )
    return _check(
        "final_delivery_disposition", available,
        "verified" if available else "terminal_consumer_missing",
        [manager, end, gateway, *consumers],
    )


def _probe_command_platform_notice(sources: dict[str, str]) -> _ContractCheck:
    return _missing_hook_contract(
        "command_platform_notice", "command_platform_notice_missing", sources,
        ("plugin_manager", "gateway"),
    )


def _probe_cron_delivery(sources: dict[str, str]) -> _ContractCheck:
    return _missing_hook_contract(
        "cron_delivery", "cron_hook_missing", sources, ("plugin_manager", "cron")
    )


def _probe_exact_native_delivery(sources: dict[str, str]) -> _ContractCheck:
    return _missing_hook_contract(
        "exact_native_delivery", "exact_native_delivery_missing", sources,
        ("plugin_manager", "base"),
    )


def _plugin_manager_contract(tree: ast.AST) -> bool:
    entrypoint_calls = _named_calls(tree, "entry_points")
    selectors = [
        call for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "select"
        and any(
            keyword.arg == "group"
            and (
                isinstance(keyword.value, ast.Constant)
                and keyword.value.value == "hermes_agent.plugins"
                or isinstance(keyword.value, ast.Name)
                and keyword.value.id == "ENTRY_POINTS_GROUP"
            )
            for keyword in call.keywords
        )
    ]
    enabled_fn = _function(tree, "_get_enabled_plugins")
    discovery = _function(tree, "_discover_and_load_inner")
    loader = _function(tree, "_load_plugin")
    load_ep = _function(tree, "_load_entrypoint_module")
    register_hook = _function(tree, "register_hook")
    invokes = _functions(tree, "invoke_hook")
    invoke = next(
        (
            function for function in invokes
            if "cb(**kwargs)" in ast.unparse(function)
            and "results.append(ret)" in ast.unparse(function)
        ),
        None,
    )
    if not all((entrypoint_calls, selectors, enabled_fn, discovery, loader, load_ep,
                register_hook, invoke)):
        return False
    discovery_text = ast.unparse(discovery)
    loader_text = ast.unparse(loader)
    register_text = ast.unparse(register_hook)
    invoke_text = ast.unparse(invoke)
    return (
        "enabled" in discovery_text
        and "_load_plugin" in discovery_text
        and "register" in loader_text
        and "PluginContext" in loader_text
        and "_hooks.setdefault" in register_text
        and "cb(**kwargs)" in invoke_text
        and "results.append(ret)" in invoke_text
    )


def _missing_hook_contract(
    name: str,
    reason_code: str,
    sources: dict[str, str],
    targets: tuple[str, ...],
) -> _ContractCheck:
    trees = [ast.parse(sources[target]) for target in targets]
    manager = trees[0]
    hook_calls = [
        node for tree in trees[1:] for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _hook_name(node) == name
    ]
    available = _plugin_manager_contract(manager) and bool(hook_calls)
    return _check(
        name,
        available,
        "verified" if available else reason_code,
        [*trees, *hook_calls],
    )


def _check(
    name: str,
    available: bool,
    reason_code: str,
    nodes: Iterable[ast.AST | None],
) -> _ContractCheck:
    material = []
    for node in nodes:
        if node is None:
            continue
        try:
            material.append(ast.dump(node, annotate_fields=True, include_attributes=False))
        except TypeError:  # Python 3.8 compatibility for include_attributes
            material.append(ast.dump(node, annotate_fields=True))
    payload = json.dumps(
        {"capability": name, "contracts": material},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _ContractCheck(available, reason_code, _sha256(payload))


def _manager_pre_tool_contract(tree: ast.AST) -> bool:
    function = _function(tree, "_get_pre_tool_call_directive_details")
    if function is None:
        # Real fixed tag names this helper; a minimal fixture may keep the same.
        return False
    calls = [call for call in ast.walk(function) if _hook_name(call) == "pre_tool_call"]
    if len(calls) != 1:
        return False
    required = {"session_id", "tool_call_id", "turn_id"}
    return required <= _keyword_names(calls[0])


def _single_hook_call(tree: ast.AST | None, hook_name: str) -> ast.Call | None:
    if tree is None:
        return None
    calls = [call for call in ast.walk(tree) if _hook_name(call) == hook_name]
    return calls[0] if len(calls) == 1 else None


def _hook_name(call: ast.AST) -> str | None:
    if not isinstance(call, ast.Call) or not call.args:
        return None
    function_name = _call_name(call)
    if function_name not in {"invoke_hook", "_invoke_hook", "_fire_approval_hook"}:
        return None
    first = call.args[0]
    return first.value if isinstance(first, ast.Constant) and type(first.value) is str else None


def _single_named_call(tree: ast.AST | None, name: str) -> ast.Call | None:
    if tree is None:
        return None
    calls = _named_calls(tree, name)
    return calls[0] if len(calls) == 1 else None


def _named_calls(tree: ast.AST | None, name: str) -> list[ast.Call]:
    if tree is None:
        return []
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == name
    ]


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _keyword_names(call: ast.Call) -> set[str]:
    return {keyword.arg for keyword in call.keywords if keyword.arg is not None}


def _function(tree: ast.AST, *names: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            return node
    return None


def _functions(
    tree: ast.AST,
    *names: str,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]


def _call_enclosed_by_function(
    tree: ast.AST,
    call: ast.Call,
    *names: str,
) -> bool:
    return any(
        function.lineno <= call.lineno <= (function.end_lineno or function.lineno)
        for name in names
        if (function := _function(tree, name)) is not None
    )


def _call_result_is_consumed(tree: ast.AST, call: ast.Call) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is call:
            return True
        if isinstance(node, ast.Return) and node.value is call:
            return True
    return False


def _call_is_guarded_by(
    tree: ast.AST,
    call: ast.Call,
    *,
    required_names: set[str],
) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if not (node.lineno <= call.lineno <= (node.end_lineno or node.lineno)):
            continue
        names = {item.id for item in ast.walk(node.test) if isinstance(item, ast.Name)}
        if required_names <= names and any(
            isinstance(item, ast.UnaryOp)
            and isinstance(item.op, ast.Not)
            and isinstance(item.operand, ast.Name)
            and item.operand.id == "interrupted"
            for item in ast.walk(node.test)
        ):
            return True
    return False


def _call_precedes_return_in_same_function(
    tree: ast.AST,
    call: ast.Call,
    return_name: str,
) -> bool:
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not (function.lineno <= call.lineno <= (function.end_lineno or function.lineno)):
            continue
        return any(
            isinstance(node, ast.Return)
            and node.lineno > call.lineno
            and isinstance(node.value, ast.Name)
            and node.value.id == return_name
            for node in ast.walk(function)
        )
    return False


def _call_precedes_dispatch(tree: ast.AST, call: ast.Call) -> bool:
    dispatches = _named_calls(tree, "dispatch") + _named_calls(
        tree, "run_tool_execution_middleware"
    )
    return bool(dispatches) and call.lineno < min(item.lineno for item in dispatches)


def _normal_post_tool_call(tree: ast.AST) -> ast.Call | None:
    calls = [
        call for call in _named_calls(tree, "_emit_post_tool_call_hook")
        if _keyword_value(call, "result") is not None
        and _keyword_value(call, "duration_ms") is not None
    ]
    return max(calls, key=lambda item: item.lineno) if calls else None


def _turn_id_assignment_before(
    tree: ast.AST,
    call: ast.Call | None,
) -> ast.Assign | ast.AnnAssign | None:
    if call is None:
        return None
    candidates: list[ast.Assign | ast.AnnAssign] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.lineno >= call.lineno:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Name) and target.id == "turn_id"
                or isinstance(target, ast.Attribute) and target.attr == "_current_turn_id"
            ):
                candidates.append(node)
                break
    return max(candidates, key=lambda item: item.lineno) if candidates else None


def _named_assignment_between(
    tree: ast.AST,
    name: str,
    first: ast.Call | None,
    second: ast.Call | None,
) -> ast.Assign | ast.AnnAssign | None:
    if first is None or second is None:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if first.lineno < node.lineno < second.lineno and any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            return node
    return None


def _keyword_value(call: ast.Call | None, name: str) -> ast.AST | None:
    if call is None:
        return None
    return next(
        (keyword.value for keyword in call.keywords if keyword.arg == name),
        None,
    )


def _nonempty_expression(node: ast.AST | None) -> bool:
    return not (
        node is None
        or isinstance(node, ast.Constant) and node.value in {None, ""}
    )


def _immutable_parent_turn_reference(node: ast.AST | None) -> bool:
    if isinstance(node, ast.Attribute):
        return (
            node.attr == "_parent_turn_id"
            and isinstance(node.value, ast.Name)
            and node.value.id == "child"
        )
    if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
        candidate = node
    elif len(node.values) == 2 and isinstance(node.values[1], ast.Constant):
        candidate = node.values[0]
    else:
        return False
    if not isinstance(candidate, ast.Call) or _call_name(candidate) != "getattr":
        return False
    if len(candidate.args) < 2:
        return False
    owner, attribute = candidate.args[:2]
    return (
        isinstance(owner, ast.Name)
        and owner.id == "child"
        and isinstance(attribute, ast.Constant)
        and type(attribute.value) is str
        and attribute.value == "_parent_turn_id"
    )


def _approval_contextvars_present(tree: ast.AST) -> bool:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if (
            isinstance(value, ast.Call)
            and _call_name(value) == "ContextVar"
            and len(targets) == 1
            and isinstance(targets[0], ast.Name)
        ):
            names.add(targets[0].id)
    source = ast.unparse(tree)
    return (
        {"_approval_turn_id", "_approval_tool_call_id"} <= names
        and "_approval_turn_id.get()" in source
        and "_approval_tool_call_id.get()" in source
    )


def _approval_enqueue_pre_notify_wait_order(
    tree: ast.AST | None,
    pre: ast.Call,
    post: ast.Call,
) -> bool:
    if tree is None:
        return False
    entry_calls = _named_calls(tree, "_ApprovalEntry")
    notify_calls = _named_calls(tree, "notify_cb")
    wait_calls = _named_calls(tree, "wait")
    return (
        bool(entry_calls and notify_calls and wait_calls)
        and entry_calls[0].lineno < pre.lineno < notify_calls[0].lineno
        and notify_calls[0].lineno < wait_calls[0].lineno < post.lineno
    )


def _status(
    name: str,
    available: bool,
    reason_code: str,
    targets: Iterable[str],
    *,
    signature: str | None = None,
) -> NativeCapabilityStatus:
    signature_payload = json.dumps(
        {"capability": name, "targets": sorted(targets)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return NativeCapabilityStatus(
        name=name,
        available=available,
        reason_code=reason_code,
        callsite_signature=signature or _sha256(signature_payload),
    )


def _closed_probe(
    reason_code: str,
    *,
    observed_commit: str = "",
    plugin_evidence_sha256: str = "",
) -> NativeHookCapabilityProbe:
    statuses = tuple(
        _status(name, False, reason_code, ())
        for name in sorted(KNOWN_NATIVE_CAPABILITIES)
    )
    return NativeHookCapabilityProbe(
        capabilities=NativeHookCapabilities.from_names(()),
        statuses=statuses,
        source_commit=(
            observed_commit
            if type(observed_commit) is str
            and _COMMIT_RE.fullmatch(observed_commit)
            else ""
        ),
        source_digests=(),
        plugin_evidence_sha256=plugin_evidence_sha256,
        reason_code=reason_code,
    )


_PLUGIN_PROBE_PAYLOAD_KEYS = frozenset({
    "schema",
    "python_version",
    "executable",
    "prefix",
    "base_prefix",
    "purelib",
    "platlib",
    "manager_origin",
    "distribution_name",
    "distribution_version",
    "distribution_metadata_path",
    "record_path",
    "record_sha256",
    "entrypoint_group",
    "entrypoint_key",
    "entrypoint_value",
    "entrypoint_origin",
    "package_origin",
    "enabled_config",
    "matching_entrypoint_count",
    "matching_discovered_count",
    "matching_enabled_count",
    "matching_loaded_count",
    "registered_hooks",
    "loaded_modules",
    "pycache_prefix",
    "runtime_fd_identity",
    "snapshot_fd_identity_before",
    "snapshot_fd_identity_after",
    "home_fd_identity_before",
    "home_fd_identity_after",
    "snapshot_alias_identity_before",
    "snapshot_alias_identity_after",
    "home_alias_identity_before",
    "home_alias_identity_after",
    "child_cwd_identity_before",
    "child_cwd_identity_after",
    "descriptor_root_mode",
    "source_import_root",
    "home_root",
    "bundled_plugins_root",
    "child_sys_path",
    "attestation_sha256",
})

def _produce_plugin_manager_evidence(
    snapshot: _SourceSnapshot,
    runtime_python: str | Path,
) -> tuple[_PluginManagerEvidence | None, str]:
    try:
        runtime = _validate_runtime_python(runtime_python)
    except (OSError, TypeError, ValueError):
        return None, "plugin_runtime_unverified"
    home_name = tempfile.mkdtemp(prefix="hfc-native-hook-probe-")
    home = Path(os.path.realpath(home_name))
    home_descriptor = -1
    try:
        home_descriptor = _open_absolute_directory(home)
        home_info = os.fstat(home_descriptor)
        home_identity = (home_info.st_dev, home_info.st_ino)
        os.mkdir("bundled-plugins", 0o700, dir_fd=home_descriptor)
        os.mkdir("pycache", 0o700, dir_fd=home_descriptor)
        config = b"plugins:\n  enabled:\n    - hermes-feishu-card\n  disabled: []\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        config_descriptor = os.open(
            "config.yaml", flags, 0o600, dir_fd=home_descriptor
        )
        try:
            _write_all(config_descriptor, config)
        finally:
            os.close(config_descriptor)
        environment = {
            "HOME": ".",
            "HERMES_HOME": ".",
            "HERMES_BUNDLED_PLUGINS": "bundled-plugins",
            "HERMES_ENABLE_PROJECT_PLUGINS": "0",
            "HERMES_SAFE_MODE": "0",
            "HERMES_FEISHU_CARD_ENABLED": "0",
        }
        try:
            child_output, child_ok = _run_forked_plugin_probe(
                snapshot=snapshot,
                runtime=runtime,
                home=home,
                home_descriptor=home_descriptor,
                home_identity=home_identity,
                environment=environment,
            )
        except OSError:
            return None, "plugin_runtime_unverified"
        if not child_ok:
            return None, "plugin_runtime_unverified"
        try:
            payload = _decode_canonical_json_object(child_output)
        except (UnicodeDecodeError, ValueError):
            return None, "plugin_evidence_invalid"
        reason = _validate_plugin_manager_payload(
            payload,
            runtime=runtime,
            snapshot=snapshot,
            home=home,
            home_descriptor=home_descriptor,
        )
        if reason != "verified":
            return None, reason
        return _PluginManagerEvidence(payload["attestation_sha256"]), "verified"
    finally:
        if home_descriptor >= 0:
            os.close(home_descriptor)
        runtime.close()
        shutil.rmtree(home, ignore_errors=True)


def _run_forked_plugin_probe(
    *,
    snapshot: _SourceSnapshot,
    runtime: _RuntimeBinding,
    home: Path,
    home_descriptor: int,
    home_identity: tuple[int, int],
    environment: dict[str, str],
) -> tuple[bytes, bool]:
    if (
        _descriptor_alias_identity(snapshot.descriptor) != snapshot.identity
        or _descriptor_alias_identity(home_descriptor) != home_identity
    ):
        raise OSError("descriptor root unavailable")
    read_descriptor, write_descriptor = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(read_descriptor)
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
            os.dup2(devnull, 2)
            os.close(devnull)
            _plugin_probe_child(
                snapshot=snapshot,
                runtime=runtime,
                home_descriptor=home_descriptor,
                home_identity=home_identity,
                environment=environment,
                output_descriptor=write_descriptor,
            )
            os._exit(0)
        except BaseException:
            os._exit(70)
    os.close(write_descriptor)
    deadline = time.monotonic() + _PLUGIN_PROBE_TIMEOUT_SECONDS
    status = None
    try:
        while time.monotonic() < deadline:
            waited, child_status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                status = child_status
                break
            time.sleep(0.01)
        if status is None:
            os.kill(pid, signal.SIGKILL)
            _, status = os.waitpid(pid, 0)
        output = _read_pipe_bounded(read_descriptor, _MAX_SUBPROCESS_BYTES)
    finally:
        os.close(read_descriptor)
    return output, bool(
        status is not None
        and os.WIFEXITED(status)
        and os.WEXITSTATUS(status) == 0
    )


def _read_pipe_bounded(descriptor: int, limit: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(65536, limit + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError("child output exceeds bound")
        chunks.append(chunk)
    return b"".join(chunks)


def _plugin_probe_child(
    *,
    snapshot: _SourceSnapshot,
    runtime: _RuntimeBinding,
    home_descriptor: int,
    home_identity: tuple[int, int],
    environment: dict[str, str],
    output_descriptor: int,
) -> None:
    snapshot_before = _fd_identity(snapshot.descriptor)
    home_before = _fd_identity(home_descriptor)
    runtime_identity = _fd_identity(runtime.descriptor)
    snapshot_alias_before = _descriptor_alias_identity(snapshot.descriptor)
    home_alias_before = _descriptor_alias_identity(home_descriptor)
    if (
        snapshot_before != snapshot.identity
        or home_before != home_identity
        or runtime_identity != runtime.identity
        or snapshot_alias_before != snapshot.identity
        or home_alias_before != home_identity
    ):
        raise RuntimeError("fd identity mismatch")
    os.fchdir(home_descriptor)
    child_cwd_before = _cwd_identity()
    pycache_descriptor = _open_bound_relative_directory(
        home_descriptor, "pycache"
    )
    try:
        if os.listdir(pycache_descriptor):
            raise RuntimeError("private pycache is not empty")
    finally:
        os.close(pycache_descriptor)
    sys.pycache_prefix = "pycache"
    os.environ.clear()
    os.environ.update(environment)
    for name in tuple(sys.modules):
        if (
            name == "hermes_feishu_card"
            or name.startswith("hermes_feishu_card.")
            or name == "hermes_cli"
            or name.startswith("hermes_cli.")
            or name in {"hermes_constants", "utils", "model_tools"}
            or name == "tools"
            or name.startswith("tools.")
        ):
            sys.modules.pop(name, None)
    sys.path = [
        item for item in sys.path
        if type(item) is str
        and item
        and "hfc-fixed-source-snapshot-" not in item
    ]
    descriptor_finder = _DescriptorSourceFinder(snapshot.descriptor)
    sys.meta_path.insert(0, descriptor_finder)
    importlib.invalidate_caches()
    plugins = importlib.import_module("hermes_cli.plugins")
    manager = plugins.PluginManager()
    entrypoints = list(
        importlib.metadata.entry_points(group="hermes_agent.plugins")
    )
    matching = [
        ep for ep in entrypoints
        if type(ep.name) is str and ep.name == "hermes-feishu-card"
    ]
    enabled = plugins._get_enabled_plugins()
    manager.discover_and_load()
    rows = [
        row for row in manager.list_plugins()
        if type(row) is dict
        and type(row.get("name")) is str
        and row.get("name") == "hermes-feishu-card"
        and type(row.get("key")) is str
        and row.get("key") == "hermes-feishu-card"
    ]
    loaded = manager._plugins.get("hermes-feishu-card")
    module = getattr(loaded, "module", None)
    loaded_hooks = sorted(getattr(loaded, "hooks_registered", ()))
    manager_hooks = sorted(
        name for name, callbacks in manager._hooks.items() if callbacks
    )
    if loaded_hooks != manager_hooks:
        raise RuntimeError("hook registry mismatch")
    ep = matching[0] if len(matching) == 1 else None
    dist = getattr(ep, "dist", None) if ep is not None else None
    if dist is None and ep is not None:
        dist = importlib.metadata.distribution("hermes-feishu-streaming-card")
    dist_path = getattr(dist, "_path", None)
    if dist_path is None:
        raise RuntimeError("distribution metadata path missing")
    record_path = dist_path / "RECORD"
    record_bytes = record_path.read_bytes()
    package = importlib.import_module("hermes_feishu_card")
    loaded_modules = _loaded_module_evidence()
    snapshot_after = _fd_identity(snapshot.descriptor)
    home_after = _fd_identity(home_descriptor)
    snapshot_alias_after = _descriptor_alias_identity(snapshot.descriptor)
    home_alias_after = _descriptor_alias_identity(home_descriptor)
    child_cwd_after = _cwd_identity()
    if (
        snapshot_after != snapshot_before
        or home_after != home_before
        or _fd_identity(runtime.descriptor) != runtime_identity
        or snapshot_alias_after != snapshot_before
        or home_alias_after != home_before
        or child_cwd_before != home_before
        or child_cwd_after != home_before
    ):
        raise RuntimeError("fd identity changed")
    payload = {
        "schema": 2,
        "python_version": [
            sys.version_info.major,
            sys.version_info.minor,
            sys.version_info.micro,
        ],
        "executable": sys.executable,
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "purelib": sysconfig.get_path("purelib"),
        "platlib": sysconfig.get_path("platlib"),
        "manager_origin": plugins.__file__,
        "distribution_name": dist.metadata["Name"],
        "distribution_version": dist.version,
        "distribution_metadata_path": str(dist_path),
        "record_path": str(record_path),
        "record_sha256": _sha256(record_bytes),
        "entrypoint_group": ep.group if ep is not None else "",
        "entrypoint_key": ep.name if ep is not None else "",
        "entrypoint_value": ep.value if ep is not None else "",
        "entrypoint_origin": getattr(module, "__file__", "") or "",
        "package_origin": getattr(package, "__file__", "") or "",
        "enabled_config": sorted(enabled) if type(enabled) is set else [],
        "matching_entrypoint_count": len(matching),
        "matching_discovered_count": len(rows),
        "matching_enabled_count": sum(row.get("enabled") is True for row in rows),
        "matching_loaded_count": int(
            loaded is not None
            and module is not None
            and getattr(loaded, "enabled", None) is True
            and getattr(loaded, "error", None) is None
        ),
        "registered_hooks": manager_hooks,
        "loaded_modules": loaded_modules,
        "pycache_prefix": sys.pycache_prefix,
        "runtime_fd_identity": list(runtime_identity),
        "snapshot_fd_identity_before": list(snapshot_before),
        "snapshot_fd_identity_after": list(snapshot_after),
        "home_fd_identity_before": list(home_before),
        "home_fd_identity_after": list(home_after),
        "snapshot_alias_identity_before": list(snapshot_alias_before),
        "snapshot_alias_identity_after": list(snapshot_alias_after),
        "home_alias_identity_before": list(home_alias_before),
        "home_alias_identity_after": list(home_alias_after),
        "child_cwd_identity_before": list(child_cwd_before),
        "child_cwd_identity_after": list(child_cwd_after),
        "descriptor_root_mode": _DESCRIPTOR_ROOT_MODE,
        "source_import_root": _DESCRIPTOR_SOURCE_ROOT,
        "home_root": os.environ["HERMES_HOME"],
        "bundled_plugins_root": os.environ["HERMES_BUNDLED_PLUGINS"],
        "child_sys_path": list(sys.path),
    }
    canonical = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    payload["attestation_sha256"] = _sha256(canonical)
    output = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii") + b"\n"
    _write_all(output_descriptor, output)
    os.close(output_descriptor)


def _loaded_module_evidence() -> list[dict[str, str]]:
    evidence = []
    for name, module in sorted(sys.modules.items()):
        spec = getattr(module, "__spec__", None)
        loader = getattr(spec, "loader", None)
        if not (
            name == "hermes_feishu_card"
            or name.startswith("hermes_feishu_card.")
            or name == "hermes_cli"
            or name.startswith("hermes_cli.")
            or type(loader) is _DescriptorSourceLoader
        ):
            continue
        origin = getattr(spec, "origin", None)
        cached = getattr(spec, "cached", None)
        evidence.append({
            "name": name,
            "loader": f"{type(loader).__module__}.{type(loader).__qualname__}",
            "origin": origin if type(origin) is str else "",
            "cached": cached if type(cached) is str else "",
        })
    return evidence


def _fd_identity(descriptor: int) -> tuple[int, int]:
    info = os.fstat(descriptor)
    return info.st_dev, info.st_ino


def _descriptor_alias_identity(descriptor: int) -> tuple[int, int]:
    info = os.stat(".", dir_fd=descriptor, follow_symlinks=False)
    return info.st_dev, info.st_ino


def _cwd_identity() -> tuple[int, int]:
    descriptor = os.open(".", _directory_flags())
    try:
        return _fd_identity(descriptor)
    finally:
        os.close(descriptor)


def _decode_canonical_json_object(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_SUBPROCESS_BYTES:
        raise ValueError("invalid child output")
    stripped = raw[:-1] if raw.endswith(b"\n") else raw
    if not stripped or b"\n" in stripped or b"\r" in stripped:
        raise ValueError("invalid child output")

    def exact_object(pairs):
        result = {}
        for key, value in pairs:
            if type(key) is not str or key in result:
                raise ValueError("invalid child object")
            result[key] = value
        return result

    value = json.loads(stripped.decode("ascii"), object_pairs_hook=exact_object)
    if not _ordinary_json_value(value) or type(value) is not dict:
        raise ValueError("invalid child object")
    canonical = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    if stripped != canonical:
        raise ValueError("noncanonical child output")
    return value


def _ordinary_json_value(value: object, *, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if value is None or type(value) in {str, bool, int}:
        return True
    if type(value) is list:
        return len(value) <= 64 and all(
            _ordinary_json_value(item, depth=depth + 1) for item in value
        )
    if type(value) is dict:
        return len(value) <= 64 and all(
            type(key) is str and _ordinary_json_value(item, depth=depth + 1)
            for key, item in value.items()
        )
    return False


def _validate_plugin_manager_payload(
    payload: object,
    *,
    runtime: _RuntimeBinding,
    snapshot: _SourceSnapshot,
    home: Path,
    home_descriptor: int,
) -> str:
    if (
        type(payload) is not dict
        or not all(type(key) is str for key in payload)
        or set(payload) != _PLUGIN_PROBE_PAYLOAD_KEYS
    ):
        return "plugin_evidence_invalid"
    string_fields = _PLUGIN_PROBE_PAYLOAD_KEYS - {
        "schema",
        "python_version",
        "enabled_config",
        "matching_entrypoint_count",
        "matching_discovered_count",
        "matching_enabled_count",
        "matching_loaded_count",
        "registered_hooks",
        "loaded_modules",
        "runtime_fd_identity",
        "snapshot_fd_identity_before",
        "snapshot_fd_identity_after",
        "home_fd_identity_before",
        "home_fd_identity_after",
        "snapshot_alias_identity_before",
        "snapshot_alias_identity_after",
        "home_alias_identity_before",
        "home_alias_identity_after",
        "child_cwd_identity_before",
        "child_cwd_identity_after",
        "child_sys_path",
    }
    if any(type(payload[name]) is not str for name in string_fields):
        return "plugin_evidence_invalid"
    if type(payload["schema"]) is not int or payload["schema"] != 2:
        return "plugin_evidence_invalid"
    version = payload["python_version"]
    if (
        type(version) is not list
        or len(version) != 3
        or not all(type(item) is int and 0 <= item <= 999 for item in version)
    ):
        return "plugin_evidence_invalid"
    for name in (
        "matching_entrypoint_count",
        "matching_discovered_count",
        "matching_enabled_count",
        "matching_loaded_count",
    ):
        if type(payload[name]) is not int or not 0 <= payload[name] <= 64:
            return "plugin_evidence_invalid"
    if type(payload["enabled_config"]) is not list or not all(
        type(item) is str for item in payload["enabled_config"]
    ):
        return "plugin_evidence_invalid"
    hooks = payload["registered_hooks"]
    if (
        type(hooks) is not list
        or not all(type(item) is str for item in hooks)
        or hooks != sorted(set(hooks))
    ):
        return "plugin_evidence_invalid"
    identities = {
        "runtime_fd_identity": runtime.identity,
        "snapshot_fd_identity_before": snapshot.identity,
        "snapshot_fd_identity_after": snapshot.identity,
        "home_fd_identity_before": _fd_identity(home_descriptor),
        "home_fd_identity_after": _fd_identity(home_descriptor),
        "snapshot_alias_identity_before": snapshot.identity,
        "snapshot_alias_identity_after": snapshot.identity,
        "home_alias_identity_before": _fd_identity(home_descriptor),
        "home_alias_identity_after": _fd_identity(home_descriptor),
        "child_cwd_identity_before": _fd_identity(home_descriptor),
        "child_cwd_identity_after": _fd_identity(home_descriptor),
    }
    for name, expected_identity in identities.items():
        value = payload[name]
        if (
            type(value) is not list
            or len(value) != 2
            or not all(type(item) is int and item >= 0 for item in value)
            or tuple(value) != expected_identity
        ):
            return "plugin_runtime_unverified"
    if (
        payload["descriptor_root_mode"] != _DESCRIPTOR_ROOT_MODE
        or payload["source_import_root"] != _DESCRIPTOR_SOURCE_ROOT
        or payload["home_root"] != "."
        or payload["bundled_plugins_root"] != "bundled-plugins"
        or payload["pycache_prefix"] != "pycache"
    ):
        return "plugin_evidence_invalid"
    child_sys_path = payload["child_sys_path"]
    if (
        type(child_sys_path) is not list
        or not child_sys_path
        or len(child_sys_path) > 32
        or not all(type(item) is str and item for item in child_sys_path)
        or any(
            item == str(snapshot.root)
            or item.startswith(str(snapshot.container))
            or "hfc-fixed-source-snapshot-" in item
            for item in child_sys_path
        )
    ):
        return "plugin_evidence_invalid"
    if not _validate_loaded_module_evidence(
        payload["loaded_modules"],
        snapshot=snapshot,
        purelib=runtime.purelib,
    ):
        return "plugin_evidence_invalid"
    attestation = payload["attestation_sha256"]
    if _DIGEST_RE.fullmatch(attestation) is None:
        return "plugin_evidence_invalid"
    unsigned = dict(payload)
    del unsigned["attestation_sha256"]
    canonical = json.dumps(
        unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    if attestation != _sha256(canonical):
        return "plugin_attestation_unverified"
    if (
        payload["executable"] != str(runtime.path)
        or payload["prefix"] != str(runtime.prefix)
        or payload["base_prefix"] != str(runtime.base_prefix)
        or payload["purelib"] != str(runtime.purelib)
        or payload["platlib"] != str(runtime.platlib)
    ):
        return "plugin_runtime_unverified"
    try:
        for name in (
            "executable", "prefix", "base_prefix", "purelib", "platlib",
            "distribution_metadata_path", "record_path",
            "entrypoint_origin", "package_origin",
        ):
            _coerce_absolute_path(payload[name])
    except (TypeError, ValueError):
        return "plugin_runtime_unverified"
    if payload["manager_origin"] != (
        f"{_DESCRIPTOR_SOURCE_ROOT}/hermes_cli/plugins.py"
    ):
        return "plugin_source_commit_mismatch"
    if payload["matching_entrypoint_count"] != 1:
        return "entrypoint_ambiguous"
    if (
        payload["entrypoint_group"] != "hermes_agent.plugins"
        or payload["entrypoint_key"] != "hermes-feishu-card"
        or payload["entrypoint_value"] != "hermes_feishu_card.hermes_plugin"
        or payload["distribution_name"] != "hermes-feishu-streaming-card"
    ):
        return "entrypoint_identity_mismatch"
    if (
        payload["enabled_config"] != ["hermes-feishu-card"]
        or payload["matching_discovered_count"] != 1
        or payload["matching_enabled_count"] != 1
    ):
        return "plugin_not_enabled"
    if (
        payload["matching_loaded_count"] != 1
        or hooks != sorted(HFC_REGISTERED_HOOKS)
    ):
        return "registration_incomplete"
    if not _validate_installed_distribution(payload, runtime.purelib):
        return "entrypoint_identity_mismatch"
    return "verified"


def _validate_loaded_module_evidence(
    value: object,
    *,
    snapshot: _SourceSnapshot,
    purelib: Path,
) -> bool:
    if type(value) is not list or not 3 <= len(value) <= 128:
        return False
    names = []
    required = {
        "hermes_feishu_card",
        "hermes_feishu_card.hermes_plugin",
        "hermes_feishu_card.hermes_plugin_runtime",
        "hermes_cli",
        "hermes_cli.plugins",
    }
    for item in value:
        if (
            type(item) is not dict
            or set(item) != {"name", "loader", "origin", "cached"}
            or not all(type(key) is str for key in item)
            or not all(type(item[key]) is str for key in item)
        ):
            return False
        name = item["name"]
        names.append(name)
        if item["loader"] == _DESCRIPTOR_LOADER_NAME:
            prefix = f"{_DESCRIPTOR_SOURCE_ROOT}/"
            if not item["origin"].startswith(prefix) or item["cached"] != "":
                return False
            relative_path = item["origin"][len(prefix):]
            try:
                _validate_relative_path(relative_path)
                _read_bound_relative_file(
                    snapshot.descriptor, relative_path, _MAX_SOURCE_BYTES
                )
            except (OSError, TypeError, ValueError):
                return False
            if relative_path not in {
                f"{name.replace('.', '/')}.py",
                f"{name.replace('.', '/')}/__init__.py",
            }:
                return False
            continue
        if (
            item["loader"] != "_frozen_importlib_external.SourceFileLoader"
            or not (
                name == "hermes_feishu_card"
                or name.startswith("hermes_feishu_card.")
            )
        ):
            return False
        try:
            origin = _coerce_absolute_path(item["origin"])
            _validate_relative_path(item["cached"])
        except (TypeError, ValueError):
            return False
        if (
            origin.suffix != ".py"
            or not item["cached"].startswith("pycache/")
            or not item["cached"].endswith(".pyc")
        ):
            return False
        try:
            origin.relative_to(purelib / "hermes_feishu_card")
        except ValueError:
            return False
    return names == sorted(set(names)) and required <= set(names)


def _validate_installed_distribution(
    payload: dict[str, object], purelib: Path
) -> bool:
    try:
        metadata_path = _coerce_absolute_path(payload["distribution_metadata_path"])
        record_path = _coerce_absolute_path(payload["record_path"])
        package_origin = _coerce_absolute_path(payload["package_origin"])
        entrypoint_origin = _coerce_absolute_path(payload["entrypoint_origin"])
        if (
            metadata_path.parent != purelib
            or record_path != metadata_path / "RECORD"
            or package_origin != purelib / "hermes_feishu_card" / "__init__.py"
            or entrypoint_origin
            != purelib / "hermes_feishu_card" / "hermes_plugin.py"
        ):
            return False
        candidates = []
        with os.scandir(purelib) as entries:
            for entry in entries:
                if (
                    entry.name.endswith(".dist-info")
                    and entry.is_dir(follow_symlinks=False)
                ):
                    metadata_file = Path(entry.path) / "METADATA"
                    try:
                        metadata_bytes = _read_absolute_regular_file(
                            metadata_file, _MAX_MANIFEST_BYTES
                        )
                    except (OSError, ValueError):
                        continue
                    message = BytesParser().parsebytes(metadata_bytes)
                    if message.get_all("Name") == ["hermes-feishu-streaming-card"]:
                        candidates.append((Path(entry.path), message, metadata_bytes))
        if len(candidates) != 1 or candidates[0][0] != metadata_path:
            return False
        _, message, metadata_bytes = candidates[0]
        versions = message.get_all("Version")
        if (
            type(payload["distribution_version"]) is not str
            or versions != [payload["distribution_version"]]
            or not versions[0]
        ):
            return False
        entry_points_path = metadata_path / "entry_points.txt"
        entry_points_bytes = _read_absolute_regular_file(
            entry_points_path, _MAX_MANIFEST_BYTES
        )
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        parser.read_string(entry_points_bytes.decode("utf-8"))
        if (
            not parser.has_section("hermes_agent.plugins")
            or parser.items("hermes_agent.plugins")
            != [("hermes-feishu-card", "hermes_feishu_card.hermes_plugin")]
        ):
            return False
        record_bytes = _read_absolute_regular_file(record_path, _MAX_MANIFEST_BYTES)
        if _sha256(record_bytes) != payload["record_sha256"]:
            return False
        rows = list(csv.reader(io.StringIO(record_bytes.decode("utf-8"))))
        if not rows or any(len(row) != 3 for row in rows):
            return False
        records = {}
        for relative_path, digest, size in rows:
            if relative_path != "../../../bin/hermes-feishu-card":
                _validate_relative_path(relative_path)
            if relative_path in records:
                return False
            if size and re.fullmatch(r"0|[1-9][0-9]*", size) is None:
                return False
            records[relative_path] = (digest, size)
        metadata_relative = f"{metadata_path.name}/METADATA"
        entry_points_relative = f"{metadata_path.name}/entry_points.txt"
        record_relative = f"{metadata_path.name}/RECORD"
        critical = {
            "hermes_feishu_card/__init__.py": package_origin,
            "hermes_feishu_card/hermes_plugin.py": entrypoint_origin,
            metadata_relative: metadata_path / "METADATA",
            entry_points_relative: entry_points_path,
        }
        loaded_modules = payload.get("loaded_modules")
        if type(loaded_modules) is not list:
            return False
        for item in loaded_modules:
            if (
                type(item) is not dict
                or type(item.get("name")) is not str
                or type(item.get("origin")) is not str
                or not (
                    item["name"] == "hermes_feishu_card"
                    or item["name"].startswith("hermes_feishu_card.")
                )
            ):
                continue
            origin = _coerce_absolute_path(item["origin"])
            try:
                relative = origin.relative_to(purelib).as_posix()
            except ValueError:
                return False
            critical[relative] = origin
        if not set(critical) <= set(records) or record_relative not in records:
            return False
        if records[record_relative] != ("", ""):
            return False
        for relative_path, absolute_path in critical.items():
            data = _read_absolute_regular_file(absolute_path, _MAX_SOURCE_BYTES)
            digest, size = records[relative_path]
            expected_digest = "sha256=" + _urlsafe_sha256(data)
            if digest != expected_digest or size != str(len(data)):
                return False
        direct_url = metadata_path / "direct_url.json"
        try:
            direct_bytes = _read_absolute_regular_file(
                direct_url, _MAX_MANIFEST_BYTES
            )
        except FileNotFoundError:
            direct_bytes = b""
        if direct_bytes:
            direct = json.loads(direct_bytes.decode("utf-8"))
            if not _valid_distribution_direct_url(
                direct,
                payload["distribution_version"],
            ):
                return False
        return metadata_bytes == _read_absolute_regular_file(
            metadata_path / "METADATA", _MAX_MANIFEST_BYTES
        )
    except (
        OSError, UnicodeDecodeError, configparser.Error, csv.Error,
        json.JSONDecodeError, TypeError, ValueError,
    ):
        return False


def _valid_distribution_direct_url(
    direct: object,
    distribution_version: object,
) -> bool:
    if (
        type(direct) is not dict
        or type(distribution_version) is not str
        or _PACKAGE_VERSION_RE.fullmatch(distribution_version) is None
        or type(direct.get("url")) is not str
        or "dir_info" in direct
    ):
        return False
    if set(direct) == {"url", "archive_info"}:
        return (
            direct["url"].endswith(".whl")
            and type(direct["archive_info"]) is dict
        )
    if set(direct) != {"url", "vcs_info"}:
        return False
    vcs_info = direct["vcs_info"]
    return (
        direct["url"] == _OFFICIAL_HFC_VCS_URL
        and type(vcs_info) is dict
        and set(vcs_info) == {"vcs", "commit_id", "requested_revision"}
        and vcs_info["vcs"] == "git"
        and type(vcs_info["commit_id"]) is str
        and _COMMIT_RE.fullmatch(vcs_info["commit_id"]) is not None
        and vcs_info["requested_revision"] == f"v{distribution_version}"
    )


def _urlsafe_sha256(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _validate_runtime_python(value: str | Path) -> _RuntimeBinding:
    runtime = _coerce_absolute_path(value)
    if (
        type(sys.executable) is not str
        or runtime != _coerce_absolute_path(sys.executable)
        or Path(sys.prefix) != runtime.parent.parent
    ):
        raise ValueError("probe parent is not the bound runtime")
    parent_descriptor = _open_absolute_directory(runtime.parent)
    try:
        info = os.stat(runtime.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            raise ValueError("invalid runtime launcher")
    finally:
        os.close(parent_descriptor)
    resolved = Path(os.path.realpath(runtime))
    descriptor = _open_absolute_regular_file(resolved)
    info = os.fstat(descriptor)
    purelib = _coerce_absolute_path(sysconfig.get_path("purelib"))
    platlib = _coerce_absolute_path(sysconfig.get_path("platlib"))
    base_prefix = _coerce_absolute_path(sys.base_prefix)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o111 == 0
        or purelib != Path(sys.prefix) / "lib" / (
            f"python{sys.version_info.major}.{sys.version_info.minor}"
        ) / "site-packages"
        or platlib != purelib
    ):
        os.close(descriptor)
        raise ValueError("invalid runtime executable")
    return _RuntimeBinding(
        path=runtime,
        descriptor=descriptor,
        identity=(info.st_dev, info.st_ino),
        prefix=Path(sys.prefix),
        base_prefix=base_prefix,
        purelib=purelib,
        platlib=platlib,
    )


def _git_head(root: Path) -> str:
    try:
        result = _run_trusted_git(
            root, ["rev-parse", "--verify", "HEAD"], timeout=3
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if type(result.stdout) is not bytes:
        return ""
    try:
        value = result.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        return ""
    return value if _COMMIT_RE.fullmatch(value) else ""


def _git_source_state(root: Path) -> tuple[str, bool]:
    commit = _git_head(root)
    try:
        result = _run_trusted_git(
            root,
            ["status", "--porcelain=v1", "--untracked-files=all"],
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return commit, False
    return commit, type(result.stdout) is bytes and result.stdout == b""


def _run_trusted_git(
    root: Path,
    arguments: list[str],
    *,
    timeout: float,
    stdout: object = subprocess.PIPE,
) -> subprocess.CompletedProcess:
    if (
        type(arguments) is not list
        or not arguments
        or not all(type(item) is str and item for item in arguments)
    ):
        raise ValueError("invalid git arguments")
    git_descriptor = _open_absolute_regular_file(_TRUSTED_GIT)
    try:
        info = os.fstat(git_descriptor)
        if info.st_mode & 0o111 == 0:
            raise ValueError("trusted git is not executable")
    finally:
        os.close(git_descriptor)
    environment = {
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "HOME": os.devnull,
        "PATH": "/usr/bin:/bin",
    }
    return subprocess.run(
        [
            str(_TRUSTED_GIT),
            "--no-optional-locks",
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-C",
            str(root),
            *arguments,
        ],
        check=True,
        stdout=stdout,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        env=environment,
    )


def _build_trusted_source_snapshot(
    root: Path,
    expected_commit: str,
    provenance: FixedTagNativeHookProvenance,
) -> _SourceSnapshot:
    tree_result = _run_trusted_git(
        root,
        ["ls-tree", "-r", "-z", expected_commit],
        timeout=10,
    )
    expected_tree = _parse_git_tree(tree_result.stdout)
    container = Path(os.path.realpath(tempfile.mkdtemp(
        prefix="hfc-fixed-source-snapshot-"
    )))
    snapshot_root = container / "source"
    snapshot: _SourceSnapshot | None = None
    try:
        snapshot_root.mkdir(mode=0o700)
        descriptor = _open_absolute_directory(snapshot_root)
        info = os.fstat(descriptor)
        snapshot = _SourceSnapshot(
            root=snapshot_root,
            descriptor=descriptor,
            identity=(info.st_dev, info.st_ino),
            container=container,
        )
        archive_path = container / "source.tar"
        archive_descriptor = _open_new_regular_file(
            archive_path, 0o600, readable=True
        )
        try:
            _run_trusted_git(
                root,
                ["archive", "--format=tar", expected_commit],
                timeout=30,
                stdout=archive_descriptor,
            )
            os.lseek(archive_descriptor, 0, os.SEEK_SET)
            extracted = _extract_and_verify_git_archive(
                archive_descriptor, snapshot_root, expected_tree
            )
            if extracted != set(expected_tree):
                raise ValueError("git snapshot paths mismatch")
        finally:
            os.close(archive_descriptor)
        archive_path.unlink()
        _make_tree_read_only(snapshot_root)
        _verify_snapshot_provenance(snapshot, provenance)
        return snapshot
    except BaseException:
        if snapshot is not None:
            snapshot.close()
        else:
            shutil.rmtree(container, ignore_errors=True)
        raise


def _parse_git_tree(raw: object) -> dict[str, tuple[str, str]]:
    if type(raw) is not bytes or not raw or len(raw) > 16 * 1024 * 1024:
        raise ValueError("invalid git tree")
    result = {}
    for record in raw.rstrip(b"\0").split(b"\0"):
        try:
            metadata, path_raw = record.split(b"\t", 1)
            mode_raw, kind_raw, oid_raw = metadata.split(b" ", 2)
            path = path_raw.decode("utf-8")
            mode = mode_raw.decode("ascii")
            kind = kind_raw.decode("ascii")
            oid = oid_raw.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("invalid git tree") from exc
        _validate_relative_path(path)
        if (
            kind != "blob"
            or mode not in {"100644", "100755"}
            or _COMMIT_RE.fullmatch(oid) is None
            or path in result
        ):
            raise ValueError("unsupported git tree entry")
        result[path] = (mode, oid)
    return result


def _extract_and_verify_git_archive(
    archive_descriptor: int,
    snapshot_root: Path,
    expected_tree: dict[str, tuple[str, str]],
) -> set[str]:
    extracted = set()
    total = 0
    with os.fdopen(os.dup(archive_descriptor), "rb") as archive_file:
        with tarfile.open(fileobj=archive_file, mode="r:") as archive:
            for member in archive:
                path = member.name.rstrip("/")
                _validate_relative_path(path)
                if member.isdir():
                    _ensure_private_directory(snapshot_root, path)
                    continue
                if (
                    not member.isfile()
                    or path not in expected_tree
                    or path in extracted
                    or member.size < 0
                    or member.size > _MAX_ARCHIVE_MEMBER_BYTES
                ):
                    raise ValueError("invalid git archive member")
                total += member.size
                if total > _MAX_ARCHIVE_TOTAL_BYTES:
                    raise ValueError("git archive exceeds bound")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("missing git archive data")
                destination = snapshot_root / PurePosixPath(path)
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                data = source.read(_MAX_ARCHIVE_MEMBER_BYTES + 1)
                if len(data) != member.size:
                    raise ValueError("git archive member size mismatch")
                expected_mode, expected_oid = expected_tree[path]
                oid = hashlib.sha1(
                    f"blob {len(data)}\0".encode("ascii") + data
                ).hexdigest()
                if oid != expected_oid:
                    raise ValueError("git archive blob mismatch")
                descriptor = _open_new_regular_file(
                    destination, 0o500 if expected_mode == "100755" else 0o400
                )
                try:
                    _write_all(descriptor, data)
                finally:
                    os.close(descriptor)
                extracted.add(path)
    return extracted


def _open_new_regular_file(
    path: Path, mode: int, *, readable: bool = False
) -> int:
    parent = _open_absolute_directory(path.parent)
    try:
        flags = (os.O_RDWR if readable else os.O_WRONLY) | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return os.open(path.name, flags, mode, dir_fd=parent)
    finally:
        os.close(parent)


def _ensure_private_directory(root: Path, relative_path: str) -> None:
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise ValueError("invalid snapshot directory")


def _make_tree_read_only(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=False):
        for name in files:
            path = Path(current) / name
            descriptor = _open_absolute_regular_file(path)
            try:
                mode = os.fstat(descriptor).st_mode
                os.fchmod(descriptor, 0o500 if mode & 0o111 else 0o400)
            finally:
                os.close(descriptor)
        for name in directories:
            descriptor = _open_absolute_directory(Path(current) / name)
            try:
                os.fchmod(descriptor, 0o500)
            finally:
                os.close(descriptor)
    descriptor = _open_absolute_directory(root)
    try:
        os.fchmod(descriptor, 0o500)
    finally:
        os.close(descriptor)


def _verify_snapshot_provenance(
    snapshot: _SourceSnapshot,
    provenance: FixedTagNativeHookProvenance,
) -> None:
    for source in provenance.sources:
        data = _read_bound_relative_file(
            snapshot.descriptor, source.relative_path, _MAX_SOURCE_BYTES
        )
        if (
            _sha256(data) != source.sha256
            or not _anchors_match_source(data, source.anchors)
        ):
            raise ValueError("snapshot provenance mismatch")
    if not _bound_directory_unchanged(snapshot.root, snapshot.descriptor):
        raise ValueError("snapshot identity changed")


def _coerce_absolute_path(value: object) -> Path:
    if type(value) is str:
        raw = value
    elif isinstance(value, Path):
        raw = str(value)
    else:
        raise TypeError("path must be exact")
    if (
        not raw
        or "\x00" in raw
        or not os.path.isabs(raw)
        or raw != os.path.normpath(raw)
    ):
        raise ValueError("path must be canonical absolute")
    return Path(raw)


def _directory_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _open_absolute_directory(path: Path) -> int:
    path = _coerce_absolute_path(path)
    current = os.open(os.sep, _directory_flags())
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(
                part, _directory_flags(), dir_fd=current
            )
            info = os.fstat(next_descriptor)
            if not stat.S_ISDIR(info.st_mode):
                os.close(next_descriptor)
                raise ValueError("path parent is not a directory")
            os.close(current)
            current = next_descriptor
        return current
    except BaseException:
        os.close(current)
        raise


def _bound_directory_unchanged(path: Path, descriptor: int) -> bool:
    try:
        reopened = _open_absolute_directory(path)
    except (OSError, TypeError, ValueError):
        return False
    try:
        original = os.fstat(descriptor)
        current = os.fstat(reopened)
        return (original.st_dev, original.st_ino) == (current.st_dev, current.st_ino)
    finally:
        os.close(reopened)


def _open_absolute_regular_file(path: Path) -> int:
    path = _coerce_absolute_path(path)
    parent = _open_absolute_directory(path.parent)
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path.name, flags, dir_fd=parent)
    finally:
        os.close(parent)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise ValueError("path leaf is not regular")
    return descriptor


def _open_bound_relative_directory(
    root_descriptor: int, relative_path: str
) -> int:
    _validate_relative_path(relative_path)
    if type(root_descriptor) is not int:
        raise ValueError("invalid descriptor root")
    current = os.dup(root_descriptor)
    try:
        for part in PurePosixPath(relative_path).parts:
            next_descriptor = os.open(
                part, _directory_flags(), dir_fd=current
            )
            info = os.fstat(next_descriptor)
            if not stat.S_ISDIR(info.st_mode):
                os.close(next_descriptor)
                raise ValueError("relative path is not a directory")
            os.close(current)
            current = next_descriptor
        return current
    except BaseException:
        os.close(current)
        raise


def _read_bound_relative_file(
    root_descriptor: int, relative_path: str, limit: int
) -> bytes:
    _validate_relative_path(relative_path)
    if type(root_descriptor) is not int or type(limit) is not int or limit < 0:
        raise ValueError("invalid bounded read")
    parts = PurePosixPath(relative_path).parts
    current = os.dup(root_descriptor)
    parent_identities = []
    try:
        for part in parts[:-1]:
            next_descriptor = os.open(
                part, _directory_flags(), dir_fd=current
            )
            info = os.fstat(next_descriptor)
            if not stat.S_ISDIR(info.st_mode):
                os.close(next_descriptor)
                raise ValueError("invalid source parent")
            parent_identities.append((info.st_dev, info.st_ino))
            os.close(current)
            current = next_descriptor
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        leaf = os.open(parts[-1], flags, dir_fd=current)
        try:
            leaf_info = os.fstat(leaf)
            data = _read_regular_descriptor(leaf, limit, require_single_link=True)
        finally:
            os.close(leaf)
    finally:
        os.close(current)
    if not _revalidate_relative_identity(
        root_descriptor, parts, parent_identities, leaf_info
    ):
        raise ValueError("source identity changed")
    return data


def _revalidate_relative_identity(
    root_descriptor: int,
    parts: tuple[str, ...],
    parent_identities: list[tuple[int, int]],
    leaf_info: os.stat_result,
) -> bool:
    current = os.dup(root_descriptor)
    try:
        for index, part in enumerate(parts[:-1]):
            next_descriptor = os.open(
                part, _directory_flags(), dir_fd=current
            )
            info = os.fstat(next_descriptor)
            os.close(current)
            current = next_descriptor
            if (info.st_dev, info.st_ino) != parent_identities[index]:
                return False
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        leaf = os.open(parts[-1], flags, dir_fd=current)
        try:
            info = os.fstat(leaf)
            return (
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and (info.st_dev, info.st_ino)
                == (leaf_info.st_dev, leaf_info.st_ino)
            )
        finally:
            os.close(leaf)
    except (OSError, ValueError):
        return False
    finally:
        os.close(current)


def _anchors_match_source(
    data: bytes,
    anchors: tuple[NativeHookAnchorProvenance, ...],
) -> bool:
    try:
        lines = data.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return False
    for anchor in anchors:
        if anchor.line_end > len(lines):
            return False
        extracted = "".join(
            lines[anchor.line_start - 1:anchor.line_end]
        ).encode("utf-8")
        if _sha256(extracted) != anchor.slice_sha256:
            return False
    return True


def _read_absolute_regular_file(path: Path, limit: int) -> bytes:
    descriptor = _open_absolute_regular_file(path)
    try:
        return _read_regular_descriptor(
            descriptor, limit, require_single_link=True
        )
    finally:
        os.close(descriptor)


def _read_regular_descriptor(
    descriptor: int,
    limit: int,
    *,
    require_single_link: bool,
) -> bytes:
    if type(descriptor) is not int or type(limit) is not int or limit < 0:
        raise ValueError("invalid bounded read")
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size > limit
        or require_single_link and info.st_nlink != 1
    ):
        raise ValueError("source must be a bounded regular file")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(65536, limit + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError("source exceeds bound")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_relative_path(value: str) -> None:
    if type(value) is not str or not value:
        raise ValueError("invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("invalid relative path")
    if str(path) != value or "\\" in value:
        raise ValueError("invalid relative path")


def _validate_digest(value: str) -> None:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError("invalid sha256 digest")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
