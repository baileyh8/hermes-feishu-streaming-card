from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Iterable, Mapping

from ..integration import (
    HYBRID_REQUIRED_PATCH_GROUPS,
    KNOWN_PATCH_GROUPS,
)
from .patch_descriptors import HYBRID_PATCH_REGISTRY, HYBRID_PATCH_TARGETS


CURRENT_INSTALL_MANIFEST_VERSION = 3
LEGACY_INSTALL_MANIFEST_VERSIONS = frozenset({1, 2})
INSTALL_PHASES = frozenset({"prepared", "plugin_enabled", "installed"})
_LOWER_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_BACKUP_ID_RE = re.compile(r"hfc-config-preimage-[0-9a-f]{32}")
CRON_INSTALL_MANIFEST_FIELDS = frozenset(
    {
        "cron_py",
        "cron_patched_sha256",
        "cron_backup",
        "cron_backup_sha256",
    }
)
BASE_INSTALL_MANIFEST_FIELDS = frozenset(
    {
        "base_py",
        "base_patched_sha256",
        "base_backup",
        "base_backup_sha256",
    }
)
UNSUPPORTED_INSTALL_MANIFEST_VERSION_MESSAGE = (
    "install manifest version is unsupported; newer installer required; "
    "refusing to mutate"
)


class ManifestVersionError(ValueError):
    pass


class ManifestStructureError(ValueError):
    pass


def validate_install_manifest_version(manifest: Mapping[str, object]) -> int:
    """Return the understood version or reject unsafe mutation semantics.

    Manifests without an explicit version predate versioning and are treated as
    legacy v1. Writers always emit the current version. Unknown versions are
    never guessed because they may own targets this installer does not know.
    """

    if "manifest_version" not in manifest:
        return 1
    version = manifest.get("manifest_version")
    # V4 describes one explicit Legacy layout, not arbitrary future ownership.
    # A bare version number must retain the existing fail-closed behavior.
    if (type(version) is int and version == 4
            and manifest.get("layout") == "gateway-decomposed-v1"
            and manifest.get("integration_mode") == "legacy-patch"):
        return version
    if type(version) is int and version in {
        *LEGACY_INSTALL_MANIFEST_VERSIONS,
        CURRENT_INSTALL_MANIFEST_VERSION,
    }:
        return version
    raise ManifestVersionError(UNSUPPORTED_INSTALL_MANIFEST_VERSION_MESSAGE)


def validate_install_manifest(manifest: Mapping[str, object]) -> int:
    version = validate_install_manifest_version(manifest)
    if version == 4:
        from .decomposed import validate_manifest
        validate_manifest(manifest)
        return version
    if version == 3:
        _validate_v3_manifest(manifest)
        return version
    _validate_managed_field_group(
        manifest,
        CRON_INSTALL_MANIFEST_FIELDS,
        "cron",
    )
    _validate_managed_field_group(
        manifest,
        BASE_INSTALL_MANIFEST_FIELDS,
        "exact Base",
    )
    return version


