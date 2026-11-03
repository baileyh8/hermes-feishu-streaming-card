"""Read-only v0.22.6 config backend verification probe and proof.

Verifies that both early-stage and project-stage config backend selections
resolve strictly to the default 'file' backend across both PM and isolated
enable environments before any PM mutation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from ._writer_0226 import FAMILY_SOURCES

MAX_LAYER_BYTES = 1024 * 1024  # 1MB bound per env layer file


class BackendRefused(Exception):
    def __init__(self, message: str, code: str = "backend-refused") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _inspect_layer_file(path: Path) -> tuple[str, str | None, tuple[int, int, int, int, int] | None]:
    """Safely inspect and read an env file without following symlinks or reparse points."""
    try:
        lmeta = path.lstat()
    except FileNotFoundError:
        return ("absent", None, None)
    except OSError as exc:
        raise BackendRefused(
            "env layer file inaccessible",
            code="backend-file-unreadable",
        ) from exc

    if stat.S_ISLNK(lmeta.st_mode):
        raise BackendRefused(
            "env layer file is a symlink",
            code="backend-file-symlink",
        )
    if _is_reparse(lmeta):
        raise BackendRefused(
            "env layer file is a reparse point",
            code="backend-file-reparse",
        )
    if not stat.S_ISREG(lmeta.st_mode):
        raise BackendRefused(
            "env layer file is not a regular file",
            code="backend-file-type",
        )
    if lmeta.st_size > MAX_LAYER_BYTES:
        raise BackendRefused(
            "env layer file exceeds size bound",
            code="backend-file-overflow",
        )

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BackendRefused(
            "env layer open failed",
            code="backend-file-unreadable",
        ) from exc

    try:
        opened = os.fstat(fd)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_LAYER_BYTES:
                raise BackendRefused(
                    "env layer file content exceeds size bound",
                    code="backend-file-overflow",
                )
        data = b"".join(chunks)
        finished = os.fstat(fd)
    except OSError as exc:
        raise BackendRefused(
            "env layer read failed",
            code="backend-file-unreadable",
        ) from exc
    finally:
        os.close(fd)

    after = path.lstat()
    if (
        _signature(lmeta) != _signature(opened)
        or _signature(opened) != _signature(finished)
        or _signature(finished) != _signature(after)
    ):
        raise BackendRefused(
            "env layer file identity shifted during read",
            code="backend-file-race",
        )

    digest = hashlib.sha256(data).hexdigest()
    return ("present", digest, _signature(finished))


def _resolve_candidate_layers(root: Path, home: Path, env: dict[str, str]) -> dict[str, Path]:
    """Identify candidate env layers that affect backend selection under given env."""
    layers: dict[str, Path] = {
        "home_env": home / ".env",
        "home_op_env": home / ".op.env",
        "project_env": root / ".env",
    }
    managed_override = env.get("HERMES_MANAGED_DIR", "").strip()
    directory = Path(managed_override) if managed_override else Path("/etc/hermes")
    if not directory.is_absolute():
        directory = root / directory
    try:
        metadata = directory.stat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise BackendRefused("managed scope is uninspectable", code="backend-managed-unreadable") from exc
    else:
        if not stat.S_ISDIR(metadata.st_mode):
            raise BackendRefused("managed scope is not a directory", code="backend-managed-type")
        layers["managed_env"] = directory / ".env"
    return layers


_CHILD_PROBE = r'''
import sys,json,os,stat,hashlib,sysconfig,importlib.machinery
from pathlib import Path
sys.dont_write_bytecode=True
root=Path(sys.argv[1]).resolve();home=Path(sys.argv[2]).resolve()
request=json.loads(sys.stdin.read());sources=request['sources']
layers={Path(p).resolve() for p in request['layer_paths']}
def deny(reason):
    sys.stdout.write(json.dumps({'verdict':'refused','reason':reason})+'\n');sys.stdout.flush();os._exit(73)
def under(path,base): return path==base or base in path.parents
libraries={Path(p).resolve() for k,p in sysconfig.get_paths().items() if k in ('stdlib','platstdlib','purelib','platlib')}
if any(under(root,p) for p in libraries): deny('library_overlap')
def library(path): return any(under(path,p) for p in libraries)
def signature(m): return m.st_dev,m.st_ino,m.st_size,m.st_mtime_ns,m.st_ctime_ns
def verified_bytes(name):
    path=root/name
    try:
        for item in (path,*path.parents):
            if item==root.parent: break
            m=item.lstat()
            if stat.S_ISLNK(m.st_mode) or getattr(m,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400): deny('source_symlink')
        before=path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size>2097152: deny('source_type')
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
        try:
            opened=os.fstat(fd);chunks=[];size=0
            while True:
                chunk=os.read(fd,65536)
                if not chunk:break
                size+=len(chunk)
                if size>2097152:deny('source_size')
                chunks.append(chunk)
            final=os.fstat(fd)
        finally:os.close(fd)
        after=path.lstat();data=b''.join(chunks)
        if not signature(before)==signature(opened)==signature(final)==signature(after):deny('source_changed')
        if hashlib.sha256(data).hexdigest()!=sources[name]:deny('source_digest')
        return data
    except OSError:deny('source_unreadable')
for name in sources:verified_bytes(name)
if any(n=='hermes_constants' or n=='utils' or n.startswith(('hermes_cli','agent.','pm.')) for n in sys.modules):deny('preloaded_source')
original=importlib.machinery.SourceFileLoader.get_code
def get_code(loader,name):
    filename=loader.get_filename(name);path=Path(os.path.abspath(filename))
    if under(path,root) and not library(path.resolve()):
        relative=path.relative_to(root).as_posix()
        if relative not in sources:deny('unbound_source')
        return compile(verified_bytes(relative),filename,'exec',dont_inherit=True)
    return original(loader,name)
importlib.machinery.SourceFileLoader.get_code=get_code
def audit(event,args):
    if event.startswith(('subprocess','socket','os.remove','os.mkdir','os.rmdir','os.rename','os.link','os.symlink','os.chmod','os.chown','os.truncate','os.utime','os.system','os.exec','os.spawn','os.fork','os.kill')):deny('mutation_or_transport')
    if event=='exec':
        filename=args[0].co_filename
        if not filename.startswith('<'):
            path=Path(os.path.abspath(filename))
            if under(path,root) and not library(path.resolve()) and path.relative_to(root).as_posix() not in sources:deny('unbound_exec')
    if event!='open':return
    mode=args[1];flags=args[2]
    if isinstance(mode,str) and any(c in mode for c in 'wax+'):deny('write_open')
    if isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):deny('write_open')
    if not isinstance(args[0],(str,bytes,os.PathLike)):deny('unbound_descriptor')
    raw=Path(os.path.abspath(os.fsdecode(args[0])));path=raw.resolve()
    if library(path):return
    if under(raw,root) and raw.suffix=='.pyc':raise OSError('source required')
    if raw in layers and path in layers:return
    if under(raw,root) and raw.relative_to(root).as_posix() in sources and path==raw:return
    deny('unbound_read')
sys.addaudithook(audit);sys.path.insert(0,str(root))
try:
    from hermes_cli.env_loader import _bootstrap_env,selected_config_backend_name
    from hermes_cli.config_backend import BACKEND_ENV
    for name in ('hermes_cli.env_loader','hermes_cli.config_backend','hermes_cli.managed_scope'):
        module=sys.modules.get(name)
        if module is not None and Path(module.__file__).resolve()!=root/(name.replace('.','/')+'.py'):deny('module_origin')
    early=selected_config_backend_name(home)
    final=(_bootstrap_env(home,project_env=root/'.env').get(BACKEND_ENV) or '').strip().lower() or 'file'
    if early!='file' or final!='file':deny('non_file_backend')
    for name in sources:verified_bytes(name)
except Exception:deny('native_probe_failed')
print(json.dumps({'verdict':'file','reason':'file_confirmed'}),flush=True)
'''

def _layer_snapshot(root: Path, home: Path, env: dict[str, str]) -> dict[str, tuple[str, str, str | None, tuple[int, int, int, int, int] | None]]:
    candidates = _resolve_candidate_layers(root, home, env)
    return {key: (str(path.resolve()), *_inspect_layer_file(path))
            for key, path in candidates.items()}


@dataclass(frozen=True, repr=False)
class BackendProof:
    root: Path
    python: Path
    home: Path
    scope_layers: dict[str, dict[str, tuple[str, str, str | None, tuple[int, int, int, int, int] | None]]]

    def verify(self) -> None:
        """Recheck each exact scope both before and after its native probe."""
        scopes = _build_scopes(self.root, self.home)
        if set(scopes) != set(self.scope_layers):
            raise BackendRefused("backend scope changed", code="backend-drift")
        for name, env in scopes.items():
            expected = self.scope_layers[name]
            if _layer_snapshot(self.root, self.home, env) != expected:
                raise BackendRefused("backend layers changed", code="backend-drift")
            _run_child_probe(self.root, self.python, self.home, env,
                             _resolve_candidate_layers(self.root, self.home, env))
            if _layer_snapshot(self.root, self.home, env) != expected:
                raise BackendRefused("backend layers changed during probe", code="backend-drift")


def _build_scopes(root: Path, home: Path) -> dict[str, dict[str, str]]:
    """Build the two authoritative execution environments to prove."""
    from .plugin_config_bytes import isolated_child_env

    # Scope A: PM execution environment (ambient without test bypass, with HERMES_HOME)
    pm_env = dict(os.environ)
    pm_env.pop("PYTEST_CURRENT_TEST", None)
    pm_env["HERMES_HOME"] = str(home)

    # Scope B: official enable isolated execution environment
    enable_env = isolated_child_env(home, install_root=root, include_path=True)

    return {
        "pm_scope": pm_env,
        "enable_scope": enable_env,
    }


def _run_child_probe(
    root: Path,
    python: Path,
    home: Path,
    env: dict[str, str],
    candidate_layers: dict[str, Path],
) -> dict[str, str]:
    """Execute the child probe with isolated read-only arguments."""
    payload_input = json.dumps({
        "sources": FAMILY_SOURCES,
        "layer_paths": [str(p.resolve()) for p in candidate_layers.values()],
    })
    python_cmd = str(python)
    try:
        proc = subprocess.run(
            [python_cmd, "-I", "-B", "-c", _CHILD_PROBE, str(root), str(home)],
            cwd=str(root),
            input=payload_input,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackendRefused(
            "config backend probe timed out",
            code="backend-probe-timeout",
        ) from exc
    except OSError as exc:
        raise BackendRefused(
            "failed to spawn config backend probe",
            code="backend-probe-spawn",
        ) from exc

    try:
        payload = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        payload = {}

    if proc.returncode != 0:
        reason = payload.get("reason") if type(payload) is dict else None
        if reason not in {"source_symlink", "source_type", "source_size", "source_changed", "source_digest", "source_unreadable", "preloaded_source", "unbound_source", "mutation_or_transport", "unbound_exec", "write_open", "unbound_descriptor", "unbound_read", "module_origin", "non_file_backend", "native_probe_failed", "library_overlap"}:
            reason = "child_refusal"
        raise BackendRefused(
            f"config backend probe rejected: {reason}",
            code="backend-non-file",
        )

    if type(payload) is not dict or payload != {"verdict": "file", "reason": "file_confirmed"}:
        raise BackendRefused(
            "config backend selection is not file",
            code="backend-non-file",
        )
    return {"verdict": "file", "reason": "file_confirmed"}


def verify_file_backend(root: Path, python: Path, home: Path) -> BackendProof:
    """Audit env layers and execute child probe across both PM and enable scopes."""
    root = Path(os.path.abspath(os.fspath(root)))
    python = Path(os.path.abspath(os.fspath(python)))
    home = Path(os.path.abspath(os.fspath(home)))

    if not root.is_dir():
        raise BackendRefused("invalid root directory", code="backend-invalid-root")
    if not home.is_dir():
        raise BackendRefused("invalid home directory", code="backend-invalid-home")

    snapshots = {name: _layer_snapshot(root, home, env)
                 for name, env in _build_scopes(root, home).items()}
    proof = BackendProof(root, python, home, snapshots)
    proof.verify()
    return proof
