from __future__ import annotations

import base64
import json
from hashlib import sha256
from pathlib import Path
import shutil
import threading
from dataclasses import replace

import pytest

from hermes_feishu_card.diagnostics import DiagnosticFinding, DiagnosticReport
from hermes_feishu_card.diagnostics import build_diagnostic_report
from hermes_feishu_card.diagnostics import _CARD_FINDING_CODES
from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card.install.patcher import apply_patch
from hermes_feishu_card.install.recovery import execute_recovery, plan_recovery
from hermes_feishu_card.operations import (
    _FINDING_COPY,
    _operation_buttons,
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


def test_expired_preparing_record_is_reclaimed_at_capacity():
    clock = [100.0]
    store = OperationStore(secret=b"test", now=lambda: clock[0], max_records=1)
    stale, created = store.prepare(
        chat_id="oc_group",
        profile_id="default",
        group=False,
        initiator_open_id="",
        operation_id="operation-stale",
        transport_secret=b"adapter-process-local-proof",
        idempotency_key="doctor-stale",
    )
    clock[0] = stale.expires_at + 1.0

    replacement, replacement_created = store.prepare(
        chat_id="oc_group",
        profile_id="default",
        group=False,
        initiator_open_id="",
        operation_id="operation-replacement",
        transport_secret=b"adapter-process-local-proof",
        idempotency_key="doctor-replacement",
    )

    assert created is True
    assert replacement_created is True
    assert replacement.operation_id == "operation-replacement"
    assert stale.operation_id not in store._records


def test_diagnose_refreshes_operation_expiry_from_completion_time():
    clock = [100.0]
    store = OperationStore(secret=b"test", now=lambda: clock[0])
    record, _created = store.prepare(
        chat_id="oc_group",
        profile_id="default",
        group=False,
        initiator_open_id="",
        operation_id="operation-refresh",
        transport_secret=b"adapter-process-local-proof",
        idempotency_key="doctor-refresh",
    )
    clock[0] = 175.0

    diagnosed = store.diagnose(
        record.operation_id,
        report_fingerprint="report-fresh",
        recovery_fingerprint="recovery-fresh",
    )

    assert diagnosed.expires_at == 295.0


def test_prepare_rejects_existing_operation_id_without_overwriting_record():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    original, _created = store.prepare(
        chat_id="oc_original",
        profile_id="default",
        group=False,
        initiator_open_id="",
        operation_id="operation-collision",
        transport_secret=b"adapter-process-local-proof",
        idempotency_key="doctor-original",
    )

    with pytest.raises(OperationRejected, match="operation id collision"):
        store.prepare(
            chat_id="oc_replacement",
            profile_id="default",
            group=False,
            initiator_open_id="",
            operation_id="operation-collision",
            transport_secret=b"adapter-process-local-proof",
            idempotency_key="doctor-replacement",
        )

    assert store._records[original.operation_id] is original
    assert original.chat_id == "oc_original"


def test_group_repair_confirmation_requires_claimed_operator():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(
        group=True, initiator_open_id="ou_owner", **operation_kwargs()
    )
    confirm = transition(store, operation, "repair")

    with pytest.raises(OperationRejected, match="different operator"):
        transition(store, confirm, "confirm_repair", operator="ou_other")


def test_full_recovery_fingerprint_flows_from_plan_to_operation_to_executor(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures" / "hermes_v2026_4_23"
    root = tmp_path / "hermes"
    shutil.copytree(fixture, root)
    detection = detect_hermes(root)
    original = detection.run_py.read_text(encoding="utf-8")
    patched = apply_patch(original, strategy=detection.hook_strategy)
    backup = detection.run_py.with_name("run.py.hermes_feishu_card.bak")
    backup.write_text(original, encoding="utf-8")
    corrupt = patched.replace("# HERMES_FEISHU_CARD_COMPLETE_PATCH_END\n", "")
    detection.run_py.write_text(corrupt, encoding="utf-8")
    (root / ".hermes_feishu_card_manifest").write_text(
        json.dumps(
            {
                "run_py": "gateway/run.py",
                "patched_sha256": sha256(corrupt.encode("utf-8")).hexdigest(),
                "backup": "gateway/run.py.hermes_feishu_card.bak",
                "backup_sha256": sha256(original.encode("utf-8")).hexdigest(),
            },
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )

    plan = plan_recovery(detection)
    report = build_diagnostic_report(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
        detection,
        plan,
    )
    operation = OperationStore(secret=b"test").create(
        chat_id="oc_group",
        profile_id="default",
        report_fingerprint=report.fingerprint,
        recovery_fingerprint=report.recovery_fingerprint,
        group=False,
    )

    result = execute_recovery(detection, operation.recovery_fingerprint)

    assert len(operation.recovery_fingerprint) == 64
    assert operation.recovery_fingerprint == plan.fingerprint
    assert result.status == "repaired"


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
        "operator_open_id": "ou_forged",
        "timestamp": 99,
    }.items():
        changed = {**fields, key: forged}
        with pytest.raises(OperationRejected, match="transport proof"):
            store.verify_transport_proof(proof=proof, **changed)

    # The callback payload is untrusted; profile scope comes from the operation.
    changed_profile = {**fields, "callback_profile_id": "sales"}
    assert store.verify_transport_proof(proof=proof, **changed_profile) is operation

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


def test_recheck_successor_replaces_stable_predecessor_at_capacity():
    store = OperationStore(secret=b"store", now=lambda: 100.0, max_records=2)
    transport_secret = b"adapter-process-local-proof"
    previous = store.create(
        group=False,
        transport_secret=transport_secret,
        **operation_kwargs(),
    )
    inflight = store.create(group=False, **operation_kwargs())
    inflight.state = "executing"
    token = store.token(previous, "recheck")
    recheck_kwargs = {
        "callback_chat_id": "oc_group",
        "callback_profile_id": "default",
        "callback_profile_scope": store.scope_fingerprint(previous),
        "callback_report_fingerprint": "report-123",
        "callback_recovery_fingerprint": "recovery-123",
        "successor_report_fingerprint": "report-123",
        "successor_recovery_fingerprint": "recovery-123",
    }

    successor, created = store.recheck_successor(token, **recheck_kwargs)
    repeated, repeated_created = store.recheck_successor(token, **recheck_kwargs)

    assert created is True
    assert repeated_created is False
    assert repeated is successor
    assert previous.operation_id not in store._records
    assert set(store._records) == {inflight.operation_id, successor.operation_id}
    assert store.complete(
        inflight.operation_id,
        expected_state="executing",
        state="repaired",
        result={},
    ).state == "repaired"

    successor_token = store.token(successor, "details")
    transport_fields = {
        "token": successor_token,
        "action": "details",
        "callback_chat_id": "oc_group",
        "callback_profile_id": "default",
        "callback_profile_scope": store.scope_fingerprint(successor),
        "operator_open_id": "ou_owner",
        "timestamp": 100,
    }
    assert store.verify_transport_proof(
        proof=sign_transport_proof(transport_secret, **transport_fields),
        **transport_fields,
    ) is successor


def _prepared_diagnosed_operation(
    store: OperationStore,
    *,
    operation_id: str,
    diagnostic_report: DiagnosticReport,
    group: bool = False,
    initiator_open_id: str = "",
    transport_secret: bytes = b"adapter-process-local-proof",
):
    preparing, created = store.prepare(
        chat_id="oc_group",
        profile_id="default",
        group=group,
        initiator_open_id=initiator_open_id,
        operation_id=operation_id,
        transport_secret=transport_secret,
        idempotency_key=f"doctor-{operation_id}",
    )

    assert created is True
    return store.diagnose(preparing.operation_id, report=diagnostic_report)


def _recheck_callback(store: OperationStore, operation: object) -> dict[str, str]:
    return {
        "callback_chat_id": "oc_group",
        "callback_profile_id": "default",
        "callback_profile_scope": store.scope_fingerprint(operation),
        "callback_report_fingerprint": operation.report_fingerprint,
        "callback_recovery_fingerprint": operation.recovery_fingerprint,
    }


def test_diagnose_retains_report_snapshot_in_memory():
    store = OperationStore(secret=b"store", now=lambda: 100.0)
    snapshot = report()

    diagnosed = _prepared_diagnosed_operation(
        store,
        operation_id="operation-snapshot",
        diagnostic_report=snapshot,
    )

    assert diagnosed.report is snapshot
    assert diagnosed.report_fingerprint == snapshot.fingerprint
    assert diagnosed.recovery_fingerprint == snapshot.recovery_fingerprint


def test_begin_recheck_creates_preparing_successor_from_report_snapshot():
    store = OperationStore(secret=b"store", now=lambda: 100.0)
    snapshot = report()
    previous = _prepared_diagnosed_operation(
        store,
        operation_id="operation-recheck",
        diagnostic_report=snapshot,
    )

    successor, created = store.begin_recheck(
        store.token(previous, "recheck"), **_recheck_callback(store, previous)
    )

    assert created is True
    assert successor.state == "preparing"
    assert successor.report is snapshot
    assert successor.report_fingerprint == snapshot.fingerprint
    assert successor.recovery_fingerprint == snapshot.recovery_fingerprint


def test_begin_recheck_reuses_successor_and_inherits_transport_and_owner():
    store = OperationStore(secret=b"store", now=lambda: 100.0)
    transport_secret = b"adapter-process-local-proof"
    previous = _prepared_diagnosed_operation(
        store,
        operation_id="operation-owner",
        diagnostic_report=report(),
        group=True,
        initiator_open_id="ou_owner",
        transport_secret=transport_secret,
    )
    token = store.token(previous, "recheck")
    callback = _recheck_callback(store, previous)

    successor, created = store.begin_recheck(token, **callback)
    repeated, repeated_created = store.begin_recheck(token, **callback)

    assert created is True
    assert repeated_created is False
    assert repeated is successor
    assert successor.group is True
    assert successor.owner_open_id == "ou_owner"
    assert successor.transport_lineage_id == previous.transport_lineage_id
    transport_fields = {
        "token": store.token(successor, "details"),
        "action": "details",
        "callback_chat_id": "oc_group",
        "callback_profile_id": "default",
        "callback_profile_scope": store.scope_fingerprint(successor),
        "operator_open_id": "ou_reader",
        "timestamp": 100,
    }
    assert store.verify_transport_proof(
        proof=sign_transport_proof(transport_secret, **transport_fields),
        **transport_fields,
    ) is successor


def test_operations_transport_lineage_is_stable_and_rendered_on_successors():
    store = OperationStore(secret=b"store", now=lambda: 100.0)
    previous = _prepared_diagnosed_operation(
        store,
        operation_id="operation-lineage",
        diagnostic_report=report(),
    )
    preparing, _created = store.begin_recheck(
        store.token(previous, "recheck"), **_recheck_callback(store, previous)
    )
    completed = store.create_successor(preparing.operation_id, report=report())

    button = _operation_buttons(completed.report, completed, store)[0]
    value = button["behaviors"][0]["value"]

    assert previous.transport_lineage_id == "operation-lineage"
    assert preparing.transport_lineage_id == "operation-lineage"
    assert completed.transport_lineage_id == "operation-lineage"
    assert value["transport_lineage_id"] == "operation-lineage"


def test_begin_recheck_preserves_capacity_and_refuses_late_predecessor_diagnose():
    store = OperationStore(secret=b"store", now=lambda: 100.0, max_records=2)
    previous = _prepared_diagnosed_operation(
        store,
        operation_id="operation-predecessor",
        diagnostic_report=report(),
    )
    inflight = store.create(group=False, **operation_kwargs())
    inflight.state = "executing"
    token = store.token(previous, "recheck")
    callback = _recheck_callback(store, previous)

    successor, created = store.begin_recheck(token, **callback)
    repeated, repeated_created = store.begin_recheck(token, **callback)

    assert created is True
    assert repeated_created is False
    assert repeated is successor
    assert set(store._records) == {inflight.operation_id, successor.operation_id}
    with pytest.raises(OperationRejected, match="operation state changed"):
        store.diagnose(previous.operation_id, report=report())
    assert successor.report is previous.report


def test_recheck_preparing_record_keeps_snapshot_until_completion_successor():
    store = OperationStore(secret=b"store", now=lambda: 100.0)
    original_report = report()
    previous = _prepared_diagnosed_operation(
        store,
        operation_id="operation-preparing",
        diagnostic_report=original_report,
    )
    preparing, created = store.begin_recheck(
        store.token(previous, "recheck"), **_recheck_callback(store, previous)
    )
    preparing_token = store.token(preparing, "recheck")
    preparing_callback = _recheck_callback(store, preparing)
    fresh_report = replace(
        original_report,
        config={**original_report.config, "marker": "fresh-completion"},
    )

    repeated, repeated_created = store.begin_recheck(
        preparing_token, **preparing_callback
    )
    completed = store.create_successor(preparing.operation_id, report=fresh_report)
    _claims, linked_preparing = store.inspect(
        preparing_token,
        callback_chat_id="oc_group",
        callback_profile_id="default",
        callback_profile_scope=store.scope_fingerprint(preparing),
        allow_recheck_predecessor=True,
    )

    assert created is True
    assert repeated_created is False
    assert repeated is preparing
    assert preparing.report is original_report
    assert preparing.report_fingerprint == original_report.fingerprint
    assert preparing.recovery_fingerprint == original_report.recovery_fingerprint
    assert linked_preparing is preparing
    assert store.current_successor(preparing.operation_id) is completed
    assert completed.report is fresh_report
    assert completed.report_fingerprint == fresh_report.fingerprint


def test_begin_recheck_rejects_legacy_predecessor_without_report_snapshot():
    store = OperationStore(secret=b"store", now=lambda: 100.0, max_records=2)
    previous = store.create(
        group=False,
        transport_secret=b"adapter-process-local-proof",
        **operation_kwargs(),
    )
    inflight = store.create(group=False, **operation_kwargs())
    inflight.state = "executing"
    records_before = dict(store._records)
    transport_before = dict(store._transport_secrets)

    with pytest.raises(
        OperationRejected, match="operation report snapshot unavailable"
    ):
        store.begin_recheck(
            store.token(previous, "recheck"),
            **_recheck_callback(store, previous),
        )

    assert store._records == records_before
    assert store._transport_secrets == transport_before
    assert len(store._records) == store._max_records
    assert previous.successor_operation_id == ""


def test_completion_successor_keeps_predecessor_lookup_at_capacity():
    store = OperationStore(secret=b"store", now=lambda: 100.0, max_records=1)
    previous = store.create(
        group=False,
        transport_secret=b"adapter-process-local-proof",
        **operation_kwargs(),
    )
    old_token = store.token(previous, "confirm_repair")

    successor = store.create_successor(
        previous.operation_id,
        report=report(),
    )
    _claims, predecessor = store.inspect(
        old_token,
        callback_chat_id="oc_group",
        callback_profile_id="default",
        callback_profile_scope=store.scope_fingerprint(previous),
        allow_successor_predecessor=True,
    )

    assert previous.successor_operation_id == successor.operation_id
    assert predecessor is previous
    assert store.current_successor(previous.operation_id) is successor
    assert set(store._records) == {successor.operation_id}


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


def operation_buttons(card: dict[str, object]) -> list[dict[str, object]]:
    buttons: list[dict[str, object]] = []
    for element in card["body"]["elements"]:
        if element.get("tag") == "button":
            buttons.append(element)
        elif element.get("tag") == "column_set":
            for column in element["columns"]:
                buttons.extend(
                    item
                    for item in column["elements"]
                    if item.get("tag") == "button"
                )
    return buttons


def action_labels(card: dict[str, object]) -> list[str]:
    return [button["text"]["content"] for button in operation_buttons(card)]


def test_operations_card_uses_static_safe_finding_copy_and_details_state():
    safe_report = DiagnosticReport(
        status="ok",
        created_at=100.0,
        config={}, hermes={}, streaming={},
        install_state={"recovery_executable": True}, routing={}, runtime={},
        findings=(
            DiagnosticFinding("install_state_installed", "info", "TOKEN=/private/token"),
            DiagnosticFinding("route_fallback", "info", "PATH=/private/route"),
            DiagnosticFinding("future_code", "warning", "secret impact /private/key"),
        ),
    )
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())

    summary = render_operations_card(safe_report, operation, "footer", store=store)
    operation.result = {"show_details": True}
    details = render_operations_card(safe_report, operation, "footer", store=store)
    summary_text = json.dumps(summary, ensure_ascii=False)
    details_text = json.dumps(details, ensure_ascii=False)

    assert "安装状态正常" in summary_text
    assert "当前使用默认路由" in summary_text
    assert "诊断详情" in details_text
    assert "建议检查路由绑定" in details_text
    assert "检测到需要检查的项目" in details_text
    assert summary != details
    assert "查看诊断" in action_labels(summary)
    assert "查看诊断" not in action_labels(details)
    for sensitive in ("TOKEN=", "/private/token", "/private/route", "/private/key", "secret impact"):
        assert sensitive not in details_text


def test_operations_finding_copy_covers_every_card_safe_diagnostic_code():
    assert _CARD_FINDING_CODES <= set(_FINDING_COPY)
    for code in _CARD_FINDING_CODES:
        summary, detail = _FINDING_COPY[code]
        assert summary.strip()
        assert detail.strip()
        assert not any(
            sensitive in f"{summary}\n{detail}".lower()
            for sensitive in ("/private/", "token", "manifest.json", "gateway/run.py")
        )


def test_operations_card_places_actions_before_existing_divider_and_footer():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())

    card = render_operations_card(
        report(), operation, "configured footer", store=store
    )
    elements = card["body"]["elements"]
    ids = [element.get("element_id") for element in elements]
    buttons = operation_buttons(card)
    actions = ["details", "recheck", "repair", "dismiss"]

    assert ids == [
        "operations_summary",
        "operations_row_0",
        "operations_row_1",
        "operations_divider",
        "operations_footer",
    ]
    assert len(ids) == len(set(ids))
    rows = [item for item in elements if item.get("tag") == "column_set"]
    assert [row["element_id"] for row in rows] == [
        "operations_row_0",
        "operations_row_1",
    ]
    assert all(row["flex_mode"] == "none" for row in rows)
    assert all(row["horizontal_spacing"] == "8px" for row in rows)
    assert all(len(row["columns"]) == 2 for row in rows)
    assert all(
        column["width"] == "auto" and "weight" not in column
        for row in rows
        for column in row["columns"]
    )
    assert [item.get("element_id") for item in elements][-2:] == [
        "operations_divider",
        "operations_footer",
    ]
    assert len([button["element_id"] for button in buttons]) == len(
        {button["element_id"] for button in buttons}
    )
    assert not any(element.get("tag") == "action" for element in elements)
    assert elements[-1]["content"] == "configured footer"
    assert action_labels(card) == ["查看诊断", "重新检测", "安全修复", "暂不处理"]
    assert [button["type"] for button in buttons] == ["default"] * len(actions)
    assert all(button["size"] == "medium" for button in buttons)
    assert all(button["width"] == "default" for button in buttons)
    assert all("value" not in button for button in buttons)
    assert len(buttons) == len(actions)
    for button, action in zip(buttons, actions):
        assert button["behaviors"] == [
            {
                "type": "callback",
                "value": {
                    "hfc_action": "operations.select",
                    "operation_action": action,
                        "token": store.token(operation, action),
                        "profile_scope": store.scope_fingerprint(operation),
                        "transport_lineage_id": operation.transport_lineage_id,
                },
            }
        ]
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
    buttons = operation_buttons(card)

    assert [button["text"]["content"] for button in buttons] == ["确认修复", "取消"]
    assert buttons[0]["type"] == "primary"
    assert buttons[1]["type"] == "default"
    rows = [
        item
        for item in card["body"]["elements"]
        if item.get("tag") == "column_set"
    ]
    assert len(rows) == 1
    assert len(rows[0]["columns"]) == 2


def test_operations_card_shows_preparing_recheck_with_visible_fallback():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())
    operation.state = "preparing"

    card = render_operations_card(report(), operation, "footer", store=store)

    summary = card["body"]["elements"][0]["content"]
    assert "正在重新检测" in summary
    assert action_labels(card) == ["重新检测"]


@pytest.mark.parametrize("state", ("preparing", "executing", "restarting"))
def test_operations_card_keeps_recheck_fallback_while_mutation_is_inflight(state):
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())
    operation.state = state

    card = render_operations_card(report(), operation, "footer", store=store)

    assert action_labels(card) == ["重新检测"]


def test_operations_card_keeps_a_single_odd_button_in_the_left_column():
    store = OperationStore(secret=b"test", now=lambda: 100.0)
    operation = store.create(group=False, **operation_kwargs())
    operation.state = "failed"

    card = render_operations_card(report(), operation, "footer")
    rows = [
        item
        for item in card["body"]["elements"]
        if item.get("tag") == "column_set"
    ]

    assert len(rows) == 1
    assert len(rows[0]["columns"]) == 1
    assert rows[0]["columns"][0]["elements"][0]["element_id"] == "operations_recheck"


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
