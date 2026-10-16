# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Executable dummy publication payloads; explicit runtime opt-ins only."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tarfile

import pytest
import requests

SPEC = importlib.util.spec_from_file_location(
    "dummy_demo", Path(__file__).parents[1] / "ci/publication-rehearsal/demo.py"
)
DEMO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEMO)
PAYLOAD = DEMO.PAYLOAD
FIXTURE = DEMO.DRIVER.load(
    "dummy_maven_test", "ci/publication-rehearsal/maven_fixture.py"
)


def test_existing_output_is_never_overwritten(tmp_path):
    with pytest.raises(DEMO.DRIVER.RehearsalError, match="new directory"):
        DEMO.rehearse(tmp_path)


@pytest.mark.parametrize(
    "origin",
    [
        "https://public.invalid/repository/",
        "http://localhost:1/repository/",
        "http://127.0.0.1:1/repository/?redirect=external",
    ],
)
def test_maven_consumer_never_accepts_an_external_repository(tmp_path, origin):
    with pytest.raises(PAYLOAD.DRIVER.RehearsalError, match="loopback"):
        PAYLOAD.settings(tmp_path / "settings.xml", origin)
    assert not (tmp_path / "settings.xml").exists()


def test_authenticated_maven_download_requires_real_basic_challenge():
    with FIXTURE.service() as (origin, state), requests.Session() as session:
        session.trust_env = False
        path = "/repository/ai/rapids/example/26.12.0/example-26.12.0.jar"
        stored = path.replace("/repository/", "/service/local/staging/deploy/maven2/")
        state["uploads"][stored] = b"fixture-jar"
        state["handed_off"] = True
        rejected = session.get(origin + path, timeout=5, allow_redirects=False)
        assert rejected.status_code == 401
        assert rejected.headers["WWW-Authenticate"] == 'Basic realm="LocalFixture"'
        downloaded = session.get(
            origin + path,
            auth=("fixture-user", "fixture-value"),
            timeout=5,
            allow_redirects=False,
        )
        assert downloaded.status_code == 200 and downloaded.content == b"fixture-jar"


def test_tool_cache_cannot_supply_the_dummy_artifact(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    (cache / "ai/rapids").mkdir(parents=True)
    (tmp_path / "maven").mkdir()
    calls = []
    monkeypatch.setattr(PAYLOAD.DRIVER, "execute", lambda *_a, **_k: calls.append(True))
    with pytest.raises(PAYLOAD.DRIVER.RehearsalError, match="must not contain"):
        PAYLOAD.resolve_maven(tmp_path, "http://127.0.0.1:1", cache, "0" * 64)
    assert calls == []


@pytest.mark.parametrize("expected_matches", [True, False])
def test_real_maven_resolves_uploaded_jar_and_runs_or_rejects_hash(
    tmp_path, expected_matches
):
    cache = os.environ.get("OPER04_MAVEN_CACHE")
    if not cache:
        pytest.skip(
            "actual Maven/JDK integration requires explicit OPER04_MAVEN_CACHE from demo bootstrap; no publication fallback"
        )
    (tmp_path / "maven").mkdir()
    FIXTURE.package_files(tmp_path / "maven")
    PAYLOAD.java_payload(tmp_path)
    jar = tmp_path / "maven/signed/ai/rapids/example/26.12.0/example-26.12.0.jar"
    expected = hashlib.sha256(jar.read_bytes()).hexdigest()
    with FIXTURE.service() as (origin, state):
        result, _bundle = FIXTURE.run(
            tmp_path / "maven", origin, auto_drop=False, use_prepared=True
        )
        assert result.returncode == 0
        if expected_matches:
            report = PAYLOAD.resolve_maven(tmp_path, origin, Path(cache), expected)
            assert report["resolved_jar_sha256"] == expected
            assert report["java_main_output"] == PAYLOAD.MAVEN_MARKER
            assert any(
                path.endswith(".jar")
                for method, path, _query, _auth in state["requests"]
                if method == "GET"
            )
        else:
            with pytest.raises(PAYLOAD.DRIVER.RehearsalError, match="different JAR"):
                PAYLOAD.resolve_maven(tmp_path, origin, Path(cache), "0" * 64)


def test_real_oci_payload_has_executable_elf_and_matching_diff_id(tmp_path):
    image = os.environ.get("OPER04_RUNTIME_IMAGE")
    if not image:
        pytest.skip(
            "ELF image preparation requires explicit cached OPER04_RUNTIME_IMAGE; full demo records actual Docker execution"
        )
    (tmp_path / "oci").mkdir()
    report = PAYLOAD.image_payload(tmp_path, image)
    assert report["source_image_id"].startswith("sha256:")
    with tarfile.open(tmp_path / "oci/input.tar.gz") as archive:
        index = json.load(archive.extractfile("index.json"))
        manifest = json.load(
            archive.extractfile("blobs/sha256/" + index["manifests"][0]["digest"][7:])
        )
        config = json.load(
            archive.extractfile("blobs/sha256/" + manifest["config"]["digest"][7:])
        )
        layer = archive.extractfile(
            "blobs/sha256/" + manifest["layers"][0]["digest"][7:]
        ).read()
    assert config["rootfs"]["diff_ids"] == [
        "sha256:" + hashlib.sha256(layer).hexdigest()
    ]
    assert config["config"]["Cmd"] == ["/bin/echo", PAYLOAD.MARKER]
