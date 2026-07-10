from __future__ import annotations

import base64
import json
import threading
from dataclasses import replace

import pytest

from hermes_feishu_card.diagnostics import DiagnosticFinding, DiagnosticReport
from hermes_feishu_card.operations import (
    OperationRejected,
    OperationStore,
    render_operations_card,
    sign_transport_proof,
)


def operation_kwargs() -> dict[str, object]:
    return {
        "chat_id": "oc_group",
        "profile_id": "default",
        "report_fingerprint": "report-123",
        "recovery_fingerprint": "recovery-123",
    }


def transition(
    store: OperationStore,
    record: object,
    action: str,
    *,
    operator: str = "ou_owner",
    chat_id: str = "oc_group",
    profile_id: str = "default",
    report_fingerprint: str = "report-123",
    recovery_fingerprint: str = "recovery-123",
):
    return store.transition(
        store.token(record, action),
        action=action,
        operator_open_id=operator,
        callback_chat_id=chat_id,
        callback_profile_id=profile_id,
        callback_report_fingerprint=report_fingerprint,
        callback_recovery_fingerprint=recovery_fingerprint,
    )


def test_group_repair_confirmation_requires_claimed_operator():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(
        group=True, initiator_open_id="ou_owner", **operation_kwargs()
    )
    confirm = transition(store, operation, "repair")

    with pytest.raises(OperationRejected, match="different operator"):
        transition(store, confirm, "confirm_repair", operator="ou_other")


def test_private_repair_confirmation_does_not_compare_operators():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(
        group=False, initiator_open_id="ou_first", **operation_kwargs()
    )
    confirm = transition(store, operation, "repair", operator="ou_first")
    accepted = transition(store, confirm, "confirm_repair", operator="ou_second")

    assert accepted.state == "executing"
    assert accepted.owner_open_id == ""


def test_group_initiator_owns_first_mutation_click():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(
        group=True, initiator_open_id="ou_initiator", **operation_kwargs()
    )

    with pytest.raises(OperationRejected, match="different operator"):
        transition(store, operation, "repair", operator="ou_other")


def test_group_first_mutation_click_claims_missing_initiator():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=True, **operation_kwargs())

    confirm = transition(store, operation, "repair", operator="ou_claimant")

    assert confirm.owner_open_id == "ou_claimant"
    with pytest.raises(OperationRejected, match="different operator"):
        transition(store, confirm, "confirm_repair", operator="ou_other")


@pytest.mark.parametrize("action", ["repair", "restart"])
def test_group_mutation_requires_operator_identity(action):
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=True, **operation_kwargs())
    if action == "restart":
        operation.state = "repaired"

    with pytest.raises(OperationRejected, match="operator identity required"):
        transition(store, operation, action, operator="")


def test_read_only_actions_do_not_compare_group_operator():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(
        group=True, initiator_open_id="ou_owner", **operation_kwargs()
    )

    assert transition(store, operation, "details", operator="ou_reader").state == "diagnosed"
    assert transition(store, operation, "recheck", operator="ou_reader").state == "diagnosed"


@pytest.mark.parametrize(
    "state", ["repaired", "failed", "expired", "restarted", "restart_failed"]
)
def test_recheck_is_available_from_every_stable_result_state(state):
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())
    operation.state = state

    assert transition(store, operation, "recheck", operator="ou_reader").state == state


def test_cancel_returns_to_stable_state():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())

    confirm_repair = transition(store, operation, "repair")
    assert transition(store, confirm_repair, "cancel").state == "diagnosed"
    operation.state = "repaired"
    confirm_restart = transition(store, operation, "restart")
    assert transition(store, confirm_restart, "cancel").state == "repaired"


def test_group_restart_first_click_claim_and_confirmation_operator_matrix():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=True, **operation_kwargs())
    operation.state = "repaired"

    confirm = transition(store, operation, "restart", operator="ou_first")
    with pytest.raises(OperationRejected, match="different operator"):
        transition(store, confirm, "confirm_restart", operator="ou_other")
    assert transition(
        store, confirm, "confirm_restart", operator="ou_first"
    ).state == "restarting"


def test_group_restart_initiator_must_make_first_click_and_confirmation():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(
        group=True, initiator_open_id="ou_owner", **operation_kwargs()
    )
    operation.state = "repaired"

    with pytest.raises(OperationRejected, match="different operator"):
        transition(store, operation, "restart", operator="ou_other")
    confirm = transition(store, operation, "restart", operator="ou_owner")
    with pytest.raises(OperationRejected, match="different operator"):
        transition(store, confirm, "confirm_restart", operator="ou_other")
    assert transition(
        store, confirm, "confirm_restart", operator="ou_owner"
    ).state == "restarting"


