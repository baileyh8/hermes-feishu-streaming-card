"""Precompute exact official-enable bytes using two pinned upstream writer families.

This is a private-copy writer adapter, not a YAML merge implementation. Unknown
source, bindings, serializer paths or side effects refuse before real enable.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any, Mapping


class ConfigBytesRefused(ValueError):
    """The official command's exact config postimage cannot be proved."""


# Whole-source hashes bind cmd_enable's sequence, its imported helpers and the
# indirect serializer implementations. Hashes are stable across Python AST APIs.
# fixed: 3c27eb6234bf91b8ceee9e9071591b31e9b148cb
# pm: d923a5eb487a17c0a68801bba9ef0f56c81040a8
# pm-main: 0ab43d4f67e41be75f4f6c0fa14a86dbcfd3e5b3
FAMILIES: dict[str, dict[str, str]] = {
    "fixed": {
        "providers/__init__.py": "f1b572f3fe41efef5d8c6d7aeff1a24c0e9272dad6a6649019530d1bd0078818",
        "hermes_cli/plugins_cmd.py": "848a0ae1648d5e781706e6c0175d422899b08e8c730694bc69c1fc399d99d142",
        "hermes_cli/config.py": "0eca789d0351fa0f0f311934730629b76be6d1ff7b0182fb7bfa06fa79b2725c",
        "utils.py": "269375fff6ceea0976b43ead1b74083f68d3540c8d182d8d2bbcb421b82427c9",
    },
    "pm": {
        "hermes_cli/plugin_capabilities.py": "35cbdea310ceeb69829693f5077e30eafc739ade1923fc88fc9a06c66e56ab3e",
        "providers/__init__.py": "796d3ee0ed3aea06afc21525ec19dc39f17091845fa1e3f9be485acaa058423a",
        "hermes_cli/version_info.py": "2a422bbd2b32691d871b4fe550cd687be0bbb49989a76fedda8c00432d4944a9",
        "hermes_cli/plugins_cmd.py": "aec2fd443b48fba00b96308af8ba4ed139f2a7ab1a07770b1773028f021bd023",
        "hermes_cli/config.py": "a1667a958f52aa6201200bde354c3a380d79d49fd7635aeee3a3ebb23533a788",
        "utils.py": "394b44230f13ba608c6ce9b2d143563b2554a73a219567b03884637776a4859a",
        "hermes_yaml.py": "f97f0f08b6474c52434d0cffaa7ba0ecaa5b9279c4abcbb35c11d15f9c89db76",
        "hermes_cli/plugins_cmd_capabilities.py": "1322530460936f794e0b36cba11bafc87f592b7b0a9d713bb22bca9eb06d35f6",
        "pm/publication.py": "4f707e2a2d927c7e96a0327dadfe8af80c811dc288b7d992d2bc683e17860c2b",
        "pm/workspace.py": "a1a69386b2b7cc4eb2fd06d2f2c39a520913cadfee92fa282f1b25de0c1a58a2",
        "pm/plugins_state.py": "e4300eac3c00155b85cf6aba3c7b40faea05c67dde99385ff254a1345bebb5af",
        "hermes_cli/left_core_migration.py": "4899e985f2042954c0799ff2e12809aee78ee947133209eff88f076790df552c",
    },
    "pm-main": {
        "hermes_cli/plugin_capabilities.py": "35cbdea310ceeb69829693f5077e30eafc739ade1923fc88fc9a06c66e56ab3e",
        "providers/__init__.py": "89cb97d8fcb0ddcf97e47709d478bb49c959bb38e84dcfbe6a4f64ba58d44e5a",
        "hermes_cli/version_info.py": "2a422bbd2b32691d871b4fe550cd687be0bbb49989a76fedda8c00432d4944a9",
        "hermes_cli/plugins_cmd.py": "3ab3807bb4594e65fdba473cd035fb572b418f26d1f45249cff2fd1b9fee8ff4",
        "hermes_cli/config.py": "68239827c32c60114cc01d03a2c29f872c91d2114a3f0c1d1094de49980d0d2e",
        "utils.py": "9c4751dc9fbcbfe7e16d9e0cc5fb25566517c4927d2dc84061d4cac993a98445",
        "hermes_yaml.py": "f97f0f08b6474c52434d0cffaa7ba0ecaa5b9279c4abcbb35c11d15f9c89db76",
        "hermes_cli/plugins_cmd_capabilities.py": "1322530460936f794e0b36cba11bafc87f592b7b0a9d713bb22bca9eb06d35f6",
        "pm/publication.py": "4f707e2a2d927c7e96a0327dadfe8af80c811dc288b7d992d2bc683e17860c2b",
        "pm/workspace.py": "a1a69386b2b7cc4eb2fd06d2f2c39a520913cadfee92fa282f1b25de0c1a58a2",
        "pm/plugins_state.py": "e4300eac3c00155b85cf6aba3c7b40faea05c67dde99385ff254a1345bebb5af",
        "hermes_cli/left_core_migration.py": "78ae5dd2b5320a8402d5bb7f2bf29dfc7b2f3291d363ca425d77cafaccf972bb",
        "hermes_cli/config_worker_save.py": "d998581d802deb67eeef32d3fcbd7696f9d4a7a6c83f2bc83d6c8881c5d7dcf2",
    },
}