def render_install_manifest_v3(
    *,
    phase: str,
    target_contents: Mapping[str, tuple[bytes, bytes]],
    mode: str,
    capability_fingerprint: str,
    patch_groups: Iterable[str],
    patch_targets: Mapping[str, Iterable[str]],
    plugin_version: str,
    python_identity: str,
    plugin_config: Mapping[str, object],
) -> str:
    if type(target_contents) is not dict or set(target_contents) != HYBRID_PATCH_TARGETS:
        raise ManifestStructureError("manifest v3 target contents must be exact")
    targets: dict[str, dict[str, str]] = {}
    for target in sorted(target_contents):
        pair = target_contents[target]
        if (
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not bytes
            or type(pair[1]) is not bytes
        ):
            raise ManifestStructureError("manifest v3 target contents are invalid")
        patched, original = pair
        targets[target] = {
            "path": target,
            "patched_sha256": hashlib.sha256(patched).hexdigest(),
            "backup": target + ".hermes_feishu_card.bak",
            "backup_sha256": hashlib.sha256(original).hexdigest(),
        }
    try:
        normalized_groups = sorted(patch_groups)
        normalized_targets = {
            target: sorted(patch_targets[target]) for target in sorted(patch_targets)
        }
    except (KeyError, TypeError) as exc:
        raise ManifestStructureError("manifest patch ownership is invalid") from exc
    manifest = {
        "manifest_version": CURRENT_INSTALL_MANIFEST_VERSION,
        "phase": phase,
        "targets": targets,
        "integration": {
            "mode": mode,
            "capability_fingerprint": capability_fingerprint,
            "patch_groups": normalized_groups,
            "patch_targets": normalized_targets,
            "plugin": {
                "key": "hermes-feishu-card",
                "entry_point": "hermes_feishu_card.hermes_plugin",
                "distribution": "hermes-feishu-streaming-card",
                "version": plugin_version,
                "python_identity": python_identity,
            },
            "plugin_config": dict(plugin_config),
        },
    }
    validate_install_manifest(manifest)
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"


