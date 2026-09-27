from types import SimpleNamespace
import pytest
from hermes_feishu_card import hook_runtime as runtime

@pytest.mark.parametrize('name', ['_delivery_adapter_for', '_intake_adapter_for', '_adapter_for_source'])
def test_delivery_adapter_resolver_supports_old_and_split_api(name):
    adapter = SimpleNamespace(name='feishu')
    source = SimpleNamespace(platform='feishu', profile='satellite')
    runner = SimpleNamespace(**{name: lambda value: adapter if value is source else None})
    assert runtime._hfc_feishu_adapter_from_runner(runner, source) is adapter

@pytest.mark.parametrize('failure', [None, RuntimeError('offline')])
def test_split_delivery_denial_does_not_fall_back_to_other_bot(failure):
    def resolve(source):
        if isinstance(failure, Exception): raise failure
        return failure
    other = SimpleNamespace(name='feishu')
    runner = SimpleNamespace(_delivery_adapter_for=resolve, _adapter_for_source=lambda _: other,
                             _intake_adapter_for=lambda _: other, adapters={'feishu':other})
    assert runtime._hfc_feishu_adapter_from_runner(runner, SimpleNamespace(platform='feishu')) is None


def test_delivery_resolver_wins_over_intake_for_restored_source():
    delivery = SimpleNamespace(name='feishu')
    runner = SimpleNamespace(_delivery_adapter_for=lambda _:delivery, _intake_adapter_for=lambda _:None)
    assert runtime._hfc_feishu_adapter_from_runner(runner, SimpleNamespace(platform='feishu')) is delivery
