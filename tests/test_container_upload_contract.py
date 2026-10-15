# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Actual authenticated registry/Docker roundtrip and local destination guards."""

import hashlib
import gzip
import http.server
import io
import importlib.util
import json
from pathlib import Path
import tarfile
import threading

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "ci/container-publish/local_rehearsal.py"
SPEC = importlib.util.spec_from_file_location("container_rehearsal", SCRIPT)
REHEARSAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REHEARSAL)


def test_real_registry_docker_roundtrip_and_denials():
    report = REHEARSAL.rehearse()
    assert report["local_contract_only"]
    assert report["synthetic_images_only"]
    assert report["platforms"] == ["amd64", "arm64"]
    assert len(report["blob_digests"]) >= 3
    assert report["anonymous_read"] == "denied401"
    assert report["wrong_auth"] == "denied"
    assert report["wrong_digest"] == "rejected"
    assert report["wrong_digest_api"] == "MANIFEST_UNKNOWN/404"
    assert report["digest_pulls"] == "passed"
    assert report["identical_retry"] == "unchanged"
    assert report["oras_version"].startswith("Version:    1.3.3")
    assert report["oci_archive_sha256"]
    assert report["oci_source_index_digest"] == report["oci_import_index_digest"]
    assert report["oci_archive_platforms"] == ["linux/amd64", "linux/arm64"]
    assert len(report["oci_import_manifest_digests"]) == 3
    assert len(report["oci_import_blob_digests"]) >= 3
    assert report["oci_archive_import"] == "byte-identical"
    assert report["oci_import_wrong_digest"] == "rejected-before-upload"
    assert report["oci_import_missing_variant"] == "rejected-before-upload"
    assert report["oci_import_external_url"] == "rejected-before-upload"
    assert report["oci_import_conflict"] == "preserved-existing-tag"
    assert report["oci_import_retry"] == "unchanged"
    assert report["single_platform_import"] == "manifest-digest-byte-identical"
    assert report["single_platform_handoff_mismatch"] == "rejected-before-upload"
    assert report["cleanup_verified"]


def _single_platform_archive(tmp_path, *, architecture="arm64"):
    config = json.dumps(
        {"architecture": architecture, "os": "linux"}, separators=(",", ":")
    ).encode()
    layer = b"small producer-shaped OCI layer"
    config_digest = hashlib.sha256(config).hexdigest()
    layer_digest = hashlib.sha256(layer).hexdigest()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + config_digest,
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": "sha256:" + layer_digest,
                    "size": len(layer),
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    tag = f"26.12-cuda13.3.0-rockylinux8-py3.12-{architecture}"
    index = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + manifest_digest,
                    "size": len(manifest),
                    "annotations": {
                        "io.containerd.image.name": "docker.io/rapidsai/citestwheel:"
                        + tag,
                        "org.opencontainers.image.ref.name": tag,
                    },
                    "platform": {"architecture": architecture, "os": "linux"},
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    archive_path = tmp_path / "producer-shaped-single.oci.tar"
    contents = {
        "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
        "index.json": index,
        "blobs/sha256/" + config_digest: config,
        "blobs/sha256/" + layer_digest: layer,
        "blobs/sha256/" + manifest_digest: manifest,
    }
    with tarfile.open(archive_path, "w") as bundle:
        for name, content in contents.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
    return archive_path


def _handoff_for_archive(archive_path, *, architecture="arm64"):
    inspected = REHEARSAL.inspect_single_platform_oci_archive(
        archive_path,
        hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        archive_path.parent,
    )
    tag = f"26.12-cuda13.3.0-rockylinux8-py3.12-{architecture}"
    return {
        "schema_version": 1,
        "train_sha256": "a" * 64,
        "release_catalog_key": "container:ci-images",
        "source": {
            "repository": "rapidsai/ci-imgs",
            "sha": "b" * 40,
            "run_id": "123",
            "run_attempt": "1",
            "workflow_artifact": "ci-imgs-123.1",
        },
        "identity": {
            "ecosystem": "container",
            "name": "docker.io/rapidsai/citestwheel",
            "version": "26.12",
            "build": tag,
            "platform": "linux/" + architecture,
        },
        "archive": {
            key: inspected[key]
            for key in (
                "transport_sha256",
                "oci_index_sha256",
                "runnable_manifest_sha256",
                "config_sha256",
                "layer_sha256s",
            )
        },
    }


