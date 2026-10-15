# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Actual authenticated registry/Docker roundtrip and local destination guards."""

import hashlib
import http.server
import io
import importlib.util
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
    assert report["cleanup_verified"]


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
