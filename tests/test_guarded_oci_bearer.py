# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real Distribution JWT verification; controlled issuer and hostile endpoints."""

import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import http.server
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import ssl
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
import jwt
import pytest
import requests


SPEC = importlib.util.spec_from_file_location(
    "bearer_tls", Path(__file__).with_name("test_guarded_oci_tls.py")
)
TLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TLS)
certificates = TLS.certificates
BOUNDARY, TOOLS = TLS.BOUNDARY, TLS.TOOLS
TRANSPORT = BOUNDARY.TRANSPORT
TOKEN = TRANSPORT.BEARER
REPOSITORY = "private/rehearsal/image"
SERVICE = "pub01-registry"
ISSUER = "pub01-fixture-issuer"


@pytest.fixture(scope="module")
def signing_certificates(certificates):
    openssl = shutil.which("openssl")
    assert openssl
    TOOLS.command(
        [
            openssl,
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=PUB01 Token Signer",
            "-keyout",
            str(certificates / "issuer.key"),
            "-out",
            str(certificates / "issuer.csr"),
        ]
    )
    extensions = certificates / "issuer.extensions"
    extensions.write_text(
        "keyUsage=critical,digitalSignature\nbasicConstraints=critical,CA:FALSE\n"
    )
    TOOLS.command(
        [
            openssl,
            "x509",
            "-req",
            "-in",
            str(certificates / "issuer.csr"),
            "-CA",
            str(certificates / "ca.pem"),
            "-CAkey",
            str(certificates / "ca.key"),
            "-CAcreateserial",
            "-days",
            "1",
            "-extfile",
            str(extensions),
            "-out",
            str(certificates / "issuer.pem"),
        ]
    )
    (certificates / "issuer.key").chmod(0o600)
    return certificates


@contextmanager
def issuer(certificates, *, mode="valid", document=None, redirect=None):
    state = {"calls": [], "tokens": []}
    expected_auth = "Basic " + base64.b64encode(b"fixture-user:fixture-value").decode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            authenticated = self.headers.get("Authorization") == expected_auth
            state["calls"].append(
                {
                    "path": parsed.path,
                    "query": query,
                    "authenticated": authenticated,
                    "accept_encoding": self.headers.get("Accept-Encoding"),
                }
            )
            if redirect:
                self.send_response(307)
                self.send_header("Location", redirect)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not authenticated or parsed.path != "/token":
                status, body = 401, b'{"error":"private-issuer-error"}'
            else:
                now = datetime.now(timezone.utc)
                claim = {
                    "iss": ISSUER,
                    "aud": SERVICE,
                    "sub": "fixture-user",
                    "iat": int(now.timestamp()),
                    "nbf": int(now.timestamp()) - 1,
                    "exp": int(now.timestamp()) + 300,
                    "jti": uuid.uuid4().hex,
                    "access": [
                        {
                            "type": "repository",
                            "name": REPOSITORY,
                            "actions": ["pull", "push"],
                        }
                    ],
                }
                if mode == "wrong-service":
                    claim["aud"] = "different-registry"
                elif mode == "wrong-issuer":
                    claim["iss"] = "different-issuer"
                elif mode == "wrong-repository":
                    claim["access"][0]["name"] = "private/other/image"
                elif mode == "pull-only":
                    claim["access"][0]["actions"] = ["pull"]
                chain = [
                    base64.b64encode(
                        x509.load_pem_x509_certificate(
                            (certificates / name).read_bytes()
                        ).public_bytes(Encoding.DER)
                    ).decode()
                    for name in ("issuer.pem", "ca.pem")
                ]
                key = certificates / (
                    "trusted.key" if mode == "wrong-signature" else "issuer.key"
                )
                token = jwt.encode(
                    claim, key.read_bytes(), algorithm="RS256", headers={"x5c": chain}
                )
                state["tokens"].append(token)
                response = {
                    "token": token,
                    "expires_in": 300,
                    "issued_at": now.isoformat(),
                }
                if document is not None:
                    response = document(now)
                body = (
                    response
                    if isinstance(response, bytes)
                    else json.dumps(response).encode()
                )
                status = 200
            if mode == "encoded":
                body = gzip.compress(body)
            self.send_response(status)
            if mode == "encoded":
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Type", "application/json")
            if mode != "streamed":
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificates / "trusted.pem", certificates / "trusted.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}/token", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def policy(endpoint):
    return {
        "endpoint": endpoint,
        "service": SERVICE,
        "repository": REPOSITORY,
        "actions": ["pull", "push"],
    }