def test_single_platform_handoff_requires_exact_train_identity_and_archive(tmp_path):
    archive = _single_platform_archive(tmp_path)
    handoff = _handoff_for_archive(archive)
    identity = dict(handoff["identity"])

    REHEARSAL.validate_ci_image_import_handoff(handoff, "a" * 64, identity)
    with pytest.raises(ValueError, match="authenticated release train"):
        REHEARSAL.validate_ci_image_import_handoff(handoff, "c" * 64, identity)
    with pytest.raises(ValueError, match="frozen request"):
        REHEARSAL.validate_ci_image_import_handoff(
            handoff, "a" * 64, {**identity, "name": "docker.io/rapidsai/ci-conda"}
        )
    with pytest.raises(ValueError, match="unsupported or missing fields"):
        REHEARSAL.validate_ci_image_import_handoff(
            {**handoff, "trusted": True}, "a" * 64, identity
        )
    with pytest.raises(ValueError, match="unsupported"):
        REHEARSAL.validate_ci_image_import_handoff(
            {**handoff, "schema_version": True}, "a" * 64, identity
        )

    changed = json.loads(json.dumps(handoff))
    changed["archive"]["runnable_manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="runnable_manifest_sha256 does not match"):
        REHEARSAL.import_single_platform_oci_archive(
            "unused",
            archive,
            changed,
            "a" * 64,
            identity,
            tmp_path,
            "http://127.0.0.1:5000",
            "127.0.0.1:5000/pub01-aa/fixture-import:one",
            tmp_path / "auth.json",
            tmp_path,
        )


@pytest.mark.parametrize(
    "field", ["transport_sha256", "oci_index_sha256", "config_sha256"]
)
def test_single_platform_handoff_rejects_archive_digest_mismatch_before_registry(
    tmp_path, field
):
    archive = _single_platform_archive(tmp_path)
    handoff = _handoff_for_archive(archive)
    handoff["archive"][field] = "0" * 64

    expected_error = (
        "does not match the handoff"
        if field == "transport_sha256"
        else field + " does not match"
    )
    with pytest.raises(ValueError, match=expected_error):
        REHEARSAL.import_single_platform_oci_archive(
            "unused",
            archive,
            handoff,
            "a" * 64,
            handoff["identity"],
            tmp_path,
            "http://127.0.0.1:5000",
            "127.0.0.1:5000/pub01-aa/fixture-import:one",
            tmp_path / "auth.json",
            tmp_path,
        )


@pytest.mark.parametrize("field", ["runnable_manifest_sha256", "layer_sha256s"])
def test_single_platform_handoff_rejects_manifest_and_layer_digest_mismatch(
    tmp_path, field
):
    archive = _single_platform_archive(tmp_path)
    handoff = _handoff_for_archive(archive)
    handoff["archive"][field] = ["0" * 64] if field == "layer_sha256s" else "0" * 64

    with pytest.raises(ValueError, match=field + " does not match"):
        REHEARSAL.import_single_platform_oci_archive(
            "unused",
            archive,
            handoff,
            "a" * 64,
            handoff["identity"],
            tmp_path,
            "http://127.0.0.1:5000",
            "127.0.0.1:5000/pub01-aa/fixture-import:one",
            tmp_path / "auth.json",
            tmp_path,
        )


def test_single_platform_import_checks_archive_platform_against_external_identity(
    tmp_path,
):
    archive = _single_platform_archive(tmp_path, architecture="amd64")
    handoff = _handoff_for_archive(archive, architecture="amd64")
    arm_identity = dict(handoff["identity"])
    arm_identity["platform"] = "linux/arm64"
    arm_identity["build"] = arm_identity["build"].removesuffix("amd64") + "arm64"
    handoff["identity"] = arm_identity

    with pytest.raises(ValueError, match="archive platform"):
        REHEARSAL.import_single_platform_oci_archive(
            "unused",
            archive,
            handoff,
            "a" * 64,
            arm_identity,
            tmp_path,
            "http://127.0.0.1:5000",
            "127.0.0.1:5000/pub01-aa/fixture-import:one",
            tmp_path / "auth.json",
            tmp_path,
        )


def test_single_platform_archive_accepts_gzip_transport_and_preserves_transport_digest(
    tmp_path,
):
    plain = _single_platform_archive(tmp_path)
    compressed = tmp_path / "producer-shaped-single.tar.gz"
    with plain.open("rb") as source, gzip.open(compressed, "wb") as output:
        REHEARSAL.shutil.copyfileobj(source, output)
    transport_sha = hashlib.sha256(compressed.read_bytes()).hexdigest()

    report = REHEARSAL.inspect_single_platform_oci_archive(
        compressed, transport_sha, tmp_path
    )

    assert report["transport_sha256"] == transport_sha
    assert report["platform"] == "linux/arm64"


def test_single_platform_prepare_rejects_bad_hash_truncation_and_expansion_cap(
    tmp_path, monkeypatch
):
    plain = _single_platform_archive(tmp_path)
    compressed = tmp_path / "bounded.tar.gz"
    with plain.open("rb") as source, gzip.open(compressed, "wb") as output:
        REHEARSAL.shutil.copyfileobj(source, output)
    digest = hashlib.sha256(compressed.read_bytes()).hexdigest()
    original = set(tmp_path.iterdir())
    with pytest.raises(ValueError, match="changed after handoff"):
        REHEARSAL._prepare_oras_tar(compressed, "0" * 64, tmp_path)
    assert set(tmp_path.iterdir()) == original

    monkeypatch.setattr(REHEARSAL, "MAX_SINGLE_PLATFORM_CONTENT_BYTES", 16)
    with pytest.raises(ValueError, match="decompressed OCI archive exceeds"):
        REHEARSAL._prepare_oras_tar(compressed, digest, tmp_path)
    assert set(tmp_path.iterdir()) == original

    truncated = tmp_path / "truncated.tar.gz"
    truncated.write_bytes(compressed.read_bytes()[:-4])
    with pytest.raises(ValueError, match="safely copied"):
        REHEARSAL._prepare_oras_tar(
            truncated, hashlib.sha256(truncated.read_bytes()).hexdigest(), tmp_path
        )
    assert set(tmp_path.iterdir()) == original | {truncated}


@pytest.mark.parametrize(
    "reference",
    [
        "docker.io/rapidsai/base:latest",
        "localhost:5000/pub01-aa/fixture:amd64",
        "127.0.0.1:5000/other/fixture:amd64",
        "127.0.0.1:65536/pub01-aa/fixture:amd64",
    ],
)
def test_external_or_unreserved_image_reference_rejected(reference):
    with pytest.raises(ValueError):
        REHEARSAL.local_reference(reference)


def test_import_reference_is_reserved_and_loopback_only():
    assert (
        REHEARSAL.local_reference("127.0.0.1:5000/pub01-aa/fixture-import:multi")
        == "127.0.0.1:5000/pub01-aa/fixture-import:multi"
    )


@pytest.mark.parametrize(
    "member_name",
    ["./oci-layout", "../oci-layout", "/oci-layout", "blobs//sha256/example"],
)
def test_oci_archive_rejects_noncanonical_or_unsafe_member_paths(tmp_path, member_name):
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as bundle:
        info = tarfile.TarInfo(member_name)
        info.size = 1
        bundle.addfile(info, io.BytesIO(b"x"))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="unsafe or duplicate path"):
        REHEARSAL.inspect_oci_archive(
            archive, digest, ["linux/amd64", "linux/arm64"], tmp_path
        )


