from __future__ import annotations


from hermes_feishu_card.approval_rule_hints import (
    approval_rule_hint_line, bounded_native_rule_keys,
)
from hermes_feishu_card.session import CardSession, InteractionState
from hermes_feishu_card.session_store import SessionStore
from hermes_feishu_card.render import render_card


def _approval_session(rule_keys=None, status="pending") -> CardSession:
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.active_interaction = InteractionState(
        interaction_id="approval-1", kind="approval",
        prompt="需要授权后继续执行",
        description="```\nrm -rf build/\n```",
        status=status,
        approval_rule_keys=list(rule_keys or []),
    )
    return session


def test_canonical_keys_map_to_bounded_chinese_hint():
    line = approval_rule_hint_line(["recursive delete"])
    assert line is not None and "递归删除" in line


def test_unknown_keys_are_never_echoed_verbatim():
    line = approval_rule_hint_line(["plugin_rule:custom-internal-id"])
    assert line is not None
    assert "custom-internal-id" not in line


def test_mixed_known_and_unknown_hint_deduplicates():
    line = approval_rule_hint_line(["recursive delete", "recursive delete", "unknown"])
    assert line is not None
    assert line.count("递归删除") == 1


def test_empty_keys_have_no_hint_line():
    assert approval_rule_hint_line([]) is None
    assert approval_rule_hint_line(None) is None


def test_producer_keys_are_bounded_and_normalized():
    keys = ["recursive delete", " ", "x" * 200, 123]
    bounded = bounded_native_rule_keys(keys)
    assert bounded == ["recursive delete"]


def test_pending_card_shows_hint_when_enabled():
    card = render_card(_approval_session(["recursive delete"]), approval_rule_hints=True)
    body = card.get("body", {}).get("elements", []) if "body" in card else card["elements"]
    hints = [e for e in body if isinstance(e, dict) and "规则提示" in str(e.get("content", ""))]
    assert len(hints) == 1 and "递归删除" in hints[0]["content"]


def test_hint_absent_when_disabled_or_unconfigured():
    # Disabled/unset rendering must be byte-identical to a session with no
    # keys at all, so the toggle cannot change any pre-existing card bytes.
    with_keys = render_card(_approval_session(["recursive delete"]))
    without_keys = render_card(_approval_session([]))
    assert with_keys == without_keys
    enabled = render_card(_approval_session(["recursive delete"]), approval_rule_hints=True)
    assert enabled != with_keys


def test_decided_card_keeps_hint_in_review_elements():
    session = _approval_session(["recursive delete"], status="completed")
    session.active_interaction.choice = "once"
    card = render_card(session, approval_rule_hints=True)
    body = card.get("body", {}).get("elements", []) if "body" in card else card["elements"]
    hints = [e for e in body if isinstance(e, dict) and e.get("element_id", "").startswith("approval_rule_hint")
             or isinstance(e, dict) and "递归删除" in str(e.get("content", ""))]
    assert hints


def test_rule_keys_are_not_persisted_in_checkpoint(tmp_path):
    store = SessionStore(tmp_path)
    session = CardSession("conversation", "turn", "chat")
    session.active_interaction = InteractionState(
        "approval-1", "approval", "p", description="d",
        approval_rule_keys=["recursive delete"],
    )
    store.save("turn", session, "om_fixture", None, "", {}, "fixture_client")
    assert "recursive delete" not in next(store.root.glob("*.json")).read_text()

def test_server_render_path_wires_config_to_hint(monkeypatch):
    from hermes_feishu_card.render_options import card_render_options
    opts = card_render_options({'approval_rule_hints': True})
    card = render_card(_approval_session(['recursive delete']), **opts)
    body = card.get('body', {}).get('elements', []) if 'body' in card else card['elements']
    hints = [e for e in body if isinstance(e, dict) and '规则提示' in str(e.get('content', ''))]
    assert len(hints) == 1


def test_legacy_schema1_hint_has_no_element_id_and_budget_rerender_drops_hint():
    # Legacy callback rail is schema 1; existing markdown rows carry no
    # element_id, so the hint must not introduce one.
    session = _approval_session(['recursive delete'])
    card = render_card(session, approval_rule_hints=True)
    assert 'schema' not in card  # legacy rail has no schema field (implicit v1)
    hint_rows = [e for e in card['elements'] if isinstance(e, dict) and '规则提示' in str(e.get('content', ''))]
    assert len(hint_rows) == 1 and 'element_id' not in hint_rows[0]

def test_budget_hint_pushes_over_threshold_and_removal_restores(monkeypatch):
    from hermes_feishu_card.card_limits import (
        inspect_card_limits, serialize_card_json,
    )
    from hermes_feishu_card.render import render_card_result, _render_card_unchecked
    from hermes_feishu_card.session import InteractionOption
    session = _approval_session(['recursive delete'])
    session.active_interaction.description = 'FULL ORIGINAL COMMAND ' + 'x' * 1_400
    session.active_interaction.options = [
        InteractionOption('允许一次', 'once', 'primary'),
        InteractionOption('拒绝', 'deny', 'danger'),
    ]
    session.active_interaction.callback_token = 'fixture-not-a-live-approval'
    baseline_raw = _render_card_unchecked(session, approval_rule_hints=False)
    hint_raw = _render_card_unchecked(session, approval_rule_hints=True)
    baseline_bytes = len(serialize_card_json(baseline_raw).encode('utf-8'))
    hint_bytes = len(serialize_card_json(hint_raw).encode('utf-8'))
    assert baseline_bytes < hint_bytes
    monkeypatch.setattr(
        'hermes_feishu_card.card_limits.SAFE_CARD_JSON_BYTES', baseline_bytes
    )
    assert inspect_card_limits(baseline_raw).safe
    assert not inspect_card_limits(hint_raw).safe
    baseline = render_card_result(session, approval_rule_hints=False)
    removed = render_card_result(session, approval_rule_hints=True)
    assert baseline.disposition == 'card' and baseline.inspection.safe
    assert removed.disposition == 'card' and removed.inspection.safe
    assert serialize_card_json(removed.card) == serialize_card_json(baseline.card)
