"""Narrow evidence checks for adopting an already-enabled 9b904 configuration.

This is only the config-state part of the contract. It does not authorize PM
admission, prove a runtime/entrypoint, or publish an observer receipt by itself.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from typing import Any
import urllib.request

from . import plugin_config_bytes, plugin_config_delta, posix_transaction
from .legacy_9b904_contract import SOURCE_SHA256

ADAPTER_ID = "hermes-9b904-existing-identity-v1"
UPSTREAM_COMMIT = "9b904dcc22c23fe8b82834339bbdd96495072e59"
SDK_URL = ("https://files.pythonhosted.org/packages/21/7b/54d1df1f73ac1339b7df051982d386223202add5560a97839cf6326e6adc/"
           "lark_oapi-1.7.5-py3-none-any.whl")
SDK_SIZE = 7711907
_MANAGED_SCOPE = Path("/etc/hermes")


class LegacyAdoptionRefused(ValueError):
    """The current config cannot be adopted without an unproved change."""


class LegacySDKUnpreservable(LegacyAdoptionRefused):
    """The selected SDK cannot be reconstructed by the official PM graph."""


def verify_unmanaged_scope() -> None:
    """Do not adopt raw config when a managed overlay may change its meaning."""
    if os.environ.get("HERMES_MANAGED_DIR", "").strip():
        raise LegacyAdoptionRefused("legacy adoption managed configuration is unsupported")
    try:
        _MANAGED_SCOPE.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise LegacyAdoptionRefused("legacy adoption managed configuration is uninspectable") from exc
    raise LegacyAdoptionRefused("legacy adoption managed configuration is unsupported")


@dataclass(frozen=True)
class ExistingConfig:
    """The exact config preimage; the zero-delta postimage is identical."""

    contents: bytes
    digest: str


@dataclass(frozen=True)
class BootEvidence:
    path: Path
    snapshot: tuple[int, int, str]
    directories: dict[Path, tuple[int, int]]


@dataclass(frozen=True)
class GraphEvidence:
    facts_path: Path
    facts_identity: tuple[int, int, str]
    facts_version: tuple[int, int, int, int, int]
    lock_path: Path
    lock_identity: tuple[int, int, str]
    lock_version: tuple[int, int, int, int, int]
    directories: dict[Path, tuple[int, int]]


@dataclass(frozen=True)
class EnvironmentEvidence:
    files: dict[Path, tuple[tuple[int, int, str] | None, tuple[int, int, int, int, int] | None]]
    directories: dict[Path, tuple[int, int]]


@dataclass(frozen=True)
class AdmissionEvidence:
    config: ExistingConfig
    config_identity: tuple[int, int, str]
    home_identity: tuple[int, int]
    plugin_identity: tuple[int, int]
    boot: BootEvidence
    generation: str
    python: Path
    old_owner: dict[str, Any]
    old_member_identity: tuple[int, int]
    sdk_mode: str
    facts_path: Path
    facts_identity: tuple[int, int, str]
    marker_identity: tuple[int, int, str] | None
    marker_files: dict[Path, tuple[int, int, str]]
    marker_directories: dict[Path, tuple[int, int]]
    graph: GraphEvidence | None
    environment: EnvironmentEvidence


def verify_source(root: Path) -> None:
    """Verify this adapter's pinned PM inputs; the PM worker remains trusted."""
    try:
        absolute = plugin_config_bytes._verify_launch_root(root)
        for name, digest in SOURCE_SHA256.items():
            path = plugin_config_bytes._launch_path(absolute, name)
            if plugin_config_bytes._digest(path) != digest:
                raise LegacyAdoptionRefused("legacy adoption source is unknown")
        git = shutil.which("git")
        if not git:
            raise LegacyAdoptionRefused("legacy adoption source is unknown")
        result = subprocess.run(
            [git, "-C", str(absolute), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
            env={"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if result.returncode or result.stdout.strip() != UPSTREAM_COMMIT:
            raise LegacyAdoptionRefused("legacy adoption source is unknown")
    except (OSError, subprocess.SubprocessError, plugin_config_bytes.ConfigBytesRefused) as exc:
        raise LegacyAdoptionRefused("legacy adoption source is unknown") from exc


def _read_evidence(path: Path) -> tuple[tuple[int, int, str], bytes]:
    snapshot, payload, _mode = posix_transaction.read_file_state(
        path, parent_fd=None, missing_allowed=False, refused=LegacyAdoptionRefused,
        non_regular_refusal="legacy adoption evidence is unsafe",
        changed_refusal="legacy adoption evidence changed", wrap_read_error=True,
    )
    if snapshot is None or payload is None or len(payload) > 2 * 1024 * 1024:
        raise LegacyAdoptionRefused("legacy adoption evidence is unavailable")
    return snapshot, payload


def _read_versioned_evidence(path: Path, message: str) -> tuple[tuple[int, int, str], bytes, tuple[int, int, int, int, int]]:
    def version(info: os.stat_result) -> tuple[int, int, int, int, int]:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    try:
        before = version(path.lstat())
        identity, payload = _read_evidence(path)
        after = version(path.lstat())
    except (OSError, ValueError) as exc:
        raise LegacyAdoptionRefused(message) from exc
    if before != after or identity[:2] != before[:2]:
        raise LegacyAdoptionRefused(message)
    return identity, payload, after


def _assert_versioned_evidence(path: Path, identity: tuple[int, int, str],
                               version: tuple[int, int, int, int, int], message: str) -> None:
    current_identity, _payload, current_version = _read_versioned_evidence(path, message)
    if current_identity != identity or current_version != version:
        raise LegacyAdoptionRefused(message)


_DOTENV_KEYS_CHILD = r'''
import base64, io, json, sys
from dotenv.parser import parse_stream
request=json.loads(sys.stdin.read())
for encoded in request['files']:
    raw=base64.b64decode(encoded,validate=True)
    text=raw.decode('utf-8-sig')
    if '\x00' in text: raise RuntimeError('unsupported dotenv encoding')
    for item in parse_stream(io.StringIO(text)):
        if item.error: raise RuntimeError('unparseable dotenv')
        if item.key=='HERMES_MANAGED_DIR': raise RuntimeError('managed scope assignment')
print(json.dumps({'clear':True}))
'''


def inspect_unmanaged_environment(root: Path, python: Path, home: Path) -> EnvironmentEvidence:
    """Check only bootstrap key names, without executing dotenv or secret providers."""
    verify_unmanaged_scope()
    paths = [home / '.env', home / '.op.env', root / '.env']
    directories = {
        path: posix_transaction.read_directory_identity(path, LegacyAdoptionRefused,
                                                         "legacy adoption environment directory is unsafe")
        for path in (root, home)
    }
    files: dict[Path, tuple[tuple[int, int, str] | None, tuple[int, int, int, int, int] | None]] = {}
    contents = []
    for path in paths:
        try:
            path.lstat()
        except FileNotFoundError:
            files[path] = (None, None)
        except OSError as exc:
            raise LegacyAdoptionRefused("legacy adoption environment is uninspectable") from exc
        else:
            identity, payload, version = _read_versioned_evidence(path, "legacy adoption environment is uninspectable")
            files[path] = (identity, version)
            contents.append(base64.b64encode(payload).decode('ascii'))
    evidence = EnvironmentEvidence(files, directories)
    verify_unmanaged_environment(evidence)
    if contents:
        try:
            result = subprocess.run([str(python), '-I', '-B', '-c', _DOTENV_KEYS_CHILD],
                                    input=json.dumps({'files': contents}), capture_output=True, text=True,
                                    timeout=30, check=False, cwd=str(root),
                                    env=plugin_config_bytes.isolated_child_env(home))
            if result.returncode or json.loads(result.stdout) != {'clear': True}:
                raise LegacyAdoptionRefused("legacy adoption environment bootstrap is unsupported")
        except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
            if isinstance(exc, LegacyAdoptionRefused):
                raise
            raise LegacyAdoptionRefused("legacy adoption environment bootstrap is uninspectable") from exc
    verify_unmanaged_environment(evidence)
    return evidence


def verify_unmanaged_environment(evidence: EnvironmentEvidence) -> None:
    verify_unmanaged_scope()
    for path, directory_identity in evidence.directories.items():
        posix_transaction.assert_directory_snapshot(path, directory_identity, LegacyAdoptionRefused,
                                                     "legacy adoption environment directory changed",
                                                     allow_original_absence=False)
    for path, (identity, version) in evidence.files.items():
        if identity is None:
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise LegacyAdoptionRefused("legacy adoption environment is uninspectable") from exc
            raise LegacyAdoptionRefused("legacy adoption environment changed")
        if version is None:
            raise LegacyAdoptionRefused("legacy adoption environment is uninspectable")
        _assert_versioned_evidence(path, identity, version, "legacy adoption environment changed")


def inspect_boot(root: Path, home: Path) -> BootEvidence:
    """Only the default profile with a completed, up-to-date boot is accepted."""
    if home.parent.name == "profiles":
        raise LegacyAdoptionRefused("legacy adoption profile is unsupported")
    key = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    record = home / "installs" / key / "bootstrap" / "default.json"
    try:
        _, directories = posix_transaction.snapshot_controlled_ancestries(
            home, [record], LegacyAdoptionRefused,
            outside_refusal="legacy adoption boot is outside home",
            unsafe_refusal="legacy adoption boot directory is unsafe",
        )
        snapshot, contents = _read_evidence(record)
        data = json.loads(contents.decode("utf-8-sig"))
        migration = data.get("results", {}).get("migrate_config", {})
        if (type(data) is not dict or data.get("identity") != UPSTREAM_COMMIT
                or type(migration) is not dict or migration.get("ok") is not True
                or migration.get("skipped") != "up-to-date"):
            raise LegacyAdoptionRefused("legacy adoption boot is unverified")
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, LegacyAdoptionRefused):
            raise
        raise LegacyAdoptionRefused("legacy adoption boot is unverified") from exc
    return BootEvidence(record, snapshot, directories)


def verify_boot(evidence: BootEvidence) -> None:
    for path, identity in evidence.directories.items():
        posix_transaction.assert_directory_snapshot(
            path, identity, LegacyAdoptionRefused, "legacy adoption boot directory changed",
            allow_original_absence=False,
        )
    posix_transaction.assert_path_snapshot(
        evidence.path, evidence.snapshot, LegacyAdoptionRefused, "legacy adoption boot changed"
    )


_CANONICAL_CHILD = r'''
import base64, hashlib, io, json, sys, types
request=json.loads(sys.stdin.read())
source=base64.b64decode(request['source'],validate=True)
before=base64.b64decode(request['config'],validate=True)
if hashlib.sha256(source).hexdigest()!=request['digest']:
    raise RuntimeError('serializer source changed')
module=types.ModuleType('hermes_yaml')
exec(compile(source,'<verified-hermes-yaml>','exec',dont_inherit=True),module.__dict__)
yaml=module.roundtrip_yaml(); document=yaml.load(before.decode('utf-8-sig'))
plugins=document['plugins']
plugins['enabled']=sorted(plugins.get('enabled') or [])
plugins['disabled']=sorted(plugins.get('disabled') or [])
output=io.StringIO(); yaml.dump(document,output)
print(json.dumps({'canonical':output.getvalue().encode('utf-8')==before}))
'''


def require_canonical(root: Path, python: Path, home: Path, contents: bytes) -> None:
    """PM must round-trip the supported config without changing any bytes."""
    path = plugin_config_bytes._launch_path(root, "hermes_yaml.py")
    _snapshot, source = _read_evidence(path)
    if sha256(source).hexdigest() != SOURCE_SHA256["hermes_yaml.py"]:
        raise LegacyAdoptionRefused("legacy adoption serializer is unknown")
    request = {"source": base64.b64encode(source).decode("ascii"),
               "config": base64.b64encode(contents).decode("ascii"),
               "digest": SOURCE_SHA256["hermes_yaml.py"]}
    try:
        result = subprocess.run(
            [str(python), "-I", "-B", "-c", _CANONICAL_CHILD], input=json.dumps(request),
            capture_output=True, text=True, timeout=30, check=False,
            cwd=str(root), env=plugin_config_bytes.isolated_child_env(home),
        )
        if result.returncode or json.loads(result.stdout) != {"canonical": True}:
            raise LegacyAdoptionRefused("legacy adoption config is not canonical")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        if isinstance(exc, LegacyAdoptionRefused):
            raise
        raise LegacyAdoptionRefused("legacy adoption config is not canonical") from exc


def verify_runtime(binding: object, *, version: str) -> None:
    """Require the owned wheel and one HFC entrypoint without capability claims."""
    from . import plugin, pm_dependency

    if type(binding) is not plugin.HermesRuntimeBinding:
        raise LegacyAdoptionRefused("legacy adoption runtime is unverified")
    member = binding.hermes_home / "plugins" / pm_dependency.NAME
    owner = pm_dependency._owned(member)
    if (owner is None or owner.get("version") != version
            or not pm_dependency.verify_installed(member, binding.runtime_python)
            or plugin.probe_plugin_entrypoint(binding, expected_version=version).status != "verified"):
        raise LegacyAdoptionRefused("legacy adoption runtime is unverified")
    program = r'''
import importlib.metadata as metadata, json
entries=metadata.entry_points().select(group='hermes_agent.plugin_capabilities')
claims=[entry for entry in entries if entry.name.startswith('hermes-feishu-card.')
        and entry.value=='hermes_feishu_card.hermes_plugin']
print(json.dumps({'clear': not claims}))
'''
    try:
        result = subprocess.run(
            [str(binding.runtime_python), "-I", "-B", "-c", program], capture_output=True,
            text=True, timeout=30, check=False, cwd=str(binding.checkout_root),
            env=plugin_config_bytes.isolated_child_env(binding.hermes_home),
        )
        if result.returncode or json.loads(result.stdout) != {"clear": True}:
            raise LegacyAdoptionRefused("legacy adoption capabilities are unverified")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        if isinstance(exc, LegacyAdoptionRefused):
            raise
        raise LegacyAdoptionRefused("legacy adoption capabilities are unverified") from exc
    if pm_dependency._owned(member) != owner:
        raise LegacyAdoptionRefused("legacy adoption runtime changed")


def verify_plugin_namespace(home: Path, *, backup: Path | None = None,
                            backup_identity: tuple[int, int] | None = None) -> tuple[int, int]:
    """The first cohort has only HFC's owned PM member in this home."""
    directory = home / "plugins"
    identity = posix_transaction.read_directory_identity(
        directory, LegacyAdoptionRefused, "legacy adoption plugin directory is unsafe"
    )
    try:
        for entry in directory.iterdir():
            metadata = entry.lstat()
            if stat.S_ISDIR(metadata.st_mode) and entry.name == "hfc-runtime-dependency":
                continue
            if (stat.S_ISDIR(metadata.st_mode) and entry == backup and backup_identity is not None
                    and (metadata.st_dev, metadata.st_ino) == backup_identity):
                continue
            raise LegacyAdoptionRefused("legacy adoption plugin namespace is unsupported")
    except OSError as exc:
        raise LegacyAdoptionRefused("legacy adoption plugin namespace is unavailable") from exc
    posix_transaction.assert_directory_snapshot(
        directory, identity, LegacyAdoptionRefused, "legacy adoption plugin directory changed",
        allow_original_absence=False,
    )
    return identity


_SDK_STATE_CHILD = r'''
import importlib.metadata as metadata, importlib.util, json, os, sysconfig
items=list(metadata.distributions(name='lark-oapi'))
spec=importlib.util.find_spec('lark_oapi')
locations={sysconfig.get_path(key) for key in ('purelib','platlib')}
present=False
for location in locations:
    if not location: raise RuntimeError('site-packages unavailable')
    with os.scandir(location) as entries:
        for entry in entries:
            name=entry.name.lower().replace('-','_')
            if name=='lark_oapi' or name.startswith(('lark_oapi.','lark_oapi_')):
                present=True
print(json.dumps({'count':len(items),'version':items[0].version if len(items)==1 else None,
                  'module_present':spec is not None,'path_present':present}))
'''


def probe_sdk(root: Path, python: Path, home: Path) -> dict[str, Any]:
    """Distinguish a missing SDK from incomplete or uninspectable metadata."""
    try:
        result = subprocess.run(
            [str(python), "-I", "-B", "-c", _SDK_STATE_CHILD], capture_output=True, text=True,
            cwd=str(root), env=plugin_config_bytes.isolated_child_env(home), timeout=30, check=False,
        )
        data = json.loads(result.stdout)
        if (result.returncode or type(data) is not dict or set(data) != {"count", "version", "module_present", "path_present"}
                or type(data['count']) is not int or type(data['module_present']) is not bool
                or type(data['path_present']) is not bool
                or (data['version'] is not None and type(data['version']) is not str)):
            raise LegacyAdoptionRefused("legacy adoption Feishu SDK is uninspectable")
        return data
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        if isinstance(exc, LegacyAdoptionRefused):
            raise
        raise LegacyAdoptionRefused("legacy adoption Feishu SDK is uninspectable") from exc


def inspect_sdk_before(root: Path, python: Path, home: Path, *, owned: bool = False) -> tuple[str, Path, tuple[int, int, str]]:
    state = probe_sdk(root, python, home)
    key = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    facts_path = home / "installs" / key / "facts.json"
    try:
        posix_transaction.snapshot_controlled_ancestries(
            home, [facts_path], LegacyAdoptionRefused,
            outside_refusal="legacy adoption PM facts are outside home",
            unsafe_refusal="legacy adoption PM facts directory is unsafe",
        )
        facts_identity, contents = _read_evidence(facts_path)
        facts = json.loads(contents.decode("utf-8-sig"))
        extras = facts["packages"]["venv"]["extras"]
        if type(extras) is not list or any(type(item) is not str for item in extras):
            raise LegacyAdoptionRefused("legacy adoption PM extras are unverified")
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, LegacyAdoptionRefused):
            raise
        raise LegacyAdoptionRefused("legacy adoption PM facts are unverified") from exc
    if state == {"count": 0, "version": None, "module_present": False, "path_present": False} and "feishu" not in extras:
        return "absent", facts_path, facts_identity
    if owned and state == {"count": 1, "version": "1.7.5", "module_present": True, "path_present": True}:
        return "owned", facts_path, facts_identity
    if state == {"count": 1, "version": "1.7.5", "module_present": True, "path_present": True} and "feishu" in extras:
        return "pm-feishu", facts_path, facts_identity
    raise LegacySDKUnpreservable("legacy adoption cannot preserve the existing Feishu SDK version and dependencies")


def prepare_sdk_wheel(home: Path) -> dict[str, str]:
    """Fetch only the exact wheel already pinned by this upstream source family."""
    from . import pm_wheel_store

    digest = pm_wheel_store._SDK_WHEEL_SHA256
    name = pm_wheel_store._SDK_WHEEL_NAME
    path = home / '.hermes_feishu_card' / 'wheel-store' / digest / name
    reference = {"path": str(path), "sha256": digest}
    try:
        path.lstat()
    except FileNotFoundError:
        try:
            with urllib.request.urlopen(SDK_URL, timeout=30) as response:
                payload = response.read(SDK_SIZE + 1)
        except (OSError, ValueError) as exc:
            raise LegacyAdoptionRefused("legacy adoption SDK source is unavailable") from exc
        if len(payload) != SDK_SIZE or sha256(payload).hexdigest() != digest:
            raise LegacyAdoptionRefused("legacy adoption SDK source is unverified") from None
        return pm_wheel_store.publish_sdk(home, payload)
    except OSError as exc:
        raise LegacyAdoptionRefused("legacy adoption SDK source is unavailable") from exc
    pm_wheel_store.validate_sdk(home, reference)
    return reference


_VERIFY_SDK_CHILD = r'''
import hashlib, importlib.abc, importlib.metadata as metadata, importlib.util, inspect, io, json, os, stat, sys, sysconfig, zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import url2pathname
request=json.loads(sys.stdin.read()); wheel=Path(request['path'])
wheel_bytes=wheel.read_bytes()
if hashlib.sha256(wheel_bytes).hexdigest()!=request['sha256']:
    raise RuntimeError('SDK wheel changed')
distributions=list(metadata.distributions(name='lark-oapi'))
if len(distributions)!=1 or distributions[0].version!='1.7.5':
    raise RuntimeError('SDK distribution is ambiguous')
direct=json.loads(distributions[0].read_text('direct_url.json') or '{}')
url=urlsplit(direct.get('url',''))
if request.get('require_local') and (url.scheme!='file' or url.netloc or url.query or url.fragment
        or Path(url2pathname(url.path))!=wheel):
    raise RuntimeError('SDK source is not the owned wheel')
root=Path(sysconfig.get_path('purelib')).resolve()
if any(name=='lark_oapi' or name.startswith('lark_oapi.') for name in sys.modules):
    raise RuntimeError('SDK was loaded before validation')
top=importlib.util.find_spec('lark_oapi')
if top is None or top.origin != str(root/'lark_oapi'/'__init__.py'):
    raise RuntimeError('SDK import is not from the inspected root')
def checked_resource(name, expected):
    path=root
    for part in name.parts:
        path=path/part
        item=path.lstat()
        if (stat.S_ISLNK(item.st_mode)
                or getattr(item,'st_file_attributes',0) & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400)):
            raise RuntimeError('SDK resource path is unsafe')
    before=path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError('SDK resource is invalid')
    payload=path.read_bytes(); after=path.lstat()
    if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
            != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
            or payload != expected):
        raise RuntimeError('SDK resource changed')
    return path
modules={}
with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
    seen=set()
    for info in archive.infolist():
        if info.is_dir() or info.filename.endswith('.dist-info/RECORD'):
            continue
        name=PurePosixPath(info.filename)
        if name.is_absolute() or '..' in name.parts or '.data' in info.filename or info.filename in seen:
            raise RuntimeError('SDK wheel path is invalid')
        seen.add(info.filename)
        source=archive.read(info)
        checked_resource(name, source)
        if name.parts[0]=='lark_oapi' and name.suffix=='.py':
            is_package=name.name=='__init__.py'
            components=name.parts[:-1] if is_package else (*name.parts[:-1],name.stem)
            fullname='.'.join(components)
            if fullname in modules:
                raise RuntimeError('SDK source is ambiguous')
            modules[fullname]=(name,source,is_package)
class CheckedLoader(importlib.abc.Loader):
    def create_module(self,spec): return None
    def exec_module(self,module):
        name,source,_package=modules[module.__name__]
        filename=checked_resource(name,source)
        exec(compile(source,str(filename),'exec',dont_inherit=True),module.__dict__)
class CheckedFinder(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname!='lark_oapi' and not fullname.startswith('lark_oapi.'):
            return None
        if fullname not in modules:
            raise ImportError('SDK module is absent from the inspected wheel')
        name,_source,is_package=modules[fullname]
        filename=root.joinpath(*name.parts)
        return importlib.util.spec_from_file_location(
            fullname,str(filename),loader=CheckedLoader(),
            submodule_search_locations=[str(filename.parent)] if is_package else None)
sys.meta_path.insert(0,CheckedFinder())
from lark_oapi.ws import Client
if 'extra_ua_tags' not in inspect.signature(Client.__init__).parameters:
    raise RuntimeError('SDK capability is missing')
print(json.dumps({'ok':True}))
'''


def verify_installed_sdk(root: Path, python: Path, home: Path, sdk: dict[str, str], *,
                         require_local: bool = True) -> bool:
    from . import pm_wheel_store
    try:
        pm_wheel_store.validate_sdk(home, sdk)
        result = subprocess.run(
            [str(python), "-I", "-B", "-c", _VERIFY_SDK_CHILD],
            input=json.dumps({**sdk, "require_local": require_local}),
            capture_output=True, text=True, cwd=str(root),
            env=plugin_config_bytes.isolated_child_env(home), timeout=60, check=False,
        )
        return (result.returncode == 0 and json.loads(result.stdout) == {"ok": True}
                and pm_wheel_store.validate_sdk(home, sdk) == Path(sdk['path']))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return False


_LOCK_CHILD = r'''
import base64, json, sys, tomllib
request=json.loads(sys.stdin.read())
lock=tomllib.loads(base64.b64decode(request['lock'],validate=True).decode('utf-8'))
packages=lock['package']
def one(name):
    matches=[p for p in packages if p.get('name')==name]
    if len(matches)!=1: raise RuntimeError('ambiguous PM lock package')
    return matches[0]
def wheel(name,version,reference):
    package=one(name)
    if package.get('version')!=version or package.get('source')!={'path':reference['path']}:
        raise RuntimeError('PM lock package source differs')
    matches=[item for item in package.get('wheels',[]) if item.get('filename')==reference['filename']
             and item.get('hash')=='sha256:'+reference['sha256']]
    if len(matches)!=1: raise RuntimeError('PM lock wheel differs')
wheel('hermes-feishu-streaming-card',request['version'],request['hfc'])
wheel('lark-oapi','1.7.5',request['sdk'])
member=one('hermes-plugin-'+request['key'])
if member.get('source')!={'virtual':'plugin-sources/'+request['key']}:
    raise RuntimeError('PM lock member differs')
dependencies=member.get('dependencies',[])
if sorted(item.get('name') for item in dependencies)!=['hermes-feishu-streaming-card','lark-oapi']:
    raise RuntimeError('PM lock member dependencies differ')
requires=member.get('metadata',{}).get('requires-dist',[])
expected={'hermes-feishu-streaming-card':request['hfc']['path'],'lark-oapi':request['sdk']['path']}
if len(requires)!=2 or {item.get('name'):item.get('path') for item in requires}!=expected:
    raise RuntimeError('PM lock member sources differ')
print(json.dumps({'ok':True}))
'''


def verify_pm_graph(root: Path, python: Path, home: Path, owner: dict[str, Any], generation: str,
                    config_digest: str, *, expected: GraphEvidence | None = None,
                    plugin_enabled: bool = True) -> GraphEvidence:
    """Bind the official current stamp, selected facts and exact resolved wheel sources."""
    from . import pm_dependency

    key = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    facts_path = home / "installs" / key / "facts.json"
    generation_path = Path(generation)
    lock_path = generation_path.parent / "workspace" / "uv.lock"
    _ancestries, directories = posix_transaction.snapshot_controlled_ancestries(
        home, [facts_path, lock_path], LegacyAdoptionRefused,
        outside_refusal="legacy adoption PM graph is outside home",
        unsafe_refusal="legacy adoption PM graph directory is unsafe",
    )
    facts_identity, facts_bytes, facts_version = _read_versioned_evidence(
        facts_path, "legacy adoption PM facts changed")
    lock_identity, lock_bytes, lock_version = _read_versioned_evidence(
        lock_path, "legacy adoption PM resolved lock changed")
    try:
        fact = json.loads(facts_bytes.decode("utf-8-sig"))["packages"]["venv"]
        if (type(fact) is not dict or fact.get("environment") != str(generation_path)
                or fact.get("resolved_lock") != str(lock_path)):
            raise LegacyAdoptionRefused("legacy adoption PM graph paths changed")
        current = pm_dependency._verified_call(root, python, home, {"operation": "current"}, ADAPTER_ID)
        if (current.get("current") is not True or current.get("generation") != generation
                or current.get("expected") != config_digest
                or pm_dependency.NAME not in current.get("enabled", [])
                or (plugin_enabled and (plugin_config_delta.KEY not in current.get("enabled", [])
                                        or plugin_config_delta.KEY in current.get("disabled", [])))
                or (not plugin_enabled and (plugin_config_delta.KEY in current.get("enabled", [])
                                            or plugin_config_delta.KEY not in current.get("disabled", [])))):
            raise LegacyAdoptionRefused("legacy adoption PM graph is not current")
        _assert_versioned_evidence(facts_path, facts_identity, facts_version, "legacy adoption PM facts changed")
        _assert_versioned_evidence(lock_path, lock_identity, lock_version, "legacy adoption PM resolved lock changed")
        member = home / "plugins" / pm_dependency.NAME
        member_key = pm_dependency.NAME + "-" + sha256(str(member.resolve()).encode("utf-8")).hexdigest()[:16]
        hfc, sdk = owner["external_wheel"], owner["sdk_wheel"]
        request = {"lock": base64.b64encode(lock_bytes).decode("ascii"), "key": member_key,
                   "version": owner["version"],
                   "hfc": {**hfc, "filename": Path(hfc["path"]).name},
                   "sdk": {**sdk, "filename": Path(sdk["path"]).name}}
        result = subprocess.run([str(python), "-I", "-B", "-c", _LOCK_CHILD],
                                input=json.dumps(request), capture_output=True, text=True, timeout=30,
                                check=False, cwd=str(root), env=plugin_config_bytes.isolated_child_env(home))
        if result.returncode or json.loads(result.stdout) != {"ok": True}:
            raise LegacyAdoptionRefused("legacy adoption PM resolved lock is unverified")
    except (OSError, UnicodeError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, LegacyAdoptionRefused):
            raise
        raise LegacyAdoptionRefused("legacy adoption PM resolved lock is unverified") from exc
    for path, identity in directories.items():
        posix_transaction.assert_directory_snapshot(path, identity, LegacyAdoptionRefused,
                                                     "legacy adoption PM graph directory changed",
                                                     allow_original_absence=False)
    _assert_versioned_evidence(facts_path, facts_identity, facts_version, "legacy adoption PM facts changed")
    _assert_versioned_evidence(lock_path, lock_identity, lock_version, "legacy adoption PM resolved lock changed")
    graph = GraphEvidence(facts_path, facts_identity, facts_version, lock_path, lock_identity, lock_version, directories)
    if expected is not None and graph != expected:
        raise LegacyAdoptionRefused("legacy adoption PM graph changed")
    return graph


def capture_admission(root: Path, python: Path, home: Path, probe: dict[str, Any],
                      owner: dict[str, Any] | None) -> AdmissionEvidence:
    """Prove a receipt-free schema-2 member or a bound schema-3 repeat."""
    from . import legacy_observer, plugin, pm_dependency

    verify_unmanaged_scope()
    environment = inspect_unmanaged_environment(root, python, home)
    if not posix_transaction._cli_dirfd_binding_supported():
        raise LegacyAdoptionRefused("legacy adoption requires secure filesystem transactions")
    marker = root / legacy_observer.MARKER
    marker_raw = legacy_observer._read(marker)
    repeat = owner is not None and owner.get("schema") == 3
    if (owner is None or owner.get("schema") not in (2, 3) or pm_dependency.NAME not in probe.get("enabled", [])
            or (repeat and marker_raw is None) or (not repeat and marker_raw is not None)):
        raise LegacyAdoptionRefused("legacy adoption requires an existing unbound PM member")
    marker_identity = (_read_evidence(marker)[0] if repeat else None)
    marker_files: dict[Path, tuple[int, int, str]] = {}
    marker_directories: dict[Path, tuple[int, int]] = {}
    config_identity, contents = _read_evidence(home / "config.yaml")
    config = inspect_existing_config(contents)
    if probe.get("expected") != config.digest:
        raise LegacyAdoptionRefused("legacy adoption config changed before admission")
    home_identity = posix_transaction.read_directory_identity(
        home, LegacyAdoptionRefused, "legacy adoption home is unsafe"
    )
    plugin_identity = verify_plugin_namespace(home)
    old_member_identity = posix_transaction.read_directory_identity(
        home / "plugins" / pm_dependency.NAME, LegacyAdoptionRefused, "legacy adoption member is unsafe"
    )
    boot = inspect_boot(root, home)
    require_canonical(root, python, home, contents)
    binding = plugin.resolve_runtime_binding(checkout_root=root, hermes_home=home, profile_id=None)
    generation = probe.get("generation")
    if (type(generation) is not str or not Path(generation).is_absolute()
            or binding.runtime_python.parent.parent.resolve() != Path(generation).resolve()
            or binding.runtime_python != python):
        raise LegacyAdoptionRefused("legacy adoption selected runtime changed")
    verify_runtime(binding, version=owner["version"])
    if repeat:
        marker_evidence = legacy_observer.inspect(root, home=home)
        if (marker_evidence is None or marker_evidence[0].get("protocol") != legacy_observer.ADOPTION_PROTOCOL
                or marker_evidence[0].get("adapter") != ADAPTER_ID
                or marker_evidence[0].get("python_identity") != binding.python_identity
                or marker_evidence[1] != marker_raw
                or marker_evidence[4] != contents
                or legacy_observer._config_state(marker_evidence, root) != "installed"):
            raise LegacyAdoptionRefused("legacy adoption existing marker is unverified")
        preimage = marker_evidence[2]
        paths = [marker, preimage.backup_path, preimage.journal_path]
        for base, targets in ((root, [marker]), (home, paths[1:])):
            _ancestries, directories = posix_transaction.snapshot_controlled_ancestries(
                base, targets, LegacyAdoptionRefused,
                outside_refusal="legacy adoption receipt is outside its owner",
                unsafe_refusal="legacy adoption receipt directory is unsafe",
            )
            marker_directories.update(directories)
        marker_files = {path: _read_evidence(path)[0] for path in paths}
        if marker_files[marker] != marker_identity:
            raise LegacyAdoptionRefused("legacy adoption marker changed")
    sdk_mode, facts_path, facts_identity = inspect_sdk_before(root, python, home, owned=repeat)
    if repeat and sdk_mode != "owned":
        raise LegacyAdoptionRefused("legacy adoption existing SDK is unverified")
    graph = verify_pm_graph(root, python, home, owner, generation, config.digest) if repeat else None
    if graph is not None and graph.facts_identity != facts_identity:
        raise LegacyAdoptionRefused("legacy adoption PM facts changed")
    evidence = AdmissionEvidence(config, config_identity, home_identity, plugin_identity, boot, generation,
                                 python, owner, old_member_identity, sdk_mode, facts_path, facts_identity,
                                 marker_identity, marker_files, marker_directories, graph, environment)
    verify_before(root, home, evidence)
    return evidence


def verify_before(root: Path, home: Path, evidence: AdmissionEvidence, *, backup: Path | None = None,
                  backup_identity: tuple[int, int] | None = None,
                  sdk: dict[str, str] | None = None) -> None:
    """Bind the original HOME/config/boot until PM is called."""
    from . import legacy_observer

    verify_source(root)
    verify_unmanaged_scope()
    verify_unmanaged_environment(evidence.environment)
    posix_transaction.assert_directory_snapshot(
        home, evidence.home_identity, LegacyAdoptionRefused, "legacy adoption home changed",
        allow_original_absence=False,
    )
    if verify_plugin_namespace(home, backup=backup, backup_identity=backup_identity) != evidence.plugin_identity:
        raise LegacyAdoptionRefused("legacy adoption plugin directory changed")
    posix_transaction.assert_path_snapshot(
        home / "config.yaml", evidence.config_identity, LegacyAdoptionRefused,
        "legacy adoption config changed",
    )
    verify_boot(evidence.boot)
    posix_transaction.assert_path_snapshot(
        evidence.facts_path, evidence.facts_identity, LegacyAdoptionRefused, "legacy adoption PM facts changed"
    )
    mode, facts_path, facts_identity = inspect_sdk_before(root, evidence.python, home,
                                                           owned=evidence.sdk_mode == "owned")
    if (mode != evidence.sdk_mode or facts_path != evidence.facts_path
            or facts_identity != evidence.facts_identity):
        raise LegacyAdoptionRefused("legacy adoption Feishu SDK changed")
    if (sdk is not None and mode == "pm-feishu"
            and not verify_installed_sdk(root, evidence.python, home, sdk, require_local=False)):
        raise LegacyAdoptionRefused("legacy adoption previous SDK is unverified")
    if evidence.marker_identity is None:
        if legacy_observer._read(root / legacy_observer.MARKER) is not None:
            raise LegacyAdoptionRefused("legacy adoption marker appeared")
    else:
        verify_existing_receipt(root, home, evidence)


def verify_existing_receipt(root: Path, home: Path, evidence: AdmissionEvidence) -> None:
    from . import legacy_observer

    def verify_snapshots() -> None:
        for path, directory_identity in evidence.marker_directories.items():
            posix_transaction.assert_directory_snapshot(
                path, directory_identity, LegacyAdoptionRefused, "legacy adoption receipt directory changed",
                allow_original_absence=False,
            )
        for path, identity in evidence.marker_files.items():
            posix_transaction.assert_path_snapshot(path, identity, LegacyAdoptionRefused,
                                                   "legacy adoption receipt changed")
    verify_snapshots()
    receipt = legacy_observer.inspect(root, home=home)
    if receipt is None or legacy_observer._config_state(receipt, root) != "installed":
        raise LegacyAdoptionRefused("legacy adoption receipt is unverified")
    verify_snapshots()


def verify_after(root: Path, home: Path, evidence: AdmissionEvidence, *, backup: Path | None,
                 backup_identity: tuple[int, int] | None = None) -> None:
    """After PM, permit a new inode only when every original config byte remains."""
    verify_source(root)
    verify_unmanaged_scope()
    verify_unmanaged_environment(evidence.environment)
    posix_transaction.assert_directory_snapshot(
        home, evidence.home_identity, LegacyAdoptionRefused, "legacy adoption home changed",
        allow_original_absence=False,
    )
    if verify_plugin_namespace(home, backup=backup, backup_identity=backup_identity) != evidence.plugin_identity:
        raise LegacyAdoptionRefused("legacy adoption plugin directory changed")
    _snapshot, contents = _read_evidence(home / "config.yaml")
    if contents != evidence.config.contents:
        raise LegacyAdoptionRefused("legacy adoption PM config changed")
    verify_boot(evidence.boot)
    if evidence.marker_identity is not None:
        posix_transaction.assert_path_snapshot(home / "config.yaml", evidence.config_identity,
                                               LegacyAdoptionRefused, "legacy adoption config changed")
        posix_transaction.assert_path_snapshot(evidence.facts_path, evidence.facts_identity,
                                               LegacyAdoptionRefused, "legacy adoption PM facts changed")
        verify_existing_receipt(root, home, evidence)


def inspect_existing_config(contents: bytes) -> ExistingConfig:
    """Accept only the first, no-config-change adoption cohort.

    PM source, other profiles, boot, runtime identity and entrypoint capabilities
    are independent gates; callers must establish them before any mutation.
    """
    if type(contents) is not bytes or len(contents) > 2 * 1024 * 1024:
        raise LegacyAdoptionRefused("legacy adoption config is unavailable")
    try:
        document = plugin_config_delta._parse(contents)
        enabled, disabled, entry = plugin_config_delta._parts(document)
        mapping = document.mapping
        plugins = mapping.get("plugins")
        secrets = mapping.get("secrets")
        if (type(mapping.get("_config_version")) is not int
                or mapping["_config_version"] != 52
                or type(plugins) is not dict or "disabled_by_update" in plugins
                or disabled or plugin_config_delta.KEY not in enabled
                or set(enabled) - {plugin_config_delta.KEY, "hfc-runtime-dependency"}
                or enabled != sorted(enabled)
                or type(entry.get("allow_tool_override")) is not bool
                or entry["allow_tool_override"] is not False):
            raise LegacyAdoptionRefused("legacy adoption config is unsupported")
        if (secrets is not None and (type(secrets) is not dict or any(
                type(value) is dict and "enabled" in value and value["enabled"] is not False
                for value in secrets.values()))):
            raise LegacyAdoptionRefused("legacy adoption external secret sources are unsupported")
    except plugin_config_delta.ConfigDeltaRefused as exc:
        raise LegacyAdoptionRefused("legacy adoption config is unsupported") from exc
    return ExistingConfig(contents, sha256(contents).hexdigest())
