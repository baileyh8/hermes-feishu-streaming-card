"""Observed process context must stay local to a turn and never invent an ETA."""
import json
from hermes_feishu_card import hook_runtime
from hermes_feishu_card.session import CardSession
from hermes_feishu_card.render import render_card_result
from tests.unit.test_session import event

PID = 'proc_123456abcdef'


def tool(seq, name, args, result=None, status='running', call_id=None):
    data = hook_runtime._event_data('tool.updated', {
        'name': name, 'args': args, 'result': result, 'status': status,
        'tool_id': call_id or str(seq), 'call_id': call_id or str(seq),
    }, None, None)
    return event('tool.updated', seq, data)


def make_session():
    return CardSession(conversation_id='chat-1', message_id='msg-1', chat_id='oc_abc')


def test_spawn_then_wait_shows_same_turn_command_and_timeout_semantics():
    s = make_session()
    s.apply(tool(0, 'terminal', {'command': 'python synthetic_job.py', 'background': True},
                 json.dumps({'session_id': PID, 'output': 'Background process started'}), 'completed'))
    s.apply(tool(1, 'process_manage', {'action': 'wait', 'session_id': PID, 'timeout': 900}))
    card = render_card_result(s).card
    text = json.dumps(card, ensure_ascii=False)
    assert '等待后台进程' in text and 'python synthetic_job.py' in text
    assert '900' in text and '非预计完成时间' in text
    assert 'None' not in s.tools['1'].detail


def test_poll_output_and_command_survive_next_wait_without_marking_job_done():
    s = make_session()
    s.apply(tool(0, 'process', {'action': 'poll', 'session_id': PID},
                 {'session_id': PID, 'command': 'pytest synthetic_tests', 'status': 'running',
                  'uptime_seconds': 35, 'output_preview': 'step 2 / 5'}, 'completed'))
    s.apply(tool(1, 'process_manage', {'action': 'wait', 'session_id': PID, 'timeout': 60}))
    detail = s.tools['1'].detail
    assert 'pytest synthetic_tests' in detail and 'step 2 / 5' in detail
    assert '最近输出' in detail and '非预计完成时间' in detail
    assert '已完成' not in detail


def test_unrelated_or_new_turn_never_inherits_process_details():
    a, b = make_session(), make_session()
    a.apply(tool(0, 'terminal', {'command': 'private job', 'background': True}, {'session_id': PID}, 'completed'))
    for s, pid in ((a, 'proc_ffffffffffff'), (b, PID)):
        s.apply(tool(1, 'process_manage', {'action': 'wait', 'session_id': pid}))
        assert 'private job' not in s.tools['1'].detail
        assert '尚未观测到命令' in s.tools['1'].detail


def test_metadata_is_allowlisted_bounded_and_redacted_before_transport():
    data = tool(0, 'process', {'action': 'poll', 'session_id': PID}, {
        'command': 'job --token synthetic-secret', 'output_preview': 'password=hidden-value',
        'status': 'running', 'unrelated_private_field': 'do-not-copy',
    }, 'completed').data
    assert 'process_activity' in data
    encoded = json.dumps(data['process_activity'])
    assert 'synthetic-secret' not in encoded and 'hidden-value' not in encoded
    assert 'do-not-copy' not in encoded


def test_non_process_tools_and_invalid_results_do_not_gain_metadata():
    for name, args, result in [('search', {}, {'session_id': PID}),
                               ('terminal', {}, {'session_id': '../other'}),
                               ('process', {'action': 'wait'}, '{bad json')]:
        assert 'process_activity' not in tool(0, name, args, result).data


def test_process_context_is_bounded():
    s = make_session()
    for i in range(40):
        s.apply(tool(i, 'terminal', {'command': f'job-{i}', 'background': True},
                     {'session_id': f'proc_{i:012x}'}, 'completed'))
    s.apply(tool(40, 'process', {'action': 'wait', 'session_id': 'proc_000000000000'}))
    assert 'job-0' not in s.tools['40'].detail
    s.apply(tool(41, 'process', {'action': 'wait', 'session_id': 'proc_000000000027'}))
    assert 'job-39' in s.tools['41'].detail


def test_duplicate_spawn_completion_cannot_regress_later_exit_observation():
    s = make_session()
    s.apply(tool(0, 'terminal', {'command': 'job', 'background': True}, {'session_id': PID}, 'completed', 'spawn'))
    s.apply(tool(1, 'process', {'action': 'poll', 'session_id': PID},
                 {'status': 'exited', 'exit_code': 7, 'output': 'job failed'}, 'completed', 'poll'))
    s.apply(tool(2, 'terminal', {'command': 'job', 'background': True}, {'session_id': PID}, 'completed', 'spawn'))
    s.apply(tool(3, 'process', {'action': 'wait', 'session_id': PID}, call_id='wait'))
    assert '已退出，退出码 7' in s.tools['wait'].detail
    assert 'job failed' in s.tools['wait'].detail


def test_timeout_is_not_a_process_failure_and_mismatched_result_is_rejected():
    s = make_session()
    s.apply(tool(0, 'process', {'action': 'wait', 'session_id': PID, 'timeout': 30},
                 {'status': 'timeout', 'command': 'job', 'process_running': True}, 'completed'))
    assert '不代表进程结束' in s.tools['0'].detail
    assert '失败' not in s.tools['0'].detail
    data = tool(1, 'process', {'action': 'poll', 'session_id': PID},
                {'session_id': 'proc_ffffffffffff', 'command': 'wrong job'}, 'completed').data
    assert 'process_activity' not in data


def test_native_plugin_routes_only_redacted_process_observations():
    from tests.unit.test_hermes_plugin_runtime import active_task4_runtime
    posted = []; runtime = active_task4_runtime(posted)
    try:
        runtime.handle_post_tool_call(turn_id='turn-1', tool_call_id='spawn', tool_name='terminal',
                                      args={'command': 'job --token native-secret', 'background': True},
                                      result={'session_id': PID}, status='success')
        runtime.handle_pre_tool_call(turn_id='turn-1', tool_call_id='wait', tool_name='process_manage',
                                     args={'session_id': PID, 'action': 'wait', 'timeout': 20})
        runtime.drain_observers(1.0)
        assert len(posted) == 2
        s = make_session()
        for i, p in enumerate(posted):
            s.apply(event('tool.updated', i, p['data']))
        assert 'job --token [REDACTED]' in s.tools['wait'].detail
        assert '非预计完成时间' in s.tools['wait'].detail
        assert 'native-secret' not in json.dumps(posted)
    finally:
        runtime.close()


def test_process_lookup_is_not_checkpointed_or_restored(tmp_path):
    from hermes_feishu_card.session_store import SessionStore
    s = make_session()
    s.apply(tool(0, 'terminal', {'command': 'job --password test-secret', 'background': True},
                 {'session_id': PID}, 'completed'))
    store = SessionStore(tmp_path)
    store.save('turn', s, 'card-id', None, '', {}, 'fixture')
    record = json.loads(next(store.root.glob('*.json')).read_text())['record']
    assert '_process_activity' not in record['session']
    assert store.load()[0]['session']._process_activity == {}
