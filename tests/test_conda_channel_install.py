# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Actual Conda index/Pixi retrieval and install; no public fallback channel."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "ci/publication-rehearsal" / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHANNEL = load("install_channel", "conda_channel_install.py")
FIXTURE = load("install_package", "conda_fixture.py")
DRIVER = load("install_driver", "run_local.py")


def tools():
    python, pixi = os.environ.get("PUB01_CONDA_PYTHON"), os.environ.get("PUB01_PIXI")
    if not python and not pixi:
        pytest.skip(
            "real Conda install requires explicit PUB01_CONDA_PYTHON (conda-index) and PUB01_PIXI; no solver fallback"
        )
    if not python or not pixi:
        pytest.fail("both explicit Conda runtime tools are required")
    return CHANNEL.executable(python), CHANNEL.executable(pixi)


@pytest.fixture
def indexed(tmp_path):
    python, pixi = tools()
    archive = FIXTURE.package(tmp_path)
    expected = CHANNEL.digest(archive)
    channel, report = CHANNEL.index(archive, expected, tmp_path, python)
    return channel, report, pixi, expected


def test_real_index_authenticated_solve_retrieval_and_installed_payload(
    indexed, tmp_path, monkeypatch
):
    channel, report, pixi, expected = indexed
    monkeypatch.setenv("HTTP_PROXY", "http://external.invalid:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://external.invalid:9")
    monkeypatch.setenv("PIXI_CONFIG_FILE", "/unreadable/operator/config.toml")
    monkeypatch.setenv("RATTLER_AUTH_FILE", "/unreadable/operator/auth.json")
    monkeypatch.setenv("NETRC", "/unreadable/operator/netrc")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "operator-placeholder-never-forward")
    with CHANNEL.service(channel) as (origin, calls):
        installed = CHANNEL.solve_install(origin, tmp_path / "consumer", pixi, expected)
    assert installed["installed_archive_sha256"] == expected
    assert (
        installed["installed_payload_sha256"]
        == hashlib.sha256(b"immutable fixture").hexdigest()
    )
    assert installed["installed_identity"] == {
        "name": "conda-fixture",
        "version": "26.12.00",
        "build": "h0_0",
        "subdir": "noarch",
    }
    assert report["versions"]["conda-index"]
    assert report["repodata_sha256"] == CHANNEL.digest(channel / "noarch/repodata.json")
    assert all(call["authenticated"] for call in calls)
    assert any(
        call["path"] == "/noarch/" + CHANNEL.PACKAGE and call["sha256"] == expected
        for call in calls
    )
    manifest = (tmp_path / "consumer/pixi.toml").read_text()
    assert "conda-forge" not in manifest and "rapidsai" not in manifest
    assert not (tmp_path / "consumer/auth.json").exists()
    assert "fixture-value" not in json.dumps(installed)


@pytest.mark.parametrize("authentication", ["anonymous", "wrong"])
def test_real_channel_denies_anonymous_and_wrong_credentials(
    indexed, tmp_path, authentication
):
    channel, _report, pixi, expected = indexed
    with CHANNEL.service(channel) as (origin, calls):
        with pytest.raises(CHANNEL.ChannelError, match="local channel client failed"):
            CHANNEL.solve_install(
                origin,
                tmp_path / "denied",
                pixi,
                expected,
                authentication=authentication,
            )
    assert calls and all(not call["authenticated"] for call in calls)
    assert not (tmp_path / "denied/auth.json").exists()
    assert not (
        tmp_path
        / "denied/.pixi/envs/default/conda-meta/conda-fixture-26.12.00-h0_0.json"
    ).exists()


def test_real_installer_rejects_changed_archive_after_indexing(
    indexed, tmp_path, monkeypatch
):
    channel, _report, pixi, expected = indexed
    failures = []
    original_run = CHANNEL.subprocess.run

    def capture_failure(*args, **kwargs):
        result = original_run(*args, **kwargs)
        if result.returncode:
            failures.append(result.stderr.lower())
        return result

    monkeypatch.setattr(CHANNEL.subprocess, "run", capture_failure)
    archive = channel / "noarch" / CHANNEL.PACKAGE
    changed = FIXTURE.package(
        tmp_path / "valid-but-different", content=b"different fixture payload"
    )
    archive.write_bytes(changed.read_bytes())
    with CHANNEL.service(channel) as (origin, calls):
        with pytest.raises(CHANNEL.ChannelError, match="local channel client failed"):
            CHANNEL.solve_install(origin, tmp_path / "tampered", pixi, expected)
    assert any(
        call["path"] == "/noarch/" + CHANNEL.PACKAGE and call["sha256"] != expected
        for call in calls
    )
    assert not (tmp_path / "tampered/auth.json").exists()
    assert any("hash mismatch" in text and expected in text for text in failures)


def test_real_solver_rejects_missing_package_without_fallback(indexed, tmp_path):
    channel, _report, pixi, expected = indexed
    metadata = channel / "noarch/repodata.json"
    record = json.loads(metadata.read_bytes())
    record["packages.conda"] = {}
    metadata.write_text(json.dumps(record))
    with CHANNEL.service(channel) as (origin, calls):
        with pytest.raises(CHANNEL.ChannelError, match="local channel client failed"):
            CHANNEL.solve_install(origin, tmp_path / "missing", pixi, expected)
    assert not any(call["path"].endswith(".conda") for call in calls)


def test_changed_planned_bytes_fail_before_indexer(tmp_path, monkeypatch):
    archive = FIXTURE.package(tmp_path)
    monkeypatch.setattr(
        CHANNEL, "command", lambda *_a, **_kw: pytest.fail("indexer called")
    )
    with pytest.raises(CHANNEL.ChannelError, match="archive bytes differ"):
        CHANNEL.index(archive, "0" * 64, tmp_path, Path(os.sys.executable))


def test_partial_tool_context_rejected_before_publication(tmp_path):
    with pytest.raises(DRIVER.RehearsalError, match="both explicit runtime tools"):
        DRIVER.run(tmp_path, "0" * 64, tmp_path, conda_python=Path(os.sys.executable))


@pytest.mark.parametrize(
    "origin",
    ["https://external.invalid", "http://localhost:1234", "http://127.0.0.1:99999"],
)
def test_non_fixture_channel_rejected_before_client(tmp_path, origin, monkeypatch):
    monkeypatch.setattr(
        CHANNEL, "command", lambda *_a, **_kw: pytest.fail("client called")
    )
    with pytest.raises(CHANNEL.ChannelError, match="IPv4 loopback"):
        CHANNEL.solve_install(
            origin, tmp_path / "consumer", Path(os.sys.executable), "0" * 64
        )
