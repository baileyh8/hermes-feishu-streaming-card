from __future__ import annotations

import hashlib
import hmac
from ipaddress import ip_address
import secrets
import threading
import time
from collections.abc import Mapping
from typing import Callable


EVENT_TIMESTAMP_HEADER = "X-HFC-Event-Timestamp"
EVENT_NONCE_HEADER = "X-HFC-Event-Nonce"
EVENT_SIGNATURE_HEADER = "X-HFC-Event-Signature"

_ROOT_SECRET_BYTES = 32
_PROOF_MAX_AGE_SECONDS = 30
_MAX_NONCES = 512


class EventAuthenticationError(ValueError):
    pass


def sign_event_request(
    secret: bytes,
    body: bytes,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    _validate_secret(secret)
    if not isinstance(body, bytes):
        raise ValueError("event request body must be bytes")
    signed_at = int(time.time()) if timestamp is None else timestamp
    request_nonce = secrets.token_urlsafe(18) if nonce is None else nonce
    if (
        isinstance(signed_at, bool)
        or not isinstance(signed_at, int)
        or not isinstance(request_nonce, str)
        or not 16 <= len(request_nonce) <= 128
    ):
        raise ValueError("event proof metadata is invalid")
    signature = hmac.new(
        secret,
        _event_signing_input(signed_at, request_nonce, _body_hash(body)),
        hashlib.sha256,
    ).hexdigest()
    return {
        EVENT_TIMESTAMP_HEADER: str(signed_at),
        EVENT_NONCE_HEADER: request_nonce,
        EVENT_SIGNATURE_HEADER: signature,
    }


class EventProofVerifier:
    def __init__(
        self,
        secret: bytes,
        *,
        now: Callable[[], float] = time.time,
        max_nonces: int = _MAX_NONCES,
    ):
        _validate_secret(secret)
        if max_nonces < 1:
            raise ValueError("max_nonces must be positive")
        self._secret = secret
        self._now = now
        self._max_nonces = max_nonces
        self._nonces: dict[str, float] = {}
        self._lock = threading.Lock()

    def verify(self, headers: Mapping[str, str], body: bytes) -> None:
        if not isinstance(body, bytes):
            raise EventAuthenticationError("invalid event proof")
        timestamp_text = _header_value(headers, EVENT_TIMESTAMP_HEADER)
        nonce = _header_value(headers, EVENT_NONCE_HEADER)
        signature = _header_value(headers, EVENT_SIGNATURE_HEADER)
        try:
            timestamp = int(timestamp_text) if timestamp_text is not None else None
        except (TypeError, ValueError):
            timestamp = None
        if (
            timestamp is None
            or isinstance(timestamp, bool)
            or not isinstance(nonce, str)
            or not 16 <= len(nonce) <= 128
            or not isinstance(signature, str)
            or len(signature) != 64
        ):
            raise EventAuthenticationError("invalid event proof")

        now = self._now()
        if abs(now - timestamp) > _PROOF_MAX_AGE_SECONDS:
            raise EventAuthenticationError("event proof expired")
        expected = hmac.new(
            self._secret,
            _event_signing_input(timestamp, nonce, _body_hash(body)),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise EventAuthenticationError("invalid event proof")

        with self._lock:
            self._prune_nonces_locked(now)
            if nonce in self._nonces:
                raise EventAuthenticationError("event proof replayed")
            if len(self._nonces) >= self._max_nonces:
                raise EventAuthenticationError("event proof verifier overloaded")
            self._nonces[nonce] = timestamp + _PROOF_MAX_AGE_SECONDS

    def _prune_nonces_locked(self, now: float) -> None:
        for nonce, expires_at in list(self._nonces.items()):
            if expires_at < now:
                self._nonces.pop(nonce, None)


def is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_secret(secret: bytes) -> None:
    if not isinstance(secret, bytes) or len(secret) != _ROOT_SECRET_BYTES:
        raise ValueError("event transport root is invalid")


def _body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _event_signing_input(timestamp: int, nonce: str, body_hash: str) -> bytes:
    return f"hfc-event-v1\0{timestamp}\0{nonce}\0{body_hash}".encode("utf-8")


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is not None:
        return value
    normalized = name.lower()
    for key, candidate in headers.items():
        if str(key).lower() == normalized:
            return candidate
    return None
