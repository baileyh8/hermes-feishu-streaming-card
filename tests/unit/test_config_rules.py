"""Original config semantics and loader-independent pure dependency closure."""
import ast
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_feishu_card import config_rules as rules
from hermes_feishu_card.config_snapshot import minimal_reading_config
from hermes_feishu_card.reading import explain_reading_config

FROZEN = json.loads(
    (Path(__file__).parents[1] / 'fixtures' / 'config_rules_2e9eb02.json').read_text()
)


@pytest.mark.parametrize('case', FROZEN['cases'], ids=[
    f"{case['operation']}-{index}" for index, case in enumerate(FROZEN['cases'])
])
def test_pure_rules_match_original_scope_normalization_and_errors(case):
    owners = {
        'expand': rules.expand_reading_preset,
        'merge': rules.merge_card_config,
        'sizes': rules.normalize_text_sizes,
        'width': rules.normalize_width_mode,
        'width_config': rules.card_width_config,
        'overflow': rules.normalize_table_overflow_mode,
        'boolean': rules.normalize_boolean,
        'native': rules.normalize_native_chats,
        'mention': rules.card_interaction_mention_enabled,
        'completion': rules.card_completion_mention_enabled,
        'card': rules.normalize_card_config,
        'options': rules.normalize_config_card_options,
        'explain': explain_reading_config,
        'minimal': minimal_reading_config,
    }
    args = copy.deepcopy(case['args'])
    expected = case['expected']
    if 'error' in expected:
        with pytest.raises(ValueError) as error:
            owners[case['operation']](*args, **case['kwargs'])
        assert type(error.value).__name__ == expected['error']
        assert str(error.value) == expected['message']
    else:
        actual = owners[case['operation']](*args, **case['kwargs'])
        assert json.loads(json.dumps(actual)) == expected['value']
    assert args == expected['mutated']


