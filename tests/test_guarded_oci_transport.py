# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Actual HTTP boundary fixtures; successful OCI protocol uses the real registry."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import http.server
import importlib.util
from pathlib import Path
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TRANSPORT = load(
    "guarded_transport", ROOT / "ci/container-publish/guarded_oci_transport.py"
)
FIXTURE = load("guarded_fixture", ROOT / "tests/test_container_upload_contract.py")


@contextmanager
def service(
    status,
    headers=None,
    body=b"",
    *,
    head_status=404,
    responses=None,
    disconnect_methods=(),
):
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def handle_call(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            size = int(self.headers.get("Content-Length", 0))
            if size:
                self.rfile.read(size)
            if self.command in disconnect_methods:
                self.close_connection = True
                return
            response_status, response_headers = (responses or {}).get(
                self.command,
                (head_status if self.command == "HEAD" else status, headers or {}),
            )
            self.send_response(response_status)
            for key, value in response_headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_HEAD = handle_call

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def publish(tmp_path, origin, *, credentials=("fixture-user", "fixture-value")):
    archive = FIXTURE._single_platform_archive(tmp_path)
    handoff = FIXTURE._handoff_for_archive(archive)
    now = datetime.now(timezone.utc)
    prep = TRANSPORT.PREPARATION
    policy = {
        "schema_version": 1,
        "origin": origin,
        "namespace": "private/rehearsal",
        "revision": "synthetic",
        "expires_at": (now + timedelta(hours=2)).isoformat(),
        "private_acl_evidence": "fixture:synthetic",
        "transport_boundary_evidence": "fixture:guarded-http",
        "credential_reference": "kv2/rapids-team/rapids/ci/rehearsal/container/synthetic",
    }
    descriptor = {
        "schema_version": 1,
        "policy_sha256": prep.digest(policy),
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "repository": "private/rehearsal/image",
        "tag": "fixture",
        "handoff": handoff,
    }
    return TRANSPORT.publish_prepared(
        policy,
        descriptor,
        archive,
        tmp_path,
        credentials=credentials,
        policy_sha256=prep.digest(policy),
        handoff_sha256=prep.digest(handoff),
        train_sha256="a" * 64,
        identity=handoff["identity"],
        loopback_fixture=True,
        now=now,
    )


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_actual_redirect_never_reaches_target_or_forwards_credentials(tmp_path, status):
    with service(200) as (target, target_calls):
        with service(
            status, {"Location": target + "/leaked-secret"}, b"secret-error-body"
        ) as (origin, calls):
            with pytest.raises(
                TRANSPORT.TransportError, match="redirect rejected"
            ) as error:
                publish(tmp_path, origin)
    assert len(calls) == 1
    assert target_calls == []
    assert "fixture-value" not in str(error.value)
    assert "leaked-secret" not in str(error.value)
    assert "secret-error-body" not in str(error.value)


def test_bearer_token_realm_never_contacted(tmp_path):
    with service(200) as (realm, realm_calls):
        with service(
            401, {"WWW-Authenticate": f'Bearer realm="{realm}/token",service="fixture"'}
        ) as (origin, calls):
            with pytest.raises(
                TRANSPORT.TransportError, match="authentication challenge"
            ):
                publish(tmp_path, origin)
    assert len(calls) == 1
    assert realm_calls == []


@pytest.mark.parametrize(
    "location",
    [
        "http://external.invalid/v2/private/rehearsal/image/blobs/uploads/id",
        "//external.invalid/v2/private/rehearsal/image/blobs/uploads/id",
        "/v2/private/other/image/blobs/uploads/id",
        "/v2/private/rehearsal/image/blobs/uploads/../manifests/public",
        "/v2/private/rehearsal/image/blobs/uploads/%2e%2e",
        "/v2/private/rehearsal/image/blobs/uploads/id%2fescape",
        "/v2/private/rehearsal/image/blobs/uploads/id?redirect=http%3A%2F%2Fexternal.invalid",
        "/v2/private/rehearsal/image/blobs/uploads/id?_state=one&_state=two",
        "/v2/private/rehearsal/image/blobs/uploads/id?digest=sha256%3A" + "a" * 64,
        "/v2/private/rehearsal/image/blobs/uploads/id?_state=%252fsecret",
        "../uploads/id",
        "/v2/private/rehearsal/image/blobs/uploads/id#secret",
    ],
)
def test_upload_location_escape_rejected_before_put(tmp_path, location):
    blob = tmp_path / "blob"
    blob.write_bytes(b"fixture")
    with service(202, responses={"POST": (202, {"Location": location})}) as (
        origin,
        calls,
    ):
        registry = TRANSPORT.Registry(
            origin, "private/rehearsal/image", ("fixture-user", "fixture-value")
        )
        try:
            with pytest.raises(TRANSPORT.TransportError):
                registry.blob("a" * 64, blob)
        finally:
            registry.close()
    assert [method for method, _path, _auth in calls] == ["HEAD", "POST"]


def test_absolute_foreign_upload_location_not_contacted(tmp_path):
    with service(200) as (foreign, foreign_calls):
        with service(
            202,
            responses={
                "POST": (
                    202,
                    {
                        "Location": foreign
                        + "/v2/private/rehearsal/image/blobs/uploads/id"
                    },
                )
            },
        ) as (origin, calls):
            registry = TRANSPORT.Registry(
                origin, "private/rehearsal/image", ("fixture-user", "fixture-value")
            )
            blob = tmp_path / "blob"
            blob.write_bytes(b"fixture")
            try:
                with pytest.raises(TRANSPORT.TransportError, match="Location rejected"):
                    registry.blob("a" * 64, blob)
            finally:
                registry.close()
    assert len(calls) == 2
    assert foreign_calls == []


def test_environment_netrc_and_proxy_not_used(tmp_path, monkeypatch):
    netrc = tmp_path / "netrc"
    netrc.write_text(
        "machine 127.0.0.1 login unrelated-user password unrelated-value\n"
    )
    netrc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(netrc))
    monkeypatch.setenv("HTTPS_PROXY", "http://external.invalid:9")
    monkeypatch.setenv("HTTP_PROXY", "http://external.invalid:9")
    with service(401, {"WWW-Authenticate": 'Basic realm="fixture"'}) as (origin, calls):
        with pytest.raises(TRANSPORT.TransportError, match="HTTP status 401"):
            publish(tmp_path, origin)
    assert calls[0][2] == "Basic Zml4dHVyZS11c2VyOmZpeHR1cmUtdmFsdWU="


