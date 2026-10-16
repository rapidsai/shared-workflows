# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Actual four-client fixture composition; never a hosted-delivery claim."""

from functools import partial
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "four_family_rehearsal", ROOT / "ci/publication-rehearsal/run_local.py"
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


def sdk_source():
    source = os.environ.get("PUB01_KITMAKER_SOURCE")
    if not source:
        pytest.skip(
            "Cross-repository SDK integration requires explicit PUB01_KITMAKER_SOURCE at reviewed b95debb; no SDK or hosted fallback"
        )
    return Path(source)


def test_real_four_family_plan_clients_retrieval_install_and_completion(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KITMAKER_API_TOKEN", "operator-placeholder-never-forward")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "operator-placeholder-never-forward")
    monkeypatch.setenv("HTTP_PROXY", "http://external.invalid:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://external.invalid:9")
    expected = DRIVER.prepare(tmp_path)
    report = DRIVER.run(tmp_path, expected, sdk_source())
    assert set(report["families"]) == {"conda", "python-kitmaker", "maven", "oci"}
    assert report["local_fixture_only"] is True
    assert report["production_authorization"] is False
    assert report["kitmaker_worker_simulated"] is True
    assert report["families"]["conda"]["identical_retry_upload_count"] == 1
    python = report["families"]["python-kitmaker"]
    assert python["worker_delivery"] == "SIMULATED"
    assert python["pip_offline_install_import"] == "26.12.0:local-rehearsal"
    assert "pip-simple-index" in python["events"]
    assert "pip-wheel-download" in python["events"]
    assert len(report["families"]["maven"]["artifact_sha256s"]) == 24
    oci = report["families"]["oci"]
    assert (
        oci["archive"]["transport_sha256"]
        == json.loads((tmp_path / "plan.json").read_text())["inputs"][
            "oci/input.tar.gz"
        ]
    )
    assert oci["archive"]["layer_sha256s"][0] == oci["archive"]["layer_sha256s"][2]
    assert oci["cleanup_verified"] is True
    for filename, expected_hash in report["result_sha256s"].items():
        path = tmp_path / "results" / filename
        assert DRIVER.sha(path) == expected_hash
        assert path.stat().st_mode & 0o777 == 0o400
    assert (tmp_path / "completion.json").stat().st_mode & 0o777 == 0o400
    assert not (tmp_path / "python/fixture.netrc").exists()
    text = (tmp_path / "completion.json").read_text()
    assert "fixture-value" not in text
    assert "operator-placeholder-never-forward" not in text
    assert "Authorization" not in text
    before = (tmp_path / "completion.json").read_bytes()
    with pytest.raises(DRIVER.RehearsalError, match="fresh planned"):
        DRIVER.run(tmp_path, expected, sdk_source())
    assert (tmp_path / "completion.json").read_bytes() == before


def test_actual_sdk_pending_status_preserves_partial_receipt_without_completion(
    tmp_path, monkeypatch
):
    expected = DRIVER.prepare(tmp_path)
    original_load = DRIVER.load

    def pending_portal(name, relative):
        module = original_load(name, relative)
        if name == "unified_kitmaker":
            module.service = partial(module.service, delivery_status="pending")
        return module

    monkeypatch.setattr(DRIVER, "load", pending_portal)
    with pytest.raises(DRIVER.RehearsalError, match="status 2"):
        DRIVER.run(tmp_path, expected, sdk_source())
    assert (tmp_path / "results/conda.json").is_file()
    assert not (tmp_path / "results/python-kitmaker.json").exists()
    assert not (tmp_path / "completion.json").exists()


@pytest.mark.parametrize("declared", [True, False])
def test_family_readback_enforces_declared_and_streamed_size(declared):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            if declared:
                self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"data")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(DRIVER.RehearsalError, match="size bound"):
            DRIVER.fetch(
                f"http://127.0.0.1:{server.server_port}",
                "/fixture",
                hashlib.sha256(b"data").hexdigest(),
                auth="fixture-only",
                maximum_bytes=1,
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_planned_oci_digest_mismatch_precedes_any_client_operation(
    tmp_path, monkeypatch
):
    oci = DRIVER.load("planned_oci_negative", "ci/container-publish/local_rehearsal.py")
    archive = tmp_path / "input"
    archive.write_bytes(b"wrong input")
    monkeypatch.setattr(
        oci, "command", lambda *_args, **_kwargs: pytest.fail("client must not run")
    )
    with pytest.raises(ValueError, match="before any registry"):
        oci.rehearse(single_archive_input=archive, single_archive_sha256="0" * 64)
