"""Pinned official Hermes image acceptance for HFC.

The image receives an ordinary wheel, a private Hermes home and this script.
The repository root is never mounted or placed on PYTHONPATH.
"""
from __future__ import annotations

import argparse
import configparser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.parser import BytesParser
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
from typing import Any, Mapping, Sequence
import zipfile


REPO = "nousresearch/hermes-agent"
HERMES_ROOT = "/opt/hermes"
HERMES_HOME = "/opt/data"
CONTAINER_WHEELS = "/opt/hfc/wheels"
CONTAINER_TOOL = "/opt/hfc/tool.py"
CONTAINER_EVIDENCE = "/opt/hfc/evidence"
PLUGIN_NAME = "hermes-feishu-card"
PLUGIN_DISTRIBUTION = "hermes-feishu-streaming-card"
PLUGIN_ENTRY_POINT = "hermes_feishu_card.hermes_plugin"
MINIMAL_SEED = b"plugins: {}\nmodel: acceptance-model\n"
FULL_SEED = (
    b"plugins:\n"
    b"  enabled: []\n"
    b"  disabled: [keep-disabled]\n"
    b"  isolation: in_process\n"
    b"platform_toolsets:\n"
    b"  feishu: [terminal]\n"
    b"other: keep\n"
    b"_config_version: 50\n"
)
LEGACY_SEED = (
    b"plugins:\n"
    b"  enabled: [hermes-feishu-card]\n"
    b"  disabled: [keep-disabled]\n"
    b"  isolation: in_process\n"
    b"platform_toolsets:\n"
    b"  feishu: [terminal]\n"
    b"other: keep\n"
    b"_config_version: 51\n"
)
SOURCE_SKIP_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "playwright",
    "pm-runtime",
    "site-packages",
    "tools",
    "ui-tui",
    "web_dist",
}


@dataclass(frozen=True)
class ImageSpec:
    tag: str
    source_sha: str
    index_digest: str
    amd64_manifest: str
    layer_bytes: int

    @property
    def reference(self) -> str:
        return f"{REPO}@{self.index_digest}"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"reference": self.reference}


IMAGE_SPECS: dict[str, ImageSpec] = {
    "v0.22.1": ImageSpec(
        "v0.22.1",
        "297552a9d646119871391d202509822cd86330df",
        "sha256:861c9756ed6c558530c73d5c27860343b00c0d5efd6997a1d8ec6416c8a2b485",
        "sha256:cd9cb455576d27c9b6311de1104494ad7e221b6e68dc77e9ac2a8af661a4dc21",
        1122427033,
    ),
    "v0.22.2": ImageSpec(
        "v0.22.2",
        "d923a5eb487a17c0a68801bba9ef0f56c81040a8",
        "sha256:36099926da316742423544e055d4734777105ffe6341873af5459b957d0c2919",
        "sha256:a11574b7fe8e1631d7d88d556677dac693bc058abf558cdeb899155688c1bf05",
        1121639598,
    ),
    "v0.22.3": ImageSpec(
        "v0.22.3",
        "5b147ab036d6d19131bff8d20701b5001eab844a",
        "sha256:2f305c9bdc81731f48664586d23cb6a6f838a15ad7be1bfabe0c3c269d8e09b9",
        "sha256:1d33f6087fab79d580ffaec34ceed825e11d7bb8e803206be75a56d027c62538",
        1124276896,
    ),
    "v0.22.4": ImageSpec(
        "v0.22.4",
        "4d04303818b61bf3aa09278af48356315515965b",
        "sha256:7a69201726bc64db2072a1371557137c23312d29705cda2b8ea2a0214a50a4cf",
        "sha256:a04cf256fecbb24add4db2ae1840d6b65cf37b8dc9b9c290b0cde516e44e67fa",
        1124283305,
    ),
    "v0.22.5": ImageSpec(
        "v0.22.5",
        "bf0203a794a07aac3ff764972ab9087baa4e4f4e",
        "sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d",
        "sha256:fd822bae0a61bf023450737edbeaeea5fc6460f17cb4ee240a7e67b306ed63fb",
        1059361696,
    ),
}


class AcceptanceFailure(RuntimeError):
    """A bounded acceptance failure with no arbitrary child output."""