def test_oci_archive_rejects_normalized_duplicate_paths(tmp_path):
    archive = tmp_path / "duplicate.tar"
    with tarfile.open(archive, "w") as bundle:
        for name in ("oci-layout", "./oci-layout"):
            info = tarfile.TarInfo(name)
            info.size = 1
            bundle.addfile(info, io.BytesIO(b"x"))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="unsafe or duplicate path"):
        REHEARSAL.inspect_oci_archive(
            archive, digest, ["linux/amd64", "linux/arm64"], tmp_path
        )


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_oci_archive_rejects_links(tmp_path, member_type):
    archive = tmp_path / "link.tar"
    with tarfile.open(archive, "w") as bundle:
        info = tarfile.TarInfo("oci-layout")
        info.type = member_type
        info.linkname = "../../outside"
        bundle.addfile(info)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="regular files"):
        REHEARSAL.inspect_oci_archive(
            archive, digest, ["linux/amd64", "linux/arm64"], tmp_path
        )


def test_oci_import_rejects_digest_destination_before_registry_access(tmp_path):
    with pytest.raises(ValueError, match="destinations must use a tag"):
        REHEARSAL.import_oci_archive(
            "unused",
            tmp_path / "missing.tar",
            "0" * 64,
            ["linux/amd64", "linux/arm64"],
            tmp_path,
            "http://127.0.0.1:5000",
            "127.0.0.1:5000/pub01-aa/fixture-import@sha256:" + "0" * 64,
            tmp_path / "auth.json",
            tmp_path,
        )


def test_readback_http_redirect_rejected_without_contacting_target():
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(self.path)
            self.send_response(307)
            self.send_header(
                "Location", f"http://127.0.0.1:{self.server.server_port}/target"
            )
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ValueError, match="redirects are rejected"):
            REHEARSAL.registry_request(f"http://127.0.0.1:{server.server_port}", "/v2/")
        assert requests == ["/v2/"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    "origin",
    ["https://registry-1.docker.io", "http://localhost:5000", "http://127.0.0.1:65536"],
)
def test_external_readback_origin_rejected_before_request(origin):
    with pytest.raises(ValueError):
        REHEARSAL.registry_request(origin, "/v2/")
