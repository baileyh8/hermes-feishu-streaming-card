"""Read-only decomposed source ownership and reversible patch evidence.

No detection, planner, transaction or service dependencies belong here.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import stat
from typing import Any, Mapping

from . import git_evidence, patcher
from .manifest import validate_decomposed_manifest as validate_manifest
from .source_layout import (
    BACKUP_SUFFIX, MANIFEST_NAME,
    DECOMPOSED_MANIFEST_VERSION as MANIFEST_VERSION,
    DECOMPOSED_SOURCE_TARGETS as SOURCE_TARGETS,
)

VERSION_TARGETS = ("VERSION", "hermes_cli/__init__.py", ".git/HEAD", ".git/packed-refs")
EXPLICIT_UPGRADE_STATES = frozenset({"stale_unpatched", "stale_reapplied"})


def is_managed(root: Path) -> bool:
    try:
        value = json.loads((root / MANIFEST_NAME).read_bytes())
        return isinstance(value, dict) and value.get("manifest_version") == MANIFEST_VERSION
    except (OSError, ValueError):
        return False


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Bind every candidate, including absence, so a new file invalidates a plan."""
    result: dict[str, bytes | None] = {}
    for name in (*SOURCE_TARGETS, *(n + BACKUP_SUFFIX for n in SOURCE_TARGETS),
                 MANIFEST_NAME, *VERSION_TARGETS):
        path = root / name
        for parent in (path, *path.parents):
            if parent.is_symlink():
                raise ValueError("decomposed source ancestry must not contain symlinks")
            if parent == root:
                break
        try:
            metadata = path.lstat()
        except (FileNotFoundError, NotADirectoryError):
            result[name] = None
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("decomposed ownership evidence must be a regular file")
        result[name] = path.read_bytes()
    # A symbolic HEAD file does not change when its branch advances. Bind the
    # resolved commit too, including when the source set itself is unchanged.
    result["!resolved_git_head"] = None
    if (root / ".git").exists():
        try:
            result["!resolved_git_head"] = git_evidence._git_head(git_evidence._exact_git_root(root)).encode("ascii")
        except (OSError, ValueError):
            pass  # Ordinary owned installs do not require Git; rebasing does.
    return result


def _fingerprint(snapshot: Mapping[str, bytes | None]) -> str:
    evidence = [(key, None if value is None else sha256(value).hexdigest())
                for key, value in sorted(snapshot.items())]
    return sha256(json.dumps(evidence).encode()).hexdigest()


def render(sources: Mapping[str, bytes], strategy: str = "gateway_run_013_plus") -> dict[str, bytes]:
    rendered = {}
    completion_sources = {name: raw.decode("utf-8") for name, raw in sources.items()}
    for target, raw in sources.items():
        text = raw.decode("utf-8")
        if target == "gateway/platforms/base.py":
            patched = patcher.apply_base_patch(text)
            restored = patcher.remove_base_patch(patched)
        elif target.startswith("cron/"):
            patched = patcher.apply_cron_patch(text)
            restored = patcher.remove_cron_patch(patched)
        else:
            patched = patcher.apply_gateway_fragment(text, target, strategy=strategy,
                                                      completion_sources=completion_sources)
            restored = patcher.remove_patch(patched)
        compile(patched, target, "exec")
        if restored != text:
            raise ValueError(f"{target}: patch is not reversible")
        rendered[target] = patched.encode("utf-8")
    all_bytes = b"\n".join(rendered.values())
    for name, marker in patcher.DECOMPOSED_LAYOUT_MARKERS.items():
        count = all_bytes.count(marker.encode())
        if count != 1 and not (name == "status_callback" and count == 0):
            raise ValueError(f"decomposed patch group missing or ambiguous: {name}")
    for marker in (patcher.CRON_PATCH_BEGIN, patcher.EXACT_BASE_NO_TEXT_PATCH_BEGIN,
                   patcher.EXACT_BASE_FINAL_DELIVERY_PATCH_BEGIN):
        if all_bytes.count(marker.encode()) != 1:
            raise ValueError("decomposed delivery patch group missing or ambiguous")
    return rendered