@dataclass(frozen=True)
class WheelInfo:
    path: Path
    sha256: str
    version: str
    package_files: Mapping[str, str]
    entry_points: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.path.name,
            "sha256": self.sha256,
            "version": self.version,
            "resource_count": len(self.package_files),
            "entry_points_sha256": sha256(self.entry_points.encode()).hexdigest(),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _safe_message(exc: BaseException, limit: int = 500) -> str:
    return " ".join(str(exc).split())[:limit]


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _regular_file(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AcceptanceFailure(f"{label} is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise AcceptanceFailure(f"{label} must be a regular file")
    return path


def _directory(path: Path, label: str, *, create: bool = False) -> Path:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AcceptanceFailure(f"{label} is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise AcceptanceFailure(f"{label} must be a real directory")
    return path


def inspect_wheel(path: Path) -> WheelInfo:
    path = _regular_file(path.expanduser(), "wheel")
    if path.suffix != ".whl":
        raise AcceptanceFailure("wheel filename must end in .whl")
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if len(names) != len(set(names)):
                raise AcceptanceFailure("wheel contains duplicate entries")
            for name in names:
                relative = PurePosixPath(name)
                if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                    raise AcceptanceFailure("wheel contains an unsafe entry")
            metadata_names = [
                name for name in names if name.endswith(".dist-info/METADATA")
            ]
            entry_names = [
                name for name in names if name.endswith(".dist-info/entry_points.txt")
            ]
            if len(metadata_names) != 1 or len(entry_names) != 1:
                raise AcceptanceFailure("wheel metadata or entry points are ambiguous")
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
            version = str(metadata.get("Version") or "")
            if str(metadata.get("Name") or "") != PLUGIN_DISTRIBUTION or not version:
                raise AcceptanceFailure("wheel distribution identity is invalid")
            expected_prefix = (
                PLUGIN_DISTRIBUTION.replace("-", "_") + "-" + version + "-"
            )
            if not path.name.startswith(expected_prefix):
                raise AcceptanceFailure("wheel filename does not match its metadata")
            if "hermes_feishu_card/hook_runtime.py" not in names:
                raise AcceptanceFailure("wheel lacks hook_runtime.py")
            package_files = {
                name: _sha256_bytes(archive.read(name))
                for name in names
                if name.startswith("hermes_feishu_card/")
            }
            entry_points = archive.read(entry_names[0]).decode("utf-8")
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise AcceptanceFailure("wheel cannot be inspected") from exc
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(entry_points)
    except configparser.Error as exc:
        raise AcceptanceFailure("wheel entry points are invalid") from exc
    plugins = (
        dict(parser.items("hermes_agent.plugins"))
        if parser.has_section("hermes_agent.plugins")
        else {}
    )
    if plugins != {PLUGIN_NAME: PLUGIN_ENTRY_POINT}:
        raise AcceptanceFailure("wheel must expose exactly one HFC plugin entry point")
    console = (
        dict(parser.items("console_scripts"))
        if parser.has_section("console_scripts")
        else {}
    )
    if console.get("hermes-feishu-card") != "hermes_feishu_card.cli:main":
        raise AcceptanceFailure("wheel console entry point is invalid")
    return WheelInfo(
        path=path,
        sha256=_sha256_file(path),
        version=version,
        package_files=package_files,
        entry_points=entry_points,
    )


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 1800,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AcceptanceFailure("child process did not complete") from exc


def _write_log(path: Path, result: subprocess.CompletedProcess[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.with_suffix(path.suffix + ".stdout")).write_text(result.stdout or "")
    (path.with_suffix(path.suffix + ".stderr")).write_text(result.stderr or "")
    _write_json(
        path.with_suffix(path.suffix + ".json"),
        {
            "returncode": result.returncode,
            "stdout_bytes": len((result.stdout or "").encode()),
            "stderr_bytes": len((result.stderr or "").encode()),
            "stdout_sha256": _sha256_bytes((result.stdout or "").encode()),
            "stderr_sha256": _sha256_bytes((result.stderr or "").encode()),
        },
    )


def _source_snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if name not in SOURCE_SKIP_PARTS and not name.startswith(".pytest")
        )
        base = Path(current)
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = base / name
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise AcceptanceFailure("source tree metadata is unavailable") from exc
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                continue
            result[path.relative_to(root).as_posix()] = _sha256_file(path)
    return result


def _source_diff(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, list[str]]:
    before_names = set(before)
    after_names = set(after)
    return {
        "added": sorted(after_names - before_names),
        "removed": sorted(before_names - after_names),
        "changed": sorted(
            name for name in before_names & after_names if before[name] != after[name]
        ),
    }


def _tree_digest(values: Mapping[str, str]) -> str:
    digest = sha256()
    for name in sorted(values):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(values[name].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _generation_names(home: Path) -> list[str]:
    installs = home / "installs"
    if not installs.is_dir():
        return []
    return sorted(
        entry.name
        for entry in installs.iterdir()
        if entry.is_dir() and not entry.is_symlink()
    )


def _count_pyc(root: Path) -> dict[str, Any]:
    pyc: list[str] = []
    caches: list[str] = []
    for current, directories, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in list(directories):
            if name == "__pycache__":
                caches.append((base / name).relative_to(root).as_posix())
        for name in files:
            if name.endswith(".pyc"):
                pyc.append((base / name).relative_to(root).as_posix())
    return {
        "pyc_count": len(pyc),
        "pycache_count": len(caches),
        "pyc_sample": sorted(pyc)[:20],
        "pycache_sample": sorted(caches)[:20],
    }


def _pyc_snapshot(root: Path) -> dict[str, Any]:
    entries: dict[str, list[int]] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        base = Path(current)
        directories[:] = sorted(directories)
        for name in sorted(files):
            if not name.endswith(".pyc"):
                continue
            path = base / name
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise AcceptanceFailure("bytecode metadata is unavailable") from exc
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                continue
            entries[path.relative_to(root).as_posix()] = [
                metadata.st_size,
                metadata.st_mtime_ns,
            ]
    digest = sha256()
    for name in sorted(entries):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entries[name][0]).encode("ascii"))
        digest.update(b":")
        digest.update(str(entries[name][1]).encode("ascii"))
        digest.update(b"\n")
    return {
        "count": len(entries),
        "manifest_sha256": digest.hexdigest(),
        "sample": sorted(entries)[:20],
    }


def _clear_pyc(root: Path) -> dict[str, Any]:
    removed_files = 0
    removed_directories = 0
    for current, _directories, files in os.walk(root, topdown=False, followlinks=False):
        base = Path(current)
        for name in files:
            if not name.endswith(".pyc"):
                continue
            path = base / name
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise AcceptanceFailure("bytecode cleanup metadata is unavailable") from exc
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                continue
            path.unlink()
            removed_files += 1
        if base.name == "__pycache__":
            try:
                base.rmdir()
                removed_directories += 1
            except OSError:
                pass
    return {"files": removed_files, "directories": removed_directories}


def _seed(path: Path, value: bytes = FULL_SEED) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _clean_container_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    allowed = {
        "CC",
        "CXX",
        "HERMES_HOME",
        "HERMES_PYTHON",
        "HERMES_RUNTIME_DIR",
        "HERMES_TUI_DIR",
        "HERMES_WEB_DIST",
        "HERMES_WRITE_SAFE_ROOT",
        "LANG",
        "LC_ALL",
        "PATH",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "XDG_RUNTIME_DIR",
    }
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    env.pop("HERMES_INSTALL_ROOT", None)
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "HOME": HERMES_HOME,
            "HERMES_HOME": HERMES_HOME,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if extra:
        env.update(extra)
    return env


def _run_logged(
    name: str,
    command: Sequence[str],
    *,
    evidence: Path,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 1800,
    expected: int = 0,
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    result = _run_command(command, cwd=cwd, env=env, timeout=timeout)
    _write_log(evidence / name, result)
    _write_json(
        evidence / f"{name}.json",
        {
            "name": name,
            "command": [str(value) for value in command[:3]],
            "returncode": result.returncode,
            "expected": expected,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        },
    )
    if result.returncode != expected:
        raise AcceptanceFailure(
            f"{name} returned {result.returncode}; expected {expected}"
        )
    return result


def _base_python() -> Path:
    return _regular_file(
        Path(HERMES_ROOT) / ".venv" / "bin" / "python", "Hermes base Python"
    )


def _base_cli() -> list[str]:
    return [str(_base_python()), "-I", "-m", "hermes_feishu_card.cli"]


def _container_wheel(env_name: str, label: str) -> Path:
    filename = os.environ.get(env_name, "")
    if not filename or Path(filename).name != filename:
        raise AcceptanceFailure(f"{label} filename is missing or unsafe")
    path = Path(CONTAINER_WHEELS) / filename
    return _regular_file(path, label)


def _current_wheel() -> Path:
    return _container_wheel("HFC_WHEEL_FILENAME", "current wheel")


def _legacy_wheel() -> Path:
    return _container_wheel("HFC_LEGACY_WHEEL_FILENAME", "legacy wheel")


def _bootstrap_pip(evidence: Path) -> None:
    python = _base_python()
    wheel = _current_wheel()
    _run_logged(
        "ensurepip",
            [str(python), "-m", "ensurepip", "--upgrade", "--no-compile"],
        evidence=evidence,
        env=_clean_container_env(),
        timeout=300,
    )
    _run_logged(
        "install-wheel",
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--no-compile",
            "--disable-pip-version-check",
            "--no-warn-script-location",
            str(wheel),
            "lark-oapi==1.6.8",
            "qrcode",
        ],
        evidence=evidence,
        env=_clean_container_env(),
        timeout=900,
    )


def _install_legacy_cli(evidence: Path) -> None:
    wheel = _legacy_wheel()
    _run_logged(
        "install-legacy-wheel",
        [
            str(_base_python()),
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--no-compile",
            "--disable-pip-version-check",
            "--no-warn-script-location",
            "--force-reinstall",
            "--no-deps",
            str(wheel),
        ],
        evidence=evidence,
        env=_clean_container_env(),
        timeout=600,
    )


def _install_current_cli(evidence: Path) -> None:
    wheel = _current_wheel()
    _run_logged(
        "install-current-wheel",
        [
            str(_base_python()),
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--no-compile",
            "--disable-pip-version-check",
            "--no-warn-script-location",
            "--force-reinstall",
            "--no-deps",
            str(wheel),
        ],
        evidence=evidence,
        env=_clean_container_env(),
        timeout=600,
    )


def _import_upstream() -> dict[str, Any]:
    if HERMES_ROOT not in sys.path:
        sys.path.insert(0, HERMES_ROOT)
    import hermes_constants  # type: ignore
    import pm.environments  # type: ignore
    import pm.filesystem  # type: ignore
    import pm.paths  # type: ignore
    import pm.plugins_state  # type: ignore
    from hermes_cli import plugins_admission  # type: ignore

    return {
        "hermes_constants": hermes_constants,
        "environments": pm.environments,
        "filesystem": pm.filesystem,
        "paths": pm.paths,
        "plugins_state": pm.plugins_state,
        "plugins_admission": plugins_admission,
    }


def _selected_runtime() -> Path:
    upstream = _import_upstream()
    return Path(upstream["environments"].committed_venv(Path(HERMES_ROOT)))


def _selected_python() -> Path:
    return _selected_runtime() / "bin" / "python"


def _sysconfig_paths() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        raw = sysconfig.get_paths().get(key)
        if not raw:
            continue
        path = Path(raw).absolute()
        result[key] = {
            "path": str(path),
            "is_symlink": path.is_symlink(),
            "resolved": str(path.resolve()),
        }
    return result


def _layout_report() -> dict[str, Any]:
    upstream = _import_upstream()
    root = Path(HERMES_ROOT)
    pm_root = Path(upstream["paths"].repo_root()).resolve()
    version_path = root / "VERSION"
    facts_path = root / "facts.json"
    version_value = ""
    if version_path.is_file() and version_path.stat().st_size <= 4096:
        version_value = version_path.read_text(errors="replace").strip()
    facts: dict[str, Any] = {}
    if facts_path.is_file() and facts_path.stat().st_size <= 64 * 1024:
        try:
            raw = json.loads(facts_path.read_text())
            if isinstance(raw, dict):
                for key in ("version", "commit", "source_sha", "channel", "revision"):
                    if key in raw and isinstance(raw[key], (str, int, bool)):
                        facts[key] = raw[key]
        except (OSError, UnicodeError, json.JSONDecodeError):
            facts = {}
    pyproject_version = ""
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and pyproject.stat().st_size <= 2 * 1024 * 1024:
        match = re.search(
            r'(?m)^version\s*=\s*["\']([^"\']+)["\']',
            pyproject.read_text(errors="replace"),
        )
        if match:
            pyproject_version = match.group(1)
    key_files: dict[str, Any] = {}
    for name in (
        "VERSION",
        "facts.json",
        "pyproject.toml",
        "pm/environments.py",
        "agent/turn_response_intake.py",
        "hermes_cli/plugins_admission.py",
    ):
        path = root / name
        if path.is_file() and not path.is_symlink():
            key_files[name] = {
                "sha256": _sha256_file(path),
                "mode": f"0o{stat.S_IMODE(path.stat().st_mode):04o}",
                "size": path.stat().st_size,
            }
    source = _source_snapshot(root)
    return {
        "repo_root": str(pm_root),
        "repo_root_expected": HERMES_ROOT,
        "hermes_install_root_present": "HERMES_INSTALL_ROOT" in os.environ,
        "pythonpath_present": "PYTHONPATH" in os.environ,
        "version_file": version_value,
        "facts": facts,
        "pyproject_version": pyproject_version,
        "key_files": key_files,
        "source_file_count": len(source),
        "source_tree_sha256": _tree_digest(source),
        "root_mode": f"0o{stat.S_IMODE(root.stat().st_mode):04o}",
        "pm_mode": f"0o{stat.S_IMODE((root / 'pm').stat().st_mode):04o}",
        "pyc": _count_pyc(root),
        "pyc_manifest_sha256": _pyc_snapshot(root)["manifest_sha256"],
        "home_pyc": _count_pyc(Path(HERMES_HOME)),
        "home_pyc_manifest_sha256": _pyc_snapshot(Path(HERMES_HOME))[
            "manifest_sha256"
        ],
        "sysconfig_paths": _sysconfig_paths(),
    }


def _verify_runtime_payload(payload: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    if payload.get("status") != "ok":
        raise AcceptanceFailure("runtime verification did not report ok")
    if payload.get("distribution_version") != expected.get("version"):
        raise AcceptanceFailure("runtime distribution version does not match wheel")
    if payload.get("wheel_sha256") != expected.get("wheel_sha256"):
        raise AcceptanceFailure("runtime direct URL does not match exact wheel")
    if payload.get("editable") is not False:
        raise AcceptanceFailure("runtime package is editable")
    if payload.get("plugin_entry_points") != 1:
        raise AcceptanceFailure("runtime must expose exactly one plugin entry point")
    if payload.get("missing_resources"):
        raise AcceptanceFailure("runtime package is missing wheel resources")
    if payload.get("extra_resources"):
        raise AcceptanceFailure("runtime package has resources absent from wheel")
    if payload.get("mismatched_resources"):
        raise AcceptanceFailure("runtime package resources differ from wheel")
    if payload.get("resource_count") != expected.get("resource_count"):
        raise AcceptanceFailure("runtime package resource count does not match wheel")
    if not payload.get("origin_under_prefix"):
        raise AcceptanceFailure("runtime package origin escapes selected prefix")
    if not payload.get("direct_url_is_file"):
        raise AcceptanceFailure("runtime direct URL is not a local wheel")
    if payload.get("direct_url_in_workspace"):
        raise AcceptanceFailure("runtime direct URL still points into PM workspace")
    if payload.get("pyc"):
        raise AcceptanceFailure("runtime verification observed bytecode files")


def _runtime_verify(
    *,
    wheel: Path,
    expected_sha256: str,
    output: Path,
    mode: str,
) -> int:
    try:
        info = inspect_wheel(wheel)
        import importlib.metadata as metadata
        from urllib.parse import urlsplit
        from urllib.request import url2pathname
        import hermes_feishu_card as package

        package_file = Path(package.__file__).resolve()
        site_packages = package_file.parent.parent
        prefix = Path(sys.prefix).resolve()
        distribution = metadata.distribution(PLUGIN_DISTRIBUTION)
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
        source = urlsplit(str(direct.get("url") or ""))
        direct_path = (
            Path(url2pathname(source.path)).resolve()
            if source.scheme == "file" and not source.netloc
            else Path("")
        )
        direct_exists = bool(direct_path) and direct_path.is_file()
        actual_sha = _sha256_file(direct_path) if direct_exists else ""
        expected_files = dict(info.package_files)
        installed_files = {
            path.relative_to(site_packages).as_posix(): _sha256_file(path)
            for path in (site_packages / "hermes_feishu_card").rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        missing = sorted(set(expected_files) - set(installed_files))
        extra = sorted(set(installed_files) - set(expected_files))
        mismatched = sorted(
            name
            for name in set(expected_files) & set(installed_files)
            if expected_files[name] != installed_files[name]
        )
        plugins = [
            entry
            for entry in metadata.entry_points(group="hermes_agent.plugins")
            if entry.name == PLUGIN_NAME
        ]
        payload = {
            "status": "ok",
            "mode": mode,
            "python": sys.executable,
            "python_version": sys.version.split()[0],
            "prefix": str(prefix),
            "origin": str(package_file),
            "origin_under_prefix": _under(package_file, prefix),
            "distribution_version": distribution.version,
            "direct_url_path": str(direct_path),
            "direct_url_is_file": source.scheme == "file" and not source.netloc,
            "direct_url_in_workspace": "workspace" in direct_path.parts,
            "editable": bool((direct.get("dir_info") or {}).get("editable")),
            "wheel_sha256": actual_sha,
            "resource_count": len(installed_files),
            "missing_resources": missing[:20],
            "extra_resources": extra[:20],
            "mismatched_resources": mismatched[:20],
            "plugin_entry_points": len(plugins),
            "pyc": _count_pyc(site_packages / "hermes_feishu_card"),
        }
        _write_json(output, payload)
        _verify_runtime_payload(
            payload,
            {
                "version": info.version,
                "wheel_sha256": expected_sha256,
                "resource_count": len(info.package_files),
            },
        )
        return 0
    except Exception as exc:
        _write_json(
            output,
            {
                "status": "failed",
                "mode": mode,
                "reason": _safe_message(exc),
            },
        )
        return 1


def _run_runtime_verify(
    python: Path,
    *,
    wheel: Path,
    expected_sha256: str,
    output: Path,
    mode: str,
) -> dict[str, Any]:
    result = _run_command(
        [
            str(python),
            "-I",
            CONTAINER_TOOL,
            "--container-verify",
            "--verify-wheel",
            str(wheel),
            "--verify-expected-sha256",
            expected_sha256,
            "--verify-output",
            str(output),
            "--verify-mode",
            mode,
        ],
        env=_clean_container_env(),
        timeout=300,
    )
    if not output.is_file():
        raise AcceptanceFailure("runtime verifier did not produce evidence")
    payload = json.loads(output.read_text())
    if result.returncode or payload.get("status") != "ok":
        raise AcceptanceFailure(
            f"runtime verifier failed: {payload.get('reason') or 'non-zero exit'}"
        )
    return payload


def _check_pm_installed(python: Path) -> bool:
    code = (
        "import sys;from pathlib import Path;"
        f"sys.path.insert(0,{HERMES_ROOT!r});"
        "from hermes_feishu_card.install import pm_dependency as p;"
        f"print(p.verify_installed(Path({HERMES_HOME!r})/'plugins'/p.NAME,Path({str(python)!r})))"
    )
    result = _run_command(
        [str(_base_python()), "-I", "-c", code],
        env=_clean_container_env(),
        timeout=120,
    )
    return (
        result.returncode == 0
        and result.stdout.strip().splitlines()[-1:] == ["True"]
    )


def _observer_status() -> dict[str, str]:
    code = (
        "import json,sys;from pathlib import Path;"
        f"sys.path.insert(0,{HERMES_ROOT!r});"
        "from hermes_feishu_card.install import legacy_observer as o;"
        f"print(json.dumps(o.observer_status(Path({HERMES_ROOT!r}),home=Path({HERMES_HOME!r}))))"
    )
    result = _run_command(
        [str(_base_python()), "-I", "-c", code],
        env=_clean_container_env(),
        timeout=60,
    )
    if result.returncode:
        return {"status": "failed", "reason": "observer_probe_failed"}
    try:
        value = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"status": "failed", "reason": "observer_probe_invalid"}
    return {
        "status": str(value.get("status") or "unknown"),
        "reason": str(value.get("reason") or "unknown"),
    }


def _binding_identity() -> str:
    code = (
        "import sys;from pathlib import Path;"
        f"sys.path.insert(0,{HERMES_ROOT!r});"
        "from hermes_feishu_card.install import plugin;"
        f"b=plugin.resolve_runtime_binding(checkout_root=Path({HERMES_ROOT!r}),hermes_home=Path({HERMES_HOME!r}),profile_id=None);"
        "print(b.python_identity)"
    )
    result = _run_command(
        [str(_base_python()), "-I", "-c", code],
        env=_clean_container_env(),
        timeout=60,
    )
    if result.returncode:
        raise AcceptanceFailure("runtime binding probe failed")
    return result.stdout.strip().splitlines()[-1]


def _config_families() -> dict[str, str]:
    code = (
        "import json,sys;from pathlib import Path;"
        f"sys.path.insert(0,{HERMES_ROOT!r});"
        "from hermes_feishu_card.install import plugin_config_bytes as p;"
        "out={};"
        "\nfor name in p.FAMILIES:\n"
        "    try:\n"
        "        p.verify_family(Path(%r),name);out[name]='match'\n"
        "    except Exception:\n"
        "        out[name]='no_match'\n"
        "print(json.dumps(out,sort_keys=True))"
    ) % HERMES_ROOT
    result = _run_command(
        [str(_base_python()), "-I", "-c", code],
        env=_clean_container_env(),
        timeout=120,
    )
    if result.returncode:
        raise AcceptanceFailure("config byte family probe failed")
    try:
        value = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise AcceptanceFailure("config byte family probe returned invalid evidence") from exc
    return {str(name): str(state) for name, state in value.items()}


def _launch_families() -> dict[str, str]:
    code = (
        "import json,sys;from pathlib import Path;"
        f"sys.path.insert(0,{HERMES_ROOT!r});"
        "from hermes_feishu_card.install import plugin_config_bytes as p;"
        "assert hasattr(p,'verify_launch_sources'),'launch guard missing';"
        "out={};"
        "\nfor name in p.FAMILIES:\n"
        "    try:\n"
        "        p.verify_launch_sources(Path(%r),family=name);out[name]='match'\n"
        "    except Exception:\n"
        "        out[name]='no_match'\n"
        "print(json.dumps(out,sort_keys=True))"
    ) % HERMES_ROOT
    result = _run_command(
        [str(_base_python()), "-I", "-c", code],
        env=_clean_container_env(),
        timeout=120,
    )
    if result.returncode:
        raise AcceptanceFailure("official launch source guard failed")
    try:
        value = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise AcceptanceFailure("launch source probe returned invalid evidence") from exc
    return {str(name): str(state) for name, state in value.items()}


def _owner_payload() -> dict[str, Any]:
    path = Path(HERMES_HOME) / "plugins" / "hfc-runtime-dependency" / ".hfc-owner.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceFailure("PM dependency ownership is unavailable") from exc
    if not isinstance(raw, dict):
        raise AcceptanceFailure("PM dependency ownership is invalid")
    return raw


def _config_mapping() -> dict[str, Any]:
    import yaml

    path = Path(HERMES_HOME) / "config.yaml"
    try:
        value = yaml.safe_load(path.read_bytes().decode("utf-8-sig")) or {}
    except Exception as exc:
        raise AcceptanceFailure("Hermes config is not valid YAML") from exc
    if not isinstance(value, dict):
        raise AcceptanceFailure("Hermes config root is not a mapping")
    return value


def _config_assertions() -> dict[str, Any]:
    config = _config_mapping()
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        raise AcceptanceFailure("Hermes plugins config is not a mapping")
    enabled = plugins.get("enabled")
    disabled = plugins.get("disabled")
    entries = plugins.get("entries")
    if not isinstance(enabled, list) or not all(isinstance(item, str) for item in enabled):
        raise AcceptanceFailure("Hermes plugins.enabled is invalid")
    if not isinstance(disabled, list) or not all(isinstance(item, str) for item in disabled):
        raise AcceptanceFailure("Hermes plugins.disabled is invalid")
    if not isinstance(entries, dict):
        raise AcceptanceFailure("Hermes plugins.entries is invalid")
    entry = entries.get(PLUGIN_NAME)
    if not isinstance(entry, dict) or entry.get("allow_tool_override") is not False:
        raise AcceptanceFailure("HFC plugin entry policy is not installed")
    if PLUGIN_NAME not in enabled or PLUGIN_NAME in disabled:
        raise AcceptanceFailure("HFC plugin is not enabled")
    if "hfc-runtime-dependency" not in enabled:
        raise AcceptanceFailure("HFC PM dependency is not enabled")
    return {
        "plugin_enabled": True,
        "dependency_enabled": True,
        "allow_tool_override": False,
        "keys": sorted(config),
        "enabled": enabled,
        "disabled": disabled,
    }


def _snapshot_config(evidence: Path, label: str) -> str:
    value = (Path(HERMES_HOME) / "config.yaml").read_bytes()
    (evidence / f"{label}.yaml").write_bytes(value)
    return _sha256_bytes(value)


def _failure_snapshot(evidence: Path, stage: str) -> None:
    payload: dict[str, Any] = {"stage": stage, "created_at": _utc_now()}
    try:
        payload["config_sha256"] = _snapshot_config(evidence, f"failure-{stage}-config")
    except Exception as exc:
        payload["config_error"] = _safe_message(exc)
    try:
        payload["owner"] = _owner_payload()
    except Exception as exc:
        payload["owner_error"] = _safe_message(exc)
    try:
        payload["generations"] = _generation_names(Path(HERMES_HOME))
        payload["selected_runtime"] = str(_selected_runtime())
    except Exception as exc:
        payload["generation_error"] = _safe_message(exc)
    try:
        payload["observer"] = _observer_status()
    except Exception as exc:
        payload["observer_error"] = _safe_message(exc)
    payload["source"] = _source_snapshot(Path(HERMES_ROOT))
    payload["pyc"] = {
        "hermes": _count_pyc(Path(HERMES_ROOT)),
        "home": _count_pyc(Path(HERMES_HOME)),
    }
    _write_json(evidence / f"failure-{stage}.json", payload)


def _install(
    evidence: Path,
    *,
    name: str,
    expected: int = 0,
) -> subprocess.CompletedProcess[str]:
    return _run_logged(
        name,
        [
            *_base_cli(),
            "install",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
        evidence=evidence,
        env=_clean_container_env({"HFC_INSTALL_SPEC": str(_current_wheel())}),
        timeout=1200,
        expected=expected,
    )


def _cli(
    evidence: Path,
    name: str,
    arguments: Sequence[str],
    *,
    expected: int = 0,
    install_spec: str | None = None,
) -> subprocess.CompletedProcess[str]:
    extra = {"HFC_INSTALL_SPEC": install_spec} if install_spec else None
    return _run_logged(
        name,
        [*_base_cli(), *arguments],
        evidence=evidence,
        env=_clean_container_env(extra),
        timeout=1200,
        expected=expected,
    )


def _baseline_phase(evidence: Path, wheel_info: WheelInfo) -> dict[str, Any]:
    wheel_path = _current_wheel()
    pyc_mode = os.environ.get("HFC_PYC_MODE", "preserved")
    if pyc_mode not in {"preserved", "cleared"}:
        raise AcceptanceFailure("HFC_PYC_MODE is invalid")
    pyc_clear = None
    if pyc_mode == "cleared":
        pyc_clear = {
            "hermes": _clear_pyc(Path(HERMES_ROOT)),
            "home": _clear_pyc(Path(HERMES_HOME)),
        }
        _write_json(evidence / "pyc-cleared.json", pyc_clear)
    layout = _layout_report()
    if layout["repo_root"] != layout["repo_root_expected"]:
        raise AcceptanceFailure("Hermes PM repo_root was not /opt/hermes")
    if layout["hermes_install_root_present"]:
        raise AcceptanceFailure("HERMES_INSTALL_ROOT was injected")
    if layout["pythonpath_present"]:
        raise AcceptanceFailure("PYTHONPATH was injected")
    pyc_before = _pyc_snapshot(Path(HERMES_ROOT))
    home_pyc_before = _pyc_snapshot(Path(HERMES_HOME))
    _bootstrap_pip(evidence)
    base_report = _run_runtime_verify(
        _base_python(),
        wheel=wheel_path,
        expected_sha256=wheel_info.sha256,
        output=evidence / "base-runtime.json",
        mode="base",
    )
    _seed(Path(HERMES_HOME) / "config.yaml")
    source_before = _source_snapshot(Path(HERMES_ROOT))
    config_before = _snapshot_config(evidence, "config-before-install")
    generations_before = _generation_names(Path(HERMES_HOME))
    families = _config_families()
    matches = [name for name, value in families.items() if value == "match"]
    if not matches:
        raise AcceptanceFailure("no official config writer family matched the image")
    launch_families = _launch_families()
    launch_matches = [
        name for name, value in launch_families.items() if value == "match"
    ]
    if not set(matches) & set(launch_matches):
        raise AcceptanceFailure("official launch source guard did not match writer family")

    try:
        _install(evidence, name="install")
    except AcceptanceFailure:
        _failure_snapshot(evidence, "install")
        raise

    selected = _selected_python()
    if not selected.is_file():
        raise AcceptanceFailure("PM selected runtime Python is missing")
    selected_report = _run_runtime_verify(
        selected,
        wheel=wheel_path,
        expected_sha256=wheel_info.sha256,
        output=evidence / "selected-runtime.json",
        mode="pm",
    )
    if not _check_pm_installed(selected):
        raise AcceptanceFailure("PM dependency does not match the exact wheel")
    owner = _owner_payload()
    if owner.get("schema") != 2:
        raise AcceptanceFailure("fresh PM dependency did not publish schema 2")
    external = owner.get("external_wheel") or {}
    external_path = Path(str(external.get("path") or ""))
    if not external_path.is_file() or _sha256_file(external_path) != wheel_info.sha256:
        raise AcceptanceFailure("external PM wheel is missing or changed")
    if "workspace" in external_path.parts:
        raise AcceptanceFailure("external PM wheel still lives in workspace")
    observer = _observer_status()
    if observer["status"] != "active":
        raise AcceptanceFailure("legacy observer is not active after install")
    config = _config_assertions()
    source_after_install = _source_snapshot(Path(HERMES_ROOT))
    install_diff = _source_diff(source_before, source_after_install)
    # The official image may use a native/decomposed path that needs no source
    # rewrite. Record the observed diff; exact restore below is the invariant.
    generations_after = _generation_names(Path(HERMES_HOME))
    if len(generations_after) <= len(generations_before):
        raise AcceptanceFailure("PM did not create a generation before observer")
    pyc_after = _pyc_snapshot(Path(HERMES_ROOT))
    home_pyc_after = _pyc_snapshot(Path(HERMES_HOME))
    if pyc_mode == "cleared":
        if pyc_after["count"] or home_pyc_after["count"]:
            raise AcceptanceFailure("install created bytecode in cleared mode")
    elif (
        pyc_after["manifest_sha256"] != pyc_before["manifest_sha256"]
        or home_pyc_after["manifest_sha256"] != home_pyc_before["manifest_sha256"]
    ):
        raise AcceptanceFailure("install changed the preserved bytecode baseline")

    install_state = {
        "generations_before": generations_before,
        "generations_after": generations_after,
        "source_changes": install_diff,
        "config_families": matches,
        "launch_families": launch_matches,
        "config_before_sha256": config_before,
        "config_after_sha256": _sha256_file(Path(HERMES_HOME) / "config.yaml"),
        "base_runtime": base_report,
        "selected_runtime": selected_report,
        "observer": observer,
        "owner": owner,
        "config": config,
        "layout": layout,
        "pyc_mode": pyc_mode,
        "pyc_cleared": pyc_clear,
        "pyc_before": pyc_before,
        "pyc_after": pyc_after,
        "home_pyc_before": home_pyc_before,
        "home_pyc_after": home_pyc_after,
        "selected_launcher": str(selected),
        "binding_identity": _binding_identity(),
    }
    _write_json(evidence / "installed.json", install_state)

    marker_before = (Path(HERMES_ROOT) / ".hermes_feishu_card_observer.json").read_bytes()
    config_before_reinstall = (Path(HERMES_HOME) / "config.yaml").read_bytes()
    source_before_reinstall = _source_snapshot(Path(HERMES_ROOT))
    generations_before_reinstall = _generation_names(Path(HERMES_HOME))
    pyc_before_reinstall = _pyc_snapshot(Path(HERMES_ROOT))
    home_pyc_before_reinstall = _pyc_snapshot(Path(HERMES_HOME))
    _install(evidence, name="reinstall")
    if (
        (Path(HERMES_ROOT) / ".hermes_feishu_card_observer.json").read_bytes()
        != marker_before
    ):
        raise AcceptanceFailure("reinstall changed the observer marker")
    if (Path(HERMES_HOME) / "config.yaml").read_bytes() != config_before_reinstall:
        raise AcceptanceFailure("reinstall changed Hermes config")
    if _source_snapshot(Path(HERMES_ROOT)) != source_before_reinstall:
        raise AcceptanceFailure("reinstall changed owned Hermes source")
    if _generation_names(Path(HERMES_HOME)) != generations_before_reinstall:
        raise AcceptanceFailure("reinstall created an unnecessary generation")
    if (
        _pyc_snapshot(Path(HERMES_ROOT))["manifest_sha256"]
        != pyc_before_reinstall["manifest_sha256"]
        or _pyc_snapshot(Path(HERMES_HOME))["manifest_sha256"]
        != home_pyc_before_reinstall["manifest_sha256"]
    ):
        raise AcceptanceFailure("reinstall changed the bytecode baseline")

    replacement = evidence / "replacement.whl"
    shutil.copyfile(wheel_path, replacement)
    with zipfile.ZipFile(replacement, "a") as archive:
        archive.comment = b"HFC official-image rollback acceptance"
    replacement_code = (
        "import json,sys;from pathlib import Path;"
        f"sys.path.insert(0,{HERMES_ROOT!r});"
        "from hermes_feishu_card.install import pm_dependency as p;"
        f"r=p.ensure(Path({HERMES_ROOT!r}),Path({str(selected)!r}),home=Path({HERMES_HOME!r}),spec={str(replacement)!r},version={wheel_info.version!r});"
        "assert r is not None;"
        "old=json.loads((r.member/'.hfc-owner.json').read_text());"
        "old_external=old['external_wheel']['path'];"
        "r.rollback();"
        "new=json.loads((r.member/'.hfc-owner.json').read_text());"
        "print(json.dumps({'old_external':old_external,'new_owner':new}))"
    )
    rollback_result = _run_logged(
        "pm-replacement-rollback",
        [str(_base_python()), "-I", "-c", replacement_code],
        evidence=evidence,
        env=_clean_container_env(),
        timeout=600,
    )
    rollback_payload = json.loads(rollback_result.stdout.strip().splitlines()[-1])
    if rollback_payload["new_owner"].get("schema") != 2:
        raise AcceptanceFailure("PM rollback did not restore schema 2")
    if (
        rollback_payload["new_owner"].get("external_wheel", {}).get("path")
        != external_path.as_posix()
    ):
        raise AcceptanceFailure("PM rollback did not restore the original source")
    if not Path(rollback_payload["old_external"]).is_file():
        raise AcceptanceFailure("replacement wheel was not retained after rollback")
    _write_json(evidence / "pm-rollback.json", rollback_payload)

    other = Path(HERMES_HOME) / "plugins" / "hfc-acceptance-other"
    other.mkdir(parents=True, exist_ok=True)
    (other / "hfc_acceptance_other").mkdir(exist_ok=True)
    (other / "hfc_acceptance_other" / "__init__.py").write_text("__version__ = '1'\n")
    (other / "pyproject.toml").write_text(
        '[project]\nname="hfc-acceptance-other"\nversion="1"\n'
        'requires-python=">=3.11"\ndependencies=[]\n'
        '[tool.uv]\npackage=true\n'
    )
    admission_code = (
        "import json,sys;from pathlib import Path;"
        f"sys.path.insert(0,{HERMES_ROOT!r});"
        "from hermes_constants import get_hermes_home;"
        "from pm.filesystem import file_digest;"
        "from pm.plugins_state import read_home_selection;"
        "from hermes_cli.plugins_admission import admit_plugin_set_change;"
        "home=Path(get_hermes_home());config=read_home_selection(home) or {};"
        "plugins=config.get('plugins') or {};enabled=list(plugins.get('enabled') or []);"
        "disabled=list(plugins.get('disabled') or []);"
        "expected=file_digest(home/'config.yaml') or 'missing';"
        "enabled=list(dict.fromkeys([*enabled,'hfc-acceptance-other']));"
        "admit_plugin_set_change(set(enabled),set(disabled),active_plugins_dir=home/'plugins',expected_config=expected,plugin='hfc-acceptance-other');"
        "print(json.dumps({'enabled':enabled,'disabled':disabled}))"
    )
    _run_logged(
        "pm-generation-change",
        [str(_base_python()), "-I", "-c", admission_code],
        evidence=evidence,
        env=_clean_container_env(),
        timeout=600,
    )
    new_selected = _selected_python()
    if new_selected == selected:
        raise AcceptanceFailure("official PM admission reused the old generation")
    new_binding = _binding_identity()
    if new_binding == install_state["binding_identity"]:
        raise AcceptanceFailure("official PM admission did not change runtime identity")
    marker_before_failed_restore = (
        Path(HERMES_ROOT) / ".hermes_feishu_card_observer.json"
    ).read_bytes()
    source_before_failed_restore = _source_snapshot(Path(HERMES_ROOT))
    failed_restore = _cli(
        evidence,
        "restore-stale-binding",
        [
            "restore",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
        expected=1,
    )
    if (
        Path(HERMES_ROOT) / ".hermes_feishu_card_observer.json"
    ).read_bytes() != marker_before_failed_restore:
        raise AcceptanceFailure("failed restore changed the observer marker")
    if _source_snapshot(Path(HERMES_ROOT)) != source_before_failed_restore:
        raise AcceptanceFailure("failed restore changed owned source")
    _cli(
        evidence,
        "rebind-legacy",
        [
            "integrity",
            "rebind-legacy",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
    )
    _install(evidence, name="reinstall-after-rebind")
    if _observer_status()["status"] != "active":
        raise AcceptanceFailure("observer is not active after rebind")

    _cli(
        evidence,
        "restore",
        [
            "restore",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
    )
    if (Path(HERMES_ROOT) / ".hermes_feishu_card_observer.json").exists():
        raise AcceptanceFailure("restore left the observer marker")
    if _source_snapshot(Path(HERMES_ROOT)) != source_before:
        raise AcceptanceFailure("restore did not recover exact source bytes")
    restored_config = _config_mapping()
    if restored_config.get("other") != "keep":
        raise AcceptanceFailure("restore lost unrelated user config")
    if "hfc-runtime-dependency" not in (
        (restored_config.get("plugins") or {}).get("enabled") or []
    ):
        raise AcceptanceFailure("restore removed the PM-owned dependency")
    _cli(
        evidence,
        "uninstall",
        [
            "uninstall",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
    )
    if (Path(HERMES_HOME) / "plugins" / "hfc-runtime-dependency").exists():
        raise AcceptanceFailure("uninstall left the PM dependency")
    if _source_snapshot(Path(HERMES_ROOT)) != source_before:
        raise AcceptanceFailure("uninstall changed source after restore")

    _install(evidence, name="fresh-install-after-uninstall")
    fresh_selected = _selected_python()
    _run_runtime_verify(
        fresh_selected,
        wheel=wheel_path,
        expected_sha256=wheel_info.sha256,
        output=evidence / "fresh-selected-runtime.json",
        mode="pm",
    )
    if _observer_status()["status"] != "active":
        raise AcceptanceFailure("fresh install did not activate the observer")
    _cli(
        evidence,
        "final-restore",
        [
            "restore",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
    )
    _cli(
        evidence,
        "final-uninstall",
        [
            "uninstall",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
    )
    final_pyc = _pyc_snapshot(Path(HERMES_ROOT))
    final_home_pyc = _pyc_snapshot(Path(HERMES_HOME))
    if pyc_mode == "cleared":
        if final_pyc["count"] or final_home_pyc["count"]:
            raise AcceptanceFailure("final state contains bytecode in cleared mode")
    elif (
        final_pyc["manifest_sha256"] != pyc_before["manifest_sha256"]
        or final_home_pyc["manifest_sha256"] != home_pyc_before["manifest_sha256"]
    ):
        raise AcceptanceFailure("final state changed the bytecode baseline")
    return {
        "status": "ok",
        "install_state": install_state,
        "pm_rollback": rollback_payload,
        "generation_after_change": str(new_selected),
        "new_binding_identity": new_binding,
        "failed_restore_returncode": failed_restore.returncode,
        "final_restore_source_exact": True,
        "fresh_install": True,
        "final_pyc": final_pyc,
        "final_home_pyc": final_home_pyc,
    }


def _legacy_observer_chain(
    evidence: Path,
    wheel_info: WheelInfo,
    legacy_info: WheelInfo,
    *,
    source_before: Mapping[str, str],
    old_generation: Path,
    old_marker: bytes,
    observer_setup: str,
    pyc_mode: str,
    pyc_before: Mapping[str, Any],
    home_pyc_before: Mapping[str, Any],
) -> dict[str, Any]:
    marker = Path(HERMES_ROOT) / ".hermes_feishu_card_observer.json"
    legacy_evidence = evidence / "legacy-observer"
    legacy_evidence.mkdir(parents=True, exist_ok=True)
    (legacy_evidence / "marker.before.json").write_bytes(old_marker)
    legacy_manifest = Path(HERMES_ROOT) / ".hermes_feishu_card_manifest"
    manifest_sha256 = ""
    if legacy_manifest.is_file() and not legacy_manifest.is_symlink():
        shutil.copyfile(legacy_manifest, legacy_evidence / "manifest.before")
        manifest_sha256 = _sha256_file(legacy_manifest)
    backup_hashes: dict[str, str] = {}
    install_state = Path(HERMES_HOME) / ".hermes_feishu_card" / "install"
    if install_state.is_dir():
        for item in sorted(install_state.iterdir()):
            if not item.is_file() or item.is_symlink():
                continue
            shutil.copyfile(item, legacy_evidence / ("state-" + item.name))
            backup_hashes[item.name] = _sha256_file(item)
    legacy_install_source = _source_snapshot(Path(HERMES_ROOT))
    legacy_source_changes = _source_diff(source_before, legacy_install_source)
    config_before_rebind = (Path(HERMES_HOME) / "config.yaml").read_bytes()

    other = Path(HERMES_HOME) / "plugins" / "hfc-acceptance-other"
    other.mkdir(parents=True, exist_ok=True)
    (other / "hfc_acceptance_other").mkdir(exist_ok=True)
    (other / "hfc_acceptance_other" / "__init__.py").write_text("__version__ = '1'\n")
    (other / "pyproject.toml").write_text(
        '[project]\nname="hfc-acceptance-other"\nversion="1"\n'
        'requires-python=">=3.11"\ndependencies=[]\n'
        '[tool.uv]\npackage=true\n'
    )
    admission_code = (
        "import json,sys;from pathlib import Path;"
        f"sys.path.insert(0,{HERMES_ROOT!r});"
        "from hermes_constants import get_hermes_home;"
        "from pm.filesystem import file_digest;"
        "from pm.plugins_state import read_home_selection;"
        "from hermes_cli.plugins_admission import admit_plugin_set_change;"
        "home=Path(get_hermes_home());config=read_home_selection(home) or {};"
        "plugins=config.get('plugins') or {};enabled=list(plugins.get('enabled') or []);"
        "disabled=list(plugins.get('disabled') or []);"
        "expected=file_digest(home/'config.yaml') or 'missing';"
        "enabled=list(dict.fromkeys([*enabled,'hfc-acceptance-other']));"
        "admit_plugin_set_change(set(enabled),set(disabled),active_plugins_dir=home/'plugins',expected_config=expected,plugin='hfc-acceptance-other');"
        "print(json.dumps({'enabled':enabled,'disabled':disabled}))"
    )
    _run_logged(
        "legacy-pm-generation-change",
        [str(_base_python()), "-I", "-c", admission_code],
        evidence=evidence,
        env=_clean_container_env(),
        timeout=600,
    )
    new_selected = _selected_python()
    if new_selected == old_generation:
        raise AcceptanceFailure("legacy observer generation change was not observable")
    if marker.read_bytes() != old_marker:
        raise AcceptanceFailure("generation change altered the old observer marker")

    _install_current_cli(evidence)
    launch_families = _launch_families()
    launch_matches = [
        name for name, value in launch_families.items() if value == "match"
    ]
    if not launch_matches:
        raise AcceptanceFailure("legacy candidate launch source guard did not match")
    marker_before_rebind = marker.read_bytes()
    _cli(
        evidence,
        "legacy-rebind",
        [
            "integrity",
            "rebind-legacy",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
    )
    if marker.read_bytes() == marker_before_rebind:
        raise AcceptanceFailure("legacy rebind did not update the observer marker")
    (legacy_evidence / "marker.after-rebind.json").write_bytes(marker.read_bytes())
    if (Path(HERMES_HOME) / "config.yaml").read_bytes() != config_before_rebind:
        raise AcceptanceFailure("legacy rebind changed unrelated config")

    _install(evidence, name="legacy-reinstall-after-rebind")
    selected = _selected_python()
    selected_report = _run_runtime_verify(
        selected,
        wheel=_current_wheel(),
        expected_sha256=wheel_info.sha256,
        output=evidence / "legacy-selected-runtime.json",
        mode="pm",
    )
    if _observer_status()["status"] != "active":
        raise AcceptanceFailure("legacy observer is not active after rebind")
    if not _check_pm_installed(selected):
        raise AcceptanceFailure("legacy PM member does not match candidate wheel")
    if _owner_payload().get("schema") != 2:
        raise AcceptanceFailure("legacy rebind did not publish schema 2")

    _cli(
        evidence,
        "legacy-restore",
        [
            "restore",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
    )
    if _source_snapshot(Path(HERMES_ROOT)) != source_before:
        raise AcceptanceFailure("legacy restore did not recover exact source")
    if marker.exists():
        raise AcceptanceFailure("legacy restore left the observer marker")
    (legacy_evidence / "marker.absent.txt").write_text("absent\n")
    if _owner_payload().get("schema") != 2:
        raise AcceptanceFailure("legacy restore removed the PM member")

    _cli(
        evidence,
        "legacy-uninstall",
        [
            "uninstall",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
    )
    if (Path(HERMES_HOME) / "plugins" / "hfc-runtime-dependency").exists():
        raise AcceptanceFailure("legacy uninstall left the PM member")
    if _source_snapshot(Path(HERMES_ROOT)) != source_before:
        raise AcceptanceFailure("legacy uninstall changed source")

    _install(evidence, name="legacy-fresh-install")
    if _observer_status()["status"] != "active":
        raise AcceptanceFailure("legacy fresh install did not activate observer")
    _run_runtime_verify(
        _selected_python(),
        wheel=_current_wheel(),
        expected_sha256=wheel_info.sha256,
        output=evidence / "legacy-fresh-runtime.json",
        mode="pm",
    )
    _cli(
        evidence,
        "legacy-final-restore",
        [
            "restore",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
    )
    _cli(
        evidence,
        "legacy-final-uninstall",
        [
            "uninstall",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
    )
    final_pyc = _pyc_snapshot(Path(HERMES_ROOT))
    final_home_pyc = _pyc_snapshot(Path(HERMES_HOME))
    if pyc_mode == "cleared":
        if final_pyc["count"] or final_home_pyc["count"]:
            raise AcceptanceFailure("legacy final state contains bytecode")
    elif (
        final_pyc["manifest_sha256"] != pyc_before["manifest_sha256"]
        or final_home_pyc["manifest_sha256"] != home_pyc_before["manifest_sha256"]
    ):
        raise AcceptanceFailure("legacy final state changed bytecode baseline")
    return {
        "status": "ok",
        "observer_setup": observer_setup,
        "old_generation": str(old_generation),
        "generation_after_change": str(new_selected),
        "legacy_version": legacy_info.version,
        "legacy_wheel_sha256": legacy_info.sha256,
        "legacy_marker_present": True,
        "legacy_marker_sha256": _sha256_bytes(old_marker),
        "legacy_manifest_sha256": manifest_sha256,
        "legacy_state_files": backup_hashes,
        "legacy_source_changes": legacy_source_changes,
        "launch_families": launch_matches,
        "legacy_schema_upgraded": 2,
        "rebind_updated_marker": True,
        "restore_source_exact": True,
        "fresh_install": True,
        "selected_runtime": selected_report,
        "final_pyc": final_pyc,
        "final_home_pyc": final_home_pyc,
    }


def _legacy_phase(
    evidence: Path,
    wheel_info: WheelInfo,
    legacy_info: WheelInfo,
) -> dict[str, Any]:
    pyc_mode = os.environ.get("HFC_PYC_MODE", "preserved")
    if pyc_mode not in {"preserved", "cleared"}:
        raise AcceptanceFailure("HFC_PYC_MODE is invalid")
    pyc_clear = None
    if pyc_mode == "cleared":
        pyc_clear = {
            "hermes": _clear_pyc(Path(HERMES_ROOT)),
            "home": _clear_pyc(Path(HERMES_HOME)),
        }
        _write_json(evidence / "pyc-cleared.json", pyc_clear)
    _seed(Path(HERMES_HOME) / "config.yaml")
    source_before = _source_snapshot(Path(HERMES_ROOT))
    generations_before = _generation_names(Path(HERMES_HOME))
    pyc_before = _pyc_snapshot(Path(HERMES_ROOT))
    home_pyc_before = _pyc_snapshot(Path(HERMES_HOME))
    _bootstrap_pip(evidence)
    _install_legacy_cli(evidence)
    legacy_wheel = _legacy_wheel()
    legacy_code = (
        "import json,sys;from pathlib import Path;"
        f"sys.path.insert(0,{HERMES_ROOT!r});"
        "from hermes_feishu_card.install import pm_dependency as p;"
        f"r=p.ensure(Path({HERMES_ROOT!r}),Path({str(_base_python())!r}),home=Path({HERMES_HOME!r}),spec={str(legacy_wheel)!r},version={legacy_info.version!r});"
        "assert r is not None;"
        "r.finish();"
        "owner=json.loads((r.member/'.hfc-owner.json').read_text());"
        "print(json.dumps(owner,sort_keys=True))"
    )
    legacy_result = _run_logged(
        "legacy-schema1-member",
        [str(_base_python()), "-I", "-c", legacy_code],
        evidence=evidence,
        env=_clean_container_env({"HFC_INSTALL_SPEC": str(legacy_wheel)}),
        timeout=600,
    )
    legacy_owner = json.loads(legacy_result.stdout.strip().splitlines()[-1])
    if legacy_owner.get("schema") != 1:
        raise AcceptanceFailure("legacy wheel did not create schema 1 ownership")
    if (Path(HERMES_ROOT) / ".hermes_feishu_card_observer.json").exists():
        raise AcceptanceFailure("legacy PM setup unexpectedly created an observer")
    legacy_generations = _generation_names(Path(HERMES_HOME))
    if len(legacy_generations) <= len(generations_before):
        raise AcceptanceFailure("legacy PM setup did not create a generation")
    legacy_generation = _selected_runtime()
    legacy_python = legacy_generation / "bin" / "python"
    legacy_version = _run_command(
        [
            str(legacy_python),
            "-I",
            "-c",
            "import hermes_feishu_card; print(hermes_feishu_card.__version__)",
        ],
        env=_clean_container_env(),
        timeout=60,
    )
    if legacy_version.returncode or legacy_version.stdout.strip() != legacy_info.version:
        raise AcceptanceFailure("legacy generation does not import its exact wheel")
    old_install_ok = False
    try:
        _run_logged(
            "legacy-official-install",
            [
                *_base_cli(),
                "install",
                "--hermes-dir",
                HERMES_ROOT,
                "--hermes-home",
                HERMES_HOME,
                "--yes",
            ],
            evidence=evidence,
            env=_clean_container_env({"HFC_INSTALL_SPEC": str(legacy_wheel)}),
            timeout=1200,
        )
        old_install_ok = True
    except AcceptanceFailure:
        _failure_snapshot(evidence, "legacy-official-install")
    marker = Path(HERMES_ROOT) / ".hermes_feishu_card_observer.json"
    if not marker.exists():
        prepare_code = (
            "import sys;from pathlib import Path;"
            f"sys.path.insert(0,{HERMES_ROOT!r});"
            "from hermes_feishu_card.install import plugin,legacy_observer;"
            f"b=plugin.resolve_runtime_binding(checkout_root=Path({HERMES_ROOT!r}),hermes_home=Path({HERMES_HOME!r}),profile_id=None);"
            "r=legacy_observer.prepare(b);assert r is not None;r.finish();print('prepared')"
        )
        try:
            _run_logged(
                "legacy-controlled-observer-prepare",
                [str(_base_python()), "-I", "-c", prepare_code],
                evidence=evidence,
                env=_clean_container_env({"HFC_INSTALL_SPEC": str(legacy_wheel)}),
                timeout=600,
            )
        except AcceptanceFailure:
            _failure_snapshot(evidence, "legacy-controlled-observer-prepare")
    if not marker.exists():
        _failure_snapshot(evidence, "legacy-observer-marker-missing")
        raise AcceptanceFailure(
            "old 5.0.7 observer marker could not be established by CLI or controlled old-wheel path"
        )
    old_marker = marker.read_bytes()
    old_generation = _selected_runtime()
    return _legacy_observer_chain(
        evidence,
        wheel_info,
        legacy_info,
        source_before=source_before,
        old_generation=old_generation,
        old_marker=old_marker,
        observer_setup=(
            "legacy_cli"
            if old_install_ok
            else "controlled_old_wheel_observer_prepare"
        ),
        pyc_mode=pyc_mode,
        pyc_before=pyc_before,
        home_pyc_before=home_pyc_before,
    )
    _install_current_cli(evidence)
    try:
        _install(evidence, name="schema1-upgrade-install")
    except AcceptanceFailure:
        _failure_snapshot(evidence, "schema1-upgrade-install")
        raise
    upgraded_owner = _owner_payload()
    if upgraded_owner.get("schema") != 2:
        raise AcceptanceFailure("schema 1 member was not upgraded to schema 2")
    selected = _selected_python()
    if selected == legacy_python:
        raise AcceptanceFailure("schema 1 upgrade did not select a new generation")
    selected_report = _run_runtime_verify(
        selected,
        wheel=_current_wheel(),
        expected_sha256=wheel_info.sha256,
        output=evidence / "schema1-selected-runtime.json",
        mode="pm",
    )
    if _observer_status()["status"] != "active":
        raise AcceptanceFailure("schema 1 upgrade did not activate the observer")
    if not _check_pm_installed(selected):
        raise AcceptanceFailure("schema 1 upgrade PM member does not match wheel")
    retained = _run_command(
        [
            str(legacy_python),
            "-I",
            "-c",
            "import hermes_feishu_card; print(hermes_feishu_card.__version__)",
        ],
        env=_clean_container_env(),
        timeout=60,
    )
    if retained.returncode or retained.stdout.strip() != legacy_info.version:
        raise AcceptanceFailure("old generation was not retained after upgrade")
    pyc_after = _pyc_snapshot(Path(HERMES_ROOT))
    home_pyc_after = _pyc_snapshot(Path(HERMES_HOME))
    if pyc_mode == "cleared":
        if pyc_after["count"] or home_pyc_after["count"]:
            raise AcceptanceFailure("schema 1 upgrade created bytecode in cleared mode")
    elif (
        pyc_after["manifest_sha256"] != pyc_before["manifest_sha256"]
        or home_pyc_after["manifest_sha256"] != home_pyc_before["manifest_sha256"]
    ):
        raise AcceptanceFailure("schema 1 upgrade changed preserved bytecode")
    _install(evidence, name="schema1-reinstall")
    _cli(
        evidence,
        "schema1-restore",
        [
            "restore",
            "--hermes-dir",
            HERMES_ROOT,
            "--hermes-home",
            HERMES_HOME,
            "--yes",
        ],
    )
    if _source_snapshot(Path(HERMES_ROOT)) != source_before:
        raise AcceptanceFailure("schema 1 restore did not recover source")
    if (Path(HERMES_ROOT) / ".hermes_feishu_card_observer.json").exists():
        raise AcceptanceFailure("schema 1 restore left the observer marker")
    if _owner_payload().get("schema") != 2:
        raise AcceptanceFailure("schema 1 restore removed the upgraded PM member")
    final_pyc = _pyc_snapshot(Path(HERMES_ROOT))
    final_home_pyc = _pyc_snapshot(Path(HERMES_HOME))
    if pyc_mode == "cleared":
        if final_pyc["count"] or final_home_pyc["count"]:
            raise AcceptanceFailure("schema 1 final state contains bytecode")
    elif (
        final_pyc["manifest_sha256"] != pyc_before["manifest_sha256"]
        or final_home_pyc["manifest_sha256"] != home_pyc_before["manifest_sha256"]
    ):
        raise AcceptanceFailure("schema 1 final state changed bytecode baseline")
    return {
        "status": "ok",
        "legacy_version": legacy_info.version,
        "legacy_wheel_sha256": legacy_info.sha256,
        "legacy_schema": 1,
        "legacy_generation": str(legacy_generation),
        "legacy_python": str(legacy_python),
        "generation_before_observer": True,
        "upgraded_schema": 2,
        "upgraded_generation": str(selected),
        "old_generation_retained": True,
        "selected_runtime": selected_report,
        "restore_source_exact": True,
        "pyc_mode": pyc_mode,
        "pyc_cleared": pyc_clear,
        "pyc_before": pyc_before,
        "pyc_after": pyc_after,
        "home_pyc_before": home_pyc_before,
        "home_pyc_after": home_pyc_after,
        "final_pyc": final_pyc,
        "final_home_pyc": final_home_pyc,
    }


def _diagnostic_child_code() -> str:
    from hermes_feishu_card.install import plugin_config_bytes  # type: ignore

    code = plugin_config_bytes._CHILD
    collector = (
        "\nimport stat\ncollector_events=[]\n"
        "collector_seen=set()\n"
        "collector_path=os.environ.get('HFC_DIAGNOSTIC_JSON')\n"
        "def collect(event,args):\n"
        "    if event!='exec':\n"
        "        return\n"
        "    try:\n"
        "        filename=args[0].co_filename\n"
        "        if filename.startswith('<'):\n"
        "            return\n"
        "        raw=Path(os.path.abspath(os.fspath(filename)))\n"
        "        if not under(raw,root):\n"
        "            return\n"
        "        relative=raw.relative_to(root).as_posix()\n"
        "        if relative in collector_seen:\n"
        "            return\n"
        "        collector_seen.add(relative)\n"
        "        path=raw.resolve()\n"
        "        metadata=raw.lstat()\n"
        "        is_library=library_path(path)\n"
        "        value={'relative':relative,"
        "'raw_path':str(raw),"
        "'resolved_relative':path.relative_to(root).as_posix() if under(path,root) else None,"
        "'sha256':hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else '',"
        "'mode':'0o%04o' % (stat.S_IMODE(metadata.st_mode),),"
        "'is_symlink':stat.S_ISLNK(metadata.st_mode),"
        "'nonsymlink':nonsymlink_path(raw),"
        "'library':is_library,"
        "'unbound':(not is_library and (relative not in request['sources'] or not nonsymlink_path(raw)))}\n"
        "        collector_events.append(value)\n"
        "        if collector_path:\n"
        "            with open(collector_path,'w') as stream:\n"
        "                json.dump({'events':collector_events},stream,sort_keys=True)\n"
        "    except Exception:\n"
        "        return\n"
    )
    return code.replace(
        "def audit(event,args):\n",
        collector + "def audit(event,args):\n    collect(event,args)\n",
        1,
    )


def _diagnose_variant(
    *,
    label: str,
    seed: bytes,
    family: str,
    evidence: Path,
) -> dict[str, Any]:
    import yaml
    from hermes_feishu_card.install import plugin, plugin_config_bytes  # type: ignore

    home = Path(HERMES_HOME) / "diagnose" / label
    shadow = home / "shadow"
    shadow.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_bytes(seed)
    config = yaml.safe_load(seed.decode("utf-8-sig")) or {}
    desired, enabled_before = plugin._expected_enabled_config(config)
    if enabled_before:
        desired, _ = plugin._expected_enabled_config(
            config, preserve_enabled_lists=True
        )
    request = {
        "real_home": str(home),
        "family": family,
        "sources": plugin_config_bytes.FAMILIES[family],
        "desired": desired,
        "enabled_before": enabled_before,
    }
    (shadow / "config.yaml").write_bytes(seed)
    (shadow / "request.json").write_text(json.dumps(request, sort_keys=True))
    collector = shadow / "hfc-diagnostic.json"
    env = {
        "HOME": str(shadow),
        "HERMES_HOME": str(shadow),
        "HFC_DIAGNOSTIC_JSON": str(collector),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    result = _run_command(
        [
            str(_base_python()),
            "-I",
            "-c",
            _diagnostic_child_code(),
            HERMES_ROOT,
            str(shadow),
        ],
        cwd=Path(HERMES_ROOT),
        env=env,
        timeout=60,
    )
    events: list[dict[str, Any]] = []
    if collector.is_file():
        try:
            payload = json.loads(collector.read_text())
            if isinstance(payload, dict) and isinstance(payload.get("events"), list):
                events = [
                    item
                    for item in payload["events"]
                    if isinstance(item, dict)
                    and isinstance(item.get("relative"), str)
                    and isinstance(item.get("sha256"), str)
                ][:200]
        except (OSError, UnicodeError, json.JSONDecodeError):
            events = []
    first_unbound = next(
        (item for item in events if item.get("unbound") is True), None
    )
    library_exec = [item for item in events if item.get("library") is True]
    variant = {
        "label": label,
        "family": family,
        "returncode": result.returncode,
        "stdout_bytes": len((result.stdout or "").encode()),
        "stderr_bytes": len((result.stderr or "").encode()),
        "stderr_sha256": _sha256_bytes((result.stderr or "").encode()),
        "first_unbound": first_unbound,
        "executed_local": events,
        "library_exec": library_exec,
        "fixed_reason": (
            "unbound_local_source_execution"
            if first_unbound
            else ("library_only" if library_exec else "none_observed")
        ),
    }
    _write_json(evidence / f"diagnose-{label}.json", variant)
    return variant


def _diagnose_phase(evidence: Path, wheel_info: WheelInfo) -> dict[str, Any]:
    _bootstrap_pip(evidence)
    layout = _layout_report()
    if layout["repo_root"] != layout["repo_root_expected"]:
        raise AcceptanceFailure("Hermes PM repo_root was not /opt/hermes")
    if layout["hermes_install_root_present"]:
        raise AcceptanceFailure("HERMES_INSTALL_ROOT was injected")
    if layout["pythonpath_present"]:
        raise AcceptanceFailure("PYTHONPATH was injected")
    families = _config_families()
    family = "pm-0225"
    variants = {}
    for label, seed in (("minimal", MINIMAL_SEED), ("known", FULL_SEED)):
        variants[label] = _diagnose_variant(
            label=label,
            seed=seed,
            family=family,
            evidence=evidence,
        )
    payload = {
        "schema": 1,
        "status": "ok",
        "image": {
            "tag": os.environ.get("HFC_IMAGE_TAG", ""),
            "source_sha": os.environ.get("HFC_IMAGE_SOURCE_SHA", ""),
            "index_digest": os.environ.get("HFC_IMAGE_INDEX_DIGEST", ""),
            "amd64_manifest": os.environ.get("HFC_IMAGE_AMD64_MANIFEST", ""),
        },
        "wheel": wheel_info.as_dict(),
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
            "sysconfig_paths": _sysconfig_paths(),
        },
        "layout": layout,
        "families": families,
        "variants": variants,
    }
    _write_json(evidence / "diagnostic.json", payload)
    return payload


def _container_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--container-phase", choices=("baseline", "legacy", "diagnose"))
    parser.add_argument("--container-diagnose", action="store_true")
    parser.add_argument("--container-verify", action="store_true")
    parser.add_argument("--image", choices=tuple(IMAGE_SPECS))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--verify-wheel", type=Path)
    parser.add_argument("--verify-expected-sha256")
    parser.add_argument("--verify-output", type=Path)
    parser.add_argument("--verify-mode", choices=("base", "pm"), default="pm")
    args, _ = parser.parse_known_args(argv)
    evidence = args.evidence or Path(CONTAINER_EVIDENCE)
    evidence.mkdir(parents=True, exist_ok=True)
    phase = "diagnose" if args.container_diagnose else args.container_phase
    try:
        if args.container_verify:
            if not args.verify_wheel or not args.verify_expected_sha256 or not args.verify_output:
                raise AcceptanceFailure("runtime verifier arguments are incomplete")
            return _runtime_verify(
                wheel=args.verify_wheel,
                expected_sha256=args.verify_expected_sha256,
                output=args.verify_output,
                mode=args.verify_mode,
            )
        wheel_info = inspect_wheel(_current_wheel())
        legacy_info = (
            inspect_wheel(_legacy_wheel())
            if "HFC_LEGACY_WHEEL_FILENAME" in os.environ
            else None
        )
        if phase == "baseline":
            result = _baseline_phase(evidence, wheel_info)
        elif phase == "legacy":
            if legacy_info is None:
                raise AcceptanceFailure("legacy wheel is not mounted")
            result = _legacy_phase(evidence, wheel_info, legacy_info)
        elif phase == "diagnose":
            result = _diagnose_phase(evidence, wheel_info)
        else:
            raise AcceptanceFailure("container phase is missing")
        _write_json(
            evidence / "container-result.json",
            {
                "status": "ok",
                "phase": phase,
                "created_at": _utc_now(),
                "wheel": wheel_info.as_dict(),
                "result": result,
            },
        )
        return 0
    except Exception as exc:
        _write_json(
            evidence / "container-result.json",
            {
                "status": "failed",
                "phase": phase,
                "created_at": _utc_now(),
                "reason": _safe_message(exc),
            },
        )
        return 1


def _docker_run_command(
    *,
    docker: str,
    spec: ImageSpec,
    state: Path,
    evidence: Path,
    wheel_dir: Path,
    wheel_name: str,
    legacy_name: str | None,
    phase: str,
    pyc_mode: str,
    uid: int,
    gid: int,
    tool: Path,
) -> list[str]:
    command = [
        docker,
        "run",
        "--rm",
        "--workdir",
        HERMES_HOME,
        "--entrypoint",
        "/bin/bash",
        "--env",
        f"HFC_ACCEPTANCE_UID={uid}",
        "--env",
        f"HFC_ACCEPTANCE_GID={gid}",
        "--env",
        f"HFC_IMAGE_TAG={spec.tag}",
        "--env",
        f"HFC_IMAGE_SOURCE_SHA={spec.source_sha}",
        "--env",
        f"HFC_IMAGE_INDEX_DIGEST={spec.index_digest}",
        "--env",
        f"HFC_IMAGE_AMD64_MANIFEST={spec.amd64_manifest}",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        f"HOME={HERMES_HOME}",
        "--env",
        f"HERMES_HOME={HERMES_HOME}",
        "--env",
        f"HFC_PYC_MODE={pyc_mode}",
        "--env",
        f"HFC_WHEEL_FILENAME={wheel_name}",
        "--volume",
        f"{tool}:{CONTAINER_TOOL}:ro",
        "--volume",
        f"{wheel_dir}:{CONTAINER_WHEELS}:ro",
        "--volume",
        f"{state}:{HERMES_HOME}",
        "--volume",
        f"{evidence}:{CONTAINER_EVIDENCE}",
    ]
    if legacy_name is not None:
        command.extend(["--env", f"HFC_LEGACY_WHEEL_FILENAME={legacy_name}"])
    phase_flag = "--container-diagnose" if phase == "diagnose" else "--container-phase"
    phase_args = [] if phase == "diagnose" else [phase]
    command.extend(
        [
            spec.reference,
            "-lc",
            "exec /opt/hermes/.venv/bin/python -I "
            + CONTAINER_TOOL
            + " "
            + phase_flag
            + " "
            + " ".join(phase_args),
        ]
    )
    return command


def _inspect_image(docker: str, spec: ImageSpec, evidence: Path) -> dict[str, Any]:
    result = _run_command(
        [
            docker,
            "image",
            "inspect",
            "--format",
            "{{json .Config.Env}}|{{.Architecture}}|{{.Os}}|{{json .RepoDigests}}",
            spec.reference,
        ],
        timeout=60,
    )
    if result.returncode:
        raise AcceptanceFailure("pulled image could not be inspected")
    parts = result.stdout.strip().split("|")
    if len(parts) != 4:
        raise AcceptanceFailure("image inspection output is invalid")
    try:
        env = json.loads(parts[0])
        digests = json.loads(parts[3])
    except json.JSONDecodeError as exc:
        raise AcceptanceFailure("image inspection JSON is invalid") from exc
    payload = {
        "tag": spec.tag,
        "source_sha": spec.source_sha,
        "index_digest": spec.index_digest,
        "amd64_manifest": spec.amd64_manifest,
        "reference": spec.reference,
        "architecture": parts[1],
        "os": parts[2],
        "repo_digests": digests,
        "env": env,
    }
    if parts[1] != "amd64" or parts[2] != "linux":
        raise AcceptanceFailure("official image is not linux/amd64")
    if not {"PYTHONDONTWRITEBYTECODE=1", f"HERMES_HOME={HERMES_HOME}"}.issubset(
        set(env)
    ):
        raise AcceptanceFailure("official image environment does not match the pin")
    if any(value.startswith("HERMES_INSTALL_ROOT=") for value in env):
        raise AcceptanceFailure("official image injects HERMES_INSTALL_ROOT")
    if not any(value.endswith("@" + spec.index_digest) for value in digests):
        raise AcceptanceFailure("Docker did not retain the pinned index digest")
    _write_json(evidence / "image.json", payload)
    return payload


def _chown_tree(docker: str, image: ImageSpec, path: Path, uid: int, gid: int) -> None:
    _run_command(
        [
            docker,
            "run",
            "--rm",
            "--user",
            "0:0",
            "--entrypoint",
            "/bin/chown",
            "--volume",
            f"{path}:/cleanup",
            image.reference,
            "-R",
            f"{uid}:{gid}",
            "/cleanup",
        ],
        timeout=120,
    )


def _run_host_phase(
    *,
    docker: str,
    spec: ImageSpec,
    state: Path,
    evidence: Path,
    wheel: Path,
    legacy: Path | None,
    phase: str,
    pyc_mode: str,
    tool: Path,
) -> int:
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    wheel_dir = state.parent / "wheels"
    wheel_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    staged_wheel = wheel_dir / wheel.name
    if staged_wheel.exists() and _sha256_file(staged_wheel) != _sha256_file(wheel):
        raise AcceptanceFailure("staged wheel filename collision")
    shutil.copyfile(wheel, staged_wheel)
    legacy_name = None
    if legacy is not None:
        staged_legacy = wheel_dir / legacy.name
        if staged_legacy.exists() and _sha256_file(staged_legacy) != _sha256_file(legacy):
            raise AcceptanceFailure("staged legacy wheel filename collision")
        shutil.copyfile(legacy, staged_legacy)
        legacy_name = staged_legacy.name
    uid = os.getuid()
    gid = os.getgid()
    command = _docker_run_command(
        docker=docker,
        spec=spec,
        state=state,
        evidence=evidence,
        wheel_dir=wheel_dir,
        wheel_name=staged_wheel.name,
        legacy_name=legacy_name,
        phase=phase,
        pyc_mode=pyc_mode,
        uid=uid,
        gid=gid,
        tool=tool,
    )
    _write_json(evidence / "docker-command.json", {"argv": command, "phase": phase})
    result = _run_command(command, timeout=2400)
    _write_log(evidence / f"docker-{phase}", result)
    try:
        _chown_tree(docker, spec, state, uid, gid)
        _chown_tree(docker, spec, evidence, uid, gid)
    except AcceptanceFailure:
        pass
    return result.returncode


def _host_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--legacy-wheel", type=Path)
    parser.add_argument("--evidence-dir", "--evidence", type=Path, required=True)
    parser.add_argument("--image", action="append", choices=tuple(IMAGE_SPECS))
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--keep-state", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument(
        "--pyc-mode",
        choices=("preserved", "cleared"),
        default="preserved",
    )
    args = parser.parse_args(argv)
    evidence_root = args.evidence_dir.expanduser().resolve()
    state_root = (
        args.state_root
        or Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    ).expanduser().resolve()
    try:
        wheel_info = inspect_wheel(args.wheel)
        legacy_info = inspect_wheel(args.legacy_wheel) if args.legacy_wheel else None
        images = args.image or list(IMAGE_SPECS)
        if args.diagnose:
            images = ["v0.22.5"] if not args.image else images
        plan = {
            "schema": 1,
            "mode": "diagnose" if args.diagnose else "acceptance",
            "pyc_mode": args.pyc_mode,
            "wheel": wheel_info.as_dict(),
            "legacy_wheel": legacy_info.as_dict() if legacy_info else None,
            "images": {tag: IMAGE_SPECS[tag].as_dict() for tag in images},
        }
        if args.dry_run:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_json(evidence_root / "plan.json", plan)
        failures: list[str] = []
        for tag in images:
            spec = IMAGE_SPECS[tag]
            image_evidence = evidence_root / tag
            image_evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                pull = _run_command(
                    [args.docker, "pull", spec.reference], timeout=1200
                )
                _write_log(image_evidence / "pull", pull)
                if pull.returncode:
                    raise AcceptanceFailure("docker pull failed")
                _inspect_image(args.docker, spec, image_evidence)
                with tempfile.TemporaryDirectory(
                    prefix=f"hfc-image-{tag.replace('.', '-')}-",
                    dir=str(state_root),
                ) as temporary:
                    base = Path(temporary)
                    phases = ["diagnose"] if args.diagnose else ["baseline"]
                    if legacy_info is not None and not args.diagnose:
                        phases.append("legacy")
                    for phase in phases:
                        phase_state = base / phase / "home"
                        phase_state.mkdir(mode=0o700, parents=True, exist_ok=True)
                        phase_evidence = image_evidence / phase
                        phase_evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
                        code = _run_host_phase(
                            docker=args.docker,
                            spec=spec,
                            state=phase_state,
                            evidence=phase_evidence,
                            wheel=wheel_info.path,
                            legacy=legacy_info.path if legacy_info is not None else None,
                            phase=phase,
                            pyc_mode=args.pyc_mode,
                            tool=Path(__file__).resolve(),
                        )
                        if code:
                            failures.append(f"{tag}:{phase}")
                            # Keep independent phases running so a baseline
                            # failure cannot hide the legacy-observer gate.
            except Exception as exc:
                failures.append(f"{tag}:{_safe_message(exc, 160)}")
                _write_json(
                    image_evidence / "host-result.json",
                    {
                        "status": "failed",
                        "reason": _safe_message(exc),
                        "created_at": _utc_now(),
                    },
                )
            finally:
                try:
                    _chown_tree(
                        args.docker, spec, image_evidence, os.getuid(), os.getgid()
                    )
                except Exception:
                    pass
        summary = {
            "schema": 1,
            "status": "failed" if failures else "ok",
            "failures": failures,
            "created_at": _utc_now(),
            "plan": plan,
        }
        _write_json(evidence_root / "summary.json", summary)
        print(json.dumps({"status": summary["status"], "failures": failures}))
        return 1 if failures else 0
    except Exception as exc:
        print(f"error: {_safe_message(exc)}", file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if any(
        value in {"--container-phase", "--container-verify", "--container-diagnose"}
        for value in values
    ):
        return _container_main(values)
    return _host_main(values)


if __name__ == "__main__":
    raise SystemExit(main())
