from __future__ import annotations

import os

import pytest

from hermes_feishu_card.operations_transport import (
    CommandProofVerifier,
    TransportAuthenticationError,
    derive_operation_transport_secret,
    ensure_transport_root_secret,
    read_transport_root_secret,
    sign_command_transport_proof,
)


def command_payload():
    return {
        "command": "doctor",
        "chat_id": "oc_group",
        "message_id": "om_command",
        "profile_id": "work",
        "profile_source": "event",
        "chat_type": "group",
        "operator": "ou_owner",
        "created_at": 100.0,
        "platform": "feishu",
    }


def test_sidecar_root_secret_is_atomic_private_and_reusable(tmp_path):
    state_dir = tmp_path / "state"

    first = ensure_transport_root_secret(state_dir)
    second = ensure_transport_root_secret(state_dir)

    assert first == second
    assert len(first) == 32
    assert read_transport_root_secret(state_dir) == first
    assert os.stat(state_dir).st_mode & 0o777 == 0o700
    secret_path = state_dir / "operations.transport.key"
    assert os.stat(secret_path).st_mode & 0o777 == 0o600
    assert list(state_dir.glob("*.tmp")) == []


def test_hook_refuses_missing_or_insecure_root_secret(tmp_path):
    state_dir = tmp_path / "state"
    assert read_transport_root_secret(state_dir) is None

    secret = ensure_transport_root_secret(state_dir)
    secret_path = state_dir / "operations.transport.key"
    secret_path.chmod(0o644)

    assert secret
    assert read_transport_root_secret(state_dir) is None


def test_windows_transport_uses_regular_secret_without_posix_mode_checks(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    monkeypatch.setattr("hermes_feishu_card.operations_transport._is_windows", lambda: True)

    secret = ensure_transport_root_secret(state_dir)
    state_dir.chmod(0o755)
    (state_dir / "operations.transport.key").chmod(0o644)

    assert read_transport_root_secret(state_dir) == secret


@pytest.mark.parametrize("windows", [False, True])
def test_ensure_transport_root_secret_rejects_existing_root_or_secret_symlink(
    monkeypatch, tmp_path, windows
):
    monkeypatch.setattr("hermes_feishu_card.operations_transport._is_windows", lambda: windows)
    target_root = tmp_path / "target-root"
    target_root.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target_root, target_is_directory=True)

    with pytest.raises(OSError, match="invalid"):
        ensure_transport_root_secret(root_link)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    secret_target = tmp_path / "secret-target"
    secret_target.write_bytes(b"s" * 32)
    (state_dir / "operations.transport.key").symlink_to(secret_target)

    with pytest.raises(OSError, match="invalid"):
        ensure_transport_root_secret(state_dir)


@pytest.mark.parametrize("windows", [False, True])
def test_ensure_transport_root_secret_rejects_symlink_created_by_race_winner(
    monkeypatch, tmp_path, windows
):
    monkeypatch.setattr("hermes_feishu_card.operations_transport._is_windows", lambda: windows)
    state_dir = tmp_path / "state"
    target = tmp_path / "race-target"
    target.write_bytes(b"r" * 32)

    def race_winner(_temp_path, destination):
        destination.symlink_to(target)
        raise FileExistsError

    monkeypatch.setattr("hermes_feishu_card.operations_transport.os.link", race_winner)

    with pytest.raises(OSError, match="created"):
        ensure_transport_root_secret(state_dir)


def test_command_proof_binds_body_scope_operator_and_rejects_replay():
    secret = b"r" * 32
    payload = command_payload()
    proof = sign_command_transport_proof(
        secret,
        payload,
        timestamp=100,
        nonce="nonce-1234567890",
    )
    signed = {**payload, "adapter_command_proof": proof}
    verifier = CommandProofVerifier(secret, now=lambda: 100.0)

    verifier.verify(signed)

    with pytest.raises(TransportAuthenticationError, match="replayed"):
        verifier.verify(signed)

    for key, value in {
        "chat_id": "oc_forged",
        "profile_id": "default",
        "operator": "ou_forged",
        "chat_type": "private",
    }.items():
        changed = {**signed, key: value}
        with pytest.raises(TransportAuthenticationError):
            CommandProofVerifier(secret, now=lambda: 100.0).verify(changed)


def test_command_proof_rejects_stale_timestamp_and_wrong_root():
    secret = b"r" * 32
    payload = command_payload()
    proof = sign_command_transport_proof(
        secret,
        payload,
        timestamp=100,
        nonce="nonce-1234567890",
    )
    signed = {**payload, "adapter_command_proof": proof}

    with pytest.raises(TransportAuthenticationError, match="expired"):
        CommandProofVerifier(secret, now=lambda: 131.0).verify(signed)
    with pytest.raises(TransportAuthenticationError):
        CommandProofVerifier(b"x" * 32, now=lambda: 100.0).verify(signed)


def test_command_proof_nonce_capacity_fails_closed_until_entries_expire():
    secret = b"r" * 32
    clock = [100.0]
    verifier = CommandProofVerifier(secret, now=lambda: clock[0], max_nonces=2)

    def signed_payload(nonce: str, timestamp: int) -> dict[str, object]:
        payload = command_payload()
        return {
            **payload,
            "adapter_command_proof": sign_command_transport_proof(
                secret,
                payload,
                timestamp=timestamp,
                nonce=nonce,
            ),
        }

    first = signed_payload("nonce-0000000001", 100)
    verifier.verify(first)
    verifier.verify(signed_payload("nonce-0000000002", 100))

    with pytest.raises(TransportAuthenticationError, match="overloaded"):
        verifier.verify(signed_payload("nonce-0000000003", 100))
    with pytest.raises(TransportAuthenticationError, match="replayed"):
        verifier.verify(first)

    clock[0] = 131.0
    verifier.verify(signed_payload("nonce-0000000004", 131))


def test_operation_transport_secret_is_deterministic_and_scoped():
    root = b"r" * 32

    first = derive_operation_transport_secret(root, "operation-1")

    assert first == derive_operation_transport_secret(root, "operation-1")
    assert first != derive_operation_transport_secret(root, "operation-2")
    assert len(first) == 32
