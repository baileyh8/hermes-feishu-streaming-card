from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile
from typing import Mapping

import yaml

from .runtime_python import PMRuntimeRefused, pm_managed_runtime_python


PLUGIN_KEY = "hermes-feishu-card"
PLUGIN_ENTRY_POINT = "hermes_feishu_card.hermes_plugin"
PLUGIN_DISTRIBUTION = "hermes-feishu-streaming-card"


class RuntimeBindingRefused(ValueError):
    pass


class PluginConfigRefused(ValueError):
    pass


@dataclass(frozen=True, repr=False)
class HermesRuntimeBinding:
    checkout_root: Path
    runtime_python: Path
    runtime_python_resolved: Path
    python_identity: str
    hermes_home: Path
    config_path: Path
    purelib: Path
    platlib: Path


@dataclass(frozen=True, repr=False)
class PluginEntrypointProbe:
    status: str
    reason: str
    version: str = ""
    module_origin: Path | None = None

    def sanitized(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "version": self.version,
            "module_origin_verified": self.module_origin is not None,
        }


@dataclass(frozen=True, repr=False)
class PluginConfigPreimage:
    state_dir: Path
    backup_path: Path
    journal_path: Path
    config_backup_id: str
    pre_sha256: str
    backup_sha256: str
    enabled_before: bool

    def sanitized(self) -> dict[str, object]:
        return {
            "config_backup_id": self.config_backup_id,
            "pre_sha256": self.pre_sha256,
            "backup_sha256": self.backup_sha256,
            "enabled_before": self.enabled_before,
        }


@dataclass(frozen=True, repr=False)
class PluginOwnership:
    enabled_before: bool
    added_by_hfc: bool
    pre_sha256: str
    post_sha256: str
    config_backup_id: str
    backup_sha256: str

    def sanitized(self) -> dict[str, object]:
        return {
            "enabled_before": self.enabled_before,
            "added_by_hfc": self.added_by_hfc,
            "pre_sha256": self.pre_sha256,
            "post_sha256": self.post_sha256,
            "config_backup_id": self.config_backup_id,
            "backup_sha256": self.backup_sha256,
        }


