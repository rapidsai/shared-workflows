# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Credential-free descriptor-to-sealed-archive preparation behavior."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess  # nosec B404 -- invokes this checked-in CLI without a shell.
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREP = load("hosted_preparation", ROOT / "ci/container-publish/hosted_preparation.py")
FIXTURE = load(
    "container_contract_fixture", ROOT / "tests/test_container_upload_contract.py"
)
NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


@pytest.fixture
def inputs(tmp_path):
    archive = FIXTURE._single_platform_archive(tmp_path)
    handoff = FIXTURE._handoff_for_archive(archive)
    policy = {
        "schema_version": 1,
        "origin": "https://registry.invalid",
        "namespace": "private/rehearsal",
        "revision": "review-1",
        "expires_at": "2026-10-15T14:00:00Z",
        "private_acl_evidence": "review:synthetic-acl",
        "transport_boundary_evidence": "review:synthetic-egress",
        "credential_reference": "kv2/rapids-team/rapids/ci/rehearsal/container/synthetic",
    }
    descriptor = {
        "schema_version": 1,
        "policy_sha256": PREP.digest(policy),
        "issued_at": "2026-10-15T11:00:00Z",
        "expires_at": "2026-10-15T13:00:00Z",
        "repository": "private/rehearsal/ci-conda",
        "tag": handoff["identity"]["build"],
        "handoff": handoff,
    }
    return policy, descriptor, archive


def prepare(inputs, output, **kwargs):
    policy, descriptor, archive = inputs
    return PREP.prepare(
        policy,
        descriptor,
        archive,
        output,
        policy_sha256=PREP.digest(policy),
        handoff_sha256=PREP.digest(descriptor["handoff"]),
        train_sha256="a" * 64,
        identity=descriptor["handoff"]["identity"],
        now=NOW,
        **kwargs,
    )


def test_seals_exact_bytes_and_identical_retry_without_credentials(
    inputs, tmp_path, monkeypatch
):
    monkeypatch.setenv("DOCKER_CONFIG", "/not-readable/operator-credentials")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-do-not-forward")
    monkeypatch.setenv("HTTPS_PROXY", "http://external.invalid")
    # Preparation must not invoke clients, requests, credential helpers, or read
    # credential paths. The existing importer is exercised separately end-to-end.
    monkeypatch.setattr(
        PREP.OCI,
        "command",
        lambda *_a, **_kw: pytest.fail("client called during preparation"),
    )
    receipt, directory = prepare(inputs, tmp_path)
    initial = {path.name: path.read_bytes() for path in directory.iterdir()}
    assert receipt["state"] == "prepared-not-published"
    assert "synthetic-do-not-forward" not in json.dumps(receipt)
    assert (directory / "archive.oci").read_bytes() == inputs[2].read_bytes()
    assert all(path.stat().st_mode & 0o777 == 0o400 for path in directory.iterdir())
    assert prepare(inputs, tmp_path) == (receipt, directory)
    assert initial == {path.name: path.read_bytes() for path in directory.iterdir()}


@pytest.mark.parametrize(
    "repository",
    [
        "private/rehearsal-escape/image",
        "private/rehearsal/../public",
        "private/rehearsal/%2fpublic",
        "private/rehearsal",
        "/private/rehearsal/image",
    ],
)
def test_namespace_escape_rejected(inputs, tmp_path, repository):
    inputs[1]["repository"] = repository
    with pytest.raises(ValueError, match="namespace"):
        prepare(inputs, tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        {"expires_at": "2026-10-15T12:00:00Z"},
        {"issued_at": "2026-10-15T13:00:00Z"},
        {"issued_at": "2026-10-13T11:00:00Z"},
        {"expires_at": "2026-10-15T15:00:00Z"},
    ],
)
def test_stale_future_or_overlong_descriptor_rejected(inputs, tmp_path, change):
    inputs[1].update(change)
    with pytest.raises(ValueError, match="stale"):
        prepare(inputs, tmp_path)


def test_policy_and_handoff_must_match_external_digests(inputs, tmp_path):
    policy, descriptor, archive = inputs
    kwargs = dict(
        policy_sha256=PREP.digest(policy),
        handoff_sha256=PREP.digest(descriptor["handoff"]),
        train_sha256="a" * 64,
        identity=descriptor["handoff"]["identity"],
        now=NOW,
    )
    policy["namespace"] = "public"
    with pytest.raises(ValueError, match="caller-approved"):
        PREP.prepare(policy, descriptor, archive, tmp_path, **kwargs)
    policy["namespace"] = "private/rehearsal"
    descriptor["handoff"]["source"]["run_id"] = "999"
    with pytest.raises(ValueError, match="authenticated caller"):
        PREP.prepare(policy, descriptor, archive, tmp_path, **kwargs)


