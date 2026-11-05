from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import subprocess
from types import MappingProxyType
from typing import Mapping

from ..integration import (
    HYBRID_REQUIRED_NATIVE_CAPABILITIES,
    HYBRID_REQUIRED_PATCH_GROUPS,
    IntegrationDecision,
    IntegrationMode,
)
from .native_hooks import (
    FIXED_TAG_COMMIT,
    FIXED_TAG_PROVENANCE_PATH,
    load_fixed_tag_native_hook_provenance,
)
from . import plugin as plugin_install
from .manifest import render_install_manifest_v3, validate_install_manifest
from .patcher import (
    HYBRID_PATCH_REGISTRY,
    HYBRID_PATCH_TARGET_ORDER,
    detect_patch_groups_by_target,
    remove_patch_snapshots,
    render_patch_snapshots_from_verified_originals,
)


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_BOUNDED_REFUSAL_MAX_LENGTH = 700
_BOUNDED_REFUSAL_PREFIXES = (
    f"{plugin_install.OFFICIAL_ENABLE_VISIBILITY_PROBE_FAILED} (",
    "official plugin enable failed (",
)


class FixedTagInstallRefused(ValueError):
    pass


def _bounded_install_refusal(reason: str) -> bool:
    return (
        reason.endswith(")")
        and len(reason) <= _BOUNDED_REFUSAL_MAX_LENGTH
        and all(character.isprintable() for character in reason)
        and any(reason.startswith(prefix) for prefix in _BOUNDED_REFUSAL_PREFIXES)
    )


@dataclass(frozen=True, repr=False)
class FixedTagHybridPlan:
    originals: Mapping[str, bytes]
    rendered: Mapping[str, bytes]
    verified_original_sha256: Mapping[str, str]
    patch_groups: frozenset[str]
    patch_targets: Mapping[str, frozenset[str]]
    expected_fragment_matrix: Mapping[str, tuple[tuple[str, str], ...]]
    capability_fingerprint: str

    def restore(self, snapshots: Mapping[str, bytes]) -> dict[str, bytes]:
        try:
            restored = remove_patch_snapshots(
                snapshots,
                expected_groups=self.patch_groups,
                expected_fragment_matrix=self.expected_fragment_matrix,
            )
        except Exception as exc:
            raise FixedTagInstallRefused("installed Hybrid patch is not exact") from exc
        if restored != dict(self.originals):
            raise FixedTagInstallRefused("Hybrid restore did not recover verified originals")
        return restored


@dataclass(frozen=True, repr=False)
class FixedTagInstallResult:
    status: str
    manifest_path: Path
    gateway_restart_required: bool


@dataclass(frozen=True, repr=False)
class _FileSnapshot:
    device: int
    inode: int
    sha256: str
    mode: int
    contents: bytes


def is_fixed_tag_checkout(checkout_root: str | Path) -> bool:
    root = Path(checkout_root).expanduser().absolute()
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=str(root),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == FIXED_TAG_COMMIT