def snapshot_provenance(originals: Mapping[str, bytes], rendered: Mapping[str, bytes]) -> dict[str, Any]:
    """Local ownership evidence, never Git ancestry or automatic upgrade authority."""
    return {
        "version": 3,
        "kind": "verified_owned_snapshot",
        "layout": "gateway-decomposed-v1",
        "targets": {
            name: {"original_sha256": sha256(raw).hexdigest(),
                   "patched_sha256": sha256(rendered[name]).hexdigest()}
            for name, raw in originals.items()
        },
    }


def verified_snapshot_provenance(root: Path) -> dict[str, Any]:
    snapshot, originals, rendered, state = _inspect(root)
    if state != "installed":
        raise ValueError("integrity migration requires healthy decomposed ownership")
    assert rendered is not None  # installed inspection always renders hooks
    for name, raw in rendered.items():
        if snapshot[name] != raw or _remove_target(name, raw) != originals[name]:
            raise ValueError("decomposed hook is not reversible")
    return snapshot_provenance(originals, rendered)


def _remove_target(target: str, raw: bytes) -> bytes:
    text = raw.decode("utf-8")
    if target == "gateway/platforms/base.py":
        return patcher.remove_base_patch(text).encode("utf-8")
    if target.startswith("cron/"):
        return patcher.remove_cron_patch(text).encode("utf-8")
    return patcher.remove_patch(text).encode("utf-8")


def _verified_reapplied_source(root: Path, target: str, raw: bytes, snapshot: Mapping[str, bytes | None]) -> bytes:
    """Accept only known generated hooks over the exact current Git blob.

    This is read-only evidence for an explicitly accepted upstream migration,
    never automatic repair authority or permission to discard local edits.
    """
    head = snapshot.get("!resolved_git_head")
    if head is None:
        raise ValueError(f"{target}: source drift has no exact Git proof")
    git_root = git_evidence._exact_git_root(root)
    revision = head.decode("ascii")
    original = _remove_target(target, raw)
    if (b"HERMES_FEISHU_CARD_" in original
            or original != git_evidence._git_blob(git_root, revision, target).encode("utf-8")
            or git_evidence._git_head(git_root) != revision):
        raise ValueError(f"{target}: source drift is not a verified reapplied hook")
    compile(original, target, "exec")
    return original


def _legacy_upgrade_sources(snapshot: Mapping[str, bytes | None], sources: Mapping[str, bytes], manifest: dict[str, Any]) -> dict[str, bytes]:
    """Verify old ownership before an explicitly accepted layout transition.

    An upstream updater may replace all three legacy targets while retaining
    the install manifest/backups (including when it autostashes the hooks).
    Preserve the new source, never restore the old monolithic Gateway over it.
    V3 also owns plugin configuration, so it requires its dedicated uninstaller.
    """
    from .manifest import validate_install_manifest
    version = validate_install_manifest(manifest)
    if version not in {1, 2}:
        raise ValueError("legacy layout migration requires verified V1/V2 ownership; "
                         "restore V3 ownership with its original installer first")
    groups = (
        ("gateway/run.py", "run_py", "backup", "backup_sha256", "patched_sha256"),
        ("cron/scheduler.py", "cron_py", "cron_backup", "cron_backup_sha256", "cron_patched_sha256"),
        ("gateway/platforms/base.py", "base_py", "base_backup", "base_backup_sha256", "base_patched_sha256"),
    )
    owned = set()
    originals = dict(sources)
    for target, path_key, backup_key, original_key, patched_key in groups:
        if path_key not in manifest and target != "gateway/run.py":
            continue
        if (not isinstance(manifest.get(path_key), str)
                or manifest[path_key].replace("\\", "/") != target
                or not isinstance(manifest.get(backup_key), str)
                or manifest[backup_key].replace("\\", "/") != target + BACKUP_SUFFIX):
            raise ValueError("legacy ownership path mismatch; refusing layout migration")
        for key in (original_key, patched_key):
            digest = manifest.get(key)
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)):
                raise ValueError("legacy ownership hash missing; refusing layout migration")
        backup = snapshot[target + BACKUP_SUFFIX]
        if (backup is None or sha256(backup).hexdigest() != manifest[original_key]
                or b"HERMES_FEISHU_CARD_" in backup or target not in sources):
            raise ValueError("legacy backup missing or changed; refusing layout migration")
        compile(backup, target, "exec")
        current = sources[target]
        if sha256(current).hexdigest() == manifest[patched_key]:
            if _remove_target(target, current) != backup:
                raise ValueError("legacy owned hook is not reversible")
            originals[target] = backup
        elif b"HERMES_FEISHU_CARD_" in current:
            raise ValueError("legacy source drift; refusing layout migration")
        owned.add(target)
    for target in SOURCE_TARGETS:
        if target not in owned:
            if snapshot[target + BACKUP_SUFFIX] is not None:
                raise ValueError("unowned backup exists; refusing layout migration")
            if target in sources and b"HERMES_FEISHU_CARD_" in sources[target]:
                raise ValueError("unowned hook exists; refusing layout migration")
    return originals


