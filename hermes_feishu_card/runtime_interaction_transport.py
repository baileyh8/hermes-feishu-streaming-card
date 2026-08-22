from __future__ import annotations

from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import re
import threading
import time

from .event_auth import (
    RUNTIME_INTERACTION_NONCE_HEADER,
    RUNTIME_INTERACTION_SIGNATURE_HEADER,
    RUNTIME_INTERACTION_TIMESTAMP_HEADER,
    RuntimeInteractionAuthenticationError,
    RuntimeInteractionProofVerifier,
)

RUNTIME_INTERACTION_PATH = "/runtime/interactions/resolve"
MAX_RUNTIME_INTERACTION_BODY_BYTES = 8192
_MAX_JSON_DEPTH = 8
_MAX_JSON_ITEMS = 64
_MAX_JSON_TEXT_BYTES = 4096
_CONTENT_LENGTH_RE = re.compile(r"0|[1-9][0-9]*")
_REJECTED = {"ok": False, "status": "rejected"}
_RESOLVED = {"ok": True, "status": "resolved"}


class _RuntimeInteractionServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True
    block_on_close = False

    def process_request(self, request, client_address) -> None:
        listener = self.listener  # type: ignore[attr-defined]
        try:
            request.settimeout(listener._REQUEST_SOCKET_TIMEOUT_SECONDS)
        except Exception:
            self.shutdown_request(request)
            return None
        if not listener._begin_handler():
            self.shutdown_request(request)
            return None
        thread = threading.Thread(
            target=self.process_request_thread,
            args=(request, client_address),
            name="hfc-runtime-interaction-process_request_thread",
            daemon=True,
        )
        listener._register_request_thread(thread)
        try:
            thread.start()
        except Exception:
            listener._abort_handler_start(thread)
            self.shutdown_request(request)
        return None

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.listener._end_handler()  # type: ignore[attr-defined]