def build_fixed_tag_hybrid_plan(
    checkout_root: str | Path,
    *,
    decision: IntegrationDecision,
    source_commit: str,
    plugin_evidence_sha256: str,
) -> FixedTagHybridPlan:
    if (
        type(decision) is not IntegrationDecision
        or decision.supported is not True
        or decision.mode is not IntegrationMode.HYBRID
        or decision.required_native_capabilities
        != HYBRID_REQUIRED_NATIVE_CAPABILITIES
        or decision.required_patch_groups != HYBRID_REQUIRED_PATCH_GROUPS
        or type(decision.fingerprint) is not str
        or _DIGEST_RE.fullmatch(decision.fingerprint) is None
        or source_commit != FIXED_TAG_COMMIT
        or type(plugin_evidence_sha256) is not str
        or _DIGEST_RE.fullmatch(plugin_evidence_sha256) is None
    ):
        raise FixedTagInstallRefused("fixed-tag probe evidence is incomplete")
    root = Path(checkout_root).expanduser().absolute()
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise FixedTagInstallRefused("fixed-tag checkout is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise FixedTagInstallRefused("fixed-tag checkout must be a regular directory")
    try:
        provenance = load_fixed_tag_native_hook_provenance(
            FIXED_TAG_PROVENANCE_PATH
        )
    except (OSError, TypeError, ValueError) as exc:
        raise FixedTagInstallRefused("fixed-tag provenance is unavailable") from exc
    canonical_sha256 = {
        source.relative_path: source.sha256.removeprefix("sha256:")
        for source in provenance.sources
        if source.relative_path in HYBRID_PATCH_TARGET_ORDER
    }
    if set(canonical_sha256) != set(HYBRID_PATCH_TARGET_ORDER):
        raise FixedTagInstallRefused("fixed-tag provenance target set is incomplete")

    originals: dict[str, bytes] = {}
    for target in HYBRID_PATCH_TARGET_ORDER:
        path = root / target
        try:
            target_metadata = path.lstat()
            if (
                stat.S_ISLNK(target_metadata.st_mode)
                or not stat.S_ISREG(target_metadata.st_mode)
                or target_metadata.st_size > _MAX_SOURCE_BYTES
            ):
                raise FixedTagInstallRefused("fixed-tag source is not regular")
            content = path.read_bytes()
        except OSError as exc:
            raise FixedTagInstallRefused("fixed-tag source is unavailable") from exc
        digest = hashlib.sha256(content).hexdigest()
        if digest != canonical_sha256[target]:
            raise FixedTagInstallRefused(
                f"fixed-tag source digest mismatch for {target}"
            )
        originals[target] = content

    expected_matrix = HYBRID_PATCH_REGISTRY.target_fragments(
        decision.required_patch_groups
    )
    try:
        rendered = render_patch_snapshots_from_verified_originals(
            originals,
            verified_original_sha256=canonical_sha256,
            integration_mode="hybrid",
            required_patch_groups=decision.required_patch_groups,
            expected_fragment_matrix=expected_matrix,
        )
        detected = detect_patch_groups_by_target(
            rendered,
            expected_groups=decision.required_patch_groups,
            expected_fragment_matrix=expected_matrix,
        )
        for target, content in rendered.items():
            compile(content, target, "exec")
        restored = remove_patch_snapshots(
            rendered,
            expected_groups=decision.required_patch_groups,
            expected_fragment_matrix=expected_matrix,
        )
    except Exception as exc:
        raise FixedTagInstallRefused("fixed-tag aggregate render verification failed") from exc
    expected_targets = HYBRID_PATCH_REGISTRY.target_groups(
        decision.required_patch_groups
    )
    if detected != expected_targets or restored != originals:
        raise FixedTagInstallRefused("fixed-tag aggregate patch did not converge")
    return FixedTagHybridPlan(
        originals=MappingProxyType(dict(originals)),
        rendered=MappingProxyType(dict(rendered)),
        verified_original_sha256=MappingProxyType(dict(canonical_sha256)),
        patch_groups=decision.required_patch_groups,
        patch_targets=MappingProxyType(dict(expected_targets)),
        expected_fragment_matrix=MappingProxyType(dict(expected_matrix)),
        capability_fingerprint=decision.fingerprint,
    )


def execute_fixed_tag_hybrid_install(
    *,
    binding: plugin_install.HermesRuntimeBinding,
    entrypoint: plugin_install.PluginEntrypointProbe,
    decision: IntegrationDecision,
    source_commit: str,
    plugin_evidence_sha256: str,
    package_version: str,
) -> FixedTagInstallResult:
    if (
        type(binding) is not plugin_install.HermesRuntimeBinding
        or type(entrypoint) is not plugin_install.PluginEntrypointProbe
        or entrypoint.status != "verified"
        or entrypoint.reason != "verified"
        or entrypoint.version != package_version
        or type(package_version) is not str
        or not package_version
    ):
        raise FixedTagInstallRefused("fixed-tag plugin entry point is not verified")
    root = binding.checkout_root
    plan = build_fixed_tag_hybrid_plan(
        root,
        decision=decision,
        source_commit=source_commit,
        plugin_evidence_sha256=plugin_evidence_sha256,
    )
    manifest_path = root / ".hermes_feishu_card_manifest"
    backup_paths = {
        target: root / (target + ".hermes_feishu_card.bak")
        for target in HYBRID_PATCH_TARGET_ORDER
    }
    evidence_paths = (manifest_path, *backup_paths.values())
    if any(_snapshot(path) is not None for path in evidence_paths):
        raise FixedTagInstallRefused(
            "fixed-tag install evidence already exists; migration or recovery is required"
        )
    source_paths = {target: root / target for target in HYBRID_PATCH_TARGET_ORDER}
    source_snapshots = {
        path: _require_snapshot(path, "fixed-tag source changed before install")
        for path in source_paths.values()
    }
    for target, path in source_paths.items():
        if source_snapshots[path].contents != plan.originals[target]:
            raise FixedTagInstallRefused("fixed-tag source changed before install")

    try:
        preimage = plugin_install.prepare_plugin_config(binding)
    except Exception as exc:
        raise FixedTagInstallRefused("plugin config preimage failed") from exc
    prepared_config = {
        "enabled_before": preimage.enabled_before,
        "added_by_hfc": False,
        "pre_sha256": preimage.pre_sha256,
        "post_sha256": preimage.pre_sha256,
        "config_backup_id": preimage.config_backup_id,
        "backup_sha256": preimage.backup_sha256,
    }
    prepared_manifest = _render_phase_manifest(
        plan,
        phase="prepared",
        target_contents={
            target: (plan.originals[target], plan.originals[target])
            for target in HYBRID_PATCH_TARGET_ORDER
        },
        binding=binding,
        package_version=package_version,
        plugin_config=prepared_config,
    )
    evidence_changes = {
        **{
            backup_paths[target]: plan.originals[target]
            for target in HYBRID_PATCH_TARGET_ORDER
        },
        manifest_path: prepared_manifest.encode("utf-8"),
    }
    try:
        evidence_after = _commit_file_set(
            evidence_changes,
            expected={path: None for path in evidence_changes},
        )
    except Exception as exc:
        raise FixedTagInstallRefused("prepared install evidence commit failed") from exc

    try:
        ownership = plugin_install.enable_plugin(binding, preimage)
    except plugin_install.PluginConfigRefused as exc:
        # These fixed reasons distinguish a config ownership refusal from an
        # official command failure. Only bounded diagnostics may surface;
        # never expose arbitrary exception content.
        reason = str(exc)
        if not _bounded_install_refusal(reason) and reason not in {
            "Hermes config changed after preimage",
            "official plugin enable changed unrelated config",
            plugin_install.OFFICIAL_ENABLE_VISIBILITY_PROBE_FAILED,
        }:
            reason = "official plugin enable failed"
        raise FixedTagInstallRefused(reason) from exc
    except Exception as exc:
        raise FixedTagInstallRefused("official plugin enable failed") from exc
    plugin_enabled_manifest = _render_phase_manifest(
        plan,
        phase="plugin_enabled",
        target_contents={
            target: (plan.originals[target], plan.originals[target])
            for target in HYBRID_PATCH_TARGET_ORDER
        },
        binding=binding,
        package_version=package_version,
        plugin_config=ownership.sanitized(),
    )
    try:
        plugin_manifest_after = _commit_file_set(
            {manifest_path: plugin_enabled_manifest.encode("utf-8")},
            expected={manifest_path: evidence_after[manifest_path]},
        )
    except Exception as exc:
        _restore_config_after_failure(binding, preimage, ownership)
        raise FixedTagInstallRefused("plugin-enabled manifest commit failed") from exc

    installed_manifest = _render_phase_manifest(
        plan,
        phase="installed",
        target_contents={
            target: (plan.rendered[target], plan.originals[target])
            for target in HYBRID_PATCH_TARGET_ORDER
        },
        binding=binding,
        package_version=package_version,
        plugin_config=ownership.sanitized(),
    )
    final_changes = {
        **{
            source_paths[target]: plan.rendered[target]
            for target in HYBRID_PATCH_TARGET_ORDER
        },
        manifest_path: installed_manifest.encode("utf-8"),
    }
    final_expected = {
        **source_snapshots,
        manifest_path: plugin_manifest_after[manifest_path],
    }
    try:
        final_after = _commit_file_set(final_changes, expected=final_expected)
    except Exception as exc:
        _restore_config_after_failure(binding, preimage, ownership)
        _restore_prepared_manifest(
            manifest_path,
            prepared_manifest,
            expected=plugin_manifest_after[manifest_path],
        )
        raise FixedTagInstallRefused("fixed-tag source commit failed") from exc

    try:
        installed = {
            target: _require_snapshot(
                source_paths[target], "installed source verification failed"
            ).contents
            for target in HYBRID_PATCH_TARGET_ORDER
        }
        detected = detect_patch_groups_by_target(
            installed,
            expected_groups=plan.patch_groups,
            expected_fragment_matrix=plan.expected_fragment_matrix,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_install_manifest(manifest)
        config_sha256 = hashlib.sha256(binding.config_path.read_bytes()).hexdigest()
        if (
            detected != dict(plan.patch_targets)
            or config_sha256 != ownership.post_sha256
            or _require_snapshot(
                manifest_path, "installed manifest verification failed"
            )
            != final_after[manifest_path]
        ):
            raise FixedTagInstallRefused("installed verification did not converge")
        plugin_install.mark_plugin_config_installed(preimage, ownership)
    except Exception as exc:
        rollback_changes = {
            **{
                source_paths[target]: plan.originals[target]
                for target in HYBRID_PATCH_TARGET_ORDER
            },
            manifest_path: prepared_manifest.encode("utf-8"),
        }
        try:
            _commit_file_set(rollback_changes, expected=final_after)
            _restore_config_after_failure(binding, preimage, ownership)
        except Exception as rollback_exc:
            raise FixedTagInstallRefused(
                "installed verification failed and rollback requires manual review"
            ) from rollback_exc
        raise FixedTagInstallRefused("installed verification did not converge") from exc
    return FixedTagInstallResult(
        status="installed",
        manifest_path=manifest_path,
        gateway_restart_required=True,
    )


def inspect_fixed_tag_hybrid_install(
    *,
    binding: plugin_install.HermesRuntimeBinding,
    entrypoint: plugin_install.PluginEntrypointProbe,
    package_version: str,
) -> FixedTagInstallResult:
    if (
        type(binding) is not plugin_install.HermesRuntimeBinding
        or type(entrypoint) is not plugin_install.PluginEntrypointProbe
        or entrypoint.status != "verified"
        or entrypoint.reason != "verified"
        or entrypoint.version != package_version
    ):
        raise FixedTagInstallRefused("fixed-tag plugin entry point is not verified")
    root = binding.checkout_root
    manifest_path = root / ".hermes_feishu_card_manifest"
    snapshot = _require_snapshot(manifest_path, "V3 install manifest is missing")
    if len(snapshot.contents) > 1024 * 1024:
        raise FixedTagInstallRefused("V3 install manifest is too large")
    try:
        manifest = json.loads(snapshot.contents.decode("utf-8"))
        validate_install_manifest(manifest)
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise FixedTagInstallRefused("V3 install manifest is invalid") from exc
    if manifest.get("manifest_version") != 3 or manifest.get("phase") != "installed":
        raise FixedTagInstallRefused("V3 install requires recovery before reuse")
    integration = manifest["integration"]
    plugin = integration["plugin"]
    if (
        integration["mode"] != "hybrid"
        or plugin["version"] != package_version
        or plugin["python_identity"] != binding.python_identity
    ):
        raise FixedTagInstallRefused("V3 install binding or mode changed")
    config_snapshot = _require_snapshot(
        binding.config_path, "Hermes config is missing"
    )
    if config_snapshot.sha256 != integration["plugin_config"]["post_sha256"]:
        raise FixedTagInstallRefused("Hermes plugin config changed since install")

    installed: dict[str, bytes] = {}
    backups: dict[str, bytes] = {}
    for target in HYBRID_PATCH_TARGET_ORDER:
        target_ownership = manifest["targets"][target]
        current_path = root / target_ownership["path"]
        backup_path = root / target_ownership["backup"]
        current = _require_snapshot(current_path, "installed target is missing")
        backup = _require_snapshot(backup_path, "installed backup is missing")
        if current.sha256 != target_ownership["patched_sha256"]:
            raise FixedTagInstallRefused("installed target hash changed")
        if backup.sha256 != target_ownership["backup_sha256"]:
            raise FixedTagInstallRefused("installed backup hash changed")
        installed[target] = current.contents
        backups[target] = backup.contents
    patch_groups = frozenset(integration["patch_groups"])
    registry = _installed_patch_registry(installed, backups, patch_groups)
    expected_matrix = registry.target_fragments(patch_groups)
    try:
        detected = detect_patch_groups_by_target(
            installed,
            expected_groups=patch_groups,
            expected_fragment_matrix=expected_matrix,
            registry=registry,
        )
        restored = remove_patch_snapshots(
            installed,
            expected_groups=patch_groups,
            expected_fragment_matrix=expected_matrix,
            registry=registry,
        )
    except Exception as exc:
        raise FixedTagInstallRefused("installed Hybrid patch is invalid") from exc
    expected_targets = {
        target: frozenset(groups)
        for target, groups in integration["patch_targets"].items()
    }
    if detected != expected_targets or restored != backups:
        raise FixedTagInstallRefused("installed Hybrid patch ownership is inconsistent")
    if registry is not HYBRID_PATCH_REGISTRY:
        raise FixedTagInstallRefused(
            "verified historical Hybrid revision requires official restore --yes "
            "with the same --hermes-dir and --hermes-home, then install"
        )
    return FixedTagInstallResult(
        status="installed",
        manifest_path=manifest_path,
        gateway_restart_required=False,
    )


def _installed_patch_registry(installed, backups, patch_groups):
    """Select only an exact known rendering after callers verify manifest hashes."""
    from .hybrid_renderers import (
        historical_clarify_registry, historical_publish_registry, historical_terminal_registry,
        historical_clarify_wait_registry,
    )

    provenance = load_fixed_tag_native_hook_provenance(FIXED_TAG_PROVENANCE_PATH)
    canonical = {
        source.relative_path: source.sha256.removeprefix("sha256:")
        for source in provenance.sources
        if source.relative_path in HYBRID_PATCH_TARGET_ORDER
    }
    # Only complete revisions that have actually shipped as candidates are
    # eligible. Never synthesize arbitrary mixtures of historical fragments.
    pre_metrics = historical_clarify_registry(
        historical_publish_registry(HYBRID_PATCH_REGISTRY)
    )
    for registry in (
        HYBRID_PATCH_REGISTRY,
        historical_clarify_wait_registry(HYBRID_PATCH_REGISTRY),
        pre_metrics,
        historical_terminal_registry(pre_metrics),
    ):
        matrix = registry.target_fragments(patch_groups)
        try:
            rendered = render_patch_snapshots_from_verified_originals(
                backups, verified_original_sha256=canonical,
                integration_mode="hybrid", required_patch_groups=patch_groups,
                expected_fragment_matrix=matrix, registry=registry,
            )
        except (TypeError, ValueError):
            continue
        if rendered == installed:
            return registry
    raise FixedTagInstallRefused("installed Hybrid patch is not an exact known revision")


def restore_fixed_tag_hybrid_install(
    *,
    binding: plugin_install.HermesRuntimeBinding,
) -> FixedTagInstallResult:
    if type(binding) is not plugin_install.HermesRuntimeBinding:
        raise FixedTagInstallRefused("fixed-tag runtime binding is invalid")
    root = binding.checkout_root
    manifest_path = root / ".hermes_feishu_card_manifest"
    manifest_snapshot = _require_snapshot(
        manifest_path, "V3 install manifest is missing"
    )
    try:
        manifest = json.loads(manifest_snapshot.contents.decode("utf-8"))
        validate_install_manifest(manifest)
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise FixedTagInstallRefused("V3 install manifest is invalid") from exc
    if manifest.get("manifest_version") != 3:
        raise FixedTagInstallRefused("V3 install manifest is invalid")
    phase = manifest.get("phase")
    integration = manifest["integration"]
    plugin = integration["plugin"]
    config = integration["plugin_config"]
    if plugin["python_identity"] != binding.python_identity:
        raise FixedTagInstallRefused("V3 install runtime binding changed")
    current_config = _require_snapshot(binding.config_path, "Hermes config is missing")
    expected_config_sha256 = (
        config["pre_sha256"] if phase == "prepared" else config["post_sha256"]
    )
    if current_config.sha256 != expected_config_sha256:
        raise FixedTagInstallRefused("Hermes config changed since install")

    installed: dict[str, bytes] = {}
    backups: dict[str, bytes] = {}
    source_paths: dict[str, Path] = {}
    source_snapshots: dict[Path, _FileSnapshot] = {}
    evidence_snapshots: dict[Path, _FileSnapshot] = {
        manifest_path: manifest_snapshot
    }
    for target in HYBRID_PATCH_TARGET_ORDER:
        ownership = manifest["targets"][target]
        source_path = root / ownership["path"]
        backup_path = root / ownership["backup"]
        source_snapshot = _require_snapshot(source_path, "installed target is missing")
        backup_snapshot = _require_snapshot(backup_path, "installed backup is missing")
        if source_snapshot.sha256 != ownership["patched_sha256"]:
            raise FixedTagInstallRefused("installed target hash changed")
        if backup_snapshot.sha256 != ownership["backup_sha256"]:
            raise FixedTagInstallRefused("installed backup hash changed")
        source_paths[target] = source_path
        source_snapshots[source_path] = source_snapshot
        evidence_snapshots[backup_path] = backup_snapshot
        installed[target] = source_snapshot.contents
        backups[target] = backup_snapshot.contents
    if phase == "installed":
        patch_groups = frozenset(integration["patch_groups"])
        registry = _installed_patch_registry(installed, backups, patch_groups)
        expected_matrix = registry.target_fragments(patch_groups)
        try:
            detected = detect_patch_groups_by_target(
                installed,
                expected_groups=patch_groups,
                expected_fragment_matrix=expected_matrix,
                registry=registry,
            )
            restored = remove_patch_snapshots(
                installed,
                expected_groups=patch_groups,
                expected_fragment_matrix=expected_matrix,
                registry=registry,
            )
        except Exception as exc:
            raise FixedTagInstallRefused("installed Hybrid patch is invalid") from exc
        expected_targets = {
            target: frozenset(groups)
            for target, groups in integration["patch_targets"].items()
        }
        if detected != expected_targets or restored != backups:
            raise FixedTagInstallRefused(
                "installed Hybrid patch ownership is inconsistent"
            )
    elif installed != backups:
        raise FixedTagInstallRefused("incomplete V3 source changed before recovery")

    state_dir = binding.hermes_home / ".hermes_feishu_card" / "install"
    backup_id = config["config_backup_id"]
    preimage = plugin_install.PluginConfigPreimage(
        state_dir=state_dir,
        backup_path=state_dir / f"{backup_id}.yaml",
        journal_path=state_dir / f"{backup_id}.json",
        config_backup_id=backup_id,
        pre_sha256=config["pre_sha256"],
        backup_sha256=config["backup_sha256"],
        enabled_before=config["enabled_before"],
    )
    config_ownership = plugin_install.PluginOwnership(
        enabled_before=config["enabled_before"],
        added_by_hfc=config["added_by_hfc"],
        pre_sha256=config["pre_sha256"],
        post_sha256=config["post_sha256"],
        config_backup_id=backup_id,
        backup_sha256=config["backup_sha256"],
    )
    source_after: dict[Path, _FileSnapshot] | None = None
    if phase == "installed":
        try:
            source_after = _commit_file_set(
                {
                    source_paths[target]: backups[target]
                    for target in HYBRID_PATCH_TARGET_ORDER
                },
                expected=source_snapshots,
            )
        except Exception as exc:
            raise FixedTagInstallRefused("V3 source restore failed") from exc
    if phase != "prepared":
        try:
            plugin_install.restore_plugin_config(
                binding, preimage, config_ownership
            )
        except Exception as exc:
            if source_after is not None:
                try:
                    _commit_file_set(
                        {
                            source_paths[target]: installed[target]
                            for target in HYBRID_PATCH_TARGET_ORDER
                        },
                        expected=source_after,
                    )
                except Exception as rollback_exc:
                    raise FixedTagInstallRefused(
                        "V3 config restore failed and source rollback requires manual review"
                    ) from rollback_exc
            raise FixedTagInstallRefused("V3 config restore failed") from exc
    try:
        _remove_file_set(evidence_snapshots)
        plugin_install.cleanup_plugin_config_preimage(preimage)
    except Exception as exc:
        raise FixedTagInstallRefused(
            "V3 evidence cleanup failed after verified restore"
        ) from exc
    return FixedTagInstallResult(
        status="restored",
        manifest_path=manifest_path,
        gateway_restart_required=phase != "prepared",
    )


def _render_phase_manifest(
    plan: FixedTagHybridPlan,
    *,
    phase: str,
    target_contents: dict[str, tuple[bytes, bytes]],
    binding: plugin_install.HermesRuntimeBinding,
    package_version: str,
    plugin_config: Mapping[str, object],
) -> str:
    return render_install_manifest_v3(
        phase=phase,
        target_contents=target_contents,
        mode="hybrid",
        capability_fingerprint=plan.capability_fingerprint,
        patch_groups=plan.patch_groups,
        patch_targets=plan.patch_targets,
        plugin_version=package_version,
        python_identity=binding.python_identity,
        plugin_config=plugin_config,
    )


def _restore_config_after_failure(
    binding: plugin_install.HermesRuntimeBinding,
    preimage: plugin_install.PluginConfigPreimage,
    ownership: plugin_install.PluginOwnership,
) -> None:
    try:
        plugin_install.restore_plugin_config(binding, preimage, ownership)
        plugin_install.mark_plugin_config_prepared(preimage)
    except Exception as exc:
        raise FixedTagInstallRefused(
            "plugin config rollback failed; manual review required"
        ) from exc


def _restore_prepared_manifest(
    manifest_path: Path,
    contents: str,
    *,
    expected: _FileSnapshot,
) -> None:
    try:
        _commit_file_set(
            {manifest_path: contents.encode("utf-8")},
            expected={manifest_path: expected},
        )
    except Exception as exc:
        raise FixedTagInstallRefused(
            "manifest rollback failed; manual review required"
        ) from exc


def _commit_file_set(
    changes: Mapping[Path, bytes],
    *,
    expected: Mapping[Path, _FileSnapshot | None],
) -> dict[Path, _FileSnapshot]:
    if (
        type(changes) is not dict
        or type(expected) is not dict
        or set(changes) != set(expected)
        or not changes
        or any(
            not isinstance(path, Path) or type(value) is not bytes
            for path, value in changes.items()
        )
    ):
        raise FixedTagInstallRefused("install transaction input is invalid")
    for path in changes:
        if _snapshot(path) != expected[path]:
            raise FixedTagInstallRefused("install target changed before commit")
        _require_safe_parent(path.parent)
    staged: dict[Path, Path] = {}
    try:
        for path, contents in changes.items():
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{path.name}.hfc-", dir=path.parent
            )
            temporary_path = Path(temporary)
            staged[path] = temporary_path
            mode = expected[path].mode if expected[path] is not None else 0o600
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as file:
                file.write(contents)
                file.flush()
                os.fsync(file.fileno())
        written: list[Path] = []
        after: dict[Path, _FileSnapshot] = {}
        try:
            for path in changes:
                if _snapshot(path) != expected[path]:
                    raise FixedTagInstallRefused("install target changed during commit")
                os.replace(staged[path], path)
                written.append(path)
                snapshot = _require_snapshot(path, "install write verification failed")
                if snapshot.sha256 != hashlib.sha256(changes[path]).hexdigest():
                    raise FixedTagInstallRefused("install write verification failed")
                after[path] = snapshot
            return after
        except Exception:
            for path in reversed(written):
                current = after.get(path) or _snapshot(path)
                if current is None or _snapshot(path) != current:
                    raise FixedTagInstallRefused(
                        "install rollback lost ownership; manual review required"
                    )
                previous = expected[path]
                if previous is None:
                    path.unlink()
                else:
                    _replace_bytes(path, previous.contents, previous.mode)
            raise
    finally:
        for temporary_path in staged.values():
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _replace_bytes(path: Path, contents: bytes, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.hfc-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _remove_file_set(expected: Mapping[Path, _FileSnapshot]) -> None:
    if type(expected) is not dict or not expected:
        raise FixedTagInstallRefused("evidence cleanup input is invalid")
    staged: dict[Path, Path] = {}
    try:
        for path, snapshot in expected.items():
            if _snapshot(path) != snapshot:
                raise FixedTagInstallRefused("install evidence changed before cleanup")
            _require_safe_parent(path.parent)
        for path in expected:
            temporary = path.parent / (
                f".{path.name}.hfc-remove-{os.getpid()}-{len(staged)}"
            )
            if temporary.exists() or temporary.is_symlink():
                raise FixedTagInstallRefused("evidence cleanup staging path exists")
            os.replace(path, temporary)
            staged[path] = temporary
        for temporary in staged.values():
            temporary.unlink()
    except Exception:
        for path, temporary in reversed(tuple(staged.items())):
            if temporary.exists() and not path.exists():
                os.replace(temporary, path)
        raise


def _snapshot(path: Path) -> _FileSnapshot | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise FixedTagInstallRefused("install target is not a regular file")
    contents = path.read_bytes()
    return _FileSnapshot(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        sha256=hashlib.sha256(contents).hexdigest(),
        mode=stat.S_IMODE(metadata.st_mode),
        contents=contents,
    )


def _require_snapshot(path: Path, message: str) -> _FileSnapshot:
    snapshot = _snapshot(path)
    if snapshot is None:
        raise FixedTagInstallRefused(message)
    return snapshot


def _require_safe_parent(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FixedTagInstallRefused("install target parent is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise FixedTagInstallRefused("install target parent is unsafe")