@pytest.mark.parametrize(
    "origin",
    [
        "http://registry.invalid",
        "https://user:secret@registry.invalid",
        "https://@registry.invalid",
        "https://registry.invalid/path",
        "https://registry.invalid?redirect=public",
        "https://REGISTRY.invalid",
    ],
)
def test_invalid_origin_rejected(inputs, tmp_path, origin):
    inputs[0]["origin"] = origin
    inputs[1]["policy_sha256"] = PREP.digest(inputs[0])
    with pytest.raises(ValueError, match="origin"):
        prepare(inputs, tmp_path)


def test_archive_tampering_and_structural_mismatch_rejected(inputs, tmp_path):
    policy, descriptor, archive = inputs
    original = archive.read_bytes()
    archive.write_bytes(original + b"tampered")
    with pytest.raises(ValueError, match="digest"):
        prepare(inputs, tmp_path)
    archive.write_bytes(original)
    descriptor["handoff"]["archive"]["config_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="structural identity"):
        prepare(inputs, tmp_path)
    assert not list(tmp_path.glob("prepare-*"))


def test_retry_rejects_changed_sealed_bytes_without_repair(inputs, tmp_path):
    _receipt, directory = prepare(inputs, tmp_path)
    archive = directory / "archive.oci"
    archive.chmod(0o600)
    archive.write_bytes(b"changed")
    archive.chmod(0o400)
    with pytest.raises(ValueError, match="prepared archive bytes changed"):
        prepare(inputs, tmp_path)
    assert archive.read_bytes() == b"changed"


@pytest.mark.parametrize(
    "name,mode,message",
    [
        ("archive.oci", 0o600, "not sealed"),
        ("receipt.json", 0o440, "not sealed"),
        (None, 0o750, "not private"),
    ],
)
def test_retry_rejects_unsealed_modes_without_repair(
    inputs, tmp_path, name, mode, message
):
    _receipt, directory = prepare(inputs, tmp_path)
    target = directory / name if name else directory
    target.chmod(mode)
    with pytest.raises(ValueError, match=message):
        prepare(inputs, tmp_path)
    assert target.stat().st_mode & 0o777 == mode


def test_retry_rejects_symlink_destination(inputs, tmp_path):
    target = tmp_path / PREP.digest(inputs[1])
    target.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="unexpected files"):
        prepare(inputs, tmp_path)


def test_credential_values_or_production_paths_cannot_enter_policy(inputs, tmp_path):
    policy = inputs[0]
    policy["credential_reference"] = (
        "kv2/rapids-team/rapids/ci/credentials/DOCKERHUB_TOKEN"
    )
    inputs[1]["policy_sha256"] = PREP.digest(policy)
    with pytest.raises(ValueError, match="isolated rehearsal"):
        prepare(inputs, tmp_path)
    policy["password"] = "synthetic-secret"  # nosec B105 -- rejection-only fixture, never a credential.
    with pytest.raises(ValueError, match="unsupported"):
        prepare(inputs, tmp_path)


def test_loopback_fixture_mode_cannot_admit_external_origin(inputs, tmp_path):
    with pytest.raises(ValueError, match="loopback"):
        prepare(inputs, tmp_path, loopback_fixture=True)


def test_parent_policy_not_mutated(inputs, tmp_path):
    original = deepcopy(inputs[:2])
    prepare(inputs, tmp_path)
    assert inputs[:2] == original


def test_shared_output_root_rejected_without_permission_repair(inputs, tmp_path):
    tmp_path.chmod(0o750)
    with pytest.raises(ValueError, match="mode 0700"):
        prepare(inputs, tmp_path)
    assert tmp_path.stat().st_mode & 0o777 == 0o750
    assert not list(tmp_path.glob("prepare-*"))


def test_executable_preparation_cli_emits_only_non_authorizing_receipt(
    inputs, tmp_path
):
    policy, descriptor, archive = inputs
    now = datetime.now(timezone.utc)
    policy["expires_at"] = (now + timedelta(hours=2)).isoformat()
    descriptor.update(
        policy_sha256=PREP.digest(policy),
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    )
    paths = {}
    for name, value in {
        "policy": policy,
        "descriptor": descriptor,
        "identity": descriptor["handoff"]["identity"],
    }.items():
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_text(json.dumps(value))
    argv = [sys.executable, str(ROOT / "ci/container-publish/hosted_preparation.py")]
    for name, path in paths.items():
        argv.extend(["--" + name, str(path)])
    argv.extend(
        [
            "--archive",
            str(archive),
            "--output",
            str(tmp_path),
            "--policy-sha256",
            PREP.digest(policy),
            "--handoff-sha256",
            PREP.digest(descriptor["handoff"]),
            "--train-sha256",
            "a" * 64,
        ]
    )
    result = subprocess.run(
        argv,
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )  # nosec B603 -- fixed local CLI with fixture paths and hashes, no network writer.
    receipt = json.loads(result.stdout)
    assert receipt["state"] == "prepared-not-published"
    assert receipt["train_sha256"] == "a" * 64
    assert receipt["loopback_fixture"] is False
    assert "credential_reference" not in receipt