def test_conflicting_existing_manifest_fails_without_upload(tmp_path):
    with service(200, body=b"different manifest") as (origin, calls):
        with pytest.raises(TRANSPORT.TransportError, match="readback"):
            publish(tmp_path, origin)
    assert [method for method, _path, _auth in calls] == ["GET"]


def test_ambiguous_post_error_not_retried_and_body_redacted(tmp_path):
    blob = tmp_path / "blob"
    blob.write_bytes(b"fixture")
    with service(503, body=b"sensitive-upload-state") as (origin, calls):
        registry = TRANSPORT.Registry(
            origin, "private/rehearsal/image", ("fixture-user", "fixture-value")
        )
        try:
            with pytest.raises(
                TRANSPORT.TransportError, match="HTTP status 503"
            ) as error:
                registry.blob("a" * 64, blob)
        finally:
            registry.close()
    assert [method for method, _path, _auth in calls] == ["HEAD", "POST"]
    assert "sensitive-upload-state" not in str(error.value)


@pytest.mark.parametrize("stage", ["POST", "PUT"])
def test_actual_upload_redirect_rejected_without_target_request(tmp_path, stage):
    with service(200) as (target, target_calls):
        responses = {
            "POST": (
                202,
                {
                    "Location": "/v2/private/rehearsal/image/blobs/uploads/id?_state=fixture"
                },
            )
        }
        responses[stage] = (307, {"Location": target + "/credential-leak"})
        with service(201, responses=responses) as (origin, calls):
            registry = TRANSPORT.Registry(
                origin, "private/rehearsal/image", ("fixture-user", "fixture-value")
            )
            blob = tmp_path / "blob"
            blob.write_bytes(b"fixture")
            try:
                with pytest.raises(TRANSPORT.TransportError, match="redirect rejected"):
                    registry.blob("a" * 64, blob)
            finally:
                registry.close()
    assert [method for method, _path, _auth in calls] == (
        ["HEAD", "POST"] if stage == "POST" else ["HEAD", "POST", "PUT"]
    )
    assert target_calls == []


