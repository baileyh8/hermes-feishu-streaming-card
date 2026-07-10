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


def test_operation_transport_secret_is_deterministic_and_scoped():
    root = b"r" * 32

    first = derive_operation_transport_secret(root, "operation-1")

    assert first == derive_operation_transport_secret(root, "operation-1")
    assert first != derive_operation_transport_secret(root, "operation-2")
    assert len(first) == 32
