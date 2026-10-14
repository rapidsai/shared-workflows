# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real curl/client behavior against a synthetic, loopback-only staging service."""

import base64
import contextlib
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 -- required to test the real reviewed shell client.
import threading
from urllib.parse import parse_qs, urlsplit
import zipfile

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "ci/maven-publish/maven_central_publish.sh"
)
DEPLOYMENT = "00000000-0000-0000-0000-000000000001"
# Synthetic test values only. Never read an operator's publisher credentials.
AUTH = base64.b64encode(b"fixture-user:fixture-value").decode()


@contextlib.contextmanager
def _service(mode="success", redirect=None):
    state = {"requests": [], "uploads": {}, "handed_off": False}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _reply(self, status, body=None, location=None):
            self.send_response(status)
            if location is not None:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(json.dumps(body or {}).encode())

        def _request(self):
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            expected_auth = ("Basic " if self.command == "PUT" else "Bearer ") + AUTH
            state["requests"].append(
                (self.command, parts.path, query, self.headers.get("Authorization"))
            )
            if (
                mode == "auth-failure"
                or self.headers.get("Authorization") != expected_auth
            ):
                self._reply(401, {"error": "synthetic authentication rejection"})
            elif mode == "redirect":
                self._reply(307, location=redirect)
            elif mode in {"invalid-search", "null-search"}:
                self._reply(
                    200,
                    {"repositories": "invalid" if mode == "invalid-search" else None},
                )
            elif parts.path == "/manual/search/repositories":
                rows = []
                if state["uploads"] or mode == "preexisting":
                    rows = [
                        {
                            "key": "repo-key",
                            "state": "released" if state["handed_off"] else "open",
                            "portal_deployment_id": DEPLOYMENT
                            if state["handed_off"]
                            else "",
                        }
                    ]
                self._reply(200, {"repositories": rows})
            elif self.command == "PUT" and parts.path.startswith(
                "/service/local/staging/deploy/maven2/"
            ):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                state["uploads"][parts.path] = body
                self._reply(
                    400
                    if mode == "partial-upload" and len(state["uploads"]) == 2
                    else 201
                )
            elif (
                parts.path == "/manual/upload/repository/repo-key"
                and self.command == "POST"
            ):
                state["handed_off"] = True
                self._reply(200)
            elif parts.path == "/api/v1/publisher/status" and self.command == "POST":
                status = {
                    "timeout": "PENDING",
                    "published": "PUBLISHED",
                    "failed": "FAILED",
                }.get(mode, "VALIDATED")
                self._reply(200, {"deploymentState": status})
            elif self.command == "DELETE" and parts.path in {
                "/manual/drop/repository/repo-key",
                f"/api/v1/publisher/deployment/{DEPLOYMENT}",
            }:
                self._reply(200)
            else:
                self._reply(404, {"error": "unexpected fixture endpoint"})

        do_GET = _request
        do_POST = _request
        do_PUT = _request
        do_DELETE = _request

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _run(
    tmp_path,
    base_url,
    *,
    auto_drop=True,
    username="fixture-user",
    credential="fixture-value",
):
    artifact = tmp_path / "signed/ai/rapids/example/26.12.0"
    artifact.mkdir(parents=True)
    for suffix in (
        ".pom",
        ".pom.asc",
        ".jar",
        ".jar.asc",
        "-sources.jar",
        "-sources.jar.asc",
        "-javadoc.jar",
        "-javadoc.jar.asc",
    ):
        (artifact / f"example-26.12.0{suffix}").write_bytes(
            f"fixture {suffix}".encode()
        )
    environment = {
        "PATH": os.environ["PATH"],
        "MAVEN_DEPLOY_USERNAME": username,
        "MAVEN_DEPLOY_TOKEN": credential,
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
    }
    bundle = tmp_path / "bundle.zip"
    result = subprocess.run(  # nosec B603 -- fixed reviewed script; endpoint validated as loopback by the script.
        [
            shutil.which("bash"),
            str(SCRIPT),
            "--input",
            str(tmp_path / "signed"),
            "--group-id",
            "ai.rapids",
            "--artifact-id",
            "example",
            "--version",
            "26.12.0",
            "--output-bundle",
            str(bundle),
            "--auto-drop",
            str(auto_drop).lower(),
            "--local-contract-base-url",
            base_url,
            "--local-contract-poll-timeout",
            "2",
        ],
        env=environment,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    return result, bundle


@pytest.mark.parametrize("auto_drop", [True, False])
def test_real_http_upload_handoff_poll_and_optional_drop(tmp_path, auto_drop):
    with _service() as (url, state):
        result, bundle = _run(tmp_path, url, auto_drop=auto_drop)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(state["uploads"]) == 24
    with zipfile.ZipFile(bundle) as archive:
        for path, body in state["uploads"].items():
            relative = path.removeprefix("/service/local/staging/deploy/maven2/")
            assert archive.read(relative) == body
        jar_path = "ai/rapids/example/26.12.0/example-26.12.0.jar"
        assert (
            archive.read(jar_path + ".sha1").decode().strip()
            == hashlib.sha1(archive.read(jar_path), usedforsecurity=False).hexdigest()
        )
    requests = state["requests"]
    assert requests[0][:3] == (
        "GET",
        "/manual/search/repositories",
        {"ip": ["client"], "profile_id": ["ai.rapids"], "state": ["open"]},
    )
    assert (
        "POST",
        "/manual/upload/repository/repo-key",
        {"publishing_type": ["portal_api"]},
        "Bearer " + AUTH,
    ) in requests
    assert (
        "POST",
        "/api/v1/publisher/status",
        {"id": [DEPLOYMENT]},
        "Bearer " + AUTH,
    ) in requests
    drops = [path for method, path, _query, _auth in requests if method == "DELETE"]
    assert drops == (
        [f"/api/v1/publisher/deployment/{DEPLOYMENT}"] if auto_drop else []
    )
    assert all(
        not path.endswith("/publish") and path != "/api/v1/publisher/upload"
        for _method, path, _query, _auth in requests
    )
    assert f"DEPLOYMENT_ID={DEPLOYMENT}" in (tmp_path / "outputs").read_text()


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("auth-failure", "401"),
        ("invalid-search", "invalid response"),
        ("null-search", "invalid response"),
        ("preexisting", "open OSSRH staging"),
        ("published", "unexpected PUBLISHED"),
        ("failed", "terminal state FAILED"),
        ("timeout", "timed out waiting for VALIDATED"),
    ],
)
def test_real_client_rejects_invalid_auth_state_and_timeout(tmp_path, mode, expected):
    with _service(mode) as (url, state):
        result, _bundle = _run(tmp_path, url)
    assert result.returncode != 0
    assert expected in result.stdout + result.stderr
    if mode in {"auth-failure", "invalid-search", "null-search", "preexisting"}:
        assert not state["uploads"]
    assert not any(
        "/api/v1/publisher/deployment/" in path
        for _method, path, _query, _auth in state["requests"]
    )


