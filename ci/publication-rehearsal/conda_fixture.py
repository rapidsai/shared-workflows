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

SCRIPT = Path(__file__).resolve().parents[2] / "ci/conda-publish/local_rehearsal.py"


@contextlib.contextmanager
def service(mode="success", *, redirect=None):
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
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("local fixture service did not stop")


def package(tmp_path, extension=".conda", content=b"immutable fixture"):
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


def run(package, url, *, expected=None, proxy=None, netrc=None):
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