def test_operation_expires_at_exactly_120_seconds():
    clock = [100.0]
    store = OperationStore(secret=b"test", now=lambda: clock[0])
    operation = store.create(group=False, **operation_kwargs())
    token = store.token(operation, "details")
    clock[0] = 220.0

    with pytest.raises(OperationRejected, match="expired"):
        store.transition(
            token,
            action="details",
            operator_open_id="",
            callback_chat_id="oc_group",
            callback_profile_id="default",
            callback_report_fingerprint="report-123",
            callback_recovery_fingerprint="recovery-123",
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"chat_id": "oc_other"}, "scope mismatch"),
        ({"profile_id": "sales"}, "scope mismatch"),
        ({"report_fingerprint": "report-new"}, "diagnosis changed"),
        ({"recovery_fingerprint": "recovery-new"}, "recovery changed"),
    ],
)
def test_callback_scope_and_fingerprints_must_match(overrides, message):
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())

    with pytest.raises(OperationRejected, match=message):
        transition(store, operation, "details", **overrides)


def test_token_action_and_signature_are_verified():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())
    token = store.token(operation, "details")

    with pytest.raises(OperationRejected, match="action mismatch"):
        store.transition(
            token,
            action="recheck",
            operator_open_id="",
            callback_chat_id="oc_group",
            callback_profile_id="default",
            callback_report_fingerprint="report-123",
            callback_recovery_fingerprint="recovery-123",
        )
    with pytest.raises(OperationRejected, match="invalid operation token"):
        store.transition(
            token[:-1] + ("0" if token[-1] != "0" else "1"),
            action="details",
            operator_open_id="",
            callback_chat_id="oc_group",
            callback_profile_id="default",
            callback_report_fingerprint="report-123",
            callback_recovery_fingerprint="recovery-123",
        )


@pytest.mark.parametrize("token", ["", ".", "x.y", "a" * 5000])
def test_malformed_and_oversized_tokens_are_rejected(token):
    store = OperationStore(secret=b"test", now=lambda: 100.0)

    with pytest.raises(OperationRejected, match="invalid operation token"):
        store.transition(
            token,
            action="details",
            operator_open_id="",
            callback_chat_id="oc_group",
            callback_profile_id="default",
            callback_report_fingerprint="report-123",
            callback_recovery_fingerprint="recovery-123",
        )


def test_token_payload_is_bounded_and_omits_raw_scope_and_operator_ids():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(
        group=True, initiator_open_id="ou_secret", **operation_kwargs()
    )
    token = store.token(operation, "repair")
    encoded = token.split(".", 1)[0]
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    serialized = json.dumps(payload)

    assert set(payload) == {
        "action",
        "expires_at",
        "operation_id",
        "report_fingerprint",
    }
    assert "oc_group" not in serialized
    assert "default" not in serialized
    assert "ou_secret" not in serialized
    assert "recovery-123" not in serialized
    assert len(token) < 1024


def test_duplicate_transition_is_rejected():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())
    token = store.token(operation, "repair")
    kwargs = {
        "action": "repair",
        "operator_open_id": "ou_owner",
        "callback_chat_id": "oc_group",
        "callback_profile_id": "default",
        "callback_report_fingerprint": "report-123",
        "callback_recovery_fingerprint": "recovery-123",
    }

    store.transition(token, **kwargs)
    with pytest.raises(OperationRejected, match="invalid operation transition"):
        store.transition(token, **kwargs)


def test_concurrent_confirm_executes_transition_once():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())
    transition(store, operation, "repair")
    token = store.token(operation, "confirm_repair")
    accepted = []
    rejected = []

    def click():
        try:
            accepted.append(
                store.transition(
                    token,
                    action="confirm_repair",
                    operator_open_id="ou_owner",
                    callback_chat_id="oc_group",
                    callback_profile_id="default",
                    callback_report_fingerprint="report-123",
                    callback_recovery_fingerprint="recovery-123",
                )
            )
        except OperationRejected as exc:
            rejected.append(str(exc))

    threads = [threading.Thread(target=click) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(accepted) == 1
    assert accepted[0].state == "executing"
    assert rejected == ["invalid operation transition"] * 7


def test_complete_requires_expected_state_and_publishes_result_once():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())
    transition(store, operation, "repair")
    transition(store, operation, "confirm_repair")

    completed = store.complete(
        operation.operation_id,
        expected_state="executing",
        state="repaired",
        result={"status": "repaired"},
    )

    assert completed.state == "repaired"
    assert completed.result == {"status": "repaired"}
    with pytest.raises(OperationRejected, match="state changed"):
        store.complete(
            operation.operation_id,
            expected_state="executing",
            state="failed",
            result={"status": "failed"},
        )


