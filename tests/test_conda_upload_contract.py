# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise Anaconda's actual stage/multipart/commit client on loopback."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "conda_fixture",
    Path(__file__).resolve().parents[1] / "ci/publication-rehearsal/conda_fixture.py",
)
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
_service = FIXTURE.service
_run = FIXTURE.run
_package = FIXTURE.package


@pytest.mark.parametrize("extension", [".conda", ".tar.bz2"])
def test_real_client_bytes_labels_commit_readback_and_identical_retry(
    tmp_path, extension
):
    package = _package(tmp_path, extension)
    original = package.read_bytes()
    with _service() as (url, state):
        first = _run(package, url)
        second = _run(package, url)
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert state["storage_posts"] == 1
    assert list(state["objects"].values()) == [original]
    assert state["stages"][0]["channels"] == ["candidate"]
    assert state["stages"][0]["sha256"] == hashlib.sha256(original).hexdigest()
    assert package.read_bytes() == original
    assert all(
        auth is None for _method, path, auth in state["requests"] if path == "/storage"
    )
    receipt = json.loads(second.stdout.splitlines()[-1])
    assert receipt["sha256"] == hashlib.sha256(original).hexdigest()
    assert receipt["client_version"] == "1.15.0"
    assert "requests" in receipt["dependencies"]


def test_skip_existing_is_not_hash_proof_and_readback_rejects_conflict(tmp_path):
    package = _package(tmp_path)
    with _service() as (url, state):
        assert _run(package, url).returncode == 0
        original = list(state["objects"].values())[0]
        package = _package(tmp_path, content=b"conflicting replacement")
        result = _run(package, url)
    assert result.returncode != 0
    assert "destination package digest conflict" in result.stderr
    assert state["storage_posts"] == 1
    assert list(state["objects"].values()) == [original]


@pytest.mark.parametrize("mode", ["auth-failure", "commit-failure"])
def test_failed_auth_or_commit_never_has_success_receipt(tmp_path, mode):
    package = _package(tmp_path)
    with _service(mode) as (url, state):
        result = _run(package, url)
    assert result.returncode != 0
    assert not state["objects"]
    assert '"local_contract_only": true' not in result.stdout


def test_identical_retry_recovers_after_transient_commit_failure(tmp_path):
    package = _package(tmp_path)
    original = package.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    with _service("commit-failure-once") as (url, state):
        first = _run(package, url)
        assert first.returncode != 0
        assert '"local_contract_only": true' not in first.stdout
        assert state["commit_attempts"] == 1
        assert state["storage_posts"] == 1
        assert not state["objects"]
        assert list(state["pending"].values()) == [original]

        retry = _run(package, url)

    assert retry.returncode == 0, retry.stderr
    assert state["commit_attempts"] == 2
    assert state["storage_posts"] == 2
    assert not state["pending"]
    assert list(state["objects"].values()) == [original]
    receipt = json.loads(retry.stdout.splitlines()[-1])
    assert receipt["sha256"] == digest
    assert receipt["local_contract_only"] is True


@pytest.mark.parametrize("mode", ["redirect", "foreign-storage"])
def test_origin_guard_blocks_redirect_and_server_selected_foreign_storage(
    tmp_path, mode
):
    package = _package(tmp_path)
    with (
        _service() as (target, destination),
        _service(mode, redirect=target) as (origin, state),
    ):
        result = _run(package, origin)
    assert result.returncode != 0
    assert "fixture transport rejected" in result.stderr
    assert destination["requests"] == []
    assert not state["objects"]


def test_changed_local_input_fails_before_network(tmp_path):
    package = _package(tmp_path)
    with _service() as (url, state):
        result = _run(package, url, expected="0" * 64)
    assert result.returncode != 0
    assert "input package does not match" in result.stderr
    assert state["requests"] == []


def test_fixture_transport_does_not_use_environment_proxy(tmp_path):
    package = _package(tmp_path)
    with _service() as (proxy, proxy_state), _service() as (url, state):
        result = _run(package, url, proxy=proxy)
    assert result.returncode == 0, result.stderr
    assert state["storage_posts"] == 1
    assert proxy_state["requests"] == []


def test_fixture_transport_never_loads_implicit_netrc_authentication(tmp_path):
    package = _package(tmp_path)
    netrc = tmp_path / "fixture.netrc"
    netrc.write_text(
        "default login synthetic-netrc-user password synthetic-netrc-value\n"
    )
    netrc.chmod(0o600)
    with _service() as (url, state):
        result = _run(package, url, netrc=netrc)
    assert result.returncode == 0, result.stderr
    assert state["storage_posts"] == 1
    for _method, path, auth in state["requests"]:
        assert auth == (None if path == "/storage" else "token fixture-value")


@pytest.mark.parametrize(
    "url",
    [
        "https://api.anaconda.org",
        "http://localhost:1234",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:1234/path",
    ],
)
def test_local_harness_rejects_unapproved_initial_origin(tmp_path, url):
    result = _run(_package(tmp_path), url)
    assert result.returncode != 0
    assert (
        "fixture URL must be" in result.stderr
        or "invalid fixture port" in result.stderr
    )
