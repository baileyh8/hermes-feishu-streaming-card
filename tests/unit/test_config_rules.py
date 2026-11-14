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

def test_approval_rule_hints_default_false_and_boolean_normalized():
    assert rules.DEFAULT_CONFIG['card']['approval_rule_hints'] is False
    card = {'approval_rule_hints': True}
    rules.normalize_card_config(card, path='card')
    assert card['approval_rule_hints'] is True
    card = {'approval_rule_hints': 'off'}
    rules.normalize_card_config(card, path='card')
    assert card['approval_rule_hints'] is False
    with pytest.raises(ValueError):
        rules.normalize_card_config({'approval_rule_hints': 'maybe'}, path='card')


def test_render_options_expose_approval_rule_hints_default_false():
    from hermes_feishu_card.render_options import card_render_options
    assert card_render_options({})['approval_rule_hints'] is False
    assert card_render_options({'approval_rule_hints': True})['approval_rule_hints'] is True
    assert card_render_options({'approval_rule_hints': 0})['approval_rule_hints'] is False