@contextmanager
def native_registry(certificates, endpoint, tmp_path):
    docker = shutil.which("docker")
    assert docker
    host = TOOLS.command(
        [
            docker,
            "context",
            "inspect",
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ]
    ).stdout.strip()
    assert host.startswith("unix://")
    config = tmp_path / "docker"
    config.mkdir()
    client = [docker, "--host", host, "--config", str(config)]
    TOOLS.command(client + ["image", "inspect", TOOLS.REGISTRY_IMAGE])
    name = "pub01-bearer-" + uuid.uuid4().hex
    created = False
    try:
        TOOLS.command(
            client
            + [
                "run",
                "-d",
                "--pull=never",
                "--name",
                name,
                "--publish",
                "127.0.0.1::5000",
                "--tmpfs",
                "/var/lib/registry:rw,size=16777216",
                "--mount",
                f"type=bind,src={certificates},dst=/tls,readonly",
                "-e",
                "REGISTRY_HTTP_TLS_CERTIFICATE=/tls/trusted.pem",
                "-e",
                "REGISTRY_HTTP_TLS_KEY=/tls/trusted.key",
                "-e",
                "REGISTRY_HTTP_RELATIVEURLS=true",
                "-e",
                "REGISTRY_AUTH=token",
                "-e",
                "REGISTRY_AUTH_TOKEN_REALM=" + endpoint,
                "-e",
                "REGISTRY_AUTH_TOKEN_SERVICE=" + SERVICE,
                "-e",
                "REGISTRY_AUTH_TOKEN_ISSUER=" + ISSUER,
                "-e",
                "REGISTRY_AUTH_TOKEN_ROOTCERTBUNDLE=/tls/ca.pem",
                "-e",
                'REGISTRY_AUTH_TOKEN_SIGNINGALGORITHMS=["RS256"]',
                "-e",
                "REGISTRY_STORAGE_REDIRECT_DISABLE=true",
                "-e",
                "OTEL_TRACES_EXPORTER=none",
                TOOLS.REGISTRY_IMAGE,
            ]
        )
        created = True
        bindings = json.loads(
            TOOLS.command(
                client
                + ["inspect", name, "--format", "{{json .NetworkSettings.Ports}}"]
            ).stdout
        )["5000/tcp"]
        assert len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1"
        origin = "https://127.0.0.1:" + bindings[0]["HostPort"]
        with requests.Session() as session:
            session.trust_env = False
            for _ in range(30):
                try:
                    with session.get(
                        origin + "/v2/",
                        verify=str(certificates / "ca.pem"),
                        timeout=2,
                        allow_redirects=False,
                    ) as response:
                        if response.status_code == 401:
                            break
                except requests.ConnectionError:
                    pass
                time.sleep(0.2)
            else:
                pytest.fail("native Bearer/TLS registry did not become ready")
        yield origin
    finally:
        if created:
            TOOLS.command(client + ["rm", "-f", name])
            assert (
                TOOLS.command(client + ["inspect", name], check=False).returncode != 0
            )


def token_client(endpoint, certificates, *, deadline=None):
    return TOKEN.Token(
        policy(endpoint),
        REPOSITORY,
        ("fixture-user", "fixture-value"),
        ca_file=certificates / "ca.pem",
        deadline=deadline or datetime.now(timezone.utc) + timedelta(minutes=5),
    )


def test_real_registry_accepts_signed_jwt_and_identical_retry(
    signing_certificates, tmp_path, monkeypatch
):
    certificates = signing_certificates
    monkeypatch.setenv("NETRC", "/unreadable/operator-netrc")
    monkeypatch.setenv("HTTPS_PROXY", "http://external.invalid:9")
    with issuer(certificates) as (endpoint, state):
        with native_registry(certificates, endpoint, tmp_path) as origin:
            kwargs = {
                "ca_file": certificates / "ca.pem",
                "bearer_policy": policy(endpoint),
                "token_ca_file": certificates / "ca.pem",
            }
            first = BOUNDARY.publish(tmp_path, origin, **kwargs)
            second = BOUNDARY.publish(tmp_path, origin, **kwargs)
    assert first["state"] == "uploaded-and-read-back" and second["identical_retry"]
    assert first["transport"] == "guarded-bearer-oci"
    assert first["transport_sha256"] == second["transport_sha256"]
    assert first["authentication"] == {"mode": "bearer", "token_requests": 1}
    assert first["http_requests"] > 1
    assert all(item["accept_encoding"] == "identity" for item in state["calls"])
    assert len(state["calls"]) == 2 and all(
        item["authenticated"] for item in state["calls"]
    )
    assert all(
        item["query"]
        == {"service": [SERVICE], "scope": ["repository:" + REPOSITORY + ":pull,push"]}
        for item in state["calls"]
    )
    assert all(token not in json.dumps(first) for token in state["tokens"])


