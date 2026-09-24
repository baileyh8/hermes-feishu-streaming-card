import asyncio
import json
from pathlib import Path

import pytest

from hermes_feishu_card.feishu_client import FeishuAPIError
from hermes_feishu_card.maintenance_card import (
    FeishuJobPublisher,
    render_update_inspection_card,
    render_update_job_card,
)
from hermes_feishu_card.maintenance_store import UpdateJob
from hermes_feishu_card.maintenance_update import UpdateInspection


def _callback_values(value):
    found = []
    if isinstance(value, dict):
        behaviors = value.get("behaviors")
        if isinstance(behaviors, list):
            for behavior in behaviors:
                if isinstance(behavior, dict) and isinstance(
                    behavior.get("value"), dict
                ):
                    found.append(behavior["value"])
        for child in value.values():
            found.extend(_callback_values(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_callback_values(child))
    return found


@pytest.fixture
def inspection():
    return UpdateInspection(
        ready=True,
        reason_code="ready",
        current_version="0.19.1",
        current_head="a" * 40,
        target_summary="3 updates available; target upstream/f3cda0ce",
        target_fingerprint="b" * 64,
        hfc_version="4.2.0",
        artifact_sha256="c" * 64,
        active_sessions=2,
        requires_drain=True,
        hook_state="installed",
        hook_fingerprint="d" * 64,
        maintenance_ready=True,
        changed_paths=(),
        created_at=100.0,
    )


@pytest.fixture
def job(tmp_path):
    return UpdateJob(
        schema_version=1,
        job_id="job-1",
        path=tmp_path / "jobs" / "job-1.json",
        phase="locking",
        hermes_root=Path("/Users/private/.hermes/hermes-agent"),
        config_path=Path("/Users/private/.hermes_feishu_card/config.yaml"),
        env_file=Path("/Users/private/.hermes_feishu_card/.env"),
        profile_id="default",
        chat_id="oc_private",
        card_message_id="om_card",
        operator_hash="sha256:operator",
        pre_update_version="0.19.1",
        pre_update_head="a" * 40,
        target_fingerprint="b" * 64,
        artifact_version="4.2.0",
        artifact_sha256="c" * 64,
        artifact_path=Path("/Users/private/artifacts/hfc.whl"),
        attempts={},
        created_at=100.0,
        updated_at=100.0,
        result={},
    )


def test_ready_inspection_card_has_confirm_and_cancel_only(inspection):
    card = render_update_inspection_card(
        inspection,
        confirm_value={
            "hfc_action": "operations.select",
            "operation_action": "confirm_update",
            "token": "confirm-token",
        },
        cancel_value={
            "hfc_action": "operations.select",
            "operation_action": "cancel_update",
            "token": "cancel-token",
        },
    )

    values = _callback_values(card)
    assert set(card) == {"schema", "config", "header", "body"}
    assert isinstance(card["body"]["elements"], list)
    assert [item["operation_action"] for item in values] == [
        "confirm_update",
        "cancel_update",
    ]
    serialized = json.dumps(card, ensure_ascii=False)
    assert "确认更新 Hermes" in serialized
    assert "重新获取最新" in serialized
    assert "Hermes：`0.19.1`" in serialized
    assert "HFC：`4.2.0`（保持不变）" in serialized
    assert "当前有 2 个任务" in serialized
    assert "oc_private" not in serialized
    assert "a" * 40 not in serialized
    assert "c" * 64 not in serialized


def test_unready_inspection_card_has_no_buttons_and_safe_recovery(inspection):
    unavailable = UpdateInspection(
        **{
            **inspection.__dict__,
            "ready": False,
            "reason_code": "artifact_version_mismatch",
        }
    )

    card = render_update_inspection_card(
        unavailable,
        confirm_value={"operation_action": "confirm_update"},
        cancel_value={"operation_action": "cancel_update"},
    )

    assert _callback_values(card) == []
    serialized = json.dumps(card, ensure_ascii=False)
    assert "暂不可用" in serialized
    assert "hermes-feishu-card maintenance status" in serialized


@pytest.mark.parametrize(
    "phase",
    [
        "locking",
        "draining",
        "restoring_hooks",
        "updating_hermes",
        "reinstalling_hfc",
        "starting_services",
        "verifying",
        "succeeded",
        "failed",
        "cancelled",
    ],
)
def test_job_card_has_no_buttons_and_no_private_state(job, phase):
    current = UpdateJob(**{**job.__dict__, "phase": phase})

    card = render_update_job_card(current)

    assert set(card) == {"schema", "config", "header", "body"}
    assert isinstance(card["body"]["elements"], list)
    assert _callback_values(card) == []
    serialized = json.dumps(card, ensure_ascii=False)
    assert "/Users/" not in serialized
    assert "oc_private" not in serialized
    assert "om_card" not in serialized
    assert "raw_output" not in serialized
    assert "4.2.0" in serialized


def test_success_card_reports_version_transition_and_readiness(job):
    succeeded = UpdateJob(
        **{
            **job.__dict__,
            "phase": "succeeded",
            "result": {
                "hermes_version": "0.19.2",
                "hfc_version": "4.2.0",
                "import_origin": "site-packages",
                "service_status": "ready",
            },
        }
    )

    serialized = json.dumps(
        render_update_job_card(succeeded),
        ensure_ascii=False,
    )

    assert "0.19.1 → `0.19.2`" in serialized
    assert "HFC：`4.2.0`（保持不变）" in serialized
    assert "服务：已就绪" in serialized


class FakeClient:
    def __init__(self):
        self.updated = []
        self.error = None

    async def update_card_message(self, message_id, card):
        if self.error is not None:
            raise self.error
        self.updated.append((message_id, card))


def test_width_mode_survives_update_confirmation_and_job_publication(inspection, job):
    from hermes_feishu_card.maintenance_card import render_update_operation_card
    from hermes_feishu_card.maintenance_runner import _bot_profile_config, _profile_config

    config = {
        "card": {"width_mode": "compact"},
        "profiles": {"work": {
            "card": {"width_mode": "default"},
            "bots": {"items": {"sales": {
                "app_id": "test", "app_secret": "test", "card": {"width_mode": "fill"},
            }}},
        }},
    }
    resolved = _bot_profile_config(_profile_config(config, "work"), "sales")
    mode = resolved["card"]["width_mode"]
    confirmation = render_update_inspection_card(inspection, {}, {}, width_mode=mode)
    assert confirmation["config"]["width_mode"] == "fill"
    for state in ("locking", "cancelled"):
        assert render_update_operation_card(inspection, state, width_mode=mode)["config"]["width_mode"] == "fill"
    client = FakeClient()
    publisher = FeishuJobPublisher(client, width_mode=mode)
    assert asyncio.run(publisher.publish(job)) is True
    assert client.updated[-1][0] == job.card_message_id
    assert client.updated[-1][1]["config"]["width_mode"] == "fill"
    assert "width_mode" not in render_update_job_card(job)["config"]


def test_publisher_updates_exact_original_message(job):
    client = FakeClient()
    publisher = FeishuJobPublisher(client=client)

    assert asyncio.run(publisher.publish(job)) is True
    assert len(client.updated) == 1
    assert client.updated[0][0] == job.card_message_id


def test_publisher_returns_false_for_api_failure(job, caplog):
    client = FakeClient()
    client.error = FeishuAPIError(
        "private response body token=secret",
        status_code=500,
        api_code="InternalError",
    )

    assert asyncio.run(FeishuJobPublisher(client=client).publish(job)) is False
    assert "private response body" not in caplog.text
    assert "om_card" not in caplog.text
    assert "oc_private" not in caplog.text
    assert "status=500" in caplog.text
    assert "api_code=InternalError" in caplog.text
