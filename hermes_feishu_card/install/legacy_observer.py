"""Legacy plugin config ownership, separate from the existing source manifest.

The companion record never patches Hermes or edits its configuration. Configuration
changes and exact restoration belong to the official plugin admission adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
import ast
import json
from hashlib import sha256
from pathlib import Path
import re
import stat
import sys
from typing import Any

from . import plugin
from .manifest import validate_install_manifest

MARKER = ".hermes_feishu_card_observer.json"
PROTOCOL = "hfc-legacy-observer-v1"


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
    if (type(value) is not dict or set(value) != {
        "protocol", "home", "python_identity", "plugin_config"
    } or value["protocol"] != PROTOCOL or type(value["home"]) is not str
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
    return value


def _preimage(home: Path, ownership: plugin.PluginOwnership) -> plugin.PluginConfigPreimage:
    state = home / ".hermes_feishu_card" / "install"
    key = ownership.config_backup_id
    return plugin.PluginConfigPreimage(
        state, state / (key + ".yaml"), state / (key + ".json"), key,
        ownership.pre_sha256, ownership.backup_sha256, ownership.enabled_before,
    )


def inspect(root: Path, *, home: Path | None = None) -> tuple[
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
    if plugin._sha256(plugin._read_private_backup(preimage)) != ownership.pre_sha256:
        raise ValueError("legacy observer preimage mismatch")
    journal = json.loads(_read(preimage.journal_path) or b"null")
    if journal != {"protocol": "hfc-plugin-config-preimage-v1", "phase": "installed",
                   **ownership.sanitized()}:
        raise ValueError("legacy observer journal mismatch")
    config = plugin._require_regular_file(selected / "config.yaml", "Hermes config").read_bytes()
    if plugin._sha256(config) not in (ownership.pre_sha256, ownership.post_sha256):
        raise ValueError("Hermes config changed since legacy observer install")
    return record, raw, preimage, ownership, config


@dataclass
class Registration:
    binding: plugin.HermesRuntimeBinding

    def finish(self) -> None:
        pass  # All fallible config/marker work precedes the source transaction.

    def rollback(self) -> None:
        restore(self.binding.checkout_root, home=self.binding.hermes_home)


def prepare(binding: plugin.HermesRuntimeBinding) -> Registration | None:
    existing = inspect(binding.checkout_root, home=binding.hermes_home)
    if existing is not None:
        if existing[0]["python_identity"] != binding.python_identity:
            raise ValueError("legacy observer runtime binding changed")
        if plugin._sha256(existing[4]) != existing[3].post_sha256:
            raise ValueError("legacy observer restore incomplete; run restore first")
        return None
    probe = plugin.probe_plugin_entrypoint(binding)
    if probe.status != "verified":
        raise ValueError("legacy observer entry point is not verified")
    preimage = plugin.prepare_plugin_config(binding)
    ownership = None
    marker = binding.checkout_root / MARKER
    raw = None
    try:
        ownership = plugin.enable_plugin(binding, preimage)
        plugin.mark_plugin_config_installed(preimage, ownership)
        raw = (json.dumps({"protocol": PROTOCOL, "home": str(binding.hermes_home),
                           "python_identity": binding.python_identity,
                           "plugin_config": ownership.sanitized()}, sort_keys=True) + "\n").encode()
        plugin._write_private_new(marker, raw)
        return Registration(binding)
    except BaseException:
        if ownership is not None:
            plugin.restore_plugin_config(binding, preimage, ownership)
        # Retain the preimage if enable failed after an uncertain config write.
        if plugin._sha256(plugin._read_bound_config(binding)) == preimage.pre_sha256:
            if raw is not None and _read(marker) == raw:
                marker.unlink()
            plugin.cleanup_plugin_config_preimage(preimage)
        raise


def restore(root: Path, *, home: Path | None = None) -> None:
    evidence = inspect(root, home=home)
    if evidence is None:
        return
    record, raw, preimage, ownership, config = evidence
    binding = plugin.resolve_runtime_binding(
        checkout_root=root, hermes_home=record["home"], profile_id=None)
    if binding.python_identity != record["python_identity"]:
        raise ValueError("legacy observer runtime binding changed")
    if plugin._sha256(config) != ownership.pre_sha256:
        plugin.restore_plugin_config(binding, preimage, ownership)
    if _read(root / MARKER) != raw:
        raise ValueError("legacy observer record changed before cleanup")
    # Remove the admission marker before its evidence; a leftover preimage is inert.
    (root / MARKER).unlink()
    plugin.cleanup_plugin_config_preimage(preimage)


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
        _, _, _, ownership, config = evidence
        if plugin._sha256(config) != ownership.post_sha256:
            return {"status": "disabled", "reason": "restore_incomplete"}
        manifest = json.loads(_read(root / ".hermes_feishu_card_manifest") or b"null")
        if type(manifest) is not dict or validate_install_manifest(manifest) not in (1, 2, 4):
            return {"status": "disabled", "reason": "legacy_manifest_invalid"}
        settings = plugin._load_config_mapping(config).get("plugins", {})
        if not isinstance(settings, dict):
            return {"status": "disabled", "reason": "plugin_config_invalid"}
        if (settings.get("isolation") == "host" or plugin.PLUGIN_KEY not in settings.get("enabled", [])
                or plugin.PLUGIN_KEY in settings.get("disabled", [])):
            return {"status": "disabled", "reason": "plugin_disabled_or_isolated"}
        return {"status": "active", "reason": "verified_install_admission"}
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