def test_partial_upload_failure_drops_only_owned_staging_repository(tmp_path):
    with _service("partial-upload") as (url, state):
        result, bundle = _run(tmp_path, url)
    assert result.returncode != 0
    assert bundle.is_file()
    assert (
        "DELETE",
        "/manual/drop/repository/repo-key",
        {},
        "Bearer " + AUTH,
    ) in state["requests"]
    assert not state["handed_off"]


def test_redirect_rejected_without_forwarding_request_or_credentials(tmp_path):
    with (
        _service() as (target, destination),
        _service("redirect", redirect=target) as (origin, _state),
    ):
        result, _bundle = _run(tmp_path, origin)
    assert result.returncode != 0
    assert "307" in result.stderr
    assert destination["requests"] == []


@pytest.mark.parametrize(
    "url",
    [
        "https://central.sonatype.com",
        "http://localhost:1234",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:1234/path",
        "http://user@127.0.0.1:1234",
    ],
)
def test_endpoint_injection_is_loopback_only(tmp_path, url):
    result, bundle = _run(tmp_path, url)
    assert result.returncode != 0
    assert "must be http://127.0.0.1:<port>" in result.stderr
    assert not bundle.exists()


@pytest.mark.parametrize(
    "credentials", [{"username": "other-test-user"}, {"credential": "other-test-value"}]
)
def test_local_contract_mode_rejects_nonfixture_credentials_before_any_request(
    tmp_path, credentials
):
    with _service() as (url, state):
        result, bundle = _run(tmp_path, url, **credentials)
    assert result.returncode != 0
    assert "only the built-in synthetic fixture credentials" in result.stderr
    assert state["requests"] == []
    assert not bundle.exists()
