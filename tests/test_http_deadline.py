# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Actual slow peers must not keep synchronous publication requests alive."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import http.server
import importlib.util
from pathlib import Path
import signal
import socket
import ssl
import threading
import time

import pytest

SPEC = importlib.util.spec_from_file_location(
    "deadline_bearer_tests", Path(__file__).with_name("test_guarded_oci_bearer.py")
)
BEARER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BEARER)
REGISTRY = BEARER.TRANSPORT
TOKEN = BEARER.TOKEN
DEADLINE = REGISTRY.HTTP_DEADLINE
certificates = BEARER.certificates
signing_certificates = BEARER.signing_certificates


@contextmanager
def slow_peer(*, certificate=None, phase="headers", method="GET"):
    state = {"requests": 0, "disconnected": threading.Event(), "authentication": []}
    stop = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def do_PUT(self):
            self.respond()

        def respond(self):
            state["requests"] += 1
            state["authentication"].append(
                self.headers.get("Authorization", "").split(" ")[0]
            )
            assert self.command == method
            if phase == "upload":
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                stop.wait(3)
                return
            try:
                if phase == "headers":
                    content = b"HTTP/1.1 200 OK\r\nX-Slow: " + b"x" * 100
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", "100")
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    content = b"x" * 100
                for byte in content:
                    if stop.wait(0.025):
                        break
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                state["disconnected"].set()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    scheme = "http"
    if certificate:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            certificate / "trusted.pem", certificate / "trusted.key"
        )
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"{scheme}://127.0.0.1:{server.server_port}", state
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def assert_cancelled(started, state):
    assert time.monotonic() - started < 1.5
    assert state["requests"] == 1
    assert state["disconnected"].wait(1)


@pytest.mark.parametrize("phase", ["headers", "body"])
@pytest.mark.parametrize("tls", [False, True])
def test_registry_slow_peer_cancelled_without_retry(
    certificates, monkeypatch, phase, tls
):
    monkeypatch.setattr(REGISTRY, "REQUEST_SECONDS", 0.2)
    with slow_peer(certificate=certificates if tls else None, phase=phase) as (
        origin,
        state,
    ):
        client = REGISTRY.Registry(
            origin,
            BEARER.REPOSITORY,
            ("fixture-user", "fixture-value"),
            ca_file=certificates / "ca.pem" if tls else None,
        )
        try:
            started = time.monotonic()
            with pytest.raises(REGISTRY.TransportError, match="deadline"):
                response = client.request(
                    "GET", client.prefix + "manifests/fixture", {200}
                )
                client.read(response, hashlib.sha256(b"x" * 100).hexdigest(), 100)
            assert_cancelled(started, state)
        finally:
            client.close()


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_token_slow_peer_cancelled_without_refetch(certificates, monkeypatch, phase):
    monkeypatch.setattr(TOKEN, "REQUEST_SECONDS", 0.2)
    with slow_peer(certificate=certificates, phase=phase) as (origin, state):
        client = TOKEN.Token(
            BEARER.policy(origin + "/token"),
            BEARER.REPOSITORY,
            ("fixture-user", "fixture-value"),
            ca_file=certificates / "ca.pem",
            deadline=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        try:
            started = time.monotonic()
            with pytest.raises(TOKEN.BearerError, match="deadline"):
                client.authorization()
            with pytest.raises(TOKEN.BearerError, match="already attempted"):
                client.authorization()
            assert_cancelled(started, state)
        finally:
            client.close()


def test_ambiguous_post_timeout_is_not_replayed(monkeypatch):
    monkeypatch.setattr(REGISTRY, "REQUEST_SECONDS", 0.2)
    with slow_peer(method="POST") as (origin, state):
        client = REGISTRY.Registry(
            origin, BEARER.REPOSITORY, ("fixture-user", "fixture-value")
        )
        try:
            started = time.monotonic()
            with pytest.raises(REGISTRY.TransportError, match="deadline"):
                client.request("POST", client.prefix + "blobs/uploads/", {202})
            assert_cancelled(started, state)
        finally:
            client.close()


@pytest.mark.parametrize("timeout", [False, True])
def test_alarm_handler_and_timer_restored_after_success_and_failure(timeout):
    before = signal.getsignal(signal.SIGALRM)
    assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)
    if timeout:
        with pytest.raises(DEADLINE.DeadlineError, match="expired"):
            with DEADLINE.interrupt_at(DEADLINE.expires_after(0.05)):
                time.sleep(1)
    else:
        with DEADLINE.interrupt_at(DEADLINE.expires_after(1)):
            pass
    assert signal.getsignal(signal.SIGALRM) == before
    assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)


