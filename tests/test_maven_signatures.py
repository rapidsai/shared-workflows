# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real fixture signatures and verification; no operator signer/keyring trust."""

import importlib.util
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


SIGN = load("fixture_signatures", "maven_signatures.py")
FIXTURE = load("signature_packages", "maven_fixture.py")


@pytest.fixture
def signed(tmp_path):
    FIXTURE.package_files(tmp_path)
    expected = SIGN.prepare(tmp_path)
    artifact = tmp_path / "signed/ai/rapids/example/26.12.0"
    pairs = {
        p.name: (p, p.with_suffix(p.suffix + ".asc"))
        for p in artifact.iterdir()
        if not p.name.endswith(".asc")
    }
    return tmp_path / "signer-public.asc", pairs, expected


def test_existing_signing_step_and_independent_gpgv_accept_exact_planned_key(
    signed, monkeypatch
):
    public, pairs, expected = signed
    monkeypatch.setenv("GNUPGHOME", "/unreadable/operator-keyring")
    monkeypatch.setenv("GPG_PRIVATE_KEY", "operator-placeholder-never-forward")
    report = SIGN.verify(public, pairs)
    assert report["verification"] == "gpgv-exact-planned-key"
    assert report["primary_signing_fingerprint"] == expected
    assert len(report["artifacts"]) == 4
    assert all(
        item["primary_signing_fingerprint"] == expected
        for item in report["artifacts"].values()
    )
    assert os.environ["GNUPGHOME"] == "/unreadable/operator-keyring"


@pytest.mark.parametrize("target", ["artifact", "signature"])
def test_changed_readback_artifact_or_signature_rejected(signed, target):
    public, pairs, _expected = signed
    artifact, signature = next(iter(pairs.values()))
    path = artifact if target == "artifact" else signature
    path.write_bytes(
        path.read_bytes() + b"tampered readback bytes\n"
        if target == "artifact"
        else b"invalid signature\n"
    )
    with pytest.raises(SIGN.SignatureError, match="operation rejected"):
        SIGN.verify(public, pairs)


def test_wrong_key_rejected_even_when_ambient_default_keyring_contains_correct_key(
    signed, tmp_path, monkeypatch
):
    public, pairs, _expected = signed
    other = tmp_path / "other"
    other.mkdir()
    FIXTURE.package_files(other)
    SIGN.prepare(other)
    ambient = tmp_path / "operator-placeholder"
    ambient.mkdir(mode=0o700)
    SIGN.command(
        [
            SIGN.tool("gpg"),
            "--no-options",
            "--batch",
            "--yes",
            "--output",
            ambient / "trustedkeys.gpg",
            "--dearmor",
            public,
        ],
        home=ambient,
    )
    monkeypatch.setenv("GNUPGHOME", str(ambient))
    with pytest.raises(SIGN.SignatureError, match="operation rejected"):
        SIGN.verify(other / "signer-public.asc", pairs)
    assert (ambient / "trustedkeys.gpg").is_file()


def test_public_key_with_extra_signer_rejected(signed, tmp_path):
    public, pairs, _expected = signed
    other = tmp_path / "other"
    other.mkdir()
    FIXTURE.package_files(other)
    SIGN.prepare(other)
    public.write_bytes(public.read_bytes() + (other / "signer-public.asc").read_bytes())
    with pytest.raises(SIGN.SignatureError, match="exactly one primary"):
        SIGN.verify(public, pairs)


def test_extra_detached_signature_never_reports_one_signer(signed):
    public, pairs, _expected = signed
    _artifact, signature = next(iter(pairs.values()))
    signature.write_bytes(signature.read_bytes() * 2)
    with pytest.raises(
        SIGN.SignatureError, match="unexpected signer|operation rejected"
    ):
        SIGN.verify(public, pairs)


def test_prepare_only_signs_once_and_removes_own_keyring_agent(tmp_path, monkeypatch):
    FIXTURE.package_files(tmp_path)
    original = SIGN.command
    calls = []

    def observe(argv, *, home, signing=False):
        calls.append((list(map(str, argv)), home))
        return original(argv, home=home, signing=signing)

    monkeypatch.setattr(SIGN, "command", observe)
    SIGN.prepare(tmp_path)
    homes = {home for _argv, home in calls}
    assert homes and all(not home.exists() for home in homes)
    assert any(
        argv[1:] == ["--homedir", str(home), "--kill", "gpg-agent"]
        for argv, home in calls
    )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.asc")}
    with pytest.raises(SIGN.SignatureError, match="already exists"):
        SIGN.prepare(tmp_path)
    assert all(path.read_bytes() == body for path, body in before.items())


def test_empty_signature_set_never_reports_success(signed):
    public, _pairs, _expected = signed
    with pytest.raises(SIGN.SignatureError, match="no readback signatures"):
        SIGN.verify(public, {})
