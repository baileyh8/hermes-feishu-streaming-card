"""Collect the first unbound local exec from the pinned official 0.22.5 image.

Host mode pulls only the digest-pinned image and mounts this script, an ordinary
HFC wheel, a private Hermes home and an evidence directory. The container mode
emits only allowlisted metadata; it never stores config bytes or raw stderr.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
from typing import Any, Mapping, Sequence
import zipfile


IMAGE_TAG = "v0.22.5"
SOURCE_SHA = "bf0203a794a07aac3ff764972ab9087baa4e4f4e"
INDEX_DIGEST = "sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d"
AMD64_MANIFEST = "sha256:fd822bae0a61bf023450737edbeaeea5fc6460f17cb4ee240a7e67b306ed63fb"
IMAGE = f"nousresearch/hermes-agent@{INDEX_DIGEST}"
HERMES = "/opt/hermes"
HOME = "/opt/data"
WHEEL_DIR = "/opt/hfc/wheels"
TOOL = "/opt/hfc/diagnose.py"
EVIDENCE = "/opt/hfc/evidence"
SEED = b"plugins: {}\nmodel: acceptance-model\n"


class Refused(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(value: bytes) -> str:
    return sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 1800,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused("child process did not complete") from exc


def _wheel(path: Path) -> dict[str, Any]:
    path = path.expanduser()
    if not path.is_file() or path.is_symlink() or path.suffix != ".whl":
        raise Refused("wheel must be a regular .whl")
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != len(set(names)):
            raise Refused("wheel has duplicate entries")
        if any(
            name.startswith("/")
            or ".." in Path(name).parts
            or "\\" in name
            for name in names
        ):
            raise Refused("wheel has an unsafe entry")
        metadata = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata) != 1:
            raise Refused("wheel metadata is ambiguous")
        body = archive.read(metadata[0]).decode("utf-8", errors="replace")
        name = re.search(r"(?m)^Name:\s*(.+)$", body)
        version = re.search(r"(?m)^Version:\s*(.+)$", body)
        if not name or not version:
            raise Refused("wheel metadata is incomplete")
        name_value = name.group(1).strip()
        version_value = version.group(1).strip()
        expected_prefix = (
            name_value.lower().replace("-", "_")
            + "-"
            + version_value
            + "-"
        )
        if not path.name.startswith(expected_prefix):
            raise Refused("wheel filename does not match its metadata")
        return {
            "filename": path.name,
            "sha256": _sha_file(path),
            "name": name_value,
            "version": version_value,
            "resource_count": sum(
                name.startswith("hermes_feishu_card/") for name in names
            ),
        }


def _pyc(root: Path) -> dict[str, Any]:
    files: list[str] = []
    caches: list[str] = []
    for current, directories, names in os.walk(root, followlinks=False):
        base = Path(current)
        for name in list(directories):
            if name == "__pycache__":
                caches.append((base / name).relative_to(root).as_posix())
        for name in names:
            if name.endswith(".pyc"):
                files.append((base / name).relative_to(root).as_posix())
    return {
        "pyc_count": len(files),
        "pycache_count": len(caches),
        "pyc_sample": sorted(files)[:20],
        "pycache_sample": sorted(caches)[:20],
    }


def _source(root: Path) -> dict[str, Any]:
    skip = {
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
    values: dict[str, str] = {}
    for current, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in skip)
        base = Path(current)
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = base / name
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise Refused("source metadata is unavailable") from exc
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                continue
            values[path.relative_to(root).as_posix()] = _sha_file(path)
    digest = sha256()
    for name in sorted(values):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(values[name].encode())
        digest.update(b"\n")
    facts: dict[str, Any] = {}
    facts_path = root / "facts.json"
    if facts_path.is_file() and facts_path.stat().st_size <= 64 * 1024:
        try:
            raw = json.loads(facts_path.read_text())
            if isinstance(raw, dict):
                for key in ("version", "commit", "source_sha", "channel", "revision"):
                    if key in raw and isinstance(raw[key], (str, int, bool)):
                        facts[key] = raw[key]
        except (OSError, UnicodeError, json.JSONDecodeError):
            facts = {}
    version_file = root / "VERSION"
    key_files = {}
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
            metadata = path.stat()
            key_files[name] = {
                "sha256": _sha_file(path),
                "mode": f"0o{stat.S_IMODE(metadata.st_mode):04o}",
                "size": metadata.st_size,
            }
    return {
        "root_mode": f"0o{stat.S_IMODE(root.stat().st_mode):04o}",
        "pm_mode": f"0o{stat.S_IMODE((root / 'pm').stat().st_mode):04o}",
        "version_file": (
            version_file.read_text(errors="replace").strip()
            if version_file.is_file() and version_file.stat().st_size <= 4096
            else ""
        ),
        "facts": facts,
        "key_files": key_files,
        "source_file_count": len(values),
        "source_tree_sha256": digest.hexdigest(),
        "pyc": _pyc(root),
    }


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


def _diagnostic_code() -> str:
    from hermes_feishu_card.install import plugin_config_bytes  # type: ignore

    code = plugin_config_bytes._CHILD
    collector = (
        "\nimport stat\n"
        "events=[]\n"
        "seen=set()\n"
        "out=os.environ.get('HFC_DIAGNOSTIC_JSON')\n"
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
        "        if relative in seen:\n"
        "            return\n"
        "        seen.add(relative)\n"
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
        "        events.append(value)\n"
        "        if out:\n"
        "            with open(out,'w') as stream:\n"
        "                json.dump({'events':events},stream,sort_keys=True)\n"
        "    except Exception:\n"
        "        return\n"
    )
    return code.replace(
        "def audit(event,args):\n",
        collector + "def audit(event,args):\n    collect(event,args)\n",
        1,
    )


def _container() -> int:
    evidence = Path(EVIDENCE)
    evidence.mkdir(parents=True, exist_ok=True)
    python = Path(HERMES) / ".venv" / "bin" / "python"
    try:
        filename = os.environ.get("HFC_WHEEL_FILENAME", "")
        if not filename or Path(filename).name != filename:
            raise Refused("HFC_WHEEL_FILENAME is missing or unsafe")
        wheel_path = Path(WHEEL_DIR) / filename
        wheel = _wheel(wheel_path)
    except Exception as exc:
        _write(
            evidence / "diagnostic.json",
            {
                "schema": 1,
                "status": "failed",
                "reason": "wheel_input_invalid",
                "message": " ".join(str(exc).split())[:300],
            },
        )
        return 1
    env = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE")
        if key in os.environ
    }
    env.update(
        {
            "HOME": HOME,
            "HERMES_HOME": HOME,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    steps: list[dict[str, Any]] = []
    for name, command in (
        ("ensurepip", [str(python), "-B", "-m", "ensurepip", "--upgrade"]),
        (
            "install",
            [
                str(python),
                "-B",
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "--disable-pip-version-check",
                "--no-warn-script-location",
                str(wheel_path),
                "lark-oapi==1.6.8",
                "qrcode",
            ],
        ),
    ):
        result = _run(command, env=env, timeout=900)
        steps.append(
            {
                "name": name,
                "returncode": result.returncode,
                "stderr_bytes": len((result.stderr or "").encode()),
                "stderr_sha256": _sha((result.stderr or "").encode()),
            }
        )
        if result.returncode:
            _write(
                evidence / "diagnostic.json",
                {
                    "schema": 1,
                    "status": "failed",
                    "reason": "base_install_failed",
                    "steps": steps,
                    "wheel": wheel,
                    "image": {
                        "tag": os.environ.get("HFC_IMAGE_TAG", IMAGE_TAG),
                        "source_sha": os.environ.get("HFC_IMAGE_SOURCE_SHA", SOURCE_SHA),
                        "index_digest": os.environ.get("HFC_IMAGE_INDEX_DIGEST", INDEX_DIGEST),
                        "amd64_manifest": os.environ.get(
                            "HFC_IMAGE_AMD64_MANIFEST", AMD64_MANIFEST
                        ),
                    },
                },
            )
            return 1
    (Path(HOME) / "config.yaml").write_bytes(SEED)
    try:
        import yaml
        from hermes_feishu_card.install import plugin, plugin_config_bytes  # type: ignore

        config = yaml.safe_load(SEED.decode()) or {}
        desired, enabled_before = plugin._expected_enabled_config(config)
        if enabled_before:
            desired, _ = plugin._expected_enabled_config(
                config, preserve_enabled_lists=True
            )
        family = "pm-0225"
        shadow = Path(HOME) / "diagnose" / "shadow"
        shadow.mkdir(parents=True, exist_ok=True)
        (shadow / "config.yaml").write_bytes(SEED)
        request = {
            "real_home": HOME,
            "family": family,
            "sources": plugin_config_bytes.FAMILIES[family],
            "desired": desired,
            "enabled_before": enabled_before,
        }
        (shadow / "request.json").write_text(json.dumps(request, sort_keys=True))
        collector = shadow / "collector.json"
        child_env = {
            "HOME": str(shadow),
            "HERMES_HOME": str(shadow),
            "HFC_DIAGNOSTIC_JSON": str(collector),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": env.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        }
        result = _run(
            [
                str(python),
                "-B",
                "-I",
                "-c",
                _diagnostic_code(),
                HERMES,
                str(shadow),
            ],
            cwd=Path(HERMES),
            env=child_env,
            timeout=60,
        )
        events: list[dict[str, Any]] = []
        if collector.is_file():
            try:
                raw = json.loads(collector.read_text())
                if isinstance(raw, dict) and isinstance(raw.get("events"), list):
                    events = [
                        item
                        for item in raw["events"]
                        if isinstance(item, dict)
                        and isinstance(item.get("relative"), str)
                        and isinstance(item.get("sha256"), str)
                    ][:200]
            except (OSError, UnicodeError, json.JSONDecodeError):
                events = []
        first = next((item for item in events if item.get("unbound") is True), None)
        library_exec = [item for item in events if item.get("library") is True]
        families = {}
        for name in plugin_config_bytes.FAMILIES:
            try:
                plugin_config_bytes.verify_family(Path(HERMES), name)
                families[name] = "match"
            except Exception:
                families[name] = "no_match"
        payload = {
            "schema": 1,
            "status": "ok",
            "image": {
                "tag": os.environ.get("HFC_IMAGE_TAG", IMAGE_TAG),
                "source_sha": os.environ.get("HFC_IMAGE_SOURCE_SHA", SOURCE_SHA),
                "index_digest": os.environ.get("HFC_IMAGE_INDEX_DIGEST", INDEX_DIGEST),
                "amd64_manifest": os.environ.get(
                    "HFC_IMAGE_AMD64_MANIFEST", AMD64_MANIFEST
                ),
            },
            "wheel": wheel,
            "python": {
                "version": sys.version.split()[0],
                "executable": sys.executable,
                "sysconfig_paths": _sysconfig_paths(),
            },
            "seed": "minimal_plugins_empty_model",
            "families": families,
            "layout": _source(Path(HERMES)),
            "child": {
                "returncode": result.returncode,
                "stdout_bytes": len((result.stdout or "").encode()),
                "stderr_bytes": len((result.stderr or "").encode()),
                "stderr_sha256": _sha((result.stderr or "").encode()),
            },
            "first_unbound": first,
            "executed_local": events,
            "library_exec": library_exec,
            "fixed_reason": (
                "unbound_local_source_execution"
                if first
                else ("library_only" if library_exec else "none_observed")
            ),
            "base_steps": steps,
        }
        _write(evidence / "diagnostic.json", payload)
        print(json.dumps({"status": "ok", "first_unbound": first}, sort_keys=True))
        return 0
    except Exception as exc:
        _write(
            evidence / "diagnostic.json",
            {
                "schema": 1,
                "status": "failed",
                "reason": "diagnostic_exception",
                "message": " ".join(str(exc).split())[:300],
                "base_steps": steps,
                "wheel": wheel,
            },
        )
        return 1


def _host(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--evidence-dir", "--evidence", type=Path, required=True)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    evidence = args.evidence_dir.expanduser().resolve()
    try:
        wheel = _wheel(args.wheel)
        plan = {
            "image": IMAGE,
            "tag": IMAGE_TAG,
            "source_sha": SOURCE_SHA,
            "index_digest": INDEX_DIGEST,
            "amd64_manifest": AMD64_MANIFEST,
            "wheel": wheel,
            "seed": "minimal_plugins_empty_model",
        }
        if args.dry_run:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        evidence.mkdir(parents=True, exist_ok=True)
        _write(evidence / "plan.json", plan)
        pull = _run([args.docker, "pull", IMAGE], timeout=1200)
        _write(
            evidence / "pull.json",
            {
                "returncode": pull.returncode,
                "stdout_bytes": len((pull.stdout or "").encode()),
                "stderr_bytes": len((pull.stderr or "").encode()),
            },
        )
        if pull.returncode:
            print(json.dumps({"status": "failed", "reason": "pull"}))
            return 1
        inspect = _run(
            [
                args.docker,
                "image",
                "inspect",
                "--format",
                "{{json .Config.Env}}|{{.Architecture}}|{{.Os}}|{{json .RepoDigests}}",
                IMAGE,
            ],
            timeout=60,
        )
        if inspect.returncode:
            print(json.dumps({"status": "failed", "reason": "inspect"}))
            return 1
        parts = inspect.stdout.strip().split("|")
        if len(parts) != 4:
            print(json.dumps({"status": "failed", "reason": "inspect_shape"}))
            return 1
        env = json.loads(parts[0])
        digests = json.loads(parts[3])
        if parts[1] != "amd64" or parts[2] != "linux":
            print(json.dumps({"status": "failed", "reason": "platform"}))
            return 1
        if "PYTHONDONTWRITEBYTECODE=1" not in env:
            print(json.dumps({"status": "failed", "reason": "pyc_env"}))
            return 1
        if not any(value.endswith("@" + INDEX_DIGEST) for value in digests):
            print(json.dumps({"status": "failed", "reason": "digest"}))
            return 1
        _write(
            evidence / "image.json",
            {
                "image": IMAGE,
                "source_sha": SOURCE_SHA,
                "index_digest": INDEX_DIGEST,
                "amd64_manifest": AMD64_MANIFEST,
                "architecture": parts[1],
                "os": parts[2],
                "repo_digests": digests,
                "env": env,
            },
        )
        state_root = Path(tempfile.mkdtemp(prefix="hfc-0225-diagnose-"))
        wheel_dir = state_root / "wheels"
        wheel_dir.mkdir(mode=0o700)
        staged_wheel = wheel_dir / args.wheel.name
        shutil.copyfile(args.wheel.expanduser().resolve(), staged_wheel)
        command = [
            args.docker,
            "run",
            "--rm",
            "--workdir",
            HOME,
            "--entrypoint",
            "/bin/bash",
            "--env",
            f"HFC_IMAGE_TAG={IMAGE_TAG}",
            "--env",
            f"HFC_IMAGE_SOURCE_SHA={SOURCE_SHA}",
            "--env",
            f"HFC_IMAGE_INDEX_DIGEST={INDEX_DIGEST}",
            "--env",
            f"HFC_IMAGE_AMD64_MANIFEST={AMD64_MANIFEST}",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            f"HOME={HOME}",
            "--env",
            f"HERMES_HOME={HOME}",
            "--env",
            f"HFC_WHEEL_FILENAME={staged_wheel.name}",
            "--volume",
            f"{Path(__file__).resolve()}:{TOOL}:ro",
            "--volume",
            f"{wheel_dir}:{WHEEL_DIR}:ro",
            "--volume",
            f"{state_root}:{HOME}",
            "--volume",
            f"{evidence}:{EVIDENCE}",
            IMAGE,
            "-lc",
            f"exec /opt/hermes/.venv/bin/python -B -I {TOOL} --container-diagnose",
        ]
        result = _run(command, timeout=1800)
        _write(
            evidence / "container.json",
            {
                "returncode": result.returncode,
                "stdout_bytes": len((result.stdout or "").encode()),
                "stderr_bytes": len((result.stderr or "").encode()),
            },
        )
        try:
            shutil.rmtree(state_root)
        except OSError:
            pass
        print(
            json.dumps(
                {
                    "status": "ok" if result.returncode == 0 else "failed",
                    "diagnostic": str(evidence / "diagnostic.json"),
                },
                sort_keys=True,
            )
        )
        return 0 if result.returncode == 0 else 1
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "reason": " ".join(str(exc).split())[:300]}
            )
        )
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if "--container-diagnose" in values:
        return _container()
    return _host(values)


if __name__ == "__main__":
    raise SystemExit(main())
