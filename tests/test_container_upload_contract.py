# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Actual authenticated registry/Docker roundtrip and local destination guards."""

import http.server
import importlib.util
from pathlib import Path
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