class RuntimeInteractionListener:
    """A literal-loopback, authenticated callback endpoint owned by one runtime."""

    _JOIN_SECONDS = 1.0
    _REQUEST_SOCKET_TIMEOUT_SECONDS = 0.5
    _MAX_ACTIVE_REQUESTS = 32

    def __init__(
        self,
        secret: bytes,
        resolve: Callable[[dict[str, object]], bool],
        *,
        proof_now: Callable[[], float] = time.time,
    ) -> None:
        if type(secret) is not bytes or len(secret) != 32:
            raise ValueError("runtime interaction secret is invalid")
        if not callable(resolve) or not callable(proof_now):
            raise ValueError("runtime interaction callback is invalid")
        self._resolve = resolve
        self._verifier = RuntimeInteractionProofVerifier(secret, now=proof_now)
        self._lock = threading.Lock()
        self._close_complete = threading.Event()
        self._handlers_drained = threading.Condition(self._lock)
        self._active_handlers = 0
        self._request_threads: set[threading.Thread] = set()
        self._accepting = False
        self._closing = False
        self._poisoned = False
        self._server: _RuntimeInteractionServer | None = None
        self._thread: threading.Thread | None = None
        self._resolve_url = ""

    @property
    def resolve_url(self) -> str:
        with self._lock:
            return self._resolve_url

    def start(self) -> None:
        with self._lock:
            if self._accepting:
                return None
            if self._closing or self._poisoned or self._server is not None:
                raise RuntimeError("runtime interaction listener is unavailable")
            server = _RuntimeInteractionServer(
                ("127.0.0.1", 0), _RuntimeInteractionHandler
            )
            server.listener = self  # type: ignore[attr-defined]
            port = server.server_address[1]
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.02},
                name=f"hfc-runtime-interaction-{port}",
                daemon=True,
            )
            self._server = server
            self._thread = thread
            self._resolve_url = f"http://127.0.0.1:{port}{RUNTIME_INTERACTION_PATH}"
            self._accepting = True
            try:
                thread.start()
            except Exception:
                self._accepting = False
                self._server = None
                self._thread = None
                self._resolve_url = ""
                server.server_close()
                raise
        return None

    def accepts(self) -> bool:
        with self._lock:
            return self._accepting and not self._closing and not self._poisoned

    def pause_for_replacement(self) -> bool:
        with self._lock:
            if (
                not self._accepting
                or self._closing
                or self._poisoned
                or self._active_handlers != 0
            ):
                return False
            self._accepting = False
            return True

    def resume_after_failed_replacement(self) -> bool:
        with self._lock:
            if (
                self._server is None
                or self._closing
                or self._poisoned
                or self._active_handlers != 0
            ):
                return False
            self._accepting = True
            return True

    def mark_close_failed(self) -> None:
        """Reject new admission while retaining the authoritative live owner."""
        with self._lock:
            self._accepting = False
            self._poisoned = True
        return None

    def verify_and_resolve(
        self,
        headers: Mapping[str, str],
        path: str,
        body: bytes,
    ) -> tuple[int, dict[str, object]]:
        try:
            self._verifier.verify(headers, path, body)
        except RuntimeInteractionAuthenticationError:
            return 401, dict(_REJECTED)
        payload = _decode_canonical_payload(body)
        if payload is None:
            return 400, dict(_REJECTED)
        try:
            resolved = self._resolve(payload)
        except Exception:
            resolved = False
        if resolved is True:
            return 200, dict(_RESOLVED)
        return 409, dict(_REJECTED)

    def _begin_handler(self) -> bool:
        with self._lock:
            self._request_threads = {
                thread for thread in self._request_threads if thread.is_alive()
            }
            if (
                not self._accepting
                or self._closing
                or self._poisoned
                or self._active_handlers >= self._MAX_ACTIVE_REQUESTS
            ):
                return False
            self._active_handlers += 1
            return True

    def _register_request_thread(self, thread: threading.Thread) -> None:
        with self._lock:
            self._request_threads.add(thread)

    def _abort_handler_start(self, thread: threading.Thread) -> None:
        with self._lock:
            self._request_threads.discard(thread)
            self._active_handlers -= 1
            if self._active_handlers == 0:
                self._handlers_drained.notify_all()

    def _end_handler(self) -> None:
        with self._lock:
            self._active_handlers -= 1
            if self._active_handlers == 0:
                self._handlers_drained.notify_all()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            thread = self._thread
            return {
                "accepting": self._accepting and not self._closing,
                "poisoned": self._poisoned,
                "worker_name": "" if thread is None else thread.name,
            }

    def close(self) -> None:
        wait_for_other = False
        with self._lock:
            if self._closing:
                wait_for_other = True
            elif self._server is None:
                self._accepting = False
                self._close_complete.set()
                return None
            else:
                self._closing = True
                self._accepting = False
                server = self._server
                thread = self._thread
        if wait_for_other:
            self._close_complete.wait(timeout=self._JOIN_SECONDS + 0.25)
            return None
        try:
            server.shutdown()
            server.server_close()
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=self._JOIN_SECONDS)
            with self._lock:
                deadline = time.monotonic() + self._JOIN_SECONDS
                while self._active_handlers and time.monotonic() < deadline:
                    self._handlers_drained.wait(
                        timeout=max(0.0, deadline - time.monotonic())
                    )
                request_threads = tuple(self._request_threads)
            for request_thread in request_threads:
                if request_thread is threading.current_thread():
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                request_thread.join(timeout=remaining)
            with self._lock:
                self._request_threads = {
                    value for value in self._request_threads if value.is_alive()
                }
                alive = bool(thread is not None and thread.is_alive())
                poisoned = (
                    alive
                    or self._active_handlers != 0
                    or bool(self._request_threads)
                )
                self._poisoned = poisoned
                if not poisoned:
                    self._server = None
                    self._thread = None
        finally:
            self._close_complete.set()
        return None