def resolve_runtime_binding(
    *,
    checkout_root: str | Path,
    hermes_home: str | Path | None,
    profile_id: str | None,
) -> HermesRuntimeBinding:
    root = _require_directory(checkout_root, "Hermes checkout")
    if hermes_home is None and profile_id:
        raise RuntimeBindingRefused(
            "--profile-id is not a Hermes home selector; pass --hermes-home"
        )
    homes = _binding_home_candidates(root, hermes_home)
    if not homes:
        raise RuntimeBindingRefused("Hermes home is required; pass --hermes-home")
    if len(homes) != 1:
        raise RuntimeBindingRefused("Hermes home is ambiguous; pass --hermes-home")
    home = _require_directory(homes[0], "Hermes home")
    config_path = _require_regular_file(home / "config.yaml", "Hermes config")

    runtime_python = _runtime_launcher(root, hermes_home=home)
    launcher_metadata = runtime_python.lstat()
    if not (
        stat.S_ISREG(launcher_metadata.st_mode)
        or stat.S_ISLNK(launcher_metadata.st_mode)
    ):
        raise RuntimeBindingRefused("Hermes runtime Python launcher is nonregular")
    try:
        resolved = runtime_python.resolve(strict=True)
        resolved_metadata = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise RuntimeBindingRefused("Hermes runtime Python launcher is invalid") from exc
    if not stat.S_ISREG(resolved_metadata.st_mode) or not os.access(resolved, os.X_OK):
        raise RuntimeBindingRefused(
            "Hermes runtime Python target must be a regular executable"
        )

    payload = _probe_runtime_identity(runtime_python)
    identity = _validate_runtime_identity(payload, runtime_python, resolved)
    expected_prefix = runtime_python.parents[1].resolve(strict=True)
    prefix = Path(identity["prefix"]).resolve(strict=False)
    if prefix != expected_prefix:
        raise RuntimeBindingRefused("Hermes runtime Python prefix does not match venv")
    purelib = Path(identity["purelib"]).resolve(strict=False)
    platlib = Path(identity["platlib"]).resolve(strict=False)
    if not _is_within(purelib, prefix) or not _is_within(platlib, prefix):
        raise RuntimeBindingRefused("Hermes runtime site-packages escapes venv prefix")

    link_target = ""
    if stat.S_ISLNK(launcher_metadata.st_mode):
        try:
            link_target = os.readlink(runtime_python)
        except OSError as exc:
            raise RuntimeBindingRefused("Hermes runtime Python symlink is unreadable") from exc
    canonical = json.dumps(
        {
            "domain": "hfc-hermes-runtime-binding-v1",
            "launcher": [launcher_metadata.st_dev, launcher_metadata.st_ino],
            "link_target": link_target,
            "resolved": [resolved_metadata.st_dev, resolved_metadata.st_ino],
            "prefix": str(prefix),
            "purelib": str(purelib),
            "platlib": str(platlib),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    python_identity = "sha256:" + hashlib.sha256(canonical).hexdigest()
    return HermesRuntimeBinding(
        checkout_root=root,
        runtime_python=runtime_python,
        runtime_python_resolved=resolved,
        python_identity=python_identity,
        hermes_home=home,
        config_path=config_path,
        purelib=purelib,
        platlib=platlib,
    )


def probe_plugin_entrypoint(
    binding: HermesRuntimeBinding,
    *,
    expected_version: str | None = None,
) -> PluginEntrypointProbe:
    if type(binding) is not HermesRuntimeBinding:
        return PluginEntrypointProbe("failed", "runtime binding is invalid")
    if expected_version is None:
        from hermes_feishu_card import __version__

        expected_version = __version__
    if type(expected_version) is not str or not expected_version:
        return PluginEntrypointProbe("failed", "expected version is invalid")
    try:
        payload = _run_entrypoint_probe(binding)
    except Exception:
        return PluginEntrypointProbe("failed", "entry point probe failed")
    if type(payload) is not dict or set(payload) != {"candidates", "module_origin"}:
        return PluginEntrypointProbe("failed", "entry point probe shape is invalid")
    candidates = payload.get("candidates")
    if type(candidates) is not list or len(candidates) != 1:
        return PluginEntrypointProbe("failed", "entry point requires exactly one candidate")
    candidate = candidates[0]
    expected_fields = {"name", "value", "distribution", "version"}
    if (
        type(candidate) is not dict
        or set(candidate) != expected_fields
        or not all(type(candidate[field]) is str for field in expected_fields)
    ):
        return PluginEntrypointProbe("failed", "entry point candidate is invalid")
    if candidate["name"] != PLUGIN_KEY or candidate["value"] != PLUGIN_ENTRY_POINT:
        return PluginEntrypointProbe("failed", "entry point identity mismatch")
    if candidate["distribution"] != PLUGIN_DISTRIBUTION:
        return PluginEntrypointProbe("failed", "entry point distribution mismatch")
    if candidate["version"] != expected_version:
        return PluginEntrypointProbe("failed", "entry point version mismatch")
    origin_value = payload.get("module_origin")
    if type(origin_value) is not str or not origin_value:
        return PluginEntrypointProbe("failed", "entry point module origin is missing")
    origin = Path(origin_value)
    if not origin.is_absolute():
        return PluginEntrypointProbe("failed", "entry point module origin is not absolute")
    origin = origin.resolve(strict=False)
    if not (
        _is_within(origin, binding.purelib)
        or _is_within(origin, binding.platlib)
    ):
        return PluginEntrypointProbe(
            "failed", "entry point import origin is not runtime site-packages"
        )
    return PluginEntrypointProbe(
        "verified",
        "verified",
        version=candidate["version"],
        module_origin=origin,
    )


def prepare_plugin_config(binding: HermesRuntimeBinding) -> PluginConfigPreimage:
    if type(binding) is not HermesRuntimeBinding:
        raise PluginConfigRefused("runtime binding is invalid")
    config_bytes = _read_bound_config(binding)
    config_mapping = _load_config_mapping(config_bytes)
    _expected_config, enabled_before = _expected_enabled_config(config_mapping)
    pre_sha256 = _sha256(config_bytes)
    state_root = binding.hermes_home / ".hermes_feishu_card"
    state_dir = state_root / "install"
    _ensure_private_directory(state_root)
    _ensure_private_directory(state_dir)
    config_backup_id = "hfc-config-preimage-" + secrets.token_hex(16)
    backup_path = state_dir / f"{config_backup_id}.yaml"
    journal_path = state_dir / f"{config_backup_id}.json"
    backup_sha256 = _sha256(config_bytes)
    journal = json.dumps(
        {
            "protocol": "hfc-plugin-config-preimage-v1",
            "config_backup_id": config_backup_id,
            "pre_sha256": pre_sha256,
            "backup_sha256": backup_sha256,
            "phase": "prepared",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    try:
        _write_private_new(backup_path, config_bytes)
        _write_private_new(journal_path, journal)
    except Exception:
        for path in (journal_path, backup_path):
            try:
                path.unlink()
            except OSError:
                pass
        raise
    return PluginConfigPreimage(
        state_dir=state_dir,
        backup_path=backup_path,
        journal_path=journal_path,
        config_backup_id=config_backup_id,
        pre_sha256=pre_sha256,
        backup_sha256=backup_sha256,
        enabled_before=enabled_before,
    )


def enable_plugin(
    binding: HermesRuntimeBinding,
    preimage: PluginConfigPreimage,
) -> PluginOwnership:
    if type(binding) is not HermesRuntimeBinding or type(preimage) is not PluginConfigPreimage:
        raise PluginConfigRefused("plugin config ownership input is invalid")
    before_bytes = _read_bound_config(binding)
    if _sha256(before_bytes) != preimage.pre_sha256:
        raise PluginConfigRefused("Hermes config changed after preimage")
    before = _load_config_mapping(before_bytes)
    expected, enabled_before = _expected_enabled_config(before)
    try:
        return_code = _run_official_enable(binding)
    except Exception as exc:
        raise PluginConfigRefused("official plugin enable failed") from exc
    after_bytes = _read_bound_config(binding)
    after_sha256 = _sha256(after_bytes)
    if return_code != 0:
        _restore_exact_config(binding, before_bytes, expected_sha256=after_sha256)
        raise PluginConfigRefused("official plugin enable failed")
    try:
        after = _load_config_mapping(after_bytes)
    except PluginConfigRefused:
        _restore_exact_config(binding, before_bytes, expected_sha256=after_sha256)
        raise
    if after != expected:
        _restore_exact_config(binding, before_bytes, expected_sha256=after_sha256)
        raise PluginConfigRefused("official plugin enable changed unrelated config")
    added_by_hfc = after_bytes != before_bytes
    ownership = PluginOwnership(
        enabled_before=enabled_before,
        added_by_hfc=added_by_hfc,
        pre_sha256=preimage.pre_sha256,
        post_sha256=after_sha256,
        config_backup_id=preimage.config_backup_id,
        backup_sha256=preimage.backup_sha256,
    )
    _rewrite_private_journal(preimage, phase="plugin_enabled", ownership=ownership)
    return ownership


def restore_plugin_config(
    binding: HermesRuntimeBinding,
    preimage: PluginConfigPreimage,
    ownership: PluginOwnership,
) -> None:
    if (
        type(binding) is not HermesRuntimeBinding
        or type(preimage) is not PluginConfigPreimage
        or type(ownership) is not PluginOwnership
        or ownership.config_backup_id != preimage.config_backup_id
        or ownership.pre_sha256 != preimage.pre_sha256
        or ownership.backup_sha256 != preimage.backup_sha256
    ):
        raise PluginConfigRefused("plugin config ownership input is invalid")
    current = _read_bound_config(binding)
    if _sha256(current) != ownership.post_sha256:
        raise PluginConfigRefused("Hermes config changed since plugin enable")
    backup = _read_private_backup(preimage)
    _restore_exact_config(binding, backup, expected_sha256=ownership.post_sha256)


def mark_plugin_config_installed(
    preimage: PluginConfigPreimage,
    ownership: PluginOwnership,
) -> None:
    if (
        type(preimage) is not PluginConfigPreimage
        or type(ownership) is not PluginOwnership
        or preimage.config_backup_id != ownership.config_backup_id
    ):
        raise PluginConfigRefused("plugin config ownership input is invalid")
    _rewrite_private_journal(preimage, phase="installed", ownership=ownership)


def mark_plugin_config_prepared(preimage: PluginConfigPreimage) -> None:
    if type(preimage) is not PluginConfigPreimage:
        raise PluginConfigRefused("plugin config ownership input is invalid")
    value = json.dumps(
        {
            "protocol": "hfc-plugin-config-preimage-v1",
            "phase": "prepared",
            **preimage.sanitized(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    _atomic_replace_bytes(preimage.journal_path, value, 0o600)


def cleanup_plugin_config_preimage(preimage: PluginConfigPreimage) -> None:
    if type(preimage) is not PluginConfigPreimage:
        raise PluginConfigRefused("plugin config ownership input is invalid")
    backup = _read_private_backup(preimage)
    if _sha256(backup) != preimage.pre_sha256:
        raise PluginConfigRefused("plugin config backup hash mismatch")
    snapshots = {}
    for path in (preimage.backup_path, preimage.journal_path):
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise PluginConfigRefused("plugin config evidence is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PluginConfigRefused("plugin config evidence is unsafe")
        snapshots[path] = (metadata.st_dev, metadata.st_ino)
    for path, identity in snapshots.items():
        current = path.lstat()
        if (current.st_dev, current.st_ino) != identity:
            raise PluginConfigRefused("plugin config evidence changed")
    for path in (preimage.journal_path, preimage.backup_path):
        path.unlink()


def _expected_enabled_config(before: Mapping[str, object]) -> tuple[dict[str, object], bool]:
    expected = json.loads(json.dumps(before))
    plugins = expected.get("plugins")
    if plugins is None:
        plugins = {}
        expected["plugins"] = plugins
    if type(plugins) is not dict:
        raise PluginConfigRefused("plugins config must be a mapping")
    enabled = plugins.get("enabled", [])
    disabled = plugins.get("disabled", [])
    entries = plugins.get("entries", {})
    if (
        type(enabled) is not list
        or any(type(item) is not str for item in enabled)
        or type(disabled) is not list
        or any(type(item) is not str for item in disabled)
        or type(entries) is not dict
    ):
        raise PluginConfigRefused("plugins config shape is invalid")
    enabled_before = PLUGIN_KEY in enabled and PLUGIN_KEY not in disabled
    plugins["enabled"] = sorted(set(enabled) | {PLUGIN_KEY})
    plugins["disabled"] = sorted(
        set(disabled) - {PLUGIN_KEY, "hermes_feishu_card"}
    )
    entry = entries.get(PLUGIN_KEY)
    if entry is None:
        entry = {}
        entries[PLUGIN_KEY] = entry
    if type(entry) is not dict:
        raise PluginConfigRefused("plugin entry config must be a mapping")
    entry["allow_tool_override"] = False
    plugins["entries"] = entries
    return expected, enabled_before


def _run_official_enable(binding: HermesRuntimeBinding) -> int:
    try:
        completed = subprocess.run(
            [
                str(binding.runtime_python),
                "-I",
                "-m",
                "hermes_cli.main",
                "plugins",
                "enable",
                PLUGIN_KEY,
                "--no-allow-tool-override",
            ],
            cwd=str(binding.checkout_root),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            env={
                "HOME": str(binding.hermes_home),
                "HERMES_HOME": str(binding.hermes_home),
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PluginConfigRefused("official plugin enable failed") from exc
    return completed.returncode


def _read_bound_config(binding: HermesRuntimeBinding) -> bytes:
    config_path = _require_regular_file(binding.config_path, "Hermes config")
    try:
        value = config_path.read_bytes()
    except OSError as exc:
        raise PluginConfigRefused("Hermes config could not be read") from exc
    if len(value) > 2 * 1024 * 1024:
        raise PluginConfigRefused("Hermes config is too large")
    return value


def _load_config_mapping(value: bytes) -> dict[str, object]:
    try:
        parsed = yaml.safe_load(value.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise PluginConfigRefused("Hermes config is invalid YAML") from exc
    if parsed is None:
        return {}
    if type(parsed) is not dict or not all(type(key) is str for key in parsed):
        raise PluginConfigRefused("Hermes config must be a string-keyed mapping")
    return parsed


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PluginConfigRefused("private config state directory is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PluginConfigRefused("private config state path is unsafe")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o077:
        try:
            path.chmod(0o700)
        except OSError as exc:
            raise PluginConfigRefused("private config state permissions are unsafe") from exc


def _write_private_new(path: Path, value: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as file:
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
    finally:
        os.close(descriptor)
    path.chmod(0o600)


def _rewrite_private_journal(
    preimage: PluginConfigPreimage,
    *,
    phase: str,
    ownership: PluginOwnership,
) -> None:
    value = json.dumps(
        {
            "protocol": "hfc-plugin-config-preimage-v1",
            "phase": phase,
            **ownership.sanitized(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    _atomic_replace_bytes(preimage.journal_path, value, 0o600)


def _read_private_backup(preimage: PluginConfigPreimage) -> bytes:
    try:
        metadata = preimage.backup_path.lstat()
        value = preimage.backup_path.read_bytes()
    except OSError as exc:
        raise PluginConfigRefused("plugin config backup is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PluginConfigRefused("plugin config backup is unsafe")
    if _sha256(value) != preimage.backup_sha256:
        raise PluginConfigRefused("plugin config backup hash mismatch")
    return value


def _restore_exact_config(
    binding: HermesRuntimeBinding,
    value: bytes,
    *,
    expected_sha256: str,
) -> None:
    current = _read_bound_config(binding)
    if _sha256(current) != expected_sha256:
        raise PluginConfigRefused("Hermes config changed; refusing to overwrite")
    try:
        mode = stat.S_IMODE(binding.config_path.lstat().st_mode)
    except OSError as exc:
        raise PluginConfigRefused("Hermes config identity is unavailable") from exc
    _atomic_replace_bytes(binding.config_path, value, mode)


def _atomic_replace_bytes(path: Path, value: bytes, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as file:
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_path, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _binding_home_candidates(
    checkout_root: Path,
    explicit_home: str | Path | None,
) -> tuple[Path, ...]:
    if explicit_home is not None:
        return (Path(explicit_home).expanduser().absolute(),)
    candidates: list[Path] = []
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        candidates.append(Path(env_home).expanduser().absolute())
    parent = checkout_root.parent
    if (parent / "config.yaml").exists():
        candidates.append(parent)
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def _runtime_launcher(root: Path, *, hermes_home: Path | None = None) -> Path:
    try:
        managed = pm_managed_runtime_python(root, hermes_home=hermes_home)
    except PMRuntimeRefused as exc:
        raise RuntimeBindingRefused(str(exc)) from exc
    if managed is not None:
        return managed
    candidates = (
        root / ".venv" / "bin" / "python",
        root / "venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
        root / "venv" / "Scripts" / "python.exe",
    )
    available = []
    for candidate in candidates:
        try:
            candidate.lstat()
        except OSError:
            continue
        available.append(candidate)
    if len(available) != 1:
        raise RuntimeBindingRefused(
            "Hermes runtime Python is missing or ambiguous in the selected checkout"
        )
    return available[0]


def _require_directory(path: str | Path, label: str) -> Path:
    candidate = Path(path).expanduser().absolute()
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise RuntimeBindingRefused(f"{label} is missing") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeBindingRefused(f"{label} must be a non-symlink directory")
    return candidate


def _require_regular_file(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeBindingRefused(f"{label} is missing") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeBindingRefused(f"{label} must be a non-symlink regular file")
    return path


def _probe_runtime_identity(launcher: Path) -> dict[str, object]:
    code = (
        "import json,sys,sysconfig;"
        "print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,"
        "'base_prefix':sys.base_prefix,'purelib':sysconfig.get_paths()['purelib'],"
        "'platlib':sysconfig.get_paths()['platlib']},sort_keys=True,separators=(',',':')))"
    )
    try:
        completed = subprocess.run(
            [str(launcher), "-I", "-c", code],
            cwd=str(launcher.parents[2]),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            env={"HOME": str(launcher.parents[2])},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeBindingRefused("Hermes runtime Python probe failed") from exc
    if completed.returncode != 0:
        raise RuntimeBindingRefused("Hermes runtime Python probe failed")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeBindingRefused("Hermes runtime Python probe returned invalid JSON") from exc
    if type(value) is not dict:
        raise RuntimeBindingRefused("Hermes runtime Python probe returned invalid JSON")
    return value


def _validate_runtime_identity(
    value: object,
    launcher: Path,
    resolved: Path,
) -> dict[str, str]:
    fields = {"executable", "prefix", "base_prefix", "purelib", "platlib"}
    if (
        type(value) is not dict
        or set(value) != fields
        or not all(type(value[field]) is str and value[field] for field in fields)
    ):
        raise RuntimeBindingRefused("Hermes runtime Python identity is invalid")
    executable = Path(value["executable"]).expanduser()
    try:
        executable_resolved = executable.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RuntimeBindingRefused("Hermes runtime Python executable is invalid") from exc
    if executable_resolved != resolved:
        raise RuntimeBindingRefused("Hermes runtime Python executable identity mismatch")
    return {field: value[field] for field in fields}


def _run_entrypoint_probe(binding: HermesRuntimeBinding) -> dict[str, object]:
    code = r'''
import importlib.metadata
import importlib.util
import json

candidates = []
for entry in importlib.metadata.entry_points().select(group="hermes_agent.plugins"):
    if entry.name != "hermes-feishu-card":
        continue
    distribution = entry.dist
    candidates.append({
        "name": entry.name,
        "value": entry.value,
        "distribution": distribution.metadata.get("Name", "") if distribution else "",
        "version": distribution.version if distribution else "",
    })
spec = importlib.util.find_spec("hermes_feishu_card.hermes_plugin")
print(json.dumps({
    "candidates": candidates,
    "module_origin": spec.origin if spec is not None else "",
}, sort_keys=True, separators=(",", ":")))
'''
    try:
        completed = subprocess.run(
            [str(binding.runtime_python), "-I", "-c", code],
            cwd=str(binding.checkout_root),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            env={"HOME": str(binding.hermes_home), "HERMES_HOME": str(binding.hermes_home)},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeBindingRefused("entry point probe failed") from exc
    if completed.returncode != 0:
        raise RuntimeBindingRefused("entry point probe failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeBindingRefused("entry point probe returned invalid JSON") from exc
    if type(payload) is not dict:
        raise RuntimeBindingRefused("entry point probe returned invalid JSON")
    return payload


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
