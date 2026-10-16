# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real curl/client behavior against a synthetic, loopback-only staging service."""

import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location(
    "maven_fixture",
    Path(__file__).resolve().parents[1] / "ci/publication-rehearsal/maven_fixture.py",
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
_service = FIXTURE.service
_run = FIXTURE.run
AUTH = FIXTURE.AUTH
DEPLOYMENT = FIXTURE.DEPLOYMENT


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
