"""Persist HFC as an owned, code-free PM dependency member.

The official plugin admission API alone changes config/PM state. The member
caches the exact ordinary wheel; a path source survives PM's generation copy.
It has no plugin manifest or register function and cannot be a second producer.
"""
from __future__ import annotations

from contextlib import contextmanager
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
from typing import Any, Iterator, cast
import zipfile
from email.parser import BytesParser

from . import legacy_9b904_transaction, pm_wheel_store

NAME = "hfc-runtime-dependency"
OWNER = ".hfc-owner.json"
_ADOPTION_ENABLED = True  # Restricted 9b904 adapter; unsupported states still fail closed.


class PMDependencySourceUnverified(ValueError):
    """A schema-1 flat member declaration exists but its source is unproven."""


class PMAdmissionOutcomeUncertain(ValueError):
    """Admission may have committed; retain the owned recovery evidence."""


@dataclass(frozen=True)
class LegacyParentProof:
    store_identity: tuple[int, int]
    mode: int
    store_wheel: Path
    wheel_name: str
    wheel_digest: str


@contextmanager
def _legacy_parent_guard(selected_home: Path, member: Path,
                         old: dict[str, Any] | None,
                         proof: LegacyParentProof | None) -> Iterator[None]:
    """Tighten a proven PR458 parent and restore its prior mode on failure."""
    if old is None or proof is None:
        yield
        return
    def reprove() -> bool:
        try:
            return (_owned(member) == old
                    and _pr458_flat_wheel(member, old) == (
                        proof.store_wheel, proof.wheel_name))
        except (OSError, ValueError):
            return False
    snapshot = pm_wheel_store.tighten_legacy_parent(
        selected_home,
        expected_identity=proof.store_identity,
        wheel_name=proof.wheel_name,
        digest=proof.wheel_digest,
        reprove=reprove,
    )
    try:
        yield
    except BaseException:
        try:
            if _owned(member) == old:
                pm_wheel_store.restore_legacy_parent(selected_home, snapshot)
        except (OSError, ValueError):
            # Keep the safer 0700 state if the prior mode cannot be restored
            # without crossing an unknown or swapped directory.
            pass
        raise