def test_record_retention_is_bounded_and_prunes_old_expired_records():
    clock = [0.0]
    store = OperationStore(secret=b"test", now=lambda: clock[0], max_records=2)
    first = store.create(group=False, **operation_kwargs())
    store.create(group=False, **operation_kwargs())
    store.create(group=False, **operation_kwargs())

    with pytest.raises(OperationRejected, match="expired"):
        transition(store, first, "details")
    clock[0] = 421.0
    latest = store.create(group=False, **operation_kwargs())
    assert transition(store, latest, "details").operation_id == latest.operation_id


def test_record_capacity_never_evicts_inflight_operations():
    store = OperationStore(secret=b"test", now=lambda: 100.0, max_records=2)
    executing = store.create(group=False, **operation_kwargs())
    restarting = store.create(group=False, **operation_kwargs())
    executing.state = "executing"
    restarting.state = "restarting"

    with pytest.raises(OperationRejected, match="capacity"):
        store.create(group=False, **operation_kwargs())

    assert store.complete(
        executing.operation_id,
        expected_state="executing",
        state="repaired",
        result={"status": "repaired"},
    ).state == "repaired"
    assert store.complete(
        restarting.operation_id,
        expected_state="restarting",
        state="restarted",
        result={"status": "restarted"},
    ).state == "restarted"


def test_record_capacity_prunes_non_inflight_before_inflight():
    store = OperationStore(secret=b"test", now=lambda: 100.0, max_records=2)
    executing = store.create(group=False, **operation_kwargs())
    disposable = store.create(group=False, **operation_kwargs())
    executing.state = "executing"

    created = store.create(group=False, **operation_kwargs())

    assert store.complete(
        executing.operation_id,
        expected_state="executing",
        state="repaired",
        result={"status": "repaired"},
    ).state == "repaired"
    with pytest.raises(OperationRejected, match="expired"):
        transition(store, disposable, "details")
    assert transition(store, created, "details").operation_id == created.operation_id


def test_record_retention_never_evicts_executing_or_restarting_operations():
    store = OperationStore(secret=b"test", now=lambda: 100.0, max_records=2)
    executing = store.create(group=False, **operation_kwargs())
    transition(store, executing, "repair")
    transition(store, executing, "confirm_repair")
    restarting = store.create(group=False, **operation_kwargs())
    restarting.state = "restarting"

    with pytest.raises(OperationRejected, match="store overloaded"):
        store.create(group=False, **operation_kwargs())

    assert store.complete(
        executing.operation_id,
        expected_state="executing",
        state="repaired",
        result={"status": "repaired"},
    ).state == "repaired"
    assert store.complete(
        restarting.operation_id,
        expected_state="restarting",
        state="restarted",
        result={"return_code": 0},
    ).state == "restarted"


def test_record_retention_evicts_stable_record_before_active_operation():
    store = OperationStore(secret=b"test", now=lambda: 100.0, max_records=2)
    active = store.create(group=False, **operation_kwargs())
    transition(store, active, "repair")
    transition(store, active, "confirm_repair")
    stable = store.create(group=False, **operation_kwargs())

    replacement = store.create(group=False, **operation_kwargs())

    assert store.complete(
        active.operation_id,
        expected_state="executing",
        state="repaired",
        result={},
    ).state == "repaired"
    with pytest.raises(OperationRejected, match="expired"):
        transition(store, stable, "details")
    assert transition(store, replacement, "details").operation_id == replacement.operation_id


def test_transport_proof_binds_token_scope_operator_action_and_timestamp():
    clock = [100.0]
    transport_secret = b"adapter-process-local-proof"
    store = OperationStore(secret=b"store", now=lambda: clock[0])
    operation = store.create(
        group=True,
        initiator_open_id="ou_owner",
        transport_secret=transport_secret,
        **operation_kwargs(),
    )
    token = store.token(operation, "repair")
    fields = {
        "token": token,
        "action": "repair",
        "callback_chat_id": "oc_group",
        "callback_profile_id": "default",
        "callback_profile_scope": store.scope_fingerprint(operation),
        "operator_open_id": "ou_owner",
        "timestamp": 100,
    }
    proof = sign_transport_proof(transport_secret, **fields)

    assert store.verify_transport_proof(proof=proof, **fields) is operation

    for key, forged in {
        "action": "confirm_repair",
        "callback_chat_id": "oc_other",
        "callback_profile_id": "sales",
        "operator_open_id": "ou_forged",
        "timestamp": 99,
    }.items():
        changed = {**fields, key: forged}
        with pytest.raises(OperationRejected, match="transport proof"):
            store.verify_transport_proof(proof=proof, **changed)

    clock[0] = 131.0
    with pytest.raises(OperationRejected, match="transport proof expired"):
        store.verify_transport_proof(proof=proof, **fields)


