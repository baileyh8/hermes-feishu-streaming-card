from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import threading
import time
from typing import Any, Callable

from .process import state_dir


TRANSPORT_ROOT_SECRET_NAME = "operations.transport.key"
_ROOT_SECRET_BYTES = 32
_PROOF_MAX_AGE_SECONDS = 30
_MAX_NONCES = 512


class TransportAuthenticationError(ValueError):
    pass


def ensure_transport_root_secret(directory: str | Path | None = None) -> bytes:
    root = Path(directory).expanduser() if directory is not None else state_dir()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not _valid_transport_root(root):
        raise OSError("operations transport root is invalid")
    path = root / TRANSPORT_ROOT_SECRET_NAME
    if _path_lstat(path) is not None:
        secret = _read_secret_bytes(root, path)
        if secret is None:
            raise OSError("operations transport root is invalid")
        return secret

    secret = secrets.token_bytes(_ROOT_SECRET_BYTES)
    temp_path = root / f".{TRANSPORT_ROOT_SECRET_NAME}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(
        temp_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(secret)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            pass
    finally:
        temp_path.unlink(missing_ok=True)

    persisted = _read_secret_bytes(root, path)
    if persisted is None:
        raise OSError("operations transport root could not be created")
    return persisted


def read_transport_root_secret(directory: str | Path | None = None) -> bytes | None:
    root = Path(directory).expanduser() if directory is not None else state_dir()
    path = root / TRANSPORT_ROOT_SECRET_NAME
    return _read_secret_bytes(root, path)


def _read_secret_bytes(root: Path, path: Path) -> bytes | None:
    if not _valid_transport_root(root):
        return None
    path_stat = _path_lstat(path)
    if path_stat is None or not stat.S_ISREG(path_stat.st_mode):
        return None
    if not _is_windows() and stat.S_IMODE(path_stat.st_mode) != 0o600:
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        opened_stat = os.fstat(descriptor)
        current_stat = _path_lstat(path)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_dev != path_stat.st_dev
            or opened_stat.st_ino != path_stat.st_ino
            or current_stat is None
            or current_stat.st_dev != opened_stat.st_dev
            or current_stat.st_ino != opened_stat.st_ino
            or not _valid_transport_root(root)
        ):
            return None
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            secret = handle.read()
    except OSError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return secret if len(secret) == _ROOT_SECRET_BYTES else None


def _valid_transport_root(root: Path) -> bool:
    root_stat = _path_lstat(root)
    if root_stat is None or not stat.S_ISDIR(root_stat.st_mode):
        return False
    return _is_windows() or stat.S_IMODE(root_stat.st_mode) == 0o700


def _path_lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except OSError:
        return None


def _is_windows() -> bool:
    return os.name == "nt"


def sign_command_transport_proof(
    secret: bytes,
    payload: dict[str, Any],
    *,
    timestamp: int,
    nonce: str,
) -> dict[str, object]:
    if not isinstance(secret, bytes) or len(secret) != _ROOT_SECRET_BYTES:
        raise ValueError("command transport root is invalid")
    body_hash = _command_body_hash(payload)
    signature = hmac.new(
        secret,
        _command_signing_input(timestamp, nonce, body_hash),
        hashlib.sha256,
    ).hexdigest()
    return {
        "timestamp": timestamp,
        "nonce": nonce,
        "body_hash": body_hash,
        "signature": signature,
    }


def derive_operation_transport_secret(root_secret: bytes, operation_id: str) -> bytes:
    if not isinstance(root_secret, bytes) or len(root_secret) != _ROOT_SECRET_BYTES:
        raise ValueError("command transport root is invalid")
    operation_id = str(operation_id or "").strip()
    if not operation_id:
        raise ValueError("operation id is required")
    return hmac.new(
        root_secret,
        f"hfc-operation-v1\0{operation_id}".encode("utf-8"),
        hashlib.sha256,
    ).digest()


class CommandProofVerifier:
    def __init__(
        self,
        secret: bytes,
        *,
        now: Callable[[], float] = time.time,
        max_nonces: int = _MAX_NONCES,
    ):
        if not isinstance(secret, bytes) or len(secret) != _ROOT_SECRET_BYTES:
            raise ValueError("command transport root is invalid")
        if max_nonces < 1:
            raise ValueError("max_nonces must be positive")
        self._secret = secret
        self._now = now
        self._max_nonces = max_nonces
        self._nonces: dict[str, float] = {}
        self._lock = threading.Lock()

    def verify(self, payload: dict[str, Any]) -> None:
        proof = payload.get("adapter_command_proof")
        if not isinstance(proof, dict):
            raise TransportAuthenticationError("invalid command proof")
        timestamp = proof.get("timestamp")
        nonce = proof.get("nonce")
        body_hash = proof.get("body_hash")
        signature = proof.get("signature")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or not isinstance(nonce, str)
            or not 16 <= len(nonce) <= 128
            or not isinstance(body_hash, str)
            or len(body_hash) != 64
            or not isinstance(signature, str)
            or len(signature) != 64
        ):
            raise TransportAuthenticationError("invalid command proof")
        now = self._now()
        if abs(now - timestamp) > _PROOF_MAX_AGE_SECONDS:
            raise TransportAuthenticationError("command proof expired")
        expected_body_hash = _command_body_hash(payload)
        if not hmac.compare_digest(body_hash, expected_body_hash):
            raise TransportAuthenticationError("invalid command proof")
        expected_signature = hmac.new(
            self._secret,
            _command_signing_input(timestamp, nonce, expected_body_hash),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            raise TransportAuthenticationError("invalid command proof")

        with self._lock:
            self._prune_nonces_locked(now)
            if nonce in self._nonces:
                raise TransportAuthenticationError("command proof replayed")
            if len(self._nonces) >= self._max_nonces:
                raise TransportAuthenticationError("command proof verifier overloaded")
            self._nonces[nonce] = timestamp + _PROOF_MAX_AGE_SECONDS

    def _prune_nonces_locked(self, now: float) -> None:
        for nonce, expires_at in list(self._nonces.items()):
            if expires_at < now:
                self._nonces.pop(nonce, None)


def _command_body_hash(payload: dict[str, Any]) -> str:
    canonical_payload = {
        key: value
        for key, value in payload.items()
        if key != "adapter_command_proof"
    }
    encoded = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _command_signing_input(timestamp: int, nonce: str, body_hash: str) -> bytes:
    return f"hfc-command-v1\0{timestamp}\0{nonce}\0{body_hash}".encode("utf-8")