def _inspect(root: Path, *, render_hooks: bool = True) -> tuple[dict[str, bytes | None], dict[str, bytes], dict[str, bytes] | None, str]:
    snapshot = _snapshot(root)
    sources = {name: raw for name in SOURCE_TARGETS if (raw := snapshot[name]) is not None}
    raw_manifest = snapshot[MANIFEST_NAME]
    if raw_manifest is None:
        if any(snapshot[name + BACKUP_SUFFIX] is not None for name in SOURCE_TARGETS):
            raise ValueError("decomposed backup has no manifest; refusing mutation")
        if any(b"HERMES_FEISHU_CARD_" in raw for raw in sources.values()):
            raise ValueError("decomposed hooks have no manifest; refusing mutation")
        return snapshot, sources, None, "clean"
    manifest = json.loads(raw_manifest)
    if not isinstance(manifest, dict):
        raise ValueError("invalid decomposed ownership manifest")
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        originals = _legacy_upgrade_sources(snapshot, sources, manifest)
        return snapshot, originals, render(originals) if render_hooks else None, "stale_unpatched"
    validate_manifest(manifest)
    owned_targets = set(manifest["targets"])
    added = set(sources) - owned_targets
    extension_targets = {"gateway/run_background.py", "gateway/run_turn_persistence.py", "gateway/run_turn_prepare.py",
                         "gateway/run_turn_hygiene.py"}
    if owned_targets - set(sources) or not added.issubset(extension_targets):
        raise ValueError("decomposed source target set changed")
    if any(b"HERMES_FEISHU_CARD_" in sources[name] for name in added):
        raise ValueError("new decomposed source has unowned hooks")
    originals = {}
    for target, row in manifest["targets"].items():
        backup = snapshot[target + BACKUP_SUFFIX]
        if (backup is None or sha256(backup).hexdigest() != row["original_sha256"]
                or b"HERMES_FEISHU_CARD_" in backup):
            raise ValueError(f"{target}: backup missing or changed")
        compile(backup, target, "exec")
        originals[target] = backup
    if any(snapshot[name + BACKUP_SUFFIX] is not None for name in SOURCE_TARGETS if name not in originals):
        raise ValueError("unowned decomposed backup exists")
    clean_targets = []
    replacements = {name: sources[name] for name in added}
    reapplied = False
    for target, raw in sources.items():
        if target in added:
            continue
        row = manifest["targets"][target]
        if sha256(raw).hexdigest() == row["patched_sha256"]:
            # The manifest binds the entire installed file to its verified
            # original backup. Do not ask today's renderer/remover to recognize
            # yesterday's hook body: that would make upgrades non-restorable.
            continue
        if raw == originals[target]:
            clean_targets.append(target)
        elif b"HERMES_FEISHU_CARD_" not in raw:
            replacements[target] = raw
        else:
            replacements[target] = _verified_reapplied_source(root, target, raw, snapshot)
            reapplied = True
    if replacements:
        originals = {**originals, **replacements}
        rendered = render(originals) if render_hooks else None
        return snapshot, originals, rendered, "stale_reapplied" if reapplied else "stale_unpatched"
    rendered = render(originals) if render_hooks else None
    renderer_changed = rendered is not None and any(
        sha256(raw).hexdigest() != manifest["targets"][target]["patched_sha256"]
        for target, raw in rendered.items())
    return snapshot, originals, rendered, "owned_incomplete" if clean_targets or renderer_changed else "installed"


