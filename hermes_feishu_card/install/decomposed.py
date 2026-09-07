"""Legacy-patch ownership for Hermes' September facade decomposition.

Mode selection stays in the CLI. This module owns only a verified source set;
all filesystem mutations reuse recovery's bound, rollback-capable transaction.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import stat

from . import patcher

MANIFEST_VERSION = 4
MANIFEST_NAME = ".hermes_feishu_card_manifest"
BACKUP_SUFFIX = ".hermes_feishu_card.bak"
SOURCE_TARGETS = ("gateway/run.py", *patcher.DECOMPOSED_GATEWAY_TARGETS,
                  "cron/scheduler.py", "cron/scheduler_delivery.py", "gateway/platforms/base.py")
VERSION_TARGETS = ("VERSION", "hermes_cli/__init__.py", ".git/HEAD", ".git/packed-refs")


def is_managed(root: Path) -> bool:
    try:
        value = json.loads((root / MANIFEST_NAME).read_bytes())
        return isinstance(value, dict) and value.get("manifest_version") == MANIFEST_VERSION
    except (OSError, ValueError):
        return False


def validate_manifest(manifest):
    from .manifest import ManifestStructureError
    if (not isinstance(manifest, dict)
            or type(manifest.get("manifest_version")) is not int
            or manifest.get("manifest_version") != MANIFEST_VERSION
            or manifest.get("layout") != "gateway-decomposed-v1"
            or manifest.get("integration_mode") != "legacy-patch"
            or not isinstance(manifest.get("targets"), dict)
            or not manifest["targets"]
            or not set(manifest["targets"]).issubset(SOURCE_TARGETS)
            or "gateway/run.py" not in manifest["targets"]):
        raise ManifestStructureError("invalid decomposed ownership manifest")
    for target, row in manifest["targets"].items():
        if (not isinstance(row, dict) or set(row) != {"path", "backup", "original_sha256", "patched_sha256"}
                or row["path"] != target or row["backup"] != target + BACKUP_SUFFIX
                or any(not isinstance(row[k], str) or len(row[k]) != 64
                       or any(c not in "0123456789abcdef" for c in row[k])
                       for k in ("original_sha256", "patched_sha256"))):
            raise ManifestStructureError("invalid decomposed target ownership")


def _snapshot(root):
    """Bind every candidate, including absence, so a new file invalidates a plan."""
    result = {}
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
    return result


def _fingerprint(snapshot):
    evidence = [(key, None if value is None else sha256(value).hexdigest())
                for key, value in sorted(snapshot.items())]
    return sha256(json.dumps(evidence).encode()).hexdigest()


def render(sources, strategy="gateway_run_013_plus"):
    rendered = {}
    for target, raw in sources.items():
        text = raw.decode("utf-8")
        if target == "gateway/platforms/base.py":
            patched = patcher.apply_base_patch(text)
            restored = patcher.remove_base_patch(patched)
        elif target.startswith("cron/"):
            patched = patcher.apply_cron_patch(text)
            restored = patcher.remove_cron_patch(patched)
        else:
            patched = patcher.apply_gateway_fragment(text, target, strategy=strategy)
            restored = patcher.remove_patch(patched)
        compile(patched, target, "exec")
        if restored != text:
            raise ValueError(f"{target}: patch is not reversible")
        rendered[target] = patched.encode("utf-8")
    from .detect import _LAYOUT_MARKERS
    all_bytes = b"\n".join(rendered.values())
    for name, marker in _LAYOUT_MARKERS.items():
        count = all_bytes.count(marker.encode())
        if count != 1 and not (name == "status_callback" and count == 0):
            raise ValueError(f"decomposed patch group missing or ambiguous: {name}")
    for marker in (patcher.CRON_PATCH_BEGIN, patcher.EXACT_BASE_NO_TEXT_PATCH_BEGIN,
                   patcher.EXACT_BASE_FINAL_DELIVERY_PATCH_BEGIN):
        if all_bytes.count(marker.encode()) != 1:
            raise ValueError("decomposed delivery patch group missing or ambiguous")
    return rendered


def _remove_target(target, raw):
    text = raw.decode("utf-8")
    if target == "gateway/platforms/base.py":
        return patcher.remove_base_patch(text).encode("utf-8")
    if target.startswith("cron/"):
        return patcher.remove_cron_patch(text).encode("utf-8")
    return patcher.remove_patch(text).encode("utf-8")


def _legacy_upgrade_sources(snapshot, sources, manifest):
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


def _inspect(root, *, render_hooks=True):
    snapshot = _snapshot(root)
    sources = {name: snapshot[name] for name in SOURCE_TARGETS if snapshot[name] is not None}
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
    if set(manifest["targets"]) != set(sources):
        raise ValueError("decomposed source target set changed")
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
    replacements = {}
    for target, raw in sources.items():
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
            raise ValueError(f"{target}: source drift; refusing mutation")
    if replacements:
        originals = {**originals, **replacements}
        rendered = render(originals) if render_hooks else None
        return snapshot, originals, rendered, "stale_unpatched"
    rendered = render(originals) if render_hooks else None
    renderer_changed = rendered is not None and any(
        sha256(raw).hexdigest() != manifest["targets"][target]["patched_sha256"]
        for target, raw in rendered.items())
    return snapshot, originals, rendered, "owned_incomplete" if clean_targets or renderer_changed else "installed"


def plan(detection, *, accept_hermes_upgrade=False):
    from .recovery import RecoveryFinding, RecoveryPlan
    try:
        snapshot, _, _, state = _inspect(detection.root)
        fingerprint = _fingerprint(snapshot)
        executable = state == "owned_incomplete" or (state == "stale_unpatched" and accept_hermes_upgrade and detection.supported)
        actions = ("restore_owned_hooks",) if state == "owned_incomplete" else ("accept_hermes_upgrade",) if state == "stale_unpatched" else ()
        findings = ()
        if state == "stale_unpatched":
            findings = (RecoveryFinding("hermes_upgrade_hooks_missing", "warning",
                        "Hermes source changed without owned hooks (an updater/autostash may remove them). "
                        "Use install --accept-hermes-upgrade --yes after reviewing the upgrade; "
                        "restart Gateway after successful repair."),)
        return RecoveryPlan(detection.root, state, executable, fingerprint, actions, findings)
    except (OSError, ValueError, UnicodeError):
        try:
            fingerprint = _fingerprint(_snapshot(detection.root))
        except (OSError, ValueError):
            fingerprint = sha256(b"unsafe decomposed evidence").hexdigest()
        return RecoveryPlan(detection.root, "refused", False, fingerprint, (),
                            (RecoveryFinding("user_modified", "error", "Decomposed ownership cannot be verified."),))


def install(detection, *, no_repair=False, expected_fingerprint=None, accept_hermes_upgrade=False):
    from .detect import detect_hermes
    from .recovery import _root_lock, _commit_recovery_changes, _secure_dirfd_transactions_supported
    with _root_lock(detection.root):
        snapshot, originals, rendered, state = _inspect(detection.root)
        if expected_fingerprint and _fingerprint(snapshot) != expected_fingerprint:
            raise ValueError("recovery evidence changed; rerun diagnosis")
        detection = detect_hermes(detection.root)
        if not detection.supported:
            raise ValueError("unsupported decomposed Hermes: " + detection.reason)
        if state == "installed":
            return False
        if state in {"owned_incomplete", "stale_unpatched"} and no_repair:
            raise ValueError("decomposed ownership needs repair; --no-repair set")
        if state == "stale_unpatched" and not (accept_hermes_upgrade and detection.supported):
            raise ValueError("decomposed source upgrade requires --accept-hermes-upgrade")
        fresh = plan(detection, accept_hermes_upgrade=accept_hermes_upgrade)
        if fresh.fingerprint != _fingerprint(snapshot):
            raise ValueError("decomposed evidence changed before install")
        rendered = rendered or render(originals, detection.hook_strategy)
        manifest = {
            "manifest_version": MANIFEST_VERSION, "layout": "gateway-decomposed-v1",
            "integration_mode": "legacy-patch",
            "targets": {name: {"path": name, "backup": name + BACKUP_SUFFIX,
                                "original_sha256": sha256(raw).hexdigest(),
                                "patched_sha256": sha256(rendered[name]).hexdigest()}
                        for name, raw in originals.items()},
        }
        validate_manifest(manifest)
        changes = [(detection.root / name, raw.decode("utf-8")) for name, raw in rendered.items()
                   if snapshot[name] != raw]
        if state in {"clean", "stale_unpatched"}:
            changes = [(detection.root / (name + BACKUP_SUFFIX), raw.decode("utf-8"))
                       for name, raw in originals.items()] + changes
        changes.append((detection.root / MANIFEST_NAME, json.dumps(manifest, indent=2) + "\n"))
        if _secure_dirfd_transactions_supported():
            _commit_recovery_changes(detection, fresh, changes, accept_hermes_upgrade=accept_hermes_upgrade)
        else:
            # Explicit installation on Windows uses the same verified portable
            # writer as the monolithic installer. Automatic recovery and backup
            # deletion retain their stronger directory-handle requirement.
            from ..cli import _snapshot_restore_evidence, _write_targets_portably
            evidence = {name: detection.root / name for name in
                        (*SOURCE_TARGETS, *(n + BACKUP_SUFFIX for n in SOURCE_TARGETS), MANIFEST_NAME)}
            identities, directories = _snapshot_restore_evidence(detection.root, evidence)
            if _fingerprint(_snapshot(detection.root)) != fresh.fingerprint:
                raise ValueError("decomposed evidence changed before portable install")
            _write_targets_portably(changes, expected_identities=identities,
                                    expected_directories=directories,
                                    preserve_earlier_writes_on_rollback_failure=True)
        _inspect(detection.root)
        return True


def restore(detection):
    from .recovery import _root_lock, _commit_recovery_changes, _require_secure_dirfd_transactions
    _require_secure_dirfd_transactions()
    with _root_lock(detection.root):
        snapshot, originals, _, state = _inspect(detection.root, render_hooks=False)
        if state == "clean":
            return
        if state == "stale_unpatched":
            raise ValueError("decomposed source drift; refusing restore until upgrade is accepted")
        fresh = plan(detection)
        if fresh.fingerprint != _fingerprint(snapshot):
            raise ValueError("decomposed evidence changed before restore")
        changes = [(detection.root / name, raw.decode("utf-8")) for name, raw in originals.items()
                   if snapshot[name] != raw]
        changes.extend((detection.root / (name + BACKUP_SUFFIX), None) for name in originals)
        changes.append((detection.root / MANIFEST_NAME, None))
        _commit_recovery_changes(detection, fresh, changes)