@pytest.mark.parametrize(
    "mode",
    [
        "wrong-service",
        "wrong-issuer",
        "wrong-repository",
        "pull-only",
        "wrong-signature",
    ],
)
def test_native_registry_enforces_jwt_crypto_audience_and_repository_actions(
    signing_certificates, tmp_path, mode
):
    certificates = signing_certificates
    with issuer(certificates, mode=mode) as (endpoint, state):
        with native_registry(certificates, endpoint, tmp_path) as origin:
            with pytest.raises(
                TRANSPORT.TransportError, match="authentication challenge"
            ) as error:
                BOUNDARY.publish(
                    tmp_path,
                    origin,
                    ca_file=certificates / "ca.pem",
                    bearer_policy=policy(endpoint),
                    token_ca_file=certificates / "ca.pem",
                )
    assert len(state["calls"]) == 1
    assert all(token not in str(error.value) for token in state["tokens"])


def test_token_redirect_and_untrusted_challenge_never_choose_authority(
    signing_certificates,
):
    certificates = signing_certificates
    with BOUNDARY.service(200) as (target, target_calls):
        with issuer(certificates, redirect=target + "/escape") as (endpoint, _state):
            client = token_client(endpoint, certificates)
            try:
                with pytest.raises(TOKEN.BearerError, match="redirect rejected"):
                    client.authorization()
            finally:
                client.close()
        assert target_calls == []
        with issuer(certificates) as (endpoint, state):
            with TLS.tls_service(
                certificates,
                challenge=f'Bearer realm="{target}/escape",service="untrusted",scope="repository:other:delete"',
            ) as (origin, calls):
                registry = TRANSPORT.Registry(
                    origin,
                    REPOSITORY,
                    ("fixture-user", "fixture-value"),
                    ca_file=certificates / "ca.pem",
                    bearer_policy=policy(endpoint),
                    token_ca_file=certificates / "ca.pem",
                    deadline=datetime.now(timezone.utc) + timedelta(minutes=5),
                )
                try:
                    with pytest.raises(TRANSPORT.TransportError):
                        registry.request(
                            "GET", registry.prefix + "manifests/fixture", {200}
                        )
                finally:
                    registry.close()
            assert len(state["calls"]) == 1 and len(calls) == 1
            assert calls[0].startswith("Bearer ")
        assert target_calls == []


def test_deadline_clamps_token_lifetime_and_never_refetches(signing_certificates):
    certificates = signing_certificates
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    with issuer(certificates) as (endpoint, state):
        client = token_client(endpoint, certificates, deadline=deadline)
        try:
            client.authorization()
            assert client.expires_at == deadline
            client.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            with pytest.raises(TOKEN.BearerError, match="no automatic refresh"):
                client.authorization()
            assert len(state["calls"]) == 1
        finally:
            client.close()


