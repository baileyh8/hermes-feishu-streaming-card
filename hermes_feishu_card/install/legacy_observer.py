"""Legacy plugin config ownership, separate from the existing source manifest.

The companion record never patches Hermes or edits its configuration. Configuration
changes and exact restoration belong to the official plugin admission adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import ast
import json
import os
from hashlib import sha256
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any

from . import file_transaction as portable
from . import plugin, plugin_config_delta
from . import posix_transaction as io
from .manifest import validate_install_manifest

MARKER = ".hermes_feishu_card_observer.json"
PROTOCOL = "hfc-legacy-observer-v1"
ADOPTION_PROTOCOL = "hfc-legacy-observer-v2"
RESTORE_PROTOCOL = "hfc-plugin-config-restore-v1"


class ObserverConfigChanged(ValueError):
    """The verified ownership record does not cover the current config bytes."""


def _ast_contract(node: Any) -> Any:
    """Stable AST shape across supported Python versions, without dump formatting.

    Python 3.12 adds an empty FunctionDef.type_params field. Empty means the
    same non-generic helper on 3.9; nonempty parameters remain contract drift.
    """
    if isinstance(node, ast.AST):
        return [type(node).__name__, [
            [name, _ast_contract(value)] for name, value in ast.iter_fields(node)
            if not (name == "type_params" and value == [])
        ]]
    if isinstance(node, list):
        return [_ast_contract(value) for value in node]
    return node


def _ast_sha256(node: ast.AST) -> str:
    return sha256(json.dumps(_ast_contract(node), ensure_ascii=True,
                             separators=(",", ":")).encode()).hexdigest()


def available(root: Path) -> bool:
    raw = _read(root / "agent" / "turn_response_intake.py")
    if raw is None:
        return False
    # v0.22.2 exact observation producer: bind the usage/timing expressions and
    # helper signature/imports, rather than accepting the same keyword names
    # attached to unrelated values or an uncalled helper. Comments are irrelevant.
    try:
        tree = ast.parse(raw)
    except (SyntaxError, ValueError):
        return False
    helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "_fire_post_api_request_hook"]
    owners = [n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "normalize_model_response"]
    if len(helpers) != 1 or len(owners) != 1:
        return False
    if _ast_sha256(helpers[0]) != (
        "4620431faaa1e4fada26d16e8e80ee0cade75f75587cd4fa5f2f1a693025f05b"
    ):
        return False
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == helpers[0].name]
    if len(calls) != 1 or _ast_sha256(calls[0]) != (
        "18fc5a627c600853c5d20545b8a4d541e3fde5a8f1bccceaa53c387f6b9ef6ef"
    ):
        return False
    # Only the direct response-intake statement after provider projection is
    # supported. A moved/nested/dead call cannot establish per-request timing.
    body = owners[0].body
    for index, statement in enumerate(body):
        if isinstance(statement, ast.Expr) and statement.value is calls[0]:
            previous = body[index - 1] if index else None
            return (isinstance(previous, ast.Expr) and isinstance(previous.value, ast.Call)
                    and _ast_contract(previous.value) == _ast_contract(
                        ast.parse("splice_provider_projection(agent, response, messages)", mode="eval").body))
    return False


def _read(path: Path) -> bytes | None:
    try:
        meta = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(meta.st_mode) or meta.st_size > 64 * 1024:
        raise ValueError("legacy observer record is unsafe")
    value = path.read_bytes()
    after = path.lstat()
    if (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise ValueError("legacy observer record changed")
    return value


def _decode(raw: bytes) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(raw)
    old_fields = {"protocol", "home", "python_identity", "plugin_config"}
    new_fields = old_fields | {"config_delta", "adapter"}
    if (type(value) is not dict or not (
        (value.get("protocol") == PROTOCOL and set(value) in (old_fields, old_fields | {"config_delta"}))
        or (value.get("protocol") == ADOPTION_PROTOCOL and set(value) == new_fields)
    ) or type(value["home"]) is not str
            or not Path(value["home"]).is_absolute()):
        raise ValueError("legacy observer record is invalid")
    config = value["plugin_config"]
    if type(config) is not dict or set(config) != {
        "enabled_before", "added_by_hfc", "pre_sha256", "post_sha256",
        "backup_sha256", "config_backup_id"
    }:
        raise ValueError("legacy observer config ownership is invalid")
    if (any(type(config[k]) is not bool for k in ("enabled_before", "added_by_hfc"))
            or any(type(config[k]) is not str or not re.fullmatch(r"[0-9a-f]{64}", config[k])
                   for k in ("pre_sha256", "post_sha256", "backup_sha256"))
            or type(config["config_backup_id"]) is not str
            or not re.fullmatch(r"hfc-config-preimage-[0-9a-f]{32}", config["config_backup_id"])
            or type(value["python_identity"]) is not str
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["python_identity"])):
        raise ValueError("legacy observer config ownership is invalid")
    if value["protocol"] == ADOPTION_PROTOCOL:
        from .legacy_9b904 import ADAPTER_ID
        if (value["adapter"] != ADAPTER_ID or not config["enabled_before"] or config["added_by_hfc"]
                or config["pre_sha256"] != config["post_sha256"]
                or config["pre_sha256"] != config["backup_sha256"]):
            raise ValueError("legacy observer adoption evidence is invalid")
    return value


def _preimage(home: Path, ownership: plugin.PluginOwnership) -> plugin.PluginConfigPreimage:
    state = home / ".hermes_feishu_card" / "install"
    key = ownership.config_backup_id
    return plugin.PluginConfigPreimage(
        state, state / (key + ".yaml"), state / (key + ".json"), key,
        ownership.pre_sha256, ownership.backup_sha256, ownership.enabled_before,
    )


def _restore_ticket_path(preimage: plugin.PluginConfigPreimage) -> Path:
    return preimage.state_dir / (preimage.config_backup_id + ".restore.json")


def _restore_progress(root: Path, home: Path, raw_marker: bytes,
                      preimage: plugin.PluginConfigPreimage, config: bytes) -> tuple[str, dict[str, Any] | None, io.FileIdentity | None]:
    """A restored state needs a receipt for this exact file and evidence set."""
    ticket_path = _restore_ticket_path(preimage)
    try:
        ticket_path.lstat()
    except FileNotFoundError:
        return "none", None, None
    ticket_snapshot = io.read_path_snapshot(ticket_path, ObserverConfigChanged, "config restore evidence changed")
    raw = _read(ticket_path)
    if raw is None:
        raise ObserverConfigChanged("config restore evidence changed")
    if ticket_snapshot[2] != plugin._sha256(raw):
        raise ObserverConfigChanged("config restore evidence changed")
    try:
        ticket = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ObserverConfigChanged("config restore evidence is invalid") from exc
    fields = {"protocol", "before", "after", "marker", "journal", "backup",
              "config_parent", "marker_parent", "state_parent", "stage"}
    if type(ticket) is not dict or set(ticket) != fields or ticket["protocol"] != RESTORE_PROTOCOL:
        raise ObserverConfigChanged("config restore evidence is invalid")
    def file_identity(value: Any) -> bool:
        return (type(value) is list and len(value) == 3 and all(type(x) is int and x >= 0 for x in value[:2])
                and type(value[2]) is str and re.fullmatch(r"[0-9a-f]{64}", value[2]) is not None)
    def directory_identity(value: Any) -> bool:
        return type(value) is list and len(value) == 2 and all(type(x) is int and x >= 0 for x in value)
    if (not all(file_identity(ticket[name]) for name in ("before", "after", "marker", "journal", "backup"))
            or not all(directory_identity(ticket[name]) for name in ("config_parent", "marker_parent", "state_parent"))
            or type(ticket["stage"]) is not str
            or (ticket["stage"] and re.fullmatch(r"\.config\.yaml\.[0-9a-f]{32}\.tmp", ticket["stage"]) is None)):
        raise ObserverConfigChanged("config restore evidence is invalid")
    files = {"marker": root / MARKER, "journal": preimage.journal_path, "backup": preimage.backup_path}
    for name, path in files.items():
        if io.read_path_snapshot(path, ObserverConfigChanged, "config restore evidence changed") != tuple(ticket[name]):
            raise ObserverConfigChanged("config restore evidence changed")
    if plugin._sha256(raw_marker) != ticket["marker"][2]:
        raise ObserverConfigChanged("config restore evidence changed")
    for name, path in (("config_parent", home), ("marker_parent", root), ("state_parent", preimage.state_dir)):
        meta = path.lstat()
        if (not stat.S_ISDIR(meta.st_mode) or portable._is_reparse(meta)
                or (meta.st_dev, meta.st_ino) != tuple(ticket[name])):
            raise ObserverConfigChanged("config restore directory changed")
    config_snapshot = io.read_path_snapshot(home / "config.yaml", ObserverConfigChanged, "config restore changed")
    if config_snapshot[2] != plugin._sha256(config):
        raise ObserverConfigChanged("config restore changed")
    if (_read(ticket_path) != raw or io.read_path_snapshot(ticket_path, ObserverConfigChanged,
                                                            "config restore evidence changed") != ticket_snapshot):
        raise ObserverConfigChanged("config restore evidence changed")
    if config_snapshot == tuple(ticket["after"]):
        return "restored", ticket, ticket_snapshot
    if config_snapshot == tuple(ticket["before"]):
        return "pending", ticket, ticket_snapshot
    raise ObserverConfigChanged("config changed during restore")


def _discard_pending_ticket(binding: plugin.HermesRuntimeBinding, preimage: plugin.PluginConfigPreimage,
                            ticket: dict[str, Any], ticket_snapshot: io.FileIdentity) -> None:
    """Retire only a fully verified interrupted stage before retrying."""
    ticket_path = _restore_ticket_path(preimage)
    removal = [ticket_path]
    if ticket["stage"]:
        stage = binding.config_path.parent / ticket["stage"]
        try:
            stage.lstat()
        except FileNotFoundError:
            pass
        else:
            if io.read_path_snapshot(stage, ValueError, "previous config stage changed") != tuple(ticket["after"]):
                raise ValueError("previous config stage changed")
            removal.append(stage)
    identities: dict[Path, io.FileIdentity | None] = {ticket_path: ticket_snapshot}
    for path in removal[1:]:
        identities[path] = tuple(ticket["after"])
    directories: dict[Path, io.DirectoryIdentity | None] = {
        path.parent: io.read_directory_identity(path.parent, ValueError, "config restore directory changed")
        for path in removal
    }
    if io._cli_dirfd_binding_supported():
        io._remove_verified_restore_evidence(removal, identities, directories)
    elif os.name == "nt":
        portable._remove_targets_portably(removal, expected_identities=identities,
            expected_directories=directories, recovery_contents={path: path.read_text() for path in removal})
    else:
        raise ValueError("secure config restore cleanup is unavailable")


def _write_restore_ticket(path: Path, payload: bytes, *,
                          expected_parent: io.DirectoryIdentity | None = None) -> io.FileIdentity:
    if not io._cli_dirfd_binding_supported():
        if expected_parent is not None:
            raise ValueError("secure config restore evidence is unavailable")
        plugin._write_private_new(path, payload)
        snapshot = io.read_path_snapshot(path, ValueError, "config restore evidence changed")
        if snapshot[2] != plugin._sha256(payload):
            raise ValueError("config restore evidence changed")
        return snapshot
    binding = io.bind_target(path, ValueError, staging_refusal="config restore directory changed",
                             parent_refusal="config restore directory changed", wrap_open_error=True)
    descriptor = -1
    try:
        if expected_parent is not None and binding.parent_identity != expected_parent:
            raise ValueError("config restore directory changed")
        io.assert_path_parent(binding, ValueError, "config restore directory changed")
        descriptor = os.open(binding.basename, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0) | portable.BINARY_FLAG,
                             0o600, dir_fd=binding.parent_fd)
        offset = 0
        while offset < len(payload):
            count = os.write(descriptor, payload[offset:])
            if count <= 0:
                raise OSError("config restore evidence write failed")
            offset += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        bound_snapshot, actual, _mode = io.read_file_state(binding.basename, parent_fd=binding.parent_fd,
            missing_allowed=False, refused=ValueError, non_regular_refusal="config restore evidence unsafe",
            changed_refusal="config restore evidence changed", wrap_read_error=True)
        io.assert_path_parent(binding, ValueError, "config restore directory changed")
        if bound_snapshot is None or actual != payload:
            raise ValueError("config restore evidence changed")
        return bound_snapshot
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        binding.release()


def _legacy_receipt(root: Path, record: dict[str, Any], preimage: plugin.PluginConfigPreimage,
                    ownership: plugin.PluginOwnership, before: bytes) -> dict[str, Any]:
    """Replay the pinned historical writer in disposable private scratch only."""
    binding = plugin.resolve_runtime_binding(checkout_root=root, hermes_home=record["home"], profile_id=None)
    if binding.python_identity != record["python_identity"]:
        raise ValueError("legacy observer runtime binding changed")
    desired, enabled = plugin._expected_enabled_config(plugin._load_config_mapping(before))
    if enabled:
        desired, _ = plugin._expected_enabled_config(plugin._load_config_mapping(before), preserve_enabled_lists=True)
    with tempfile.TemporaryDirectory(prefix="hfc-observer-proof-") as scratch:
        shadow = replace(preimage, state_dir=Path(scratch))
        plan = plugin._plan_enabled_config_bytes(binding, shadow, before, desired, enabled,
                                                 historical_post_sha256=ownership.post_sha256)
        plugin._verify_config_plan_source(plan)
        if plugin._sha256(plan.postimage) != ownership.post_sha256 or not plan.matches_expected(desired):
            raise ValueError("legacy observer historical postimage is unverified")
        receipt = plugin_config_delta.make_receipt(before, plan.postimage)
        plugin._verify_config_plan_source(plan)
        return receipt


def inspect(root: Path, *, home: Path | None = None, allow_config_drift: bool = False) -> tuple[
    dict[str, Any], bytes, plugin.PluginConfigPreimage, plugin.PluginOwnership, bytes
] | None:
    """Read-only config/evidence check; a missing companion means pre-migration."""
    raw = _read(root / MARKER)
    if raw is None:
        return None
    record = _decode(raw)
    selected = Path(record["home"])
    for directory in (selected, selected / ".hermes_feishu_card",
                      selected / ".hermes_feishu_card" / "install"):
        plugin._require_directory(directory, "legacy observer evidence directory")
    if home is not None and selected != Path(home).resolve():
        raise ValueError("legacy observer HOME binding changed")
    ownership = plugin.PluginOwnership(**record["plugin_config"])
    preimage = _preimage(selected, ownership)
    before = plugin._read_private_backup(preimage)
    if plugin._sha256(before) != ownership.pre_sha256:
        raise ValueError("legacy observer preimage mismatch")
    journal = json.loads(_read(preimage.journal_path) or b"null")
    receipt = record.get("config_delta")
    expected_journal = {"protocol": "hfc-plugin-config-preimage-v1", "phase": "installed",
                        **ownership.sanitized()}
    if receipt is not None:
        expected_journal["config_delta"] = receipt
        plugin_config_delta.verify_receipt(before, receipt)
    if "adapter" in record:
        expected_journal["adapter"] = record["adapter"]
    if journal != expected_journal:
        raise ValueError("legacy observer journal mismatch")
    config = plugin._require_regular_file(selected / "config.yaml", "Hermes config").read_bytes()
    try:
        plugin_config_delta.validate_preimage(config)
        progress, _ticket, _ticket_snapshot = _restore_progress(root, selected, raw, preimage, config)
        if receipt is not None:
            plugin_config_delta.classify(before, config, receipt, allow_restored=progress == "restored")
        elif plugin._sha256(config) not in (ownership.pre_sha256, ownership.post_sha256):
            if not allow_config_drift:
                receipt = _legacy_receipt(root, record, preimage, ownership, before)
                plugin_config_delta.classify(before, config, receipt, allow_restored=progress == "restored")
                # This proof is transient; inspect never upgrades the stored marker.
                record = {**record, "config_delta": receipt}
    except plugin_config_delta.ConfigDeltaRefused as exc:
        raise ObserverConfigChanged("Hermes config ownership is ambiguous or changed") from exc
    return record, raw, preimage, ownership, config


@dataclass
class Registration:
    binding: plugin.HermesRuntimeBinding

    def finish(self) -> None:
        pass  # All fallible config/marker work precedes the source transaction.

    def rollback(self) -> None:
        restore(self.binding.checkout_root, home=self.binding.hermes_home)


def prepare(binding: plugin.HermesRuntimeBinding, *, adoption: Any = None) -> Registration | None:
    if adoption is not None:
        from .recovery import _root_lock
        with _root_lock(binding.checkout_root):
            return _prepare(binding, adoption=adoption)
    return _prepare(binding, adoption=None)


def _prepare(binding: plugin.HermesRuntimeBinding, *, adoption: Any = None) -> Registration | None:
    existing = inspect(binding.checkout_root, home=binding.hermes_home)
    if existing is not None:
        if existing[0]["python_identity"] != binding.python_identity:
            raise ValueError("legacy observer runtime binding changed")
        if _config_state(existing, binding.checkout_root) != "installed":
            raise ValueError("legacy observer restore incomplete; run restore first")
        return None
    probe = plugin.probe_plugin_entrypoint(binding)
    if probe.status != "verified":
        raise ValueError("legacy observer entry point is not verified")
    plugin_config_delta.validate_preimage(plugin._read_bound_config(binding))
    preimage = plugin.prepare_plugin_config(binding)
    prepared_backup = io.read_path_snapshot(preimage.backup_path, ValueError, "plugin config backup changed")
    prepared_journal = io.read_path_snapshot(preimage.journal_path, ValueError, "plugin config journal changed")
    ownership = None
    marker = binding.checkout_root / MARKER
    raw = None
    marker_snapshot = None
    try:
        if adoption is None:
            ownership = plugin.enable_plugin(binding, preimage)
            adapter = None
        else:
            from . import legacy_9b904, pm_dependency
            if type(adoption) is not pm_dependency.Registration:
                raise ValueError("legacy adoption admission evidence is invalid")
            if adoption.source_family != legacy_9b904.ADAPTER_ID:
                raise ValueError("legacy adoption source contract is invalid")
            ownership = plugin._adopt_existing_plugin_config(binding, preimage, adoption.verify_adoption)
            adapter = legacy_9b904.ADAPTER_ID
        current_journal = ownership.journal_identity
        def validate_postimage() -> None:
            if (ownership is None or ownership.post_identity is None or ownership.post_parent_identity is None
                    or ownership.backup_identity is None or ownership.state_parent_identity is None
                    or current_journal is None):
                raise ValueError("official config postimage identity is unavailable")
            io.assert_path_snapshot(binding.config_path, ownership.post_identity, ValueError,
                                    "official config postimage changed")
            if io.read_directory_identity(binding.config_path.parent, ValueError,
                    "official config directory changed") != ownership.post_parent_identity:
                raise ValueError("official config directory changed")
            io.assert_path_snapshot(preimage.backup_path, ownership.backup_identity, ValueError,
                                    "official config preimage changed")
            io.assert_path_snapshot(preimage.journal_path, current_journal, ValueError,
                                    "official config journal changed")
            if io.read_directory_identity(preimage.state_dir, ValueError,
                    "official config evidence directory changed") != ownership.state_parent_identity:
                raise ValueError("official config evidence directory changed")
        validate_postimage()
        postimage = plugin._read_bound_config(binding)
        if plugin._sha256(postimage) != ownership.post_sha256:
            raise ValueError("official config postimage changed")
        validate_postimage()
        receipt = plugin_config_delta.make_receipt(plugin._read_private_backup(preimage), postimage)
        validate_postimage()
        current_journal = plugin.mark_plugin_config_installed(preimage, ownership, config_delta=receipt,
                                                              adapter=adapter)
        validate_postimage()
        raw = (json.dumps({"protocol": ADOPTION_PROTOCOL if adapter else PROTOCOL, "home": str(binding.hermes_home),
                           "python_identity": binding.python_identity,
                           "plugin_config": ownership.sanitized(), "config_delta": receipt,
                           **({"adapter": adapter} if adapter else {})}, sort_keys=True) + "\n").encode()
        validate_postimage()
        plugin._write_private_new(marker, raw)
        marker_snapshot = io.read_path_snapshot(marker, ValueError, "legacy observer record changed")
        if marker_snapshot[2] != plugin._sha256(raw):
            raise ValueError("legacy observer record changed")
        validate_postimage()
        return Registration(binding)
    except plugin.PluginAdoptionUncertain:
        # The journal may already contain the no-op adoption evidence. Do not
        # replace its identity with the earlier prepared snapshot or hide cause.
        raise
    except BaseException as original_error:
        if ownership is not None:
            # Never roll back a postimage that has been concurrently replaced,
            # even if the replacement happens to contain the same bytes.
            validate_postimage()
            plugin.restore_plugin_config(binding, preimage, ownership)
        # Retain the preimage if enable failed after an uncertain config write.
        if plugin._sha256(plugin._read_bound_config(binding)) == preimage.pre_sha256:
            backup = ownership.backup_identity if ownership is not None else prepared_backup
            journal = current_journal if ownership is not None else prepared_journal
            if backup is None or journal is None:
                raise ValueError("plugin config evidence identity is unavailable") from original_error
            io.assert_path_snapshot(preimage.backup_path, backup, ValueError, "plugin config backup changed")
            io.assert_path_snapshot(preimage.journal_path, journal, ValueError, "plugin config journal changed")
            removal = [preimage.journal_path, preimage.backup_path]
            identities: dict[Path, io.FileIdentity | None] = {
                preimage.journal_path: journal, preimage.backup_path: backup}
            if _read(marker) is not None:
                if marker_snapshot is None or raw is None:
                    raise ValueError("legacy observer record identity is unavailable") from original_error
                removal.insert(0, marker)
                identities[marker] = marker_snapshot
            directories: dict[Path, io.DirectoryIdentity | None] = {
                path.parent: io.read_directory_identity(path.parent, ValueError, "plugin config directory changed")
                for path in removal}
            if io._cli_dirfd_binding_supported():
                io._remove_verified_restore_evidence(removal, identities, directories)
            elif os.name == "nt":
                portable._remove_targets_portably(removal, expected_identities=identities,
                    expected_directories=directories, recovery_contents={path: path.read_text() for path in removal})
            else:
                raise ValueError("secure plugin config cleanup is unavailable") from original_error
        raise


def _config_state(evidence: tuple[dict[str, Any], bytes, plugin.PluginConfigPreimage,
                                  plugin.PluginOwnership, bytes], root: Path) -> str:
    record, raw, preimage, ownership, config = evidence
    before = plugin._read_private_backup(preimage)
    receipt = record.get("config_delta")
    progress, _ticket, _ticket_snapshot = _restore_progress(root, Path(record["home"]), raw, preimage, config)
    if progress == "restored":
        if receipt is not None:
            plugin_config_delta.classify(before, config, receipt, allow_restored=True)
        return "restored"
    if receipt is not None:
        return plugin_config_delta.classify(before, config, receipt)
    if plugin._sha256(config) == ownership.post_sha256:
        return "installed"
    if plugin._sha256(config) == ownership.pre_sha256:
        return "restored"
    raise ObserverConfigChanged("Hermes config changed since legacy observer install; preview integrity rebind-legacy")


def restore_owned_config(binding: plugin.HermesRuntimeBinding,
                         preimage: plugin.PluginConfigPreimage,
                         ownership: plugin.PluginOwnership) -> tuple[bytes, io.FileIdentity, io.FileIdentity | None]:
    """Restore only the verified delta, binding all evidence to the commit."""
    marker = binding.checkout_root / MARKER
    paths = (binding.config_path, marker, preimage.journal_path, preimage.backup_path)
    snapshots = {path: io.read_path_snapshot(path, ValueError, "config evidence changed") for path in paths}
    ticket_path = _restore_ticket_path(preimage)
    try:
        ticket_path.lstat()
    except FileNotFoundError:
        pass
    else:
        snapshots[ticket_path] = io.read_path_snapshot(ticket_path, ValueError, "config restore evidence changed")
    directories = {path.parent: io.read_directory_identity(path.parent, ValueError, "config directory changed")
                   for path in paths}
    evidence = inspect(binding.checkout_root, home=binding.hermes_home)
    if evidence is None or evidence[2] != preimage or evidence[3] != ownership:
        raise ValueError("legacy observer ownership changed")
    record, raw, _, _, current = evidence
    if record["python_identity"] != binding.python_identity:
        raise ValueError("legacy observer runtime binding changed")
    before = plugin._read_private_backup(preimage)
    if snapshots[marker][2] != plugin._sha256(raw) or snapshots[binding.config_path][2] != plugin._sha256(current):
        raise ValueError("config evidence changed")
    def validate() -> None:
        for path, snapshot in snapshots.items():
            io.assert_path_snapshot(path, snapshot, ValueError, "config evidence changed")
        for directory, directory_identity in directories.items():
            meta = directory.lstat()
            if portable._is_reparse(meta) or io.read_directory_identity(directory, ValueError, "config directory changed") != directory_identity:
                raise ValueError("config directory changed")

    progress, pending_ticket, pending_snapshot = _restore_progress(binding.checkout_root, binding.hermes_home, raw, preimage, current)
    if pending_snapshot != snapshots.get(ticket_path):
        raise ValueError("config restore evidence changed")
    if _config_state(evidence, binding.checkout_root) == "restored":
        validate()
        return current, snapshots[binding.config_path], pending_snapshot
    if progress == "pending":
        if pending_ticket is None or pending_snapshot is None:
            raise ValueError("config restore evidence is invalid")
        validate()
        _discard_pending_ticket(binding, preimage, pending_ticket, pending_snapshot)
        snapshots.pop(ticket_path)
        validate()
    receipt = record.get("config_delta")
    if plugin._sha256(current) == ownership.post_sha256:
        replacement = before
    elif receipt is not None:
        replacement = plugin_config_delta.restore_bytes(before, current, receipt)
    else:
        raise ObserverConfigChanged("Hermes config changed since legacy observer install; preview integrity rebind-legacy")
    if replacement == current:
        validate()
        return current, snapshots[binding.config_path], None

    def create_ticket(after: io.FileIdentity, stage_name: str) -> None:
        validate()
        value = {"protocol": RESTORE_PROTOCOL, "before": list(snapshots[binding.config_path]),
                 "after": list(after), "marker": list(snapshots[marker]),
                 "journal": list(snapshots[preimage.journal_path]),
                 "backup": list(snapshots[preimage.backup_path]),
                 "config_parent": list(directories[binding.config_path.parent]),
                 "marker_parent": list(directories[marker.parent]),
                 "state_parent": list(directories[preimage.state_dir]), "stage": stage_name}
        encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        snapshot = _write_restore_ticket(ticket_path, encoded)
        snapshots[ticket_path] = snapshot
        validate()

    if not io._cli_dirfd_binding_supported():
        if os.name != "nt":
            raise ValueError("secure config restore requires directory-relative filesystem operations")
        previous = snapshots[binding.config_path]
        after = (previous[0], previous[1], plugin._sha256(replacement))
        create_ticket(after, "")
        _portable_restore_config(paths + (ticket_path,), snapshots, directories, current, replacement)
        return replacement, after, snapshots[ticket_path]

    target = io.bind_target(binding.config_path, ValueError, staging_refusal="config directory changed",
                            parent_refusal="config directory changed", wrap_open_error=True)
    staged = None
    try:
        snapshot, payload, mode = io.read_file_state(target.basename, parent_fd=target.parent_fd,
            missing_allowed=False, refused=ValueError, non_regular_refusal="config is unsafe",
            changed_refusal="config changed", wrap_read_error=True)
        if snapshot != snapshots[binding.config_path] or payload != current:
            raise ValueError("config evidence changed")
        staged = io.stage_file(binding.config_path, replacement, binding=target, mode=mode,
            durable_text=False, refused=ValueError, staging_refusal="config stage failed",
            target_refusal="config is unsafe", stage_refusal="config stage changed")
        validate()
        stage_snapshot, stage_payload, stage_mode = io.read_file_state(staged.basename,
            parent_fd=staged.parent_fd, missing_allowed=False, refused=ValueError,
            non_regular_refusal="config stage is unsafe", changed_refusal="config stage changed",
            wrap_read_error=True)
        if (stage_snapshot is None or stage_snapshot[:2] != staged.identity
                or stage_payload != replacement or stage_mode != mode):
            raise ValueError("config stage changed")
        create_ticket(stage_snapshot, staged.basename)
        repeated, repeated_payload, repeated_mode = io.read_file_state(staged.basename,
            parent_fd=staged.parent_fd, missing_allowed=False, refused=ValueError,
            non_regular_refusal="config stage is unsafe", changed_refusal="config stage changed",
            wrap_read_error=True)
        if repeated != stage_snapshot or repeated_payload != replacement or repeated_mode != mode:
            raise ValueError("config stage changed")
        selected_stage = staged
        def final_validate() -> None:
            validate()
            io.assert_path_parent(target, ValueError, "config directory changed")
            final_stage, final_stage_payload, final_stage_mode = io.read_file_state(selected_stage.basename,
                parent_fd=selected_stage.parent_fd, missing_allowed=False, refused=ValueError,
                non_regular_refusal="config stage is unsafe", changed_refusal="config stage changed",
                wrap_read_error=True)
            if final_stage != stage_snapshot or final_stage_payload != replacement or final_stage_mode != mode:
                raise ValueError("config stage changed")
            final_target, final_payload, final_mode = io.read_file_state(target.basename,
                parent_fd=target.parent_fd, missing_allowed=False, refused=ValueError,
                non_regular_refusal="config is unsafe", changed_refusal="config changed", wrap_read_error=True)
            if final_target != snapshots[binding.config_path] or final_payload != current or final_mode != mode:
                raise ValueError("config changed")
        io.replace_stage(target, staged, check_parent=True, before_replace=final_validate)
        staged = None
        return replacement, stage_snapshot, snapshots[ticket_path]
    finally:
        if staged is not None:
            staged.cleanup()
        target.release()


def _portable_restore_config(paths: tuple[Path, ...], snapshots: dict[Path, io.FileIdentity],
                             directories: dict[Path, io.DirectoryIdentity],
                             before: bytes, replacement: bytes) -> None:
    """Hold Windows config and evidence handles exclusively through publication."""
    handles: dict[Path, int] = {}
    try:
        for path in paths:
            descriptor = portable._open_portable_exclusive_fd(path, create=False)
            handles[path] = descriptor
            opened, named = os.fstat(descriptor), path.lstat()
            expected = snapshots[path]
            if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode)
                    or portable._is_reparse(named) or (opened.st_dev, opened.st_ino) != expected[:2]
                    or (named.st_dev, named.st_ino) != expected[:2]):
                raise ValueError("config evidence changed")
            os.lseek(descriptor, 0, os.SEEK_SET)
            payload = bytearray()
            while chunk := os.read(descriptor, 64 * 1024):
                payload.extend(chunk)
            if plugin._sha256(bytes(payload)) != expected[2]:
                raise ValueError("config evidence changed")
        for directory, expected_directory in directories.items():
            meta = directory.lstat()
            if (not stat.S_ISDIR(meta.st_mode) or portable._is_reparse(meta)
                    or (meta.st_dev, meta.st_ino) != expected_directory):
                raise ValueError("config directory changed")
        target = paths[0]
        descriptor = handles[target]
        target_meta = os.fstat(descriptor)
        try:
            portable._write_portable_exclusive_bytes(descriptor, replacement, stat.S_IMODE(target_meta.st_mode))
            portable._verify_portable_exclusive_bytes(descriptor, target, snapshots[target][:2], replacement)
        except (OSError, ValueError):
            # The same exclusive handle still owns this inode. Roll back only
            # while the pathname and directory identities are unchanged.
            named = target.lstat()
            if (portable._is_reparse(named) or (named.st_dev, named.st_ino) != snapshots[target][:2]
                    or any((directory.lstat().st_dev, directory.lstat().st_ino) != expected
                           for directory, expected in directories.items())):
                raise
            portable._write_portable_exclusive_bytes(descriptor, before, stat.S_IMODE(target_meta.st_mode))
            portable._verify_portable_exclusive_bytes(descriptor, target, snapshots[target][:2], before)
            raise
    finally:
        for descriptor in handles.values():
            os.close(descriptor)


def restore(root: Path, *, home: Path | None = None,
            expected_marker: tuple[int, int, str] | None = None) -> None:
    raw = _read(root / MARKER)
    if raw is None:
        if expected_marker is not None:
            raise ValueError("legacy observer record changed before restore")
        return
    marker_snapshot = io.read_path_snapshot(root / MARKER, ValueError, "legacy observer record changed")
    if marker_snapshot[2] != plugin._sha256(raw) or (expected_marker is not None and marker_snapshot != expected_marker):
        raise ValueError("legacy observer record changed before restore")
    if _decode(raw)["protocol"] == ADOPTION_PROTOCOL:
        from .recovery import _root_lock
        with _root_lock(root):
            return _restore(root, home=home, expected_marker=marker_snapshot)
    return _restore(root, home=home, expected_marker=marker_snapshot)


def _restore(root: Path, *, home: Path | None, expected_marker: tuple[int, int, str]) -> None:
    evidence = inspect(root, home=home)
    if evidence is None:
        raise ValueError("legacy observer record changed before restore")
    record, raw, preimage, ownership, config = evidence
    io.assert_path_snapshot(root / MARKER, expected_marker, ValueError, "legacy observer record changed before restore")
    if expected_marker[2] != plugin._sha256(raw):
        raise ValueError("legacy observer record changed before restore")
    binding = plugin.resolve_runtime_binding(
        checkout_root=root, hermes_home=record["home"], profile_id=None)
    if binding.python_identity != record["python_identity"]:
        raise ValueError("legacy observer runtime binding changed")
    marker = root / MARKER
    removal = [marker, preimage.journal_path, preimage.backup_path]
    snapshots = {path: io.read_path_snapshot(path, ValueError, "observer evidence changed") for path in removal}
    directories = {path.parent: io.read_directory_identity(path.parent, ValueError, "observer directory changed")
                   for path in removal + [binding.config_path]}
    if snapshots[marker][2] != plugin._sha256(raw):
        raise ValueError("legacy observer record changed before cleanup")
    restored, restored_snapshot, published_ticket_snapshot = restore_owned_config(binding, preimage, ownership)
    # Config publication precedes source restoration; classify the restored
    # state on retry and retain the admission evidence until cleanup succeeds.
    if _read(root / MARKER) != raw:
        raise ValueError("legacy observer record changed before cleanup")
    if plugin._read_bound_config(binding) != restored:
        raise ValueError("Hermes config changed before cleanup")
    ticket_path = _restore_ticket_path(preimage)
    ticket_raw = _read(ticket_path)
    if ticket_raw is not None:
        progress, _ticket, ticket_snapshot = _restore_progress(root, binding.hermes_home, raw, preimage, restored)
        if progress != "restored" or ticket_snapshot is None or ticket_snapshot != published_ticket_snapshot:
            raise ValueError("config restore evidence is incomplete")
        removal.insert(1, ticket_path)
        snapshots[ticket_path] = ticket_snapshot
        if snapshots[ticket_path][2] != plugin._sha256(ticket_raw):
            raise ValueError("config restore evidence changed")
    elif published_ticket_snapshot is not None:
        raise ValueError("config restore evidence changed")
    io.assert_path_snapshot(binding.config_path, restored_snapshot, ValueError, "Hermes config changed before cleanup")
    for path, snapshot in snapshots.items():
        io.assert_path_snapshot(path, snapshot, ValueError, "observer evidence changed before cleanup")
    for directory, expected in directories.items():
        meta = directory.lstat()
        if portable._is_reparse(meta) or (meta.st_dev, meta.st_ino) != expected:
            raise ValueError("observer directory changed before cleanup")
    # Remove the marker first: any residual private evidence is then inert.
    removal_identities: dict[Path, io.FileIdentity | None] = dict(snapshots)
    removal_directories: dict[Path, io.DirectoryIdentity | None] = dict(directories)
    if io._cli_dirfd_binding_supported():
        io._remove_verified_restore_evidence(removal, removal_identities, removal_directories)
    elif os.name == "nt":
        contents = {path: path.read_bytes().decode("utf-8") for path in removal}
        portable._remove_targets_portably(removal, expected_identities=removal_identities,
            expected_directories=removal_directories, recovery_contents=contents)
    else:
        raise ValueError("secure config cleanup is unavailable")


def observer_status(root: Path, *, home: Path | None = None) -> dict[str, str]:
    """Read-only installed admission state, not proof of a running plugin."""
    try:
        (root / MARKER).lstat()
    except FileNotFoundError:
        return {"status": "not_installed", "reason": "marker_not_located"}
    except OSError:
        return {"status": "disabled", "reason": "marker_uninspectable"}
    try:
        evidence = inspect(root, home=home)
        if evidence is None:
            return {"status": "disabled", "reason": "marker_changed"}
        record, _, _, ownership, config = evidence
        if _config_state(evidence, root) != "installed":
            return {"status": "disabled", "reason": "restore_incomplete"}
        manifest = json.loads(_read(root / ".hermes_feishu_card_manifest") or b"null")
        if type(manifest) is not dict or validate_install_manifest(manifest) not in (1, 2, 4):
            return {"status": "disabled", "reason": "legacy_manifest_invalid"}
        if record["protocol"] == ADOPTION_PROTOCOL:
            from . import decomposed_ownership
            try:
                if (manifest.get("manifest_version") != 4
                        or decomposed_ownership._inspect(root, render_hooks=False)[3] != "installed"):
                    return {"status": "disabled", "reason": "legacy_source_incomplete"}
            except (OSError, UnicodeError, ValueError):
                return {"status": "disabled", "reason": "legacy_source_incomplete"}
        settings = plugin._load_config_mapping(config).get("plugins", {})
        if not isinstance(settings, dict):
            return {"status": "disabled", "reason": "plugin_config_invalid"}
        if (settings.get("isolation") == "host" or plugin.PLUGIN_KEY not in settings.get("enabled", [])
                or plugin.PLUGIN_KEY in settings.get("disabled", [])):
            return {"status": "disabled", "reason": "plugin_disabled_or_isolated"}
        return {"status": "active", "reason": "verified_install_admission"}
    except ObserverConfigChanged:
        return {"status": "disabled", "reason": "observer_config_changed"}
    except Exception:
        return {"status": "disabled", "reason": "observer_evidence_invalid"}


def process_observer_mode() -> str:
    """Unknown observer admission disables ownership; proven absence allows Hybrid."""
    from ..instance_binding import gateway_process_binding
    binding = gateway_process_binding()
    if binding is None:
        manager_source = getattr(sys.modules.get("hermes_cli.plugins"), "__file__", None)
        resolver = getattr(sys.modules.get("hermes_constants"), "get_process_hermes_home", None)
        if not manager_source:
            return "hybrid"
        try:
            root = Path(manager_source).resolve(strict=True).parent.parent
        except Exception:
            return "hybrid"
        try:
            if not callable(resolver):
                raise ValueError("process HOME unavailable")
            home = Path(resolver()).resolve(strict=True)
        except Exception:
            return "hybrid" if observer_status(root)["status"] == "not_installed" else "disabled"
        binding = root, home
    root, home = binding
    status = observer_status(root, home=home)["status"]
    return {"active": "observe", "disabled": "disabled", "not_installed": "hybrid"}[status]