def test_callback_rejects_invalid_scope_even_when_profile_id_matches():
    store = OperationStore(secret=b"store", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())

    with pytest.raises(OperationRejected, match="scope mismatch"):
        store.inspect(
            store.token(operation, "details"),
            callback_chat_id="oc_group",
            callback_profile_id="default",
            callback_profile_scope="forged-scope",
        )


def test_successor_inherits_transport_binding_when_store_is_at_capacity():
    store = OperationStore(secret=b"store", now=lambda: 100.0, max_records=1)
    transport_secret = b"adapter-process-local-proof"
    previous = store.create(
        group=False,
        transport_secret=transport_secret,
        **operation_kwargs(),
    )

    successor = store.create(
        group=False,
        transport_source_operation_id=previous.operation_id,
        **operation_kwargs(),
    )
    token = store.token(successor, "details")
    fields = {
        "token": token,
        "action": "details",
        "callback_chat_id": "oc_group",
        "callback_profile_id": "default",
        "callback_profile_scope": store.scope_fingerprint(successor),
        "operator_open_id": "ou_owner",
        "timestamp": 100,
    }

    assert store.verify_transport_proof(
        proof=sign_transport_proof(transport_secret, **fields),
        **fields,
    ) is successor


def report(*, executable: bool = True) -> DiagnosticReport:
    return DiagnosticReport(
        status="warning",
        created_at=100.0,
        config={"path": "/private/config.yaml"},
        hermes={"root": "/private/hermes", "status": "supported"},
        streaming={"status": "enabled"},
        install_state={
            "status": "incomplete",
            "recovery_executable": executable,
            "recovery_fingerprint": "recovery-card-safe",
        },
        routing={"profile_id": "default"},
        runtime={},
        findings=(
            DiagnosticFinding(
                code="owned_incomplete",
                severity="warning",
                message="Hook state needs repair.",
                impact="Streaming may be incomplete.",
            ),
        ),
    )


def action_labels(card: dict[str, object]) -> list[str]:
    labels = []
    for element in card["body"]["elements"]:
        if element.get("tag") != "action":
            continue
        labels.extend(action["text"]["content"] for action in element["actions"])
    return labels


def test_operations_card_places_actions_before_existing_divider_and_footer():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())

    card = render_operations_card(report(), operation, "configured footer")
    elements = card["body"]["elements"]
    ids = [element.get("element_id") for element in elements]

    assert ids[-2:] == ["operations_divider", "operations_footer"]
    assert elements[-1]["content"] == "configured footer"
    assert action_labels(card) == ["查看诊断", "重新检测", "安全修复", "暂不处理"]
    serialized = json.dumps(card, ensure_ascii=False)
    assert "oc_group" not in serialized
    assert '"profile_id"' not in serialized


def test_operations_card_hides_repair_when_plan_is_not_executable():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())

    card = render_operations_card(report(executable=False), operation, "footer")

    assert "安全修复" not in action_labels(card)


def test_operations_confirmation_buttons_are_primary_and_cancel_is_default():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())
    transition(store, operation, "repair")

    card = render_operations_card(report(), operation, "footer")
    action = next(item for item in card["body"]["elements"] if item.get("tag") == "action")

    assert [button["text"]["content"] for button in action["actions"]] == ["确认修复", "取消"]
    assert action["actions"][0]["type"] == "primary"
    assert action["actions"][1]["type"] == "default"


def test_operations_card_can_show_restart_only_when_result_allows_it():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())
    operation.state = "repaired"
    operation.result = {"restart_available": False}
    without_restart = render_operations_card(report(), operation, "footer")
    operation.result = {"restart_available": True}
    with_restart = render_operations_card(report(), operation, "footer")

    assert "重启 Gateway" not in action_labels(without_restart)
    assert "重启 Gateway" in action_labels(with_restart)


def test_renderer_never_displays_internal_state_or_operator_identity():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(
        group=True, initiator_open_id="ou_secret", **operation_kwargs()
    )
    operation = replace(operation, state="confirm_restart")

    card = render_operations_card(report(), operation, "footer")
    serialized = json.dumps(card, ensure_ascii=False)
    visible_text = " ".join(
        str(item.get("content") or "")
        for element in card["body"]["elements"]
        for item in (
            element.get("text", {}),
            {"content": element.get("content", "")},
        )
        if isinstance(item, dict)
    )

    assert "confirm_restart" not in visible_text
    assert "ou_secret" not in serialized
    assert "确认重启" in serialized