class _RuntimeInteractionHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def _listener(self) -> RuntimeInteractionListener:
        return self.server.listener  # type: ignore[attr-defined]

    def do_POST(self) -> None:
        self._do_exact_post()

    def _do_exact_post(self) -> None:
        if self.path != RUNTIME_INTERACTION_PATH:
            self._respond(404, _REJECTED)
            return
        transfer_encoding = self.headers.get_all("Transfer-Encoding", failobj=[])
        lengths = self.headers.get_all("Content-Length", failobj=[])
        content_types = self.headers.get_all("Content-Type", failobj=[])
        host_values = self.headers.get_all("Host", failobj=[])
        expected_host = f"127.0.0.1:{self.server.server_address[1]}"
        proof_headers = tuple(
            self.headers.get_all(name, failobj=[])
            for name in (
                RUNTIME_INTERACTION_TIMESTAMP_HEADER,
                RUNTIME_INTERACTION_NONCE_HEADER,
                RUNTIME_INTERACTION_SIGNATURE_HEADER,
            )
        )
        if (
            transfer_encoding
            or len(lengths) != 1
            or content_types != ["application/json"]
            or host_values != [expected_host]
            or any(
                len(values) != 1
                or type(values[0]) is not str
                or len(values[0]) > 128
                for values in proof_headers
            )
        ):
            self._respond(400, _REJECTED)
            return
        length_text = lengths[0]
        if (
            type(length_text) is not str
            or _CONTENT_LENGTH_RE.fullmatch(length_text) is None
        ):
            self._respond(400, _REJECTED)
            return
        length = int(length_text)
        if length > MAX_RUNTIME_INTERACTION_BODY_BYTES:
            self._respond(413, _REJECTED)
            return
        try:
            self.connection.settimeout(0.75)
            body = self.rfile.read(length)
        except Exception:
            self._respond(400, _REJECTED)
            return
        if type(body) is not bytes or len(body) != length:
            self._respond(400, _REJECTED)
            return
        status, response = self._listener.verify_and_resolve(
            self.headers, self.path, body
        )
        self._respond(status, response)

    def do_GET(self) -> None:
        self._respond(405, _REJECTED)

    def do_HEAD(self) -> None:
        self.do_GET()

    do_PUT = do_GET
    do_DELETE = do_GET
    do_PATCH = do_GET
    do_OPTIONS = do_GET
    do_CONNECT = do_GET
    do_TRACE = do_GET

    def _respond(self, status: int, payload: Mapping[str, object]) -> None:
        body = json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        self.close_connection = True

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def _decode_canonical_payload(body: bytes) -> dict[str, object] | None:
    if type(body) is not bytes or not body or len(body) > MAX_RUNTIME_INTERACTION_BODY_BYTES:
        return None
    try:
        text = body.decode("utf-8", errors="strict")

        def reject_constant(_value: str) -> object:
            raise ValueError("non-finite JSON")

        def exact_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if type(key) is not str or key in result:
                    raise ValueError("invalid JSON object")
                result[key] = value
            return result

        value = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=exact_object,
        )
        if type(value) is not dict or not _bounded_ordinary_json(value):
            return None
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if canonical != body:
            return None
        return value
    except (UnicodeError, ValueError, TypeError, OverflowError):
        return None


def _bounded_ordinary_json(value: object) -> bool:
    items = 0
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        items += 1
        if items > _MAX_JSON_ITEMS or depth > _MAX_JSON_DEPTH:
            return False
        current_type = type(current)
        if current_type is dict:
            for key, child in current.items():
                if (
                    type(key) is not str
                    or len(key.encode("utf-8")) > _MAX_JSON_TEXT_BYTES
                ):
                    return False
                stack.append((child, depth + 1))
        elif current_type is list:
            for child in current:
                stack.append((child, depth + 1))
        elif current_type is str:
            if len(current.encode("utf-8")) > _MAX_JSON_TEXT_BYTES:
                return False
        elif current is None or current_type in (bool, int):
            continue
        elif current_type is float:
            if not math.isfinite(current):
                return False
        else:
            return False
    return True
