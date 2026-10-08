"""Real Python children: deleting the Hermes venv must not delete sidecar code."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import venv

import pytest

from hermes_feishu_card import cli
import hermes_feishu_card


@pytest.mark.parametrize('separate', [False, True])
def test_lazy_import_after_managed_environment_is_removed(tmp_path, separate):
    root = tmp_path / 'hermes'
    managed = root / '.venv'
    stable = tmp_path / 'sidecar'
    package = Path(hermes_feishu_card.__file__).parent
    interpreters = []
    for prefix in (managed, stable):
        venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(prefix)
        python = prefix / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        site = Path(subprocess.check_output([str(python), '-I', '-c', 'import sysconfig;print(sysconfig.get_path("purelib"))'], text=True).strip())
        # Source fixture as a regular package inside each private venv; dependencies
        # are reused from the test interpreter, never downloaded or modified.
        shutil.copytree(package, site / 'hermes_feishu_card', ignore=shutil.ignore_patterns('__pycache__'))
        dependency_roots = {sysconfig.get_path('purelib'), *(p for p in sys.path if p.endswith('site-packages'))}
        (site / 'test-dependencies.pth').write_text('\n'.join(sorted(dependency_roots)) + '\n')
        interpreters.append(python)
    config = {'service': {'python_executable': str(interpreters[1])}} if separate else {}
    selected, _ = cli._resolve_sidecar_runtime_identity(root, config)
    child = subprocess.Popen([str(selected), '-I', '-u', '-c',
        'from hermes_feishu_card.session import CardSession;'
        'from hermes_feishu_card.events import SidecarEvent;'
        'session=CardSession(conversation_id="test",message_id="message",chat_id="chat");'
        'print("ready",flush=True);input();'
        'event=SidecarEvent.from_dict(dict(schema_version="1",event="tool.updated",'
        'conversation_id="test",message_id="message",chat_id="chat",platform="feishu",'
        'sequence=1,created_at=1777017600.0,data=dict(tool_id="t1",name="terminal",status="running")));'
        'assert session.apply(event);assert "t1" in session.tools;'
        'print("tool event applied",flush=True)' ],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        shutil.rmtree(managed)
        out, err = child.communicate('\n', timeout=20)
        if separate:
            assert child.returncode == 0, err
            assert 'tool event applied' in out
        else:
            assert child.returncode != 0
            assert 'ModuleNotFoundError' in err
    finally:
        if child.poll() is None:
            child.kill(); child.communicate()