def _validate_v3_manifest(manifest: Mapping[str, object]) -> None:
    if set(manifest) != {"manifest_version", "phase", "targets", "integration"}:
        raise ManifestStructureError("manifest v3 fields are incomplete or unknown")
    phase = manifest.get("phase")
    if type(phase) is not str or phase not in INSTALL_PHASES:
        raise ManifestVersionError(UNSUPPORTED_INSTALL_MANIFEST_VERSION_MESSAGE)
    targets = _exact_mapping(manifest.get("targets"), "targets")
    if set(targets) != HYBRID_PATCH_TARGETS:
        raise ManifestStructureError("manifest v3 targets must be exact and complete")
    for target, ownership_value in targets.items():
        ownership = _exact_mapping(ownership_value, f"target {target}")
        if set(ownership) != {
            "path",
            "patched_sha256",
            "backup",
            "backup_sha256",
        }:
            raise ManifestStructureError("manifest target fields are incomplete or unknown")
        _validate_portable_path(ownership.get("path"))
        _validate_portable_path(ownership.get("backup"))
        if ownership.get("path") != target:
            raise ManifestStructureError("manifest target path must match its target")
        _validate_sha256(ownership.get("patched_sha256"), "patched target")
        _validate_sha256(ownership.get("backup_sha256"), "target backup")

    integration = _exact_mapping(manifest.get("integration"), "integration")
    if set(integration) != {
        "mode",
        "capability_fingerprint",
        "patch_groups",
        "patch_targets",
        "plugin",
        "plugin_config",
    }:
        raise ManifestStructureError("manifest integration fields are incomplete or unknown")
    mode = integration.get("mode")
    if type(mode) is not str or mode not in {"hybrid", "native-hooks"}:
        raise ManifestVersionError(UNSUPPORTED_INSTALL_MANIFEST_VERSION_MESSAGE)
    fingerprint = integration.get("capability_fingerprint")
    if type(fingerprint) is not str or _DIGEST_RE.fullmatch(fingerprint) is None:
        raise ManifestStructureError("manifest capability fingerprint is invalid")
    patch_groups = _sorted_string_list(
        integration.get("patch_groups"), "patch groups"
    )
    if not set(patch_groups) <= KNOWN_PATCH_GROUPS:
        raise ManifestVersionError(UNSUPPORTED_INSTALL_MANIFEST_VERSION_MESSAGE)
    expected_groups = (
        HYBRID_REQUIRED_PATCH_GROUPS if mode == "hybrid" else frozenset()
    )
    if frozenset(patch_groups) != expected_groups:
        raise ManifestStructureError("manifest patch groups do not match integration mode")
    patch_targets = _exact_mapping(
        integration.get("patch_targets"), "patch targets"
    )
    if set(patch_targets) != HYBRID_PATCH_TARGETS:
        raise ManifestStructureError("manifest patch targets must be exact and complete")
    normalized_targets = {
        target: frozenset(
            _sorted_string_list(patch_targets[target], f"patch target {target}")
        )
        for target in patch_targets
    }
    expected_targets = HYBRID_PATCH_REGISTRY.target_groups(expected_groups)
    if normalized_targets != expected_targets:
        raise ManifestStructureError("manifest patch target groups are inconsistent")
    if frozenset().union(*normalized_targets.values()) != expected_groups:
        raise ManifestStructureError("manifest patch target union is inconsistent")

    plugin = _exact_mapping(integration.get("plugin"), "plugin ownership")
    if set(plugin) != {
        "key",
        "entry_point",
        "distribution",
        "version",
        "python_identity",
    }:
        raise ManifestStructureError("manifest plugin ownership fields are incomplete or unknown")
    if (
        plugin.get("key") != "hermes-feishu-card"
        or plugin.get("entry_point") != "hermes_feishu_card.hermes_plugin"
        or plugin.get("distribution") != "hermes-feishu-streaming-card"
        or type(plugin.get("version")) is not str
        or not plugin["version"]
        or type(plugin.get("python_identity")) is not str
        or _DIGEST_RE.fullmatch(plugin["python_identity"]) is None
    ):
        raise ManifestStructureError("manifest plugin ownership is invalid")

    config = _exact_mapping(
        integration.get("plugin_config"), "plugin config ownership"
    )
    if set(config) != {
        "enabled_before",
        "added_by_hfc",
        "pre_sha256",
        "post_sha256",
        "config_backup_id",
        "backup_sha256",
    }:
        raise ManifestStructureError(
            "manifest plugin config ownership fields are incomplete or unknown"
        )
    if (
        type(config.get("enabled_before")) is not bool
        or type(config.get("added_by_hfc")) is not bool
    ):
        raise ManifestStructureError("manifest plugin config booleans are invalid")
    for field in ("pre_sha256", "post_sha256", "backup_sha256"):
        _validate_sha256(config.get(field), f"plugin config {field}")
    backup_id = config.get("config_backup_id")
    if type(backup_id) is not str or _BACKUP_ID_RE.fullmatch(backup_id) is None:
        raise ManifestStructureError("manifest plugin config backup id is invalid")
    if config["backup_sha256"] != config["pre_sha256"]:
        raise ManifestStructureError("manifest plugin config backup hash is inconsistent")
    if phase == "prepared" and config["post_sha256"] != config["pre_sha256"]:
        raise ManifestStructureError("prepared manifest must retain config preimage hash")


def _exact_mapping(value: object, label: str) -> Mapping[str, object]:
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise ManifestStructureError(f"manifest {label} must be an exact mapping")
    return value


def _sorted_string_list(value: object, label: str) -> tuple[str, ...]:
    if (
        type(value) is not list
        or any(type(item) is not str or not item for item in value)
        or value != sorted(set(value))
    ):
        raise ManifestStructureError(f"manifest {label} must be a sorted unique list")
    return tuple(value)


def _validate_portable_path(value: object) -> None:
    if type(value) is not str or not value or "\\" in value:
        raise ManifestStructureError("manifest path must be portable and relative")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ManifestStructureError("manifest path must be portable and relative")


def _validate_sha256(value: object, label: str) -> None:
    if type(value) is not str or _LOWER_SHA256_RE.fullmatch(value) is None:
        raise ManifestStructureError(f"manifest {label} sha256 is invalid")


def _validate_managed_field_group(
    manifest: Mapping[str, object],
    fields: frozenset[str],
    label: str,
) -> None:
    present = fields.intersection(manifest)
    if present and present != fields:
        raise ManifestStructureError(
            f"manifest {label} ownership fields are incomplete; refusing to mutate"
        )


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
