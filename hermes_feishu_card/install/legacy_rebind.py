"""Explicit, fail-closed PM observer rebinding; no source or config mutation.

The old v1 record remains readable. New records use the same schema, pointing at
new preimage/journal files; the original evidence and marker archive are retained.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import replace
from pathlib import Path
import secrets
import tempfile
from typing import Any

from . import decomposed_ownership, legacy_observer, plugin, plugin_config_delta, pm_dependency
from . import posix_transaction as io
from .recovery import _root_lock

yaml = importlib.import_module("yaml")


class RebindRefused(ValueError):
    pass


def _plugins_span(raw: bytes) -> tuple[int, int] | None:
    """Accept simple, unique-key YAML only; offsets retain all unrelated bytes."""
    try:
        text = raw.decode("utf-8")
        if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken,
                                  yaml.tokens.TagToken)) for token in yaml.scan(text)):
            raise RebindRefused("legacy_rebind_yaml_ambiguous")
        root = yaml.compose(text)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise RebindRefused("legacy_rebind_yaml_invalid") from exc
    def check(node: Any) -> None:
        if isinstance(node, yaml.MappingNode):
            keys = []
            for key, value in node.value:
                if not isinstance(key, yaml.ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                    raise RebindRefused("legacy_rebind_yaml_ambiguous")
                if key.value in keys or key.value == "<<":
                    raise RebindRefused("legacy_rebind_yaml_duplicate")
                keys.append(key.value)
                check(value)
        elif isinstance(node, yaml.SequenceNode):
            for value in node.value:
                check(value)
    if not isinstance(root, yaml.MappingNode) or root.flow_style:
        raise RebindRefused("legacy_rebind_yaml_ambiguous")
    check(root)
    for index, (key, _) in enumerate(root.value):
        if key.value == "plugins":
            if key.start_mark.column != 0:
                raise RebindRefused("legacy_rebind_yaml_ambiguous")
            start = key.start_mark.index
            end = root.value[index + 1][0].start_mark.index if index + 1 < len(root.value) else len(text)
            return len(text[:start].encode()), len(text[:end].encode())
    return None


def _rebased_preimage(before: bytes, post: bytes, current: bytes) -> bytes:
    old_span, post_span, current_span = map(_plugins_span, (before, post, current))
    if post_span is None or current_span is None:
        raise RebindRefused("legacy_rebind_plugin_branch_changed")
    if post[slice(*post_span)] != current[slice(*current_span)]:
        raise RebindRefused("legacy_rebind_plugin_branch_changed")
    replacement = before[slice(*old_span)] if old_span is not None else b""
    result = current[:current_span[0]] + replacement + current[current_span[1]:]
    expected = plugin._load_config_mapping(current)
    original = plugin._load_config_mapping(before)
    if "plugins" in original:
        expected["plugins"] = original["plugins"]
    else:
        expected.pop("plugins", None)
    if plugin._load_config_mapping(result) != expected:
        raise RebindRefused("legacy_rebind_config_merge_ambiguous")
    return result


def _verify_runtime(binding: plugin.HermesRuntimeBinding) -> None:
    member = binding.hermes_home / "plugins" / pm_dependency.NAME
    # _owned validates the member's source ownership, not just its version.
    try:
        owner = pm_dependency._owned(member)
    except pm_dependency.PMDependencySourceUnverified as exc:
        raise RebindRefused("legacy_rebind_schema1_source_unverified") from exc
    except ValueError as exc:
        raise RebindRefused("legacy_rebind_pm_owner_unverified") from exc
    if owner is None:
        raise RebindRefused("legacy_rebind_pm_owner_required")
    if not pm_dependency.verify_installed(member, binding.runtime_python):
        raise RebindRefused("legacy_rebind_wheel_resources_unverified")
    version = owner.get("version")
    if type(version) is not str or not version:
        raise RebindRefused("legacy_rebind_pm_owner_required")
    if plugin.probe_plugin_entrypoint(
        binding, expected_version=version
    ).status != "verified":
        raise RebindRefused("legacy_rebind_entrypoint_unverified")
    if pm_dependency._owned(member) != owner:
        raise RebindRefused("legacy_rebind_pm_owner_changed")


def _source_fingerprint(root: Path) -> str:
    if not decomposed_ownership.is_managed(root):
        raise RebindRefused("legacy_rebind_decomposed_install_required")
    snapshot, _, _, state = decomposed_ownership._inspect(root)
    if state != "installed":
        raise RebindRefused("legacy_rebind_source_not_installed")
    return str(decomposed_ownership._fingerprint(snapshot))


def rebind(binding: plugin.HermesRuntimeBinding, *, apply: bool = False) -> dict[str, str]:
    """Preview or replace only the observer ownership record under recovery lock."""
    if not io._cli_dirfd_binding_supported():
        raise RebindRefused("legacy_rebind_secure_platform_required")
    root, home = binding.checkout_root, binding.hermes_home
    with _root_lock(root):
        evidence = legacy_observer.inspect(root, home=home, allow_config_drift=True)
        if evidence is None:
            raise RebindRefused("legacy_rebind_marker_required")
        record, marker_raw, old_preimage, old_owner, current = evidence
        source = _source_fingerprint(root)
        _verify_runtime(binding)
        before = plugin._read_private_backup(old_preimage)
        receipt = record.get("config_delta")
        plan = None
        if receipt is None:
            desired, enabled = plugin._expected_enabled_config(plugin._load_config_mapping(before))
            if enabled:
                desired, _ = plugin._expected_enabled_config(plugin._load_config_mapping(before), preserve_enabled_lists=True)
            with tempfile.TemporaryDirectory(prefix="hfc-rebind-proof-") as scratch:
                shadow = replace(old_preimage, state_dir=Path(scratch))
                plan = plugin._plan_enabled_config_bytes(
                    binding, shadow, before, desired, enabled,
                    historical_post_sha256=old_owner.post_sha256,
                )
            if plugin._sha256(plan.postimage) != old_owner.post_sha256 or not plan.matches_expected(desired):
                raise RebindRefused("legacy_rebind_original_postimage_unverified")
            receipt = plugin_config_delta.make_receipt(before, plan.postimage)
        if plugin_config_delta.classify(before, current, receipt) != "installed":
            raise RebindRefused("legacy_rebind_restore_incomplete")
        restored = (before if plugin._sha256(current) == old_owner.post_sha256
                    else plugin_config_delta.restore_bytes(before, current, receipt))
        new_receipt = plugin_config_delta.make_receipt(restored, current)
        # Both postimage and preimage are current-config based; no unrelated field
        # can later be removed by the ordinary exact restore path.
        key = "hfc-config-preimage-" + secrets.token_hex(16)
        pre_hash = plugin._sha256(restored)
        owner = plugin.PluginOwnership(old_owner.enabled_before, old_owner.added_by_hfc,
            pre_hash, plugin._sha256(current), key, pre_hash)
        state = old_preimage.state_dir
        marker = root / legacy_observer.MARKER
        changes = [
            (state / (key + ".yaml"), restored.decode("utf-8")),
            (state / (key + ".json"), json.dumps({"protocol": "hfc-plugin-config-preimage-v1",
                "phase": "installed", **owner.sanitized(), "config_delta": new_receipt,
                **({"adapter": record["adapter"]} if "adapter" in record else {})}, sort_keys=True) + "\n"),
            (state / (key + ".previous-observer.json"), marker_raw.decode("utf-8")),
            (marker, json.dumps({**record, "python_identity": binding.python_identity,
                "plugin_config": owner.sanitized(), "config_delta": new_receipt}, sort_keys=True) + "\n"),
        ]
        watched = [marker, binding.config_path, old_preimage.backup_path, old_preimage.journal_path]
        snapshots = {p: io.read_path_snapshot(p, RebindRefused, "legacy_rebind_evidence_changed") for p in watched}
        expected_hashes = {marker: plugin._sha256(marker_raw), binding.config_path: plugin._sha256(current),
            old_preimage.backup_path: old_owner.backup_sha256}
        expected_journal = {"protocol": "hfc-plugin-config-preimage-v1", "phase": "installed", **old_owner.sanitized()}
        if "config_delta" in record:
            expected_journal["config_delta"] = record["config_delta"]
        if "adapter" in record:
            expected_journal["adapter"] = record["adapter"]
        if any(snapshots[p][2] != digest for p, digest in expected_hashes.items()):
            raise RebindRefused("legacy_rebind_evidence_changed")
        if json.loads(legacy_observer._read(old_preimage.journal_path) or b"null") != expected_journal:
            raise RebindRefused("legacy_rebind_evidence_changed")
        directories: dict[Path, tuple[int, int] | None] = {}
        for base, targets in ((root, [marker]), (home, [p for p in watched if p != marker] + [p for p, _ in changes if p != marker])):
            _, found = io.snapshot_controlled_ancestries(base, targets, RebindRefused,
                outside_refusal="legacy_rebind_scope_changed", unsafe_refusal="legacy_rebind_directory_unsafe")
            directories.update(found)
        def validate() -> None:
            for p, snapshot in snapshots.items():
                io.assert_path_snapshot(p, snapshot, RebindRefused, "legacy_rebind_evidence_changed")
            for p, identity in directories.items():
                if io.read_directory_identity(p, RebindRefused, "legacy_rebind_directory_changed") != identity:
                    raise RebindRefused("legacy_rebind_directory_changed")
            fresh = plugin.resolve_runtime_binding(checkout_root=root, hermes_home=home, profile_id=None)
            if fresh != binding or _source_fingerprint(root) != source:
                raise RebindRefused("legacy_rebind_binding_changed")
            _verify_runtime(binding)
            if plan is not None:
                plan.verify_source()
        validate()
        for p, snapshot in snapshots.items():
            io.assert_path_snapshot(p, snapshot, RebindRefused, "legacy_rebind_evidence_changed")
        if record["python_identity"] == binding.python_identity and plugin._sha256(current) == old_owner.post_sha256:
            return {"status": "unchanged", "reason": "verified_binding_already_current"}
        if not apply:
            return {"status": "ready", "reason": "verified_pm_rebind_available"}
        expected = {p: snapshots[p] if p in snapshots else None for p, _ in changes}
        def after() -> None:
            for p in watched[1:]:
                io.assert_path_snapshot(p, snapshots[p], RebindRefused, "legacy_rebind_evidence_changed")
            if _source_fingerprint(root) != source:
                raise RebindRefused("legacy_rebind_source_changed")
            if plugin.resolve_runtime_binding(checkout_root=root, hermes_home=home, profile_id=None) != binding:
                raise RebindRefused("legacy_rebind_binding_changed")
            _verify_runtime(binding)
            if plan is not None:
                plan.verify_source()
            legacy_observer.inspect(root, home=home)
            for p, contents in changes:
                if io.read_path_snapshot(p, RebindRefused, "legacy_rebind_published_evidence_changed")[2] != plugin._sha256(contents.encode("utf-8")):
                    raise RebindRefused("legacy_rebind_published_evidence_changed")
            for p in watched[1:]:
                io.assert_path_snapshot(p, snapshots[p], RebindRefused, "legacy_rebind_evidence_changed")
        io._write_targets_transactionally(changes, expected_identities=expected,
            expected_directories=directories, pre_commit_validate=validate, validate=after)
        return {"status": "rebound", "reason": "verified_pm_rebind_complete"}
