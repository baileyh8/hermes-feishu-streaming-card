"""Persist HFC as an owned, code-free PM dependency member.

The official plugin admission API alone changes config/PM state. The member
caches the exact ordinary wheel; a path source survives PM's generation copy.
It has no plugin manifest or register function and cannot be a second producer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any
import zipfile
from email.parser import BytesParser

NAME = "hfc-runtime-dependency"
OWNER = ".hfc-owner.json"

# Read only in probe mode; mutation goes through upstream's public admission.
_CHILD = '''
import sys, json, inspect
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hermes_constants import get_hermes_home
from pm.plugins_state import read_home_selection
from pm.filesystem import file_digest
from pm.environments import committed_venv
from hermes_cli.plugins_admission import admit_plugin_set_change
required = {'active_plugins_dir', 'expected_config', 'plugin'}
if not required.issubset(inspect.signature(admit_plugin_set_change).parameters):
    raise RuntimeError('unsupported PM admission API')
home = get_hermes_home()
config = read_home_selection(home) or {}
plugins = config.get('plugins') or {}
enabled = plugins.get('enabled') or []
disabled = plugins.get('disabled') or []
expected = file_digest(home / 'config.yaml') or 'missing'
request = json.loads(sys.stdin.read())
if request['operation'] != 'probe':
    name = request['name']
    if request.get('expected') != expected:
        raise RuntimeError('selection changed')
    if request['operation'] == 'add':
        if name in disabled:
            raise RuntimeError('dependency member explicitly disabled')
        enabled = list(dict.fromkeys([*enabled, name]))
    else:
        enabled = [value for value in enabled if value != name]
    admit_plugin_set_change(set(enabled), set(disabled), active_plugins_dir=home / 'plugins',
                           expected_config=expected, plugin=name)
print(json.dumps({'home': str(home), 'enabled': enabled, 'disabled': disabled, 'expected': expected, 'generation': str(committed_venv(Path(sys.argv[1])))}))
'''


def _regular(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise ValueError("PM dependency evidence must be a regular file")
    return True


def available(root: Path) -> bool:
    return _regular(root / "pm/environments.py")


def _call(root: Path, python: Path, home: Path | None, request: dict[str, Any]) -> dict[str, Any]:
    env = dict(os.environ)
    if home is not None:
        env["HERMES_HOME"] = str(home)
    try:
        result = subprocess.run([str(python), "-I", "-c", _CHILD, str(root)],
                                input=json.dumps(request), capture_output=True, text=True,
                                cwd=str(root), env=env, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Hermes PM dependency admission did not complete; inspect PM receipts before retry") from exc
    if result.returncode:
        # PM resolver output can contain authenticated indexes/source URLs.
        raise ValueError("Hermes PM dependency admission refused; inspect private PM receipts")
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
        if (not isinstance(data, dict) or set(data) != {"home", "enabled", "disabled", "expected", "generation"}
                or not Path(data["home"]).is_absolute()):
            raise ValueError("invalid PM admission response")
        return data
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise ValueError("Hermes PM dependency admission returned invalid evidence") from exc


def _safe_directory(path: Path, *, create: bool = False) -> None:
    # Never turn inaccessible metadata into 'absent'. Reject linked ancestors.
    for parent in reversed((path, *path.parents)):
        try:
            mode = parent.lstat().st_mode
        except FileNotFoundError:
            if create:
                parent.mkdir(mode=0o700)
                continue
            raise ValueError("PM dependency directory is missing") from None
        if not stat.S_ISDIR(mode):
            raise ValueError("PM dependency directory ancestry must be real directories")


def _owned(path: Path) -> dict[str, Any] | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    _safe_directory(path)
    if not _regular(path / OWNER):
        raise ValueError("PM dependency directory is not owned by HFC")
    try:
        data = json.loads((path / OWNER).read_bytes())
        if not isinstance(data, dict):
            raise ValueError("invalid PM dependency ownership")
        if (set(data) != {"schema", "version", "spec_sha256", "files", "selection_added"}
                or type(data["schema"]) is not int or data["schema"] != 1
                or not isinstance(data["version"], str) or type(data["selection_added"]) is not bool
                or not isinstance(data["spec_sha256"], str) or len(data["spec_sha256"]) != 64):
            raise ValueError("invalid PM dependency ownership")
        files = data["files"]
        if (not isinstance(files, dict) or len(files) != 2 or "pyproject.toml" not in files
                or len([name for name in files if name.endswith(".whl") and Path(name).name == name]) != 1
                or {entry.name for entry in path.iterdir()} != {OWNER, *files}):
            raise ValueError("PM dependency member has user changes")
        for name, digest in files.items():
            if not _regular(path / name) or sha256((path / name).read_bytes()).hexdigest() != digest:
                raise ValueError("PM dependency member digest changed")
        return data
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("PM dependency ownership cannot be verified") from exc


def _install_spec_digest(spec: str | None) -> str:
    digest = sha256((spec or "").encode())
    if not spec or "://" in spec or spec.startswith("git+"):
        return digest.hexdigest()
    path = Path(spec).expanduser()
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return digest.hexdigest()  # A package/index requirement, interpreted by pip.
    if stat.S_ISREG(mode):
        digest.update(path.read_bytes())
    elif stat.S_ISDIR(mode):
        files = [path / "pyproject.toml", *(path / "hermes_feishu_card").rglob("*")]
        for entry in sorted(files):
            if ("__pycache__" not in entry.parts and entry.suffix != ".pyc"
                    and stat.S_ISREG(entry.stat().st_mode)):
                digest.update(str(entry.relative_to(path)).encode())
                digest.update(entry.read_bytes())
    else:
        raise ValueError("HFC install source is not a file or source directory")
    return digest.hexdigest()


def _wheel_metadata(wheel: Path, version: str) -> str:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        entries = [name for name in names if name.endswith('.dist-info/METADATA')]
        if len(entries) != 1:
            raise ValueError("HFC runtime wheel metadata is ambiguous")
        metadata = BytesParser().parsebytes(archive.read(entries[0]))
        if (metadata['Name'] != 'hermes-feishu-streaming-card' or metadata['Version'] != version
                or 'hermes_feishu_card/hook_runtime.py' not in names):
            raise ValueError("HFC runtime wheel identity does not match this installer")
        return wheel.name


@dataclass
class Registration:
    root: Path
    python: Path
    home: Path
    member: Path
    backup: Path | None
    added: bool
    source_created: bool = False
    identity: tuple[int, int] = field(init=False)
    owner: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        metadata = self.member.lstat()
        self.identity = (metadata.st_dev, metadata.st_ino)
        self.owner = _owned(self.member) or {}

    def _assert_current(self) -> None:
        metadata = self.member.lstat()
        if (metadata.st_dev, metadata.st_ino) != self.identity or _owned(self.member) != self.owner:
            raise ValueError("PM dependency changed after this registration; refusing rollback/cleanup")

    def finish(self) -> None:
        from .recovery import _root_lock
        with _root_lock(self.root):
            self._finish()

    def _finish(self) -> None:
        self._assert_current()
        if self.backup is not None:
            _owned(self.backup)
            shutil.rmtree(self.backup)
            self.backup = None

    def rollback(self) -> None:
        from .recovery import _root_lock
        with _root_lock(self.root):
            self._rollback()

    def _rollback(self) -> None:
        self._assert_current()
        probe = _call(self.root, self.python, self.home, {"operation": "probe"})
        if self.added:
            _call(self.root, self.python, self.home, {"operation": "remove", "name": NAME,
                                                   "expected": probe["expected"]})
        if self.backup is not None:
            _owned(self.backup)
            failed = self.member.with_name(self.member.name + ".failed")
            try:
                failed.lstat()
            except FileNotFoundError:
                pass
            else:
                raise ValueError("PM rollback staging path already exists")
            self.member.rename(failed)
            self.backup.rename(self.member)
            if not self.added:
                current = _call(self.root, self.python, self.home, {"operation": "probe"})
                _call(self.root, self.python, self.home, {"operation": "add", "name": NAME,
                                                       "expected": current["expected"]})
            _owned(failed)
            shutil.rmtree(failed)
            self.backup = None
        elif self.source_created:
            shutil.rmtree(self.member)


class PMDependencyRollbackFailed(ValueError):
    """Owned staging/backup retained because publication could not roll back."""


def _publish_member(member: Path, staging: Path, old: dict[str, Any] | None,
                    parent_identity: tuple[int, int], old_identity: tuple[int, int] | None) -> Path | None:
    from . import posix_transaction as io
    binding = None
    backup = None
    def check_parent() -> None:
        _safe_directory(member.parent)
        io.assert_directory_snapshot(member.parent, parent_identity, ValueError,
                                     "PM dependency parent changed", allow_original_absence=False)
        if binding is not None:
            binding.assert_parent()
            io.assert_path_parent(binding, ValueError, "PM dependency parent changed")
    def move(source: Path, target: Path) -> None:
        check_parent()
        if binding is not None:
            os.rename(source.name, target.name, src_dir_fd=binding.parent_fd, dst_dir_fd=binding.parent_fd)
        else:
            # The portable install path retains explicit lstat/identity checks.
            # It does not claim POSIX directory-descriptor guarantees.
            source.rename(target)
    try:
        check_parent()
        if io.DIRFD_BINDING_SUPPORTED:
            binding = io.bind_target(member, ValueError, staging_refusal="PM dependency parent changed",
                                     parent_refusal="PM dependency parent changed", wrap_open_error=True)
        check_parent()
        if _owned(member) != old:
            raise ValueError("PM dependency source changed during wheel build")
        if old_identity is not None:
            io.assert_directory_snapshot(member, old_identity, ValueError,
                                         "PM dependency source replaced during wheel build", allow_original_absence=False)
        if old is not None:
            backup = staging.with_name(staging.name + '.previous')
            move(member, backup)
        try:
            move(staging, member)
        except BaseException:
            if backup is not None:
                try:
                    check_parent()
                    if _owned(member) is not None or _owned(backup) != old:
                        raise ValueError("PM dependency rollback evidence changed")
                    move(backup, member)
                except (OSError, ValueError) as exc:
                    raise PMDependencyRollbackFailed("PM dependency rename rollback failed; owned backup/staging retained") from exc
            raise
        return backup
    finally:
        if binding is not None:
            binding.close()


def ensure(root: Path, python: Path, *, home: Path | None, spec: str | None,
           version: str) -> Registration | None:
    """Register even when the package already imports in the current generation."""
    from .recovery import _root_lock
    with _root_lock(root):
        return _ensure(root, python, home=home, spec=spec, version=version)


def _ensure(root: Path, python: Path, *, home: Path | None, spec: str | None,
            version: str) -> Registration | None:
    probe = _call(root, python, home, {"operation": "probe"})
    selected_home = Path(probe["home"])
    member = selected_home / "plugins" / NAME
    _safe_directory(selected_home)
    old = _owned(member)
    if NAME in probe['disabled']:
        raise ValueError("HFC PM dependency member is explicitly disabled")
    spec_digest = _install_spec_digest(spec)
    if old is not None and old['version'] == version and (spec is None or old['spec_sha256'] == spec_digest):
        _call(root, python, selected_home, {"operation": "add", "name": NAME,
                                           "expected": probe["expected"]})
        return (Registration(root, python, selected_home, member, None, True)
                if NAME not in probe['enabled'] else None)
    if not spec:
        raise ValueError("PM persistence needs the exact HFC install spec; set HFC_INSTALL_SPEC")
    _safe_directory(member.parent, create=True)
    from .posix_transaction import read_directory_identity
    parent_identity = read_directory_identity(member.parent, ValueError, "PM dependency parent changed")
    old_identity = (read_directory_identity(member, ValueError, "PM dependency source changed")
                    if old is not None else None)
    staging = Path(tempfile.mkdtemp(prefix=".hfc-runtime-", dir=member.parent))
    backup = None
    added = NAME not in probe['enabled']
    try:
        built = staging / 'build'
        built.mkdir()
        source = Path(spec).expanduser()
        if source.suffix == '.whl' and _regular(source):
            shutil.copyfile(source, built / source.name)
            wheels = [built / source.name]
        else:
            try:
                result = subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps',
                                         '--wheel-dir', str(built), spec], capture_output=True, text=True,
                                        cwd=str(staging), timeout=180)
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError("HFC runtime wheel build did not complete") from exc
            wheels = list(built.glob('*.whl'))
            if result.returncode or len(wheels) != 1:
                raise ValueError("HFC runtime wheel build failed; exact install source was retained")
        wheel = wheels[0]
        wheel_name = _wheel_metadata(wheel, version)
        # Keep a valid wheel filename: uv verifies distribution tags.
        (staging / 'runtime.whl').write_bytes(wheel.read_bytes())
        shutil.rmtree(built)
        # Newer uv resolves a path source that lands inside the plugin workspace
        # member as a workspace reference, which fails the lock; hermes also
        # strips direct-URL requirements from declared plugin dependencies.
        # Stage a second wheel copy in this hermes home's install state (never
        # part of a workspace member), and point the source at that absolute
        # path so the requirement stays an ordinary index-style pin.
        wheel_store = selected_home / '.hermes_feishu_card' / 'wheel-store'
        wheel_store.mkdir(parents=True, exist_ok=True)
        project = ('[project]\nname = "hfc-runtime-dependency"\nversion = "0"\n'
                   f'dependencies = ["hermes-feishu-streaming-card=={version}"]\n'
                   '[tool.uv]\npackage = false\n[tool.uv.sources]\n'
                   f'hermes-feishu-streaming-card = {{ path = "{(wheel_store / wheel_name).as_posix()}" }}\n')
        # Ownership uses a fixed logical wheel key; actual filename is recorded below.
        (staging / 'pyproject.toml').write_text(project)
        (staging / 'runtime.whl').rename(staging / wheel_name)
        shutil.copyfile(staging / wheel_name, wheel_store / wheel_name)
        owner = {"schema": 1, "version": version, "spec_sha256": spec_digest,
                 "selection_added": old['selection_added'] if old else added,
                 "files": {name: sha256((staging / name).read_bytes()).hexdigest()
                           for name in ('pyproject.toml', wheel_name)}}
        (staging / OWNER).write_text(json.dumps(owner))
        backup = _publish_member(member, staging, old, parent_identity, old_identity)
        registration = Registration(root, python, selected_home, member, backup, added, old is None)
        try:
            _call(root, python, selected_home, {"operation": "add", "name": NAME,
                                               "expected": probe['expected']})
        except ValueError:
            # Re-read through the official reader. Only an unchanged selection
            # proves a refused admission; unknown outcomes retain their source.
            current = _call(root, python, selected_home, {"operation": "probe"})
            if (current["expected"] == probe["expected"]
                    and current.get("generation") == probe.get("generation")):
                _owned(member)
                shutil.rmtree(member)
                if backup is not None:
                    backup.rename(member)
                raise ValueError("PM admission refused; previous dependency source preserved") from None
            raise ValueError("PM admission outcome uncertain; owned dependency source retained for diagnosis") from None
        return registration
    finally:
        if sys.exc_info()[0] is not PMDependencyRollbackFailed:
            try:
                metadata = staging.lstat()
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISDIR(metadata.st_mode):
                    raise ValueError("PM staging changed; refusing cleanup")
                shutil.rmtree(staging)


def remove(root: Path, python: Path, *, home: Path | None) -> None:
    from .recovery import _root_lock
    with _root_lock(root):
        _remove(root, python, home=home)


def _remove(root: Path, python: Path, *, home: Path | None) -> None:
    probe = _call(root, python, home, {"operation": "probe"})
    member = Path(probe['home']) / 'plugins' / NAME
    owner = _owned(member)
    if owner is None:
        return
    if not owner['selection_added']:
        raise ValueError("HFC did not own the pre-existing PM selection; dependency retained")
    _call(root, python, Path(probe['home']), {"operation": "remove", "name": NAME,
                                            "expected": probe['expected']})
    if _owned(member) != owner:
        raise ValueError("PM dependency changed during removal; source retained")
    shutil.rmtree(member)


def verify_installed(member: Path, python: Path) -> bool:
    """Compare every package resource with the exact cached ordinary wheel."""
    owner = _owned(member)
    if owner is None:
        return False
    wheel_name = next(name for name in owner['files'] if name.endswith('.whl'))
    wheel = member / wheel_name
    with zipfile.ZipFile(wheel) as archive:
        hashes = {name: sha256(archive.read(name)).hexdigest() for name in archive.namelist()
                  if name.startswith('hermes_feishu_card/') and not name.endswith('/')}
    code = """import sys,json,hashlib,importlib.metadata as metadata
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname
import hermes_feishu_card as package
request = json.loads(sys.stdin.read())
root = Path(package.__file__).parent.parent
wheel = json.loads(metadata.distribution('hermes-feishu-streaming-card').read_text('direct_url.json') or '{}')
source = urlsplit(wheel.get('url', ''))
actual = wheel.get('archive_info', {}).get('hashes', {}).get('sha256')
if actual is None and source.scheme == 'file' and not source.netloc:
    path = Path(url2pathname(source.path)).resolve()
    workspace = Path(sys.prefix).parent / 'workspace'
    if workspace.resolve() in path.parents:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
ok = not wheel.get('dir_info', {}).get('editable') and actual == request['wheel_sha256'] and all(
    (root / name).is_file() and hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    for name, digest in request['files'].items())
print(json.dumps({'ok': ok}))
"""
    try:
        result = subprocess.run([str(python), '-I', '-c', code], input=json.dumps(
            {'wheel_sha256': owner['files'][wheel_name], 'files': hashes}),
            capture_output=True, text=True, cwd=str(member), timeout=30)
        return result.returncode == 0 and json.loads(result.stdout)['ok'] is True
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False
