"""Call-entry isolation must survive every supported monitor backend."""
import subprocess
import sys

import pytest
from test_legacy_observer_install import SENSOR


@pytest.mark.parametrize('engine', ['automatic', 'profile', 'occupied'])
@pytest.mark.parametrize('shape', [
    'normal', 'async-start', 'async-resume', 'generator-resume',
    'generator-throw', 'generator-close', 'replace-code', 'shared-code', 'thread',
])
def test_sensor_keeps_entry_boundaries(tmp_path, engine, shape):
    prefix = ''
    if engine == 'occupied':
        prefix = ("import sys\nif hasattr(sys,'monitoring'):\n"
                  "    for i in range(6):sys.monitoring.use_tool_id(i,'occupied')\n")
    code = r'''
import types
namespace={'__name__':'hermes_feishu_card.sensor_contract','effects':[]}
if shape.startswith('async'):
    definition='class Pause:\n    def __await__(self):\n        yield 1\nasync def _post_json():\n    effects.append("entered")\n    await Pause()\n    effects.append("resumed")'
elif shape=='generator-close':
    # An exception handler requires Python code on close; a bare suspended
    # yield can be closed without entering its frame on CPython 3.13+.
    definition='def _post_json():\n    effects.append("entered")\n    try:\n        yield 1\n    except GeneratorExit:\n        effects.append("close-body")'
elif shape.startswith('generator'):
    definition='def _post_json():\n    effects.append("entered")\n    yield 1\n    effects.append("resumed")'
else:
    definition='def _post_json(): effects.append("executed")'
exec(definition,namespace)
function=namespace['_post_json']
value=None
if shape=='replace-code':
    exec('def ordinary(): effects.append("executed")',namespace)
    replacement=compile(definition,'<replacement>','exec')
    namespace['ordinary'].__code__=next(c for c in replacement.co_consts if isinstance(c,types.CodeType))
    function=namespace['ordinary']
if shape=='shared-code':
    other={'__name__':'other_module','effects':[]}
    types.FunctionType(function.__code__,other)()
    assert other['effects']==['executed']
if shape=='thread':
    import threading
    child=threading.Thread(target=function);child.start();child.join(timeout=5)
    assert not child.is_alive() and namespace['effects']==['executed']
    namespace['effects'].clear()
if shape in {'async-resume','generator-resume','generator-throw','generator-close'}:
    namespace['__name__']='other_module'
    value=function()
    assert (value.send(None) if shape.startswith('async') else next(value))==1
    assert namespace['effects']==['entered']
    namespace['effects'].clear();namespace['__name__']='hermes_feishu_card.sensor_contract'
try:
    if shape=='async-start':
        value=function();assert namespace['effects']==[];value.send(None)
    elif shape in {'async-resume','generator-resume'}:value.send(None)
    elif shape=='generator-throw':value.throw(ValueError('test'))
    elif shape=='generator-close':value.close()
    else:function()
except RuntimeError as error:
    assert str(error)=='isolated test forbids HFC ownership/transport',repr(error)
else:raise AssertionError('entry escaped sensor')
assert namespace['effects']==[]
assert (home/'hfc-forbidden.jsonl').read_text().splitlines()==[json.dumps(['hermes_feishu_card.sensor_contract','_post_json'])]
if engine=='automatic' and hasattr(sys,'monitoring'):assert _tool is not None
else:assert _tool is None
# Cleanup suspended objects only after disabling monitoring; no extra test entry.
if _tool is not None:sys.monitoring.set_events(_tool,0)
sys.setprofile(None)
if value is not None:value.close()
'''
    env = {'HOME': str(tmp_path), 'HERMES_HOME': str(tmp_path)}
    if engine == 'profile':
        env['HFC_TEST_FORCE_PROFILE'] = '1'
    child = prefix + SENSOR + '\nshape=' + repr(shape) + '\nengine=' + repr(engine) + '\n' + code
    result = subprocess.run([sys.executable, '-I', '-c', child], env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not hasattr(sys, 'monitoring'), reason='Python monitoring requires 3.12+')
def test_sensor_binding_failure_remains_visible_when_caught(tmp_path):
    code = r'''
namespace={'__name__':'hermes_feishu_card.sensor_contract','effects':[]}
exec('def _post_json(): effects.append("executed")',namespace)
assert _tool is not None
try:_entry(namespace['_post_json'].__code__,0)
except RuntimeError as error:assert str(error)=='isolated sensor cannot bind Python entry'
else:raise AssertionError('invalid frame binding accepted')
assert namespace['effects']==[]
assert (home/'hfc-forbidden.jsonl').read_text().splitlines()==[json.dumps(['sensor_binding_error','_post_json'])]
'''
    result = subprocess.run([sys.executable, '-I', '-c', SENSOR + code],
                            env={'HOME': str(tmp_path), 'HERMES_HOME': str(tmp_path)},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('engine', ['automatic', 'profile', 'occupied'])
@pytest.mark.parametrize('style', ['simple', 'finally'])
@pytest.mark.skipif(not hasattr(sys, 'monitoring'), reason='Monitoring close protocol requires 3.12+')
def test_generator_close_records_execution_without_assuming_an_entry(tmp_path, engine, style):
    prefix = ''
    if engine == 'occupied':
        prefix = ("import sys\nif hasattr(sys,'monitoring'):\n"
                  "    for i in range(6):sys.monitoring.use_tool_id(i,'occupied')\n")
    code = r'''
ns={'__name__':'other_module','effects':[]}
if style=='simple':definition='def _post_json():\n    yield 1\n    effects.append("resumed")'
else:definition='def _post_json():\n    try:\n        yield 1\n    finally:\n        effects.append("cleanup")'
exec(definition,ns)
gen=ns['_post_json']();assert next(gen)==1
ns['__name__']='hermes_feishu_card.sensor_contract'
blocked=False
try:gen.close()
except RuntimeError as error:
    assert str(error)=='isolated test forbids HFC ownership/transport'
    blocked=True
assert gen.gi_frame is None
marker=home/'hfc-forbidden.jsonl'
if style=='finally':
    # Both Python monitors inject an exception; Python must still run finally.
    # The marker makes the containing acceptance stage fail even if caught.
    assert blocked and ns['effects']==['cleanup']
else:
    assert ns['effects']==[]
    if not blocked:
        # CPython 3.13+ can close this bare yield without executing Python code.
        assert _tool is not None and sys.version_info>=(3,13) and not marker.exists()
if blocked:
    assert marker.read_text().splitlines()==[json.dumps(['hermes_feishu_card.sensor_contract','_post_json'])]
'''
    env = {'HOME': str(tmp_path), 'HERMES_HOME': str(tmp_path)}
    if engine == 'profile':
        env['HFC_TEST_FORCE_PROFILE'] = '1'
    child = prefix + SENSOR + '\nstyle=' + repr(style) + '\n' + code
    result = subprocess.run([sys.executable, '-I', '-c', child], env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
