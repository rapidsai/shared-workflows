# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise Anaconda's actual stage/multipart/commit client on loopback."""

import base64
import contextlib
from email.parser import BytesParser
from email.policy import default
import hashlib
import http.server
import json
import os
from pathlib import Path
import subprocess  # nosec B404 -- invoke the fixed local contract harness.
import sys
import threading
from urllib.parse import unquote, urlsplit

from conda_package_handling.api import create
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "ci/conda-publish/local_rehearsal.py"


@contextlib.contextmanager
def _service(mode="success", *, redirect=None):
    state = {
        "requests": [],
        "stages": [],
        "objects": {},
        "pending": {},
        "storage_posts": 0,
        "commit_attempts": 0,
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _reply(self, status, body=None, *, location=None, raw=None):
            payload = raw if raw is not None else json.dumps(body or {}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if location:
                self.send_header("Location", location)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _request(self):
            path = unquote(urlsplit(self.path).path)
            auth = self.headers.get("Authorization")
            state["requests"].append((self.command, path, auth))
            if mode == "redirect":
                self._reply(307, location=redirect)
                return
            if path != "/storage" and (
                auth != "token fixture-value" or mode == "auth-failure"
            ):
                self._reply(401, {"error": "synthetic authentication rejection"})
                return
            if self.command == "HEAD" and path == "/":
                self._reply(200)
            elif path == "/user":
                self._reply(200, {"login": "fixture-user"})
            elif path.startswith("/dist/"):
                key = path.removeprefix("/dist/")
                self._reply(
                    200 if key in state["objects"] else 404, {"error": "not found"}
                )
            elif path.startswith("/package/"):
                self._reply(200, {"package_types": ["conda"], "files": []})
            elif path.startswith("/release/"):
                self._reply(200, {"distributions": []})
            elif path.startswith("/stage/") and self.command == "POST":
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                key = path.removeprefix("/stage/")
                state["stages"].append(payload)
                state["pending"][key] = None
                storage_url = (
                    redirect
                    if mode == "foreign-storage"
                    else f"http://127.0.0.1:{self.server.server_port}/storage"
                )
                self._reply(
                    200,
                    {
                        "post_url": storage_url,
                        "form_data": {"key": key},
                        "dist_id": key,
                    },
                )
            elif path == "/storage" and self.command == "POST":
                state["storage_posts"] += 1
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                multipart = BytesParser(policy=default).parsebytes(
                    f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode()
                    + raw
                )
                fields = {
                    part.get_param(
                        "name", header="content-disposition"
                    ): part.get_payload(decode=True)
                    for part in multipart.iter_parts()
                }
                key = fields["key"].decode()
                data = fields["file"]
                assert fields["Content-Length"] == str(len(data)).encode()
                assert fields["Content-MD5"] == base64.b64encode(
                    hashlib.md5(data, usedforsecurity=False).digest()
                )
                state["pending"][key] = data
                self._reply(201, raw=b"")
            elif path.startswith("/commit/") and self.command == "POST":
                key = path.removeprefix("/commit/")
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                assert payload == {"dist_id": key}
                state["commit_attempts"] += 1
                if mode == "commit-failure" or (
                    mode == "commit-failure-once" and state["commit_attempts"] == 1
                ):
                    self._reply(400, {"error": "synthetic incomplete commit"})
                else:
                    state["objects"][key] = state["pending"].pop(key)
                    self._reply(
                        200,
                        {
                            "url": f"http://127.0.0.1:{self.server.server_port}/download/{key}"
                        },
                    )
            elif path.startswith("/download/"):
                key = path.removeprefix("/download/")
                if key in state["objects"]:
                    self._reply(200, raw=state["objects"][key])
                else:
                    self._reply(404, {"error": "not committed"})
            else:
                self._reply(404, {"error": "unexpected fixture operation"})

        do_HEAD = _request
        do_GET = _request
        do_POST = _request

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _package(tmp_path, extension=".conda", content=b"immutable fixture"):
    source = tmp_path / "source"
    (source / "info").mkdir(parents=True, exist_ok=True)
    (source / "info/index.json").write_text(
        json.dumps(
            {
                "name": "conda-fixture",
                "version": "26.12.00",
                "build": "h0_0",
                "build_number": 0,
                "subdir": "noarch",
                "noarch": "generic",
                "depends": [],
            }
        )
    )
    (source / "info/about.json").write_text(
        json.dumps({"summary": "synthetic local fixture", "license": "BSD-3-Clause"})
    )
    (source / "payload.txt").write_bytes(content)
    name = "conda-fixture-26.12.00-h0_0" + extension
    create(
        str(source),
        ["info/index.json", "info/about.json", "payload.txt"],
        name,
        out_folder=str(tmp_path),
    )
    return tmp_path / name


def _run(package, url, *, expected=None, proxy=None, netrc=None):
    digest = expected or hashlib.sha256(package.read_bytes()).hexdigest()
    environment = {"PATH": os.environ["PATH"]}
    if proxy:
        environment["HTTP_PROXY"] = proxy
        environment["ALL_PROXY"] = proxy
    if netrc:
        environment["NETRC"] = str(netrc)
    result = subprocess.run(  # nosec B603 -- fixed local script, clean environment, synthetic auth only.
        [
            sys.executable,
            str(SCRIPT),
            "--base-url",
            url,
            "--package",
            str(package),
            "--sha256",
            digest,
            "--download-path",
            f"/download/fixture-user/conda-fixture/26.12.00/noarch/{package.name}",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result


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