# Read only in probe mode; mutation goes through upstream's public admission.
_CHILD = '''
import sys, json, inspect, hashlib, os, stat, importlib.machinery, sysconfig
from pathlib import Path
root = Path(os.path.abspath(sys.argv[1]))
request = json.loads(sys.stdin.read())
sources = request.get('source_digests')
def under(path, base):
    return path == base or base in path.parents
def verified_bytes(name, expected):
    if not isinstance(name, str):
        raise RuntimeError('PM source evidence is invalid')
    relative = Path(name)
    if (relative.is_absolute() or not relative.parts
            or '..' in relative.parts or not isinstance(expected, str) or len(expected) != 64):
        raise RuntimeError('PM source evidence is invalid')
    path = root
    for part in relative.parts:
        path = path / part
        metadata = path.lstat()
        if (stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
            raise RuntimeError('PM source path is unsafe')
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 2 * 1024 * 1024:
        raise RuntimeError('PM source is unsafe')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(2 * 1024 * 1024 + 1)
        finished = os.fstat(stream.fileno())
    after = path.lstat()
    signature = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if (signature(metadata) != signature(opened) or signature(opened) != signature(finished)
            or signature(finished) != signature(after) or len(data) > 2 * 1024 * 1024
            or hashlib.sha256(data).hexdigest() != expected):
        raise RuntimeError('PM source evidence changed')
    return data
def verify_sources():
    if sources is None:
        return
    if not isinstance(sources, dict) or not sources:
        raise RuntimeError('PM source evidence is invalid')
    for ancestor in (root, *root.parents):
        metadata = ancestor.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
            raise RuntimeError('PM source ancestry is unsafe')
    for name, expected in sources.items():
        verified_bytes(name, expected)
verify_sources()
if sources is not None:
    # The interpreter and its installed libraries are trusted. The checkout's
    # writer sources are not allowed to execute a timestamp-valid cached pyc.
    sys.dont_write_bytecode = True
    libraries = [Path(os.path.abspath(value)) for key in ('stdlib', 'platstdlib', 'purelib', 'platlib')
                 if (value := sysconfig.get_paths().get(key))]
    if any(base == root or base in root.parents for base in libraries):
        raise RuntimeError('PM source overlaps runtime libraries')
    def local_source(filename):
        path = Path(os.path.abspath(os.fsdecode(filename)))
        if under(path, root) and not any(under(path, base) for base in libraries):
            return path.relative_to(root).as_posix()
        return None
    def audit(event, args):
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            name = local_source(args[0])
            if name is not None and name.endswith('.pyc'):
                # Importlib falls back to source when its cache cannot be read.
                raise OSError('PM source requires source execution')
    sys.addaudithook(audit)
    original_get_code = importlib.machinery.SourceFileLoader.get_code
    def source_get_code(loader, fullname):
        filename = loader.get_filename(fullname)
        name = local_source(filename)
        if name in sources:
            return compile(verified_bytes(name, sources[name]), filename, 'exec', dont_inherit=True)
        return original_get_code(loader, fullname)
    importlib.machinery.SourceFileLoader.get_code = source_get_code
    if any(name == 'hermes_constants' or name == 'pm' or name.startswith('pm.')
           or name == 'hermes_cli' or name.startswith('hermes_cli.') for name in sys.modules):
        raise RuntimeError('PM module was loaded before source verification')
sys.path.insert(0, str(root))
from hermes_constants import get_hermes_home
from pm.plugins_state import read_home_selection
from pm.filesystem import file_digest
from pm.environments import committed_venv
from hermes_cli.plugins_admission import admit_plugin_set_change
if sources is not None:
    for name, relative in (('hermes_constants', 'hermes_constants.py'),
                           ('pm', 'pm/__init__.py'),
                           ('pm.plugins_state', 'pm/plugins_state.py'),
                           ('pm.filesystem', 'pm/filesystem.py'),
                           ('pm.environments', 'pm/environments.py'),
                           ('hermes_cli', 'hermes_cli/__init__.py'),
                           ('hermes_cli.plugins_admission', 'hermes_cli/plugins_admission.py')):
        module = sys.modules.get(name)
        origin = getattr(module, '__file__', None)
        expected_origin = root / relative
        if (origin is None or os.path.normcase(os.path.abspath(origin)) != os.path.normcase(str(expected_origin))
                or Path(origin).resolve(strict=True) != expected_origin):
            raise RuntimeError('PM module origin is not the inspected checkout')
required = {'active_plugins_dir', 'expected_config', 'plugin'}
if not required.issubset(inspect.signature(admit_plugin_set_change).parameters):
    raise RuntimeError('unsupported PM admission API')
home = get_hermes_home()
def config_snapshot():
    path = home / 'config.yaml'
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None, 'missing'
    if (not stat.S_ISREG(before.st_mode) or before.st_size > 2 * 1024 * 1024
            or getattr(before, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
        raise RuntimeError('PM selection config is unsafe')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(2 * 1024 * 1024 + 1)
        finished = os.fstat(stream.fileno())
    after = path.lstat()
    signature = lambda value: (value.st_dev, value.st_ino, value.st_size,
                               value.st_mtime_ns, value.st_ctime_ns)
    if (signature(before) != signature(opened) or signature(opened) != signature(finished)
            or signature(finished) != signature(after) or len(data) > 2 * 1024 * 1024):
        raise RuntimeError('PM selection config changed')
    return signature(after), hashlib.sha256(data).hexdigest()
before_selection = config_snapshot() if sources is not None else None
config = read_home_selection(home) or {}
plugins = config.get('plugins') or {}
enabled = plugins.get('enabled') or []
disabled = plugins.get('disabled') or []
if sources is not None:
    after_selection = config_snapshot()
    if before_selection != after_selection:
        raise RuntimeError('PM selection config changed')
    expected = after_selection[1]
else:
    expected = file_digest(home / 'config.yaml') or 'missing'
verify_sources()
if request['operation'] == 'current':
    from pm.install import venv_is_current
    current = venv_is_current(project_root=root)
elif request['operation'] != 'probe':
    name = request['name']
    if request.get('expected') != expected:
        raise RuntimeError('selection changed')
    if request['operation'] == 'add':
        if name in disabled:
            raise RuntimeError('dependency member explicitly disabled')
        enabled = list(dict.fromkeys([*enabled, name]))
    elif request['operation'] == 'remove':
        enabled = [value for value in enabled if value != name]
    elif request['operation'] == 'disable-hfc':
        if sources is None or name != 'hermes-feishu-card' or name not in enabled or name in disabled:
            raise RuntimeError('unsupported HFC disable selection')
        enabled = [value for value in enabled if value != name]
        disabled = list(dict.fromkeys([*disabled, name]))
    else:
        raise RuntimeError('unsupported PM operation')
    admit_plugin_set_change(set(enabled), set(disabled), active_plugins_dir=home / 'plugins',
                           expected_config=expected, plugin=name)
verify_sources()
result={'home': str(home), 'enabled': enabled, 'disabled': disabled, 'expected': expected, 'generation': str(committed_venv(Path(sys.argv[1])))}
if request['operation']=='current':
    result['current']=current
print(json.dumps(result))
'''


def _regular(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode) or pm_wheel_store.is_reparse(metadata):
        raise ValueError("PM dependency evidence must be a regular file")
    return True


def available(root: Path) -> bool:
    return _regular(root / "pm/environments.py")


