"""Display finalization preserves the independent native delivery verdict."""
from types import SimpleNamespace

import pytest

from hermes_feishu_card import admission_completion as ac


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict, expected", [(False, True), (True, True), (None, True)])
async def test_native_delivery_verdict_preserved(verdict, expected):
    calls = []

    async def handoff(*args):
        pass

    def move(*args):
        pass

    async def deliver(*args):
        return verdict

    async def finish(event, ok):
        calls.append(ok)

    ingress = SimpleNamespace(_hand_over_turn_marker=handoff, _move_marker=move,
                              deliver_response=deliver)
    ac._install(ingress, finish)
    assert await ingress.deliver_response(None, SimpleNamespace(), "synthetic", "answer") is verdict
    assert calls == [expected]