def test_disconnected_post_is_not_blindly_retried(tmp_path):
    blob = tmp_path / "blob"
    blob.write_bytes(b"fixture")
    with service(202, disconnect_methods={"POST"}) as (origin, calls):
        registry = TRANSPORT.Registry(
            origin, "private/rehearsal/image", ("fixture-user", "fixture-value")
        )
        try:
            with pytest.raises(TRANSPORT.TransportError, match="no automatic retry"):
                registry.blob("a" * 64, blob)
        finally:
            registry.close()
    assert [method for method, _path, _auth in calls] == ["HEAD", "POST"]


def test_blob_readback_rejects_wrong_digest_and_oversized_body():
    with service(200, body=b"tampered bytes") as (origin, _calls):
        registry = TRANSPORT.Registry(
            origin, "private/rehearsal/image", ("fixture-user", "fixture-value")
        )
        try:
            for size, message in [(len(b"tampered bytes"), "differ"), (1, "exceeds")]:
                response = registry.request(
                    "GET", registry.prefix + "blobs/sha256:" + "a" * 64, {200}
                )
                with pytest.raises(TRANSPORT.TransportError, match=message):
                    registry.read(response, "a" * 64, size)
        finally:
            registry.close()


def test_expired_request_deadline_stops_before_any_network_request():
    with service(200) as (origin, calls):
        registry = TRANSPORT.Registry(
            origin,
            "private/rehearsal/image",
            ("fixture-user", "fixture-value"),
            deadline=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        try:
            with pytest.raises(TRANSPORT.TransportError, match="expired"):
                registry.request("GET", registry.prefix + "manifests/fixture", {200})
        finally:
            registry.close()
    assert calls == []


@pytest.mark.parametrize("method", ["GET", "HEAD", "PUT"])
def test_nonredirect_response_location_is_also_guarded(method):
    with service(200) as (target, target_calls):
        with service(200, {"Location": target + "/leak"}, head_status=200) as (
            origin,
            calls,
        ):
            registry = TRANSPORT.Registry(
                origin, "private/rehearsal/image", ("fixture-user", "fixture-value")
            )
            try:
                with pytest.raises(TRANSPORT.TransportError, match="Location rejected"):
                    registry.request(
                        method, registry.prefix + "manifests/fixture", {200}
                    )
            finally:
                registry.close()
    assert len(calls) == 1
    assert target_calls == []


def test_invalid_explicit_ca_path_is_sanitized_before_connection(tmp_path):
    with service(200) as (origin, calls):
        registry = TRANSPORT.Registry(
            origin.replace("http://", "https://"),
            "private/rehearsal/image",
            ("fixture-user", "fixture-value"),
            ca_file=tmp_path / "sensitive-operator-path",
        )
        try:
            with pytest.raises(
                TRANSPORT.TransportError, match="registry request failed"
            ) as error:
                registry.request("GET", registry.prefix + "manifests/fixture", {200})
        finally:
            registry.close()
    assert "sensitive-operator-path" not in str(error.value)
    assert "fixture-value" not in str(error.value)
    assert calls == []