def _call(root: Path, python: Path, home: Path | None, request: dict[str, Any]) -> dict[str, Any]:
    env = dict(os.environ)
    if home is not None:
        env["HERMES_HOME"] = str(home)
    guarded_mutation = (request.get("source_digests") is not None
                        and request.get("operation") not in ("probe", "current"))
    try:
        result = subprocess.run([str(python), "-I", "-B", "-c", _CHILD, str(root)],
                                input=json.dumps(request), capture_output=True, text=True,
                                cwd=str(root), env=env, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        if guarded_mutation:
            raise PMAdmissionOutcomeUncertain(
                "PM admission outcome uncertain; owned dependency source retained for diagnosis"
            ) from None
        raise ValueError("Hermes PM dependency admission did not complete; inspect PM receipts before retry") from exc
    if result.returncode:
        # PM resolver output can contain authenticated indexes/source URLs.
        if guarded_mutation:
            raise PMAdmissionOutcomeUncertain(
                "PM admission outcome uncertain; owned dependency source retained for diagnosis"
            ) from None
        raise ValueError("Hermes PM dependency admission refused; inspect private PM receipts")
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
        expected_fields = {"home", "enabled", "disabled", "expected", "generation"}
        if request.get("operation") == "current":
            expected_fields.add("current")
        if (not isinstance(data, dict) or set(data) != expected_fields
                or ("current" in data and type(data["current"]) is not bool)
                or not Path(data["home"]).is_absolute()):
            raise ValueError("invalid PM admission response")
        return data
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        if guarded_mutation:
            raise PMAdmissionOutcomeUncertain(
                "PM admission outcome uncertain; owned dependency source retained for diagnosis"
            ) from None
        raise ValueError("Hermes PM dependency admission returned invalid evidence") from exc


def _safe_directory(path: Path, *, create: bool = False) -> None:
    # Never turn inaccessible metadata into 'absent'. Reject linked ancestors.
    for parent in reversed((path, *path.parents)):
        try:
            metadata = parent.lstat()
        except FileNotFoundError:
            if create:
                parent.mkdir(mode=0o700)
                metadata = parent.lstat()
            else:
                raise ValueError("PM dependency directory is missing") from None
        if not stat.S_ISDIR(metadata.st_mode) or pm_wheel_store.is_reparse(metadata):
            raise ValueError("PM dependency directory ancestry must be real directories")


def _project_source(version: str, reference: dict[str, str]) -> str:
    return ('[project]\nname = "hfc-runtime-dependency"\nversion = "0"\n'
            f'dependencies = ["hermes-feishu-streaming-card=={version}"]\n'
            '[tool.uv]\npackage = false\n[tool.uv.sources]\n'
            f'hermes-feishu-streaming-card = {{ path = {json.dumps(reference["path"])} }}\n')


def _sdk_project_source(version: str, reference: dict[str, str], sdk: dict[str, str]) -> str:
    """Schema 3 owns the upstream-locked SDK source as well as the HFC wheel."""
    return ('[project]\nname = "hfc-runtime-dependency"\nversion = "0"\n'
            f'dependencies = ["hermes-feishu-streaming-card=={version}", "lark-oapi==1.7.5"]\n'
            '[tool.uv]\npackage = false\n[tool.uv.sources]\n'
            f'hermes-feishu-streaming-card = {{ path = {json.dumps(reference["path"])} }}\n'
            f'lark-oapi = {{ path = {json.dumps(sdk["path"])} }}\n')


def _pr458_project_source(version: str, wheel_path: str) -> str:
    """Exact bb447f6 f-string text, including a raw non-ASCII path."""
    return ('[project]\nname = "hfc-runtime-dependency"\nversion = "0"\n'
            f'dependencies = ["hermes-feishu-streaming-card=={version}"]\n'
            '[tool.uv]\npackage = false\n[tool.uv.sources]\n'
            f'hermes-feishu-streaming-card = {{ path = "{wheel_path}" }}\n')


def _pr458_flat_wheel(member: Path, owner: dict[str, Any]) -> tuple[Path, str] | None:
    """Return the exact PR458 flat wheel path, or None for other schema-1 layouts."""
    if (set(owner) != {'schema', 'version', 'spec_sha256', 'files', 'selection_added'}
            or type(owner.get('schema')) is not int or owner['schema'] != 1
            or not isinstance(owner.get('version'), str)
            or not isinstance(owner.get('spec_sha256'), str)
            or len(owner['spec_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in owner['spec_sha256'])
            or type(owner.get('selection_added')) is not bool
            or member.parent.name != 'plugins'):
        return None
    files = owner.get('files')
    if not isinstance(files, dict):
        return None
    wheels = [name for name in files if isinstance(name, str) and name.endswith('.whl')]
    if len(wheels) != 1:
        return None
    wheel_name = wheels[0]
    home = member.parent.parent
    store = home / '.hermes_feishu_card' / 'wheel-store'
    expected = _pr458_project_source(owner['version'], (store / wheel_name).as_posix())
    declared_path = member / 'pyproject.toml'
    try:
        metadata = declared_path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or pm_wheel_store.is_reparse(metadata):
            return None
        declared = declared_path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return None
    if declared != expected:
        return None
    return store / wheel_name, wheel_name


def legacy_external_wheel(member: Path, owner: dict[str, Any]) -> dict[str, str]:
    """Return the exact PR458 flat wheel source for a schema-1 owned member.

    The proof is the historical member declaration plus the member owner's
    digest, not merely matching package resources or the existence of any file
    with the same name. Schema 2 uses the digest directory validated by
    :mod:`pm_wheel_store`; this function only accepts the one flat layout
    written by commit bb447f66946def97d43fa722132c9a98a432afb3.
    """
    flat = _pr458_flat_wheel(member, owner)
    if flat is None:
        raise ValueError('legacy PM dependency is not the PR458 schema-1 member')
    store_wheel, wheel_name = flat
    verified = pm_wheel_store.validate_legacy(
        member.parent.parent, wheel_name, owner['files'][wheel_name]
    )
    if verified != store_wheel:
        raise ValueError('legacy PM wheel store path changed')
    return {'path': str(verified), 'sha256': owner['files'][wheel_name]}


def pr458_flat_proof(member: Path, owner: dict[str, Any]) -> LegacyParentProof | None:
    """Capture the pre-migration flat-store identity and digest."""
    flat = _pr458_flat_wheel(member, owner)
    if flat is None:
        return None
    store_wheel, wheel_name = flat
    digest = owner['files'][wheel_name]
    dev, ino, mode = pm_wheel_store.prove_legacy_parent(
        member.parent.parent, wheel_name, digest
    )
    return LegacyParentProof((dev, ino), mode, store_wheel, wheel_name, digest)


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
        expected = {"schema", "version", "spec_sha256", "files", "selection_added"}
        if data.get("schema") in (2, 3):
            expected.add("external_wheel")
        if data.get("schema") == 3:
            expected.add("sdk_wheel")
        if (set(data) != expected
                or type(data["schema"]) is not int or data["schema"] not in (1, 2, 3)
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
        if data['schema'] in (2, 3):
            wheel = pm_wheel_store.validate(path.parent.parent, data['external_wheel'])
            expected_project = _project_source(data['version'], data['external_wheel'])
            if data['schema'] == 3:
                pm_wheel_store.validate_sdk(path.parent.parent, data['sdk_wheel'])
                expected_project = _sdk_project_source(data['version'], data['external_wheel'], data['sdk_wheel'])
            if ((path / 'pyproject.toml').read_text() != expected_project
                    or files.get(wheel.name) != data['external_wheel']['sha256']):
                raise ValueError("PM external wheel differs from member wheel")
        elif _pr458_flat_wheel(path, data) is not None:
            # Only the exact PR458 flat declaration opts into flat-store proof.
            # Other historical schema-1 workspace members keep their original
            # member ownership checks and are verified by verify_installed().
            try:
                legacy_external_wheel(path, data)
            except (OSError, ValueError) as exc:
                raise PMDependencySourceUnverified(
                    "legacy PM flat wheel source cannot be verified"
                ) from exc
        return data
    except PMDependencySourceUnverified:
        raise
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
    source_family: str | None = None
    adoption: Any = None
    adoption_generation: str | None = None
    adoption_graph: Any = None
    repeat: bool = False
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
        if self.adoption is not None:
            from . import legacy_9b904, legacy_observer, plugin
            binding = plugin.resolve_runtime_binding(checkout_root=self.root, hermes_home=self.home, profile_id=None)
            self.verify_adoption(binding)
            evidence = legacy_observer.inspect(self.root, home=self.home)
            if (evidence is None or evidence[0].get("protocol") != legacy_observer.ADOPTION_PROTOCOL
                    or evidence[0].get("adapter") != legacy_9b904.ADAPTER_ID
                    or evidence[0].get("python_identity") != binding.python_identity
                    or legacy_observer._config_state(evidence, self.root) != "installed"):
                raise PMAdmissionOutcomeUncertain("legacy adoption final binding is unverified; backup retained")
        if self.source_family is not None:
            _verify_observer_writer_before_admission(self.root, family=self.source_family)
        self._assert_current()
        if self.backup is not None:
            if self.adoption is not None:
                from .posix_transaction import read_directory_identity
                if (read_directory_identity(self.backup, ValueError, "legacy adoption backup changed")
                        != self.adoption.old_member_identity or _owned(self.backup) != self.adoption.old_owner):
                    raise PMAdmissionOutcomeUncertain("legacy adoption backup changed; evidence retained")
                legacy_9b904_transaction.remove_member(self.home, self.backup, self.adoption.old_member_identity,
                                        self.adoption.plugin_identity, self.adoption.old_owner)
            else:
                _owned(self.backup)
                shutil.rmtree(self.backup)
            self.backup = None

    def rollback(self) -> None:
        from .recovery import _root_lock
        with _root_lock(self.root):
            self._rollback()

    def _rollback(self) -> None:
        self._assert_current()
        if self.adoption is not None:
            from . import legacy_9b904, legacy_observer, plugin
            if self.repeat:
                binding = plugin.resolve_runtime_binding(checkout_root=self.root, hermes_home=self.home, profile_id=None)
                self.verify_adoption(binding)
                if (self.backup is not None or legacy_observer.inspect(self.root, home=self.home) is None):
                    raise PMAdmissionOutcomeUncertain("legacy adoption repeat evidence changed")
                return
            if legacy_observer._read(self.root / legacy_observer.MARKER) is not None:
                raise PMAdmissionOutcomeUncertain("legacy adoption marker requires review before PM rollback")
            binding = plugin.resolve_runtime_binding(checkout_root=self.root, hermes_home=self.home, profile_id=None)
            self.verify_adoption(binding)
            if self.backup is not None and _owned(self.backup) != self.adoption.old_owner:
                raise PMAdmissionOutcomeUncertain("legacy adoption backup changed; evidence retained")
        probe = _verified_call(self.root, self.python, self.home, {"operation": "probe"}, self.source_family)
        if self.added:
            _verified_call(self.root, self.python, self.home, {"operation": "remove", "name": NAME,
                           "expected": probe["expected"]}, self.source_family)
        if self.backup is not None:
            _owned(self.backup)
            failed = self.member.with_name(self.member.name + ".failed")
            try:
                failed.lstat()
            except FileNotFoundError:
                pass
            else:
                raise ValueError("PM rollback staging path already exists")
            if self.adoption is not None:
                legacy_9b904_transaction.move_member(self.member, failed, self.identity,
                                      self.adoption.plugin_identity, self.owner)
                legacy_9b904_transaction.move_member(self.backup, self.member, self.adoption.old_member_identity,
                                      self.adoption.plugin_identity, self.adoption.old_owner)
            else:
                self.member.rename(failed)
                self.backup.rename(self.member)
            if not self.added:
                current = _verified_call(self.root, self.python, self.home, {"operation": "probe"}, self.source_family)
                _verified_call(self.root, self.python, self.home, {"operation": "add", "name": NAME,
                               "expected": current["expected"]}, self.source_family)
            if self.adoption is not None:
                from . import legacy_9b904, plugin
                if _owned(self.member) != self.adoption.old_owner:
                    raise PMAdmissionOutcomeUncertain("legacy adoption previous member is unverified")
                legacy_9b904.verify_after(self.root, self.home, self.adoption, backup=failed,
                                         backup_identity=self.identity)
                binding = plugin.resolve_runtime_binding(checkout_root=self.root, hermes_home=self.home, profile_id=None)
                legacy_9b904.verify_runtime(binding, version=self.adoption.old_owner["version"])
                sdk_mode, _facts_path, _facts_identity = legacy_9b904.inspect_sdk_before(
                    self.root, binding.runtime_python, self.home)
                if (self.adoption.sdk_mode == "pm-feishu" and sdk_mode != "pm-feishu"):
                    raise PMAdmissionOutcomeUncertain("legacy adoption previous SDK is unverified")
                if (sdk_mode == "pm-feishu" and not legacy_9b904.verify_installed_sdk(
                        self.root, binding.runtime_python, self.home, self.owner['sdk_wheel'], require_local=False)):
                    raise PMAdmissionOutcomeUncertain("legacy adoption previous SDK is unverified")
            if self.adoption is not None:
                legacy_9b904_transaction.remove_member(self.home, failed, self.identity,
                                        self.adoption.plugin_identity, self.owner)
            else:
                _owned(failed)
                shutil.rmtree(failed)
            self.backup = None
        elif self.source_created:
            shutil.rmtree(self.member)

    def verify_adoption(self, binding: Any) -> None:
        """Re-prove the post-PM state immediately before no-op observer adoption."""
        from . import legacy_9b904, plugin

        if (self.adoption is None or self.adoption_generation is None or self.adoption_graph is None
                or self.source_family != legacy_9b904.ADAPTER_ID
                or type(binding) is not plugin.HermesRuntimeBinding
                or binding.checkout_root != self.root or binding.hermes_home != self.home):
            raise legacy_9b904.LegacyAdoptionRefused("legacy adoption evidence is invalid")
        self._assert_current()
        legacy_9b904.verify_after(self.root, self.home, self.adoption, backup=self.backup,
                                 backup_identity=self.adoption.old_member_identity if self.backup else None)
        current = _verified_call(self.root, self.python, self.home, {"operation": "probe"}, self.source_family)
        if (current.get("expected") != self.adoption.config.digest
                or current.get("generation") != self.adoption_generation
                or binding.runtime_python.parent.parent.resolve() != Path(self.adoption_generation).resolve()
                or plugin.resolve_runtime_binding(checkout_root=self.root, hermes_home=self.home, profile_id=None) != binding):
            raise legacy_9b904.LegacyAdoptionRefused("legacy adoption runtime or config changed")
        legacy_9b904.verify_runtime(binding, version=self.owner["version"])
        legacy_9b904.verify_pm_graph(self.root, binding.runtime_python, self.home, self.owner,
                                     self.adoption_generation, self.adoption.config.digest,
                                     expected=self.adoption_graph)


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


def _verify_observer_writer_before_admission(root: Path, *, family: str | None = None) -> str | None:
    """Refuse an unknown legacy writer before PM can publish a generation.

    Official plugin enable happens after PM admission. A later writer refusal
    cannot undo arbitrary changes made by that earlier upstream transaction.
    Older installs that do not use the legacy observer retain their path.
    """
    from . import legacy_9b904, legacy_observer
    from .plugin_config_bytes import ConfigBytesRefused, verify_launch_sources

    try:
        if family is None:
            if not legacy_observer.available(root):
                return None
            try:
                return verify_launch_sources(root)
            except ConfigBytesRefused:
                legacy_9b904.verify_source(root)
                return legacy_9b904.ADAPTER_ID
        if family == legacy_9b904.ADAPTER_ID:
            legacy_9b904.verify_source(root)
            return family
        return verify_launch_sources(root, family=family)
    except (ConfigBytesRefused, legacy_9b904.LegacyAdoptionRefused, OSError) as exc:
        raise ValueError("official config writer is unknown; PM admission refused") from exc


def _verified_call(root: Path, python: Path, home: Path | None,
                   request: dict[str, Any], family: str | None) -> dict[str, Any]:
    if family is not None:
        from . import legacy_9b904
        from .legacy_9b904_contract import SOURCE_SHA256
        from .plugin_config_bytes import FAMILIES, LAUNCH_SOURCES
        _verify_observer_writer_before_admission(root, family=family)
        sources = (SOURCE_SHA256 if family == legacy_9b904.ADAPTER_ID
                   else {**FAMILIES[family], **LAUNCH_SOURCES[family]})
        request = {**request, "source_digests": sources}
    return _call(root, python, home, request)


def _complete_adoption(registration: Registration, evidence: Any) -> Registration:
    from . import legacy_9b904, plugin
    try:
        binding = plugin.resolve_runtime_binding(checkout_root=registration.root,
                                                 hermes_home=registration.home, profile_id=None)
        current = _verified_call(registration.root, binding.runtime_python, registration.home,
                                 {"operation": "probe"}, registration.source_family)
        legacy_9b904.verify_after(registration.root, registration.home, evidence, backup=registration.backup,
                                 backup_identity=evidence.old_member_identity if registration.backup else None)
        if (current.get("expected") != evidence.config.digest
                or NAME not in current.get("enabled", [])
                or plugin.PLUGIN_KEY not in current.get("enabled", [])):
            raise legacy_9b904.LegacyAdoptionRefused("legacy adoption PM selection changed")
        generation = current.get("generation")
        if (type(generation) is not str or not Path(generation).is_absolute()
                or binding.runtime_python.parent.parent.resolve() != Path(generation).resolve()):
            raise legacy_9b904.LegacyAdoptionRefused("legacy adoption selected runtime changed")
        legacy_9b904.verify_runtime(binding, version=registration.owner["version"])
        registration.python = binding.runtime_python
        registration.adoption = evidence
        registration.adoption_generation = generation
        registration.adoption_graph = legacy_9b904.verify_pm_graph(
            registration.root, binding.runtime_python, registration.home, registration.owner,
            generation, evidence.config.digest, expected=evidence.graph,
        )
        registration.verify_adoption(binding)
        return registration
    except (OSError, ValueError) as exc:
        raise PMAdmissionOutcomeUncertain(
            "PM admission outcome uncertain; owned dependency source retained for diagnosis"
        ) from exc


def _ensure(root: Path, python: Path, *, home: Path | None, spec: str | None,
            version: str) -> Registration | None:
    family = _verify_observer_writer_before_admission(root)
    from . import legacy_9b904
    if family == legacy_9b904.ADAPTER_ID and not _ADOPTION_ENABLED:
        raise ValueError("legacy adoption transaction is not yet supported; PM admission refused")
    if family == legacy_9b904.ADAPTER_ID:
        legacy_9b904.verify_unmanaged_scope()
    probe = _verified_call(root, python, home, {"operation": "probe"}, family)
    selected_home = Path(probe["home"])
    member = selected_home / "plugins" / NAME
    if family is not None:
        _verify_observer_writer_before_admission(root, family=family)
    _safe_directory(selected_home)
    old = _owned(member)
    try:
        adoption = (legacy_9b904.capture_admission(root, python, selected_home, probe, old)
                    if family == legacy_9b904.ADAPTER_ID else None)
    except legacy_9b904.LegacySDKUnpreservable as exc:
        raise ValueError(
            "Current Feishu SDK version or dependencies cannot be safely preserved; "
            "the existing SDK was retained and PM admission was not started"
        ) from exc
    legacy_proof = (pr458_flat_proof(member, old)
                    if old is not None and old.get('schema') == 1 else None)
    if NAME in probe['disabled']:
        raise ValueError("HFC PM dependency member is explicitly disabled")
    spec_digest = _install_spec_digest(spec)
    if adoption is not None and cast(dict[str, Any], old)['schema'] == 3:
        verified_old = cast(dict[str, Any], old)
        if verified_old['version'] != version or (spec is not None and verified_old['spec_sha256'] != spec_digest):
            raise ValueError("legacy adoption upgrade requires a verified observer rebind")
        registration = Registration(root, python, selected_home, member, None, False,
                                    source_family=family, repeat=True)
        return _complete_adoption(registration, adoption)
    reusable_schema = 3 if adoption is not None else 2
    if old is not None and old['schema'] == reusable_schema and old['version'] == version and (spec is None or old['spec_sha256'] == spec_digest):
        if adoption is not None:
            legacy_9b904.verify_before(root, selected_home, adoption)
        _verified_call(root, python, selected_home, {"operation": "add", "name": NAME,
                       "expected": probe["expected"]}, family)
        if adoption is not None:
            registration = Registration(root, python, selected_home, member, None, False,
                                        source_family=family)
            return _complete_adoption(registration, adoption)
        return (Registration(root, python, selected_home, member, None, True, source_family=family)
                if NAME not in probe['enabled'] else None)
    wheel_spec = spec
    if not wheel_spec:
        if adoption is None or old is None or old['version'] != version or old['schema'] != 2:
            raise ValueError("PM persistence needs the exact HFC install spec; set HFC_INSTALL_SPEC")
        # A same-version schema-2 upgrade uses the previously verified wheel;
        # it must not keep the old single-dependency declaration.
        wheel_spec = old['external_wheel']['path']
        spec_digest = old['spec_sha256']
    _safe_directory(member.parent, create=True)
    from .posix_transaction import read_directory_identity
    parent_identity = read_directory_identity(member.parent, ValueError, "PM dependency parent changed")
    old_identity = (read_directory_identity(member, ValueError, "PM dependency source changed")
                    if old is not None else None)
    staging = Path(tempfile.mkdtemp(prefix=".hfc-runtime-", dir=member.parent))
    staging_identity = read_directory_identity(staging, ValueError, "PM staging directory changed")
    backup = None
    planned_owner = None
    added = NAME not in probe['enabled']
    try:
        built = staging / 'build'
        built.mkdir()
        source = Path(wheel_spec).expanduser()
        if source.suffix == '.whl' and _regular(source):
            shutil.copyfile(source, built / source.name)
            wheels = [built / source.name]
        else:
            try:
                result = subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps',
                                         '--wheel-dir', str(built), wheel_spec], capture_output=True, text=True,
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
        if family is not None:
            _verify_observer_writer_before_admission(root, family=family)
        if adoption is not None:
            legacy_9b904.verify_before(root, selected_home, adoption, backup=staging,
                                       backup_identity=staging_identity)
        with _legacy_parent_guard(selected_home, member, old, legacy_proof):
            external_wheel = pm_wheel_store.publish(
                selected_home, wheel_name, (staging / 'runtime.whl').read_bytes(),
                expected_parent_identity=(legacy_proof.store_identity
                                          if legacy_proof is not None else None),
            )
            sdk_wheel = legacy_9b904.prepare_sdk_wheel(selected_home) if adoption is not None else None
            if adoption is not None:
                legacy_9b904.verify_before(root, selected_home, adoption, backup=staging,
                                           backup_identity=staging_identity, sdk=sdk_wheel)
            project = (_sdk_project_source(version, external_wheel, sdk_wheel) if sdk_wheel is not None
                       else _project_source(version, external_wheel))
            # Ownership uses a fixed logical wheel key; actual filename is recorded below.
            (staging / 'pyproject.toml').write_text(project)
            (staging / 'runtime.whl').rename(staging / wheel_name)
            owner = {"schema": 3 if sdk_wheel is not None else 2, "external_wheel": external_wheel,
                     **({"sdk_wheel": sdk_wheel} if sdk_wheel is not None else {}),
                     "version": version, "spec_sha256": spec_digest,
                     "selection_added": old['selection_added'] if old else added,
                     "files": {name: sha256((staging / name).read_bytes()).hexdigest()
                               for name in ('pyproject.toml', wheel_name)}}
            planned_owner = owner
            (staging / OWNER).write_text(json.dumps(owner))
            if family is not None:
                _verify_observer_writer_before_admission(root, family=family)
            backup = _publish_member(member, staging, old, parent_identity, old_identity)
            registration = Registration(root, python, selected_home, member, backup, added, old is None, family)
            try:
                if adoption is not None:
                    legacy_9b904.verify_before(root, selected_home, adoption, backup=backup,
                                               backup_identity=adoption.old_member_identity, sdk=sdk_wheel)
                    if backup is None or _owned(backup) != adoption.old_owner:
                        raise PMAdmissionOutcomeUncertain("legacy adoption backup changed; evidence retained")
                _verified_call(root, python, selected_home, {"operation": "add", "name": NAME,
                               "expected": probe['expected']}, family)
                if adoption is not None:
                    return _complete_adoption(registration, adoption)
            except PMAdmissionOutcomeUncertain:
                # A failed child or its post-admission checks cannot prove that
                # PM did not commit, even if a later snapshot looks unchanged.
                raise
            except ValueError as exc:
                if adoption is not None:
                    # This branch can only be reached before the guarded PM
                    # call. Restore the previous member only after the current
                    # selection and generation are re-proved; unknown evidence
                    # leaves the published candidate and backup for review.
                    try:
                        current = _verified_call(root, python, selected_home, {"operation": "probe"}, family)
                        if (backup is None or current.get("expected") != probe.get("expected")
                                or current.get("generation") != probe.get("generation")):
                            raise PMAdmissionOutcomeUncertain("legacy adoption PM state changed")
                        legacy_9b904_transaction.remove_member(selected_home, member, registration.identity,
                                                adoption.plugin_identity, registration.owner)
                        legacy_9b904_transaction.move_member(backup, member, adoption.old_member_identity,
                                              adoption.plugin_identity, adoption.old_owner)
                    except (OSError, ValueError) as cleanup_exc:
                        raise PMAdmissionOutcomeUncertain(
                            "legacy adoption source retained for diagnosis"
                        ) from cleanup_exc
                    raise ValueError("legacy adoption refused; previous dependency source restored") from exc
                # Re-read through the official reader. Only an unchanged selection
                # proves a refused admission; unknown outcomes retain their source.
                current = _verified_call(root, python, selected_home, {"operation": "probe"}, family)
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
                if not stat.S_ISDIR(metadata.st_mode) or pm_wheel_store.is_reparse(metadata):
                    raise ValueError("PM staging changed; refusing cleanup")
                if adoption is not None:
                    original_error = sys.exc_info()[1]
                    if (metadata.st_dev, metadata.st_ino) != staging_identity:
                        raise PMAdmissionOutcomeUncertain("legacy adoption staging changed; retained") from original_error
                    try:
                        staged_owner = _owned(staging)
                        if planned_owner is None or staged_owner != planned_owner:
                            raise ValueError("legacy adoption staging is incomplete or changed")
                        legacy_9b904_transaction.remove_member(selected_home, staging, staging_identity,
                                                parent_identity, cast(dict[str, Any], planned_owner))
                    except (OSError, ValueError) as exc:
                        raise PMAdmissionOutcomeUncertain(
                            "legacy adoption staging retained for diagnosis"
                        ) from (original_error or exc)
                else:
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
    if owner['schema'] == 3:
        # The SDK-owning member requires a coordinated PM/observer removal;
        # the older single-wheel removal must not discard its generation.
        raise ValueError("legacy adoption PM removal is not yet supported; dependency retained")
    if not owner['selection_added']:
        raise ValueError("HFC did not own the pre-existing PM selection; dependency retained")
    _call(root, python, Path(probe['home']), {"operation": "remove", "name": NAME,
                                            "expected": probe['expected']})
    if _owned(member) != owner:
        raise ValueError("PM dependency changed during removal; source retained")
    shutil.rmtree(member)


_VERIFY_CHILD = """import sys,json,hashlib,os,stat,importlib.metadata as metadata,importlib.util
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname
request = json.loads(sys.stdin.read())
if any(name=='hermes_feishu_card' or name.startswith('hermes_feishu_card.') for name in sys.modules):
    raise RuntimeError('HFC was loaded before verification')
spec = importlib.util.find_spec('hermes_feishu_card')
if spec is None or not spec.origin:
    raise RuntimeError('HFC source is unavailable')
root = Path(spec.origin).parent.parent
wheel = json.loads(metadata.distribution('hermes-feishu-streaming-card').read_text('direct_url.json') or '{}')
source = urlsplit(wheel.get('url', ''))
archive = wheel.get('archive_info')
if archive is None:
    archive = {}
hashes = archive.get('hashes') if isinstance(archive, dict) else None
actual = hashes.get('sha256') if isinstance(hashes, dict) else None
external = request.get('external_wheel')
source_ok = True
archive_ok = isinstance(archive, dict) and (hashes is None or isinstance(hashes, dict))
if external is not None:
    external_path = Path(external['path'])
    source_ok = (source.scheme == 'file' and not source.netloc and not source.query
                 and not source.fragment and external_path.is_absolute()
                 and Path(url2pathname(source.path)) == external_path)
    if source_ok:
        path = external_path
        before = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
        try:
            opened = os.fstat(descriptor)
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 65536):
                digest.update(chunk)
        finally:
            os.close(descriptor)
        after = path.lstat()
        if (stat.S_ISREG(before.st_mode) and stat.S_ISREG(opened.st_mode)
                and not any(getattr(item, 'st_file_attributes', 0) & 0x400
                            for item in (before, opened, after))
                and (before.st_dev,before.st_ino) == (opened.st_dev,opened.st_ino)
                == (after.st_dev,after.st_ino)):
            source_ok = digest.hexdigest() == request['wheel_sha256']
        else:
            source_ok = False
    if actual is not None and actual != request['wheel_sha256']:
        archive_ok = False
elif actual is None and source.scheme == 'file' and not source.netloc:
    path = Path(url2pathname(source.path)).resolve()
    workspace = Path(sys.prefix).parent / 'workspace'
    if workspace.resolve() in path.parents:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
if external is None:
    archive_ok = actual == request['wheel_sha256']
dir_info = wheel.get('dir_info') or {}
editable = dir_info.get('editable') if isinstance(dir_info, dict) else True
expected_version = request.get('wheel_version')
version_ok = (expected_version is None
              or metadata.version('hermes-feishu-streaming-card') == expected_version)
ok = source_ok and archive_ok and version_ok and not editable and all(
    (root / name).is_file() and hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    for name, digest in request['files'].items())
print(json.dumps({'ok': ok}))
"""


def verify_installed(member: Path, python: Path) -> bool:
    """Compare every package resource with the exact cached ordinary wheel."""
    identity = member.lstat()
    try:
        owner = _owned(member)
    except (OSError, ValueError):
        return False
    if owner is None:
        return False
    wheel_name = next(name for name in owner['files'] if name.endswith('.whl'))
    wheel = member / wheel_name
    external: dict[str, Any] | None
    wheel_version: str | None
    flat = False
    if owner['schema'] == 1:
        flat = _pr458_flat_wheel(member, owner) is not None
        if flat:
            try:
                external = legacy_external_wheel(member, owner)
            except (OSError, ValueError):
                return False
            wheel_version = owner['version']
        else:
            # Preserve the original schema-1 workspace verification path. The
            # child accepts an archive hash or a file URL below the runtime's
            # sibling workspace directory, exactly as v5.0.8 did.
            external = None
            wheel_version = None
    else:
        external = owner.get('external_wheel')
        wheel_version = owner['version']
    with zipfile.ZipFile(wheel) as archive:
        hashes = {name: sha256(archive.read(name)).hexdigest() for name in archive.namelist()
                  if name.startswith('hermes_feishu_card/') and not name.endswith('/')}
    try:
        before_child = member.lstat()
        if ((identity.st_dev, identity.st_ino) != (before_child.st_dev, before_child.st_ino)
                or _owned(member) != owner):
            return False
        result = subprocess.run([str(python), '-I', '-B', '-c', _VERIFY_CHILD], input=json.dumps(
            {'wheel_sha256': owner['files'][wheel_name], 'files': hashes,
             'external_wheel': external, 'wheel_version': wheel_version}),
            capture_output=True, text=True, cwd=str(member), timeout=30)
        after = member.lstat()
        sdk_ok = True
        if owner['schema'] == 3:
            from .legacy_9b904 import verify_installed_sdk
            sdk_ok = verify_installed_sdk(member, python, member.parent.parent, owner['sdk_wheel'])
        return (result.returncode == 0 and json.loads(result.stdout)['ok'] is True and sdk_ok
                and (identity.st_dev, identity.st_ino) == (after.st_dev, after.st_ino)
                and _owned(member) == owner
                and (not flat or legacy_external_wheel(member, owner) == external))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False
