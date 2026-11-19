import ast
from pathlib import Path

import pytest

from hermes_feishu_card.install import patcher

TRANSPORT_PATH = (
    Path(__file__).parents[1] / "fixtures/hermes_v0232/run_turn_runner_approval_transport.py"
)
TRANSPORT = TRANSPORT_PATH.read_text(encoding="utf-8")
TARGET = "gateway/run_turn_runner_approval_transport.py"

RUNNER = '''
class TurnRunner:
    def _approval_notify_sync(self, approval_data: dict) -> None:
        if self._approval_owner is not None:
            authority, session_id, generation = self._approval_owner
            authority.check_approval_generation(session_id, generation)
        self._render_approval_sync(approval_data)
        if self._approval_owner is not None:
            from gateway import local_viewer_events
            authority.register_approval(session_id, generation, self._ctx.session_key, approval_data,
                                        timeout_s=local_viewer_events.prompt_timeout_s(self, "approval"))

    def _render_approval_sync(self, approval_data: dict, *, background=False) -> None:
        ctx = self._ctx
        adapter = ctx._status_adapter

    def _register(self, session_key):
        from gateway.run_turn_runner_approval_transport import approval_transport
        register_gateway_notify(session_key, approval_transport(self))
'''

BOUND_RUNNER = RUNNER.replace(
    "from gateway.run_turn_runner_approval_transport import approval_transport\n        register_gateway_notify(session_key, approval_transport(self))",
    "register_gateway_notify(session_key, self._approval_notify_sync)",
)


def _name(content, peers=None):
    return patcher._approval_callback_name(content, peer_sources=peers)


def test_bound_notify_registration_still_accepted():
    assert _name(BOUND_RUNNER) == "_render_approval_sync"


def test_verified_approval_transport_is_accepted():
    assert _name(RUNNER, {TARGET: TRANSPORT}) == "_render_approval_sync"


@pytest.mark.parametrize("mutation", [
    "other-turn",
    "missing-peer",
    "skip-generation-check",
    "notify-other-method",
    "rewrite-transport-notify",
    "rebind-at-call",
    "rebind-in-peer",
    "drop-import",
])
def test_approval_transport_rejects_drift(mutation):
    content = RUNNER
    peers = {TARGET: TRANSPORT}
    if mutation == "other-turn":
        content = content.replace("approval_transport(self)", "approval_transport(other)")
    elif mutation == "missing-peer":
        peers = {}
    elif mutation == "skip-generation-check":
        content = content.replace(
            "authority.check_approval_generation(session_id, generation)\n        ",
            "",
        )
    elif mutation == "notify-other-method":
        content = content.replace("approval_transport(self)", "other_transport(self)")
    elif mutation == "rewrite-transport-notify":
        peers = {TARGET: TRANSPORT.replace(
            "turn._approval_notify_sync(data)",
            "presenter._render_approval_sync(data)",
        )}
    elif mutation == "rebind-at-call":
        content = content.replace("        register_gateway_notify(",
                                  "        approval_transport = other_transport\n"
                                  "        register_gateway_notify(")
    elif mutation == "rebind-in-peer":
        peers = {TARGET: TRANSPORT + "\nTurnContext = OtherContext\n"}
    elif mutation == "drop-import":
        content = content.replace(
            "from gateway.run_turn_runner_approval_transport import approval_transport\n        ",
            "",
        )
    with pytest.raises(ValueError, match="extracted approval generation/notify contract changed"):
        _name(content, peers)