def test_expired_token_blocks_post_before_network_and_never_refetches(
    signing_certificates,
):
    certificates = signing_certificates
    with issuer(certificates) as (endpoint, state):
        with TLS.tls_service(certificates) as (origin, calls):
            registry = TRANSPORT.Registry(
                origin,
                REPOSITORY,
                ("fixture-user", "fixture-value"),
                ca_file=certificates / "ca.pem",
                bearer_policy=policy(endpoint),
                token_ca_file=certificates / "ca.pem",
                deadline=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            try:
                with registry.request(
                    "GET", registry.prefix + "manifests/fixture", {404}
                ):
                    pass
                registry.bearer.expires_at = datetime.now(timezone.utc) - timedelta(
                    seconds=1
                )
                with pytest.raises(
                    TRANSPORT.TransportError, match="no automatic refresh"
                ):
                    registry.request("POST", registry.prefix + "blobs/uploads/", {202})
            finally:
                registry.close()
        assert len(calls) == len(state["calls"]) == 1


def test_token_endpoint_ca_is_independent_and_credentials_do_not_escape_tls(
    signing_certificates,
):
    with issuer(signing_certificates) as (endpoint, state):
        client = TOKEN.Token(
            policy(endpoint),
            REPOSITORY,
            ("fixture-user", "fixture-value"),
            deadline=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        try:
            with pytest.raises(TOKEN.BearerError, match="token request failed"):
                client.authorization()
        finally:
            client.close()
    assert state["calls"] == []


def test_wrong_token_endpoint_credentials_fail_without_registry_attempt(
    signing_certificates,
):
    with issuer(signing_certificates) as (endpoint, state):
        client = TOKEN.Token(
            policy(endpoint),
            REPOSITORY,
            ("fixture-user", "wrong-fixture-value"),
            ca_file=signing_certificates / "ca.pem",
            deadline=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        try:
            with pytest.raises(TOKEN.BearerError) as error:
                client.authorization()
            assert "wrong-fixture-value" not in str(error.value)
            assert "private-issuer-error" not in str(error.value)
        finally:
            client.close()
    assert len(state["calls"]) == 1 and not state["calls"][0]["authenticated"]


@pytest.mark.parametrize(
    "document",
    [
        lambda now: {
            "token": "synthetic-secret-token",
            "expires_in": True,
            "issued_at": now.isoformat(),
        },
        lambda now: {
            "token": "synthetic-secret-token",
            "expires_in": 3601,
            "issued_at": now.isoformat(),
        },
        lambda now: {
            "token": "synthetic-secret-token",
            "expires_in": 60,
            "issued_at": (now - timedelta(hours=1)).isoformat(),
        },
        lambda now: {
            "token": "synthetic-secret-token",
            "expires_in": 60,
            "issued_at": (now + timedelta(hours=1)).isoformat(),
        },
        lambda now: {
            "token": "synthetic-secret-token",
            "access_token": "different-secret",
            "expires_in": 60,
            "issued_at": now.isoformat(),
        },
        lambda now: {
            "token": "x" * (TOKEN.MAX_TOKEN_BYTES + 1),
            "expires_in": 60,
            "issued_at": now.isoformat(),
        },
        lambda now: b'{"token":"private-token","token":"different-token"}',
    ],
)
def test_malformed_token_responses_fail_without_secret_logs(
    signing_certificates, document
):
    with issuer(signing_certificates, document=document) as (endpoint, state):
        client = token_client(endpoint, signing_certificates)
        try:
            with pytest.raises(TOKEN.BearerError) as error:
                client.authorization()
            assert "synthetic-secret-token" not in str(error.value)
            assert "private-token" not in str(error.value)
            with pytest.raises(TOKEN.BearerError, match="already attempted"):
                client.authorization()
            assert len(state["calls"]) == 1 and client.requests == 1
        finally:
            client.close()


@pytest.mark.parametrize("mode", ["valid", "streamed"])
def test_token_response_declared_and_streamed_bounds(signing_certificates, mode):
    with issuer(
        signing_certificates,
        mode=mode,
        document=lambda _now: b"x" * (TOKEN.MAX_RESPONSE_BYTES + 1),
    ) as (endpoint, _state):
        client = token_client(endpoint, signing_certificates)
        try:
            with pytest.raises(TOKEN.BearerError, match="size"):
                client.authorization()
        finally:
            client.close()


def test_encoded_token_response_rejected_before_decompression(signing_certificates):
    with issuer(signing_certificates, mode="encoded") as (endpoint, state):
        client = token_client(endpoint, signing_certificates)
        try:
            with pytest.raises(TOKEN.BearerError, match="encoding rejected"):
                client.authorization()
        finally:
            client.close()
    assert state["calls"][0]["accept_encoding"] == "identity"


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", "http://127.0.0.1:1234/token"),
        ("endpoint", "https://host.invalid/token?escape=x"),
        ("endpoint", "https://host.invalid/a/../token"),
        ("service", "other service"),
        ("repository", "private/other/image"),
        ("actions", ["pull", "push", "delete"]),
    ],
)
def test_independent_policy_rejects_unapproved_scope_or_endpoint(field, value):
    selected = policy("https://host.invalid/token")
    selected[field] = value
    with pytest.raises(TOKEN.BearerError):
        TOKEN.validate_policy(selected, REPOSITORY)
