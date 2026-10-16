# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verified TLS against a real registry; malicious responses are local fixtures."""

from contextlib import contextmanager
import http.server
import importlib.util
import json
from pathlib import Path
import shutil
import ssl
import threading
import time
import uuid

import pytest
import requests


SPEC = importlib.util.spec_from_file_location(
    "tls_boundary", Path(__file__).with_name("test_guarded_oci_transport.py")
)
BOUNDARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOUNDARY)
TRANSPORT = BOUNDARY.TRANSPORT
TOOLS = BOUNDARY.FIXTURE.REHEARSAL


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    """Generate disposable test keys; nothing is installed in an operator store."""
    root = tmp_path_factory.mktemp("fixture-tls")
    openssl = shutil.which("openssl")
    assert openssl
    TOOLS.command(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=PUB01 Synthetic CA",
            "-keyout",
            str(root / "ca.key"),
            "-out",
            str(root / "ca.pem"),
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        ]
    )
    for name, san in (("trusted", "IP:127.0.0.1"), ("wrong-host", "DNS:wrong.invalid")):
        TOOLS.command(
            [
                openssl,
                "req",
                "-new",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                "/CN=synthetic-registry",
                "-keyout",
                str(root / f"{name}.key"),
                "-out",
                str(root / f"{name}.csr"),
            ]
        )
        extensions = root / f"{name}.extensions"
        extensions.write_text(
            f"subjectAltName={san}\nextendedKeyUsage=serverAuth\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "basicConstraints=critical,CA:FALSE\n"
        )
        TOOLS.command(
            [
                openssl,
                "x509",
                "-req",
                "-in",
                str(root / f"{name}.csr"),
                "-CA",
                str(root / "ca.pem"),
                "-CAkey",
                str(root / "ca.key"),
                "-CAcreateserial",
                "-days",
                "1",
                "-extfile",
                str(extensions),
                "-out",
                str(root / f"{name}.pem"),
            ]
        )
    for key in root.glob("*.key"):
        key.chmod(0o600)
    return root


@pytest.fixture(scope="module")
def registry(certificates, tmp_path_factory):
    """Native distribution TLS/auth, loopback binding, isolated tmpfs storage."""
    root = tmp_path_factory.mktemp("tls-registry")
    docker, htpasswd = shutil.which("docker"), shutil.which("htpasswd")
    assert docker and htpasswd
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
    config = root / "docker"
    config.mkdir()
    client = [docker, "--host", host, "--config", str(config)]
    TOOLS.command(client + ["image", "inspect", TOOLS.REGISTRY_IMAGE])
    auth = root / "htpasswd"
    auth.write_text(TOOLS.command([htpasswd, "-bnB", *TOOLS.AUTH_PAIR]).stdout)
    auth.chmod(0o600)
    name = "pub01-tls-" + uuid.uuid4().hex
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
                f"type=bind,src={auth},dst=/auth/htpasswd,readonly",
                "--mount",
                f"type=bind,src={certificates},dst=/tls,readonly",
                "-e",
                "REGISTRY_HTTP_TLS_CERTIFICATE=/tls/trusted.pem",
                "-e",
                "REGISTRY_HTTP_TLS_KEY=/tls/trusted.key",
                "-e",
                "REGISTRY_HTTP_RELATIVEURLS=true",
                "-e",
                "REGISTRY_AUTH=htpasswd",
                "-e",
                "REGISTRY_AUTH_HTPASSWD_REALM=LocalFixture",
                "-e",
                "REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd",
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
                + [
                    "inspect",
                    name,
                    "--format",
                    "{{json .NetworkSettings.Ports}}",
                ]
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
                pytest.fail("native TLS registry did not become ready")
        yield origin
    finally:
        if created:
            TOOLS.command(client + ["rm", "-f", name])
            result = TOOLS.command(client + ["inspect", name], check=False)
            assert result.returncode != 0


@contextmanager
def tls_service(certificates, *, certificate="trusted", redirect=None):
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            calls.append(self.headers.get("Authorization"))
            self.send_response(307 if redirect else 404)
            if redirect:
                self.send_header("Location", redirect)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_HEAD

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(
        certificates / f"{certificate}.pem", certificates / f"{certificate}.key"
    )
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_real_registry_explicit_ca_publish_readback_and_identical_retry(
    tmp_path, certificates, registry
):
    ca = certificates / "ca.pem"
    with pytest.raises(TRANSPORT.TransportError, match="registry request failed"):
        BOUNDARY.publish(tmp_path, registry)  # The fixture CA is not ambient trust.
    with pytest.raises(TRANSPORT.TransportError, match="HTTP status 401"):
        BOUNDARY.publish(
            tmp_path,
            registry,
            ca_file=ca,
            credentials=("fixture-user", "invalid-fixture-value"),
        )
    first = BOUNDARY.publish(tmp_path, registry, ca_file=ca)
    second = BOUNDARY.publish(tmp_path, registry, ca_file=ca)
    assert first["state"] == "uploaded-and-read-back"
    assert first["transport_sha256"] == second["transport_sha256"]
    assert first["runnable_manifest_sha256"] == second["runnable_manifest_sha256"]
    assert not first["identical_retry"] and second["identical_retry"]


@pytest.mark.parametrize("failure", ["untrusted", "wrong-host", "invalid-ca"])
def test_failed_tls_never_delivers_basic_credentials(tmp_path, certificates, failure):
    invalid = tmp_path / "invalid-ca.pem"
    invalid.write_text("not a certificate\n")
    ca = (
        None
        if failure == "untrusted"
        else invalid
        if failure == "invalid-ca"
        else certificates / "ca.pem"
    )
    certificate = "wrong-host" if failure == "wrong-host" else "trusted"
    with tls_service(certificates, certificate=certificate) as (origin, calls):
        with pytest.raises(
            TRANSPORT.TransportError, match="registry request failed"
        ) as error:
            BOUNDARY.publish(tmp_path, origin, ca_file=ca)
    assert calls == []
    assert "fixture-value" not in str(error.value)
    assert str(invalid) not in str(error.value)


def test_verified_https_redirect_never_reaches_other_origin(tmp_path, certificates):
    with BOUNDARY.service(200) as (target, target_calls):
        with tls_service(certificates, redirect=target + "/escaped") as (origin, calls):
            with pytest.raises(TRANSPORT.TransportError, match="redirect rejected"):
                BOUNDARY.publish(tmp_path, origin, ca_file=certificates / "ca.pem")
    assert (
        len(calls) == 1
    )  # Synthetic credentials reached only the authenticated TLS peer.
    assert target_calls == []