@pytest.mark.parametrize('consumer,call', [
    ('config_rules', 'm.normalize_config_card_options({"card":{"native_tables":"off"}})'),
    ('reading', 'm.explain_reading_config({"card":{"reading_preset":"task"}})'),
    ('bots', 'm.resolve_card_config({"text_sizes":{"body":"large"}}, {}, {"streaming_mode":False})'),
    ('config_snapshot', 'm.minimal_reading_config({"card":{"reading_preset":"task"}})'),
    ('config_snapshot', 'm.validated_reading_values(m.explain_reading_config({})["values"])'),
])
def test_fresh_pure_consumers_do_not_load_configuration_or_runtime_owners(tmp_path, consumer, call):
    root = Path(rules.__file__).resolve().parent.parent
    code = '''
import importlib, json, pathlib, sys
sys.path.insert(0, sys.argv[1])
m = importlib.import_module('hermes_feishu_card.' + sys.argv[2])
assert pathlib.Path(m.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[1]))
exec(sys.argv[3])
for name in ('hermes_feishu_card.config', 'hermes_feishu_card.render',
             'hermes_feishu_card.session', 'yaml'):
    assert name not in sys.modules, name
if sys.argv[2] != 'bots':
    assert 'hermes_feishu_card.delivery_policy' not in sys.modules
print(json.dumps({'owner': m.__name__, 'closed': True}))
'''
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), HERMES_HOME=str(tmp_path / 'hermes'),
               HERMES_FEISHU_CARD_STATE_DIR=str(tmp_path / 'state'))
    result = subprocess.run([sys.executable, '-I', '-c', code, str(root), consumer, call],
                            env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['closed'] is True


def test_compatibility_exports_are_the_same_implementation_objects():
    from hermes_feishu_card import config, delivery_policy, reading

    for name in ('DEFAULT_CONFIG', 'KNOWN_SECTIONS', 'CARD_TEXT_SIZE_VALUES',
                 'CARD_TEXT_SIZE_DEFAULTS', 'CARD_TEXT_SIZE_DEVICE_KEYS',
                 'CARD_TABLE_OVERFLOW_MODES', 'SERVICE_MANAGER_VALUES',
                 'normalize_text_sizes', 'normalize_table_overflow_mode',
                 'normalize_width_mode', 'card_width_config', 'merge_card_config',
                 'card_interaction_mention_enabled', 'card_completion_mention_enabled',
                 'expand_reading_preset', 'normalize_reading_preset', 'normalize_native_chats'):
        assert getattr(config, name) is getattr(rules, name)
    for name in ('HIDE_SUCCESSFUL_TOOL_ACTIVITY', 'PRESENTATION_MODE', 'READING_PRESETS',
                 'READING_FIELDS', 'normalize_reading_preset', 'expand_reading_preset'):
        assert getattr(reading, name) is getattr(rules, name)
    assert delivery_policy.normalize_native_chats is rules.normalize_native_chats


def test_merge_copies_nested_inputs_and_preserves_explicit_false_and_none():
    base = {'text_sizes': {'body': {'mobile': 'large'}}, 'streaming_mode': True}
    override = {'streaming_mode': False, 'completion_notify': {'mention': None}}
    before = copy.deepcopy([base, override])
    result = rules.merge_card_config(base, override)
    result['text_sizes']['body']['mobile'] = 'normal'
    result['completion_notify']['mention'] = True
    assert [base, override] == before
    assert result['streaming_mode'] is False
    assert rules.merge_card_config(base, {'text_sizes': None})['text_sizes'] is None


@pytest.mark.parametrize('value', ['none', 'off', True, 1, None])
def test_live_activity_rejects_invalid_enum(value):
    with pytest.raises(ValueError, match='live_tool_activity'):
        rules.normalize_card_config({'live_tool_activity': value}, path='card')


@pytest.mark.parametrize('overlays', [
    [], None, {'groups': {}}, {'dm': {}}, {'chats': []}, {'group': None},
    *[{'group': {key: 'value'}} for key in ('reading_preset', 'title', 'footer_fields')],
    *[{'chats': {key: {}}} for key in ('*', 're:.*', 'oc_*', 'x' * 513, 'oc_\x01test', 1)],
    {'p2p': {'show_reasoning': 'maybe'}}, {'group': {'live_tool_activity': 'off'}},
])
def test_chat_overrides_reject_invalid_structure_and_selectors(overlays):
    with pytest.raises(ValueError):
        rules.normalize_card_config({'chat_overrides': overlays}, path='card')


@pytest.mark.parametrize('chat_type,mode', [
    ('group', 'hidden'), ('p2p', 'summary'), ('dm', 'summary'), ('private', 'summary'),
    ('group_chat', 'full'), ('thread', 'full'), ('', 'full'), (None, 'full'),
])
def test_chat_overlays_exact_selection_copy_and_precedence(chat_type, mode):
    card = {'live_tool_activity': 'full', 'chat_overrides': {
        'group': {'live_tool_activity': ' Hidden ', 'show_reasoning': 'false'},
        'p2p': {'live_tool_activity': 'summary'},
        'chats': {'oc_example': {'live_tool_activity': 'full'}},
    }}
    rules.normalize_card_config(card, path='card')
    before = copy.deepcopy(card)
    actual = rules.apply_chat_card_overrides(card, chat_type=chat_type, chat_id='oc_examplf')
    assert actual['live_tool_activity'] == mode
    assert 'chat_overrides' not in actual
    assert rules.apply_chat_card_overrides(card, chat_type=chat_type, chat_id='oc_example')['live_tool_activity'] == 'full'
    assert card == before


def test_nested_chat_merge_retains_other_types_chats_and_fields():
    base = {'chat_overrides': {'group': {'show_reasoning': False}, 'p2p': {'live_tool_activity': 'full'},
                             'chats': {'oc_example': {'show_reasoning': False}, 'oc_other': {'live_tool_activity': 'summary'}}}}
    override = {'chat_overrides': {'group': {'live_tool_activity': 'hidden'}, 'chats': {'oc_example': {'live_tool_activity': 'hidden'}}}}
    before = copy.deepcopy([base, override])
    merged = rules.merge_card_config(base, override)['chat_overrides']
    assert merged['group'] == {'show_reasoning': False, 'live_tool_activity': 'hidden'}
    assert merged['p2p'] == base['chat_overrides']['p2p']
    assert merged['chats']['oc_example'] == {'show_reasoning': False, 'live_tool_activity': 'hidden'}
    assert merged['chats']['oc_other'] == base['chat_overrides']['chats']['oc_other']
    merged['chats']['oc_example']['show_reasoning'] = True
    assert [base, override] == before


def test_rules_import_only_the_standard_library():
    tree = ast.parse(Path(rules.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module in {'__future__', 'collections.abc', 'typing'}
        elif isinstance(node, ast.Import):
            assert all(alias.name == 'copy' for alias in node.names)


def test_native_chat_validation_rejects_non_list_sequence():
    with pytest.raises(ValueError, match='must be an array of exact chat ids'):
        rules.normalize_native_chats(('oc_synthetic',))