def _digest(path: Path) -> str:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > 2 * 1024 * 1024:
            raise ConfigBytesRefused("official config writer source is unsafe")
        value = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise ConfigBytesRefused(
            "official config writer source is unavailable"
        ) from exc
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ConfigBytesRefused("official config writer source changed")
    return hashlib.sha256(value).hexdigest()


def verify_family(root: Path, family: str) -> None:
    if family not in FAMILIES or any(
        _digest(root / name) != digest for name, digest in FAMILIES[family].items()
    ):
        raise ConfigBytesRefused("official config writer contract is unknown")


@dataclass(frozen=True, repr=False)
class ConfigBytesPlan:
    postimage: bytes
    root: Path
    family: str

    def verify_source(self) -> None:
        verify_family(self.root, self.family)


_CHILD = r"""
import hashlib,importlib,json,os,sys
from pathlib import Path
sys.dont_write_bytecode=True
root,home=map(Path,sys.argv[1:3])
request=json.loads((home/'request.json').read_text())
real_home=Path(request['real_home']).resolve()
# Imports may read selected source/runtime libraries. All writes and all config
# reads are confined to this private HOME; no resolver subprocess or transport.
def under(path, base):
    return path==base or base in path.parents
def deny(reason):
    sys.stderr.write('private config byte plan side effect refused\n');sys.stderr.flush()
    os._exit(73)
def audit(event,args):
    if event in {'subprocess.Popen','os.system','socket.connect','socket.bind'}:
        if event=='subprocess.Popen':
            frame=sys._getframe(1)
            while frame and frame.f_globals.get('__name__')=='subprocess': frame=frame.f_back
            # The pinned writer's optional telemetry imports version metadata.
            # Its git lookup is irrelevant to bytes and catches this refusal;
            # block it without starting a subprocess. The entire source is bound.
            if frame.f_globals.get('__name__')=='hermes_cli.version_info' and frame.f_code.co_name=='_run_git':
                raise RuntimeError('version metadata subprocess disabled in shadow')
            deny(event)
        deny(event)
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        path=Path(os.fsdecode(args[0])).resolve()
        mode=args[1];flags=args[2]
        writing=(isinstance(mode,str) and any(x in mode for x in 'wax+')) or bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
        if writing and not under(path,home): deny('write outside shadow')
        if path in {real_home/'config.yaml',real_home/'.env'}: deny('real config read')
    if event in {'os.mkdir','os.remove','os.rmdir','os.chmod'}:
        if isinstance(args[0],int):
            identity=os.fstat(args[0])
            if not any((p.stat().st_dev,p.stat().st_ino)==(identity.st_dev,identity.st_ino)
                       for p in home.rglob('*') if p.is_file() and not p.is_symlink()):
                deny('file descriptor outside shadow')
            return
        path=Path(os.fsdecode(args[0])).resolve()
        if not under(path,home): deny('mutation outside shadow')
    if event in {'os.rename','os.replace'}:
        if not all(under(Path(os.fsdecode(p)).resolve(),home) for p in args[:2]):
            deny('rename outside shadow')
sys.addaudithook(audit)
def profile(frame,event,arg):
    if event!='call': return
    module=frame.f_globals.get('__name__','') or ''
    name=frame.f_code.co_name
    if ((module.startswith('hermes_cli.plugins') and name in {
        'discover_plugins','discover_and_load','_discover_and_load_inner','_load_plugin','_load_plugin_scoped'})
        or (module.startswith('hermes_feishu_card') and name=='bootstrap_plugin_runtime')
        or (module=='providers' and name in {'_discover_providers','_run_discovery_steps','_import_plugin_dir','_discover_entry_point_providers'})
        or (module in {'pm.install','pm.client','pm._uv'} and name!='<module>')):
        deny('plugin load or resolver')
sys.setprofile(profile)
for name,digest in request['sources'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
sys.path.insert(0,str(root))
# Module origins must agree with the inspected checkout. Same interpreter and
# restricted environment as the real official command; no inherited PYTHONPATH.
def module(name,relative):
    value=importlib.import_module(name)
    assert Path(value.__file__).resolve()==root/relative
    return value
providers=module('providers','providers/__init__.py')
list_providers=providers.list_providers
def no_setup_provider_metadata(*args,**kwargs):
    caller=sys._getframe(1)
    if caller.f_globals.get('__name__')=='hermes_cli.config' and caller.f_code.co_name=='_inject_profile_env_vars':
        raise RuntimeError('setup prompt metadata is excluded from byte planning')
    deny('provider metadata outside setup import')
providers.list_providers=no_setup_provider_metadata
config=module('hermes_cli.config','hermes_cli/config.py')
providers.list_providers=list_providers
utils=module('utils','utils.py')
desired=request['desired']
commands=module('hermes_cli.plugins_cmd','hermes_cli/plugins_cmd.py')
selection_needed=not request['enabled_before'] or (request['family']!='fixed' and 'hermes-feishu-card' in config.read_user_config_raw().get('plugins',{}).get('disabled',[]))
if selection_needed:
    if request['family']=='fixed':
        commands._save_enabled_set(set(desired['plugins']['enabled']))
        commands._save_disabled_set(set(desired['plugins']['disabled']))
    else:
        yaml=module('hermes_yaml','hermes_yaml.py').roundtrip_yaml()
        state=yaml.load((home/'config.yaml').read_bytes().decode('utf-8-sig')) or {}
        section=state.setdefault('plugins',{})
        section['enabled']=desired['plugins']['enabled']
        section['disabled']=desired['plugins']['disabled']
        import io
        stream=io.StringIO();yaml.dump(state,stream)
        (home/'config.yaml').write_bytes(stream.getvalue().encode('utf-8'))
grant=commands if request['family']=='fixed' else module('hermes_cli.plugins_cmd_capabilities','hermes_cli/plugins_cmd_capabilities.py')
grant._set_plugin_entry_flag('hermes-feishu-card','allow_tool_override',False)
assert not any(name in sys.modules for name in ('pm.install','pm.client'))
for name,digest in request['sources'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
assert (home/'config.yaml').is_file()
"""