def test_absolute_budget_is_not_reset_between_headers_and_body(monkeypatch):
    monkeypatch.setattr(REGISTRY, "REQUEST_SECONDS", 0.2)
    with slow_peer(phase="body") as (origin, state):
        client = REGISTRY.Registry(
            origin, BEARER.REPOSITORY, ("fixture-user", "fixture-value")
        )
        try:
            response = client.request("GET", client.prefix + "manifests/fixture", {200})
            time.sleep(0.3)
            started = time.monotonic()
            with pytest.raises(REGISTRY.TransportError, match="deadline"):
                client.read(response, "a" * 64, 100)
            assert time.monotonic() - started < 0.1
            assert state["requests"] == 1
        finally:
            client.close()


def test_active_timer_rejected_unchanged_before_network(monkeypatch):
    requests = []
    client = REGISTRY.Registry(
        "http://127.0.0.1:1", BEARER.REPOSITORY, ("fixture", "fixture")
    )
    monkeypatch.setattr(
        client.session, "request", lambda *_a, **_k: requests.append(True)
    )
    signal.setitimer(signal.ITIMER_REAL, 5)
    try:
        with pytest.raises(REGISTRY.TransportError, match="deadline"):
            client.request("GET", client.prefix + "manifests/fixture", {200})
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 4
        assert requests == []
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        client.close()


def test_worker_thread_fails_before_network(monkeypatch):
    calls = []
    client = REGISTRY.Registry(
        "http://127.0.0.1:1", BEARER.REPOSITORY, ("fixture", "fixture")
    )
    monkeypatch.setattr(client.session, "request", lambda *_a, **_k: calls.append(True))
    errors = []

    def run():
        try:
            client.request("GET", client.prefix + "manifests/fixture", {200})
        except REGISTRY.TransportError as error:
            errors.append(str(error))

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=2)
    client.close()
    assert not thread.is_alive() and len(errors) == 1 and calls == []


def test_unsupported_signal_context_fails_before_network(monkeypatch):
    monkeypatch.delattr(DEADLINE.signal, "setitimer")
    with pytest.raises(DEADLINE.DeadlineError, match="POSIX main thread"):
        with DEADLINE.interrupt_at(time.monotonic() + 1):
            pytest.fail("unsupported context entered")


def test_bearer_registry_header_timeout_does_not_refresh_token(
    signing_certificates, monkeypatch
):
    monkeypatch.setattr(REGISTRY, "REQUEST_SECONDS", 0.2)
    with BEARER.issuer(signing_certificates) as (endpoint, issuer_state):
        with slow_peer(certificate=signing_certificates) as (origin, state):
            client = REGISTRY.Registry(
                origin,
                BEARER.REPOSITORY,
                ("fixture-user", "fixture-value"),
                ca_file=signing_certificates / "ca.pem",
                bearer_policy=BEARER.policy(endpoint),
                token_ca_file=signing_certificates / "ca.pem",
                deadline=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
            try:
                started = time.monotonic()
                with pytest.raises(REGISTRY.TransportError, match="deadline"):
                    client.request("GET", client.prefix + "manifests/fixture", {200})
                assert_cancelled(started, state)
                assert state["authentication"] == ["Bearer"]
                assert len(issuer_state["calls"]) == 1
            finally:
                client.close()


def test_stalled_put_upload_is_interrupted_without_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(REGISTRY, "REQUEST_SECONDS", 0.2)
    payload = tmp_path / "bounded-upload.bin"
    payload.write_bytes(b"x" * (8 * 1024 * 1024))
    with slow_peer(method="PUT", phase="upload") as (origin, state):
        client = REGISTRY.Registry(origin, BEARER.REPOSITORY, ("fixture", "fixture"))
        try:
            started = time.monotonic()
            with payload.open("rb") as stream:
                with pytest.raises(REGISTRY.TransportError, match="deadline"):
                    client.request(
                        "PUT",
                        client.prefix + "blobs/uploads/fixture",
                        {201},
                        uploading=True,
                        data=stream,
                        headers={"Content-Length": str(payload.stat().st_size)},
                    )
                assert stream.tell() < payload.stat().st_size
            assert time.monotonic() - started < 1.5
            assert state["requests"] == 1
        finally:
            client.close()


def test_response_without_original_budget_is_rejected(monkeypatch):
    with slow_peer(phase="body") as (origin, state):
        client = REGISTRY.Registry(origin, BEARER.REPOSITORY, ("fixture", "fixture"))
        try:
            response = client.request("GET", client.prefix + "manifests/fixture", {200})
            del response._guarded_http_expires
            with pytest.raises(
                REGISTRY.TransportError, match="original request deadline"
            ):
                client.read(response, "a" * 64, 100)
            assert state["requests"] == 1
        finally:
            client.close()