def plan_enabled_bytes(
    *,
    root: Path,
    python: Path,
    home: Path,
    state_dir: Path,
    before: bytes,
    desired: Mapping[str, Any],
    enabled_before: bool,
) -> ConfigBytesPlan:
    family = None
    for candidate in FAMILIES:
        try:
            verify_family(root, candidate)
        except ConfigBytesRefused:
            continue
        family = candidate
        break
    if family is None:
        raise ConfigBytesRefused("official config writer contract is unknown")
    try:
        with tempfile.TemporaryDirectory(
            prefix="hfc-config-plan-", dir=state_dir
        ) as scratch:
            shadow = Path(scratch) / "home"
            shadow.mkdir(mode=0o700)
            os.chmod(scratch, 0o700)
            for name, value in {
                "config.yaml": before,
                "request.json": json.dumps(
                    {
                        "real_home": str(home),
                        "family": family,
                        "sources": FAMILIES[family],
                        "desired": desired,
                        "enabled_before": enabled_before,
                    }
                ).encode(),
            }.items():
                path = shadow / name
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(value)
            child = subprocess.run(
                [str(python), "-I", "-c", _CHILD, str(root), str(shadow)],
                cwd=root,
                env={"HOME": str(shadow), "HERMES_HOME": str(shadow)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            if child.returncode != 0:
                raise ConfigBytesRefused("official config byte plan failed")
            postimage = (shadow / "config.yaml").read_bytes()
    except (OSError, subprocess.SubprocessError) as exc:
        # Never forward stderr, arbitrary exception text or config bytes.
        raise ConfigBytesRefused("official config byte plan failed") from exc
    verify_family(root, family)
    return ConfigBytesPlan(postimage, root, family)
