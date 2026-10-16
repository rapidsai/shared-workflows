# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real curl/client behavior against a synthetic, loopback-only staging service."""

import base64
import contextlib
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 -- required to test the real reviewed shell client.
import threading
from urllib.parse import parse_qs, urlsplit


SCRIPT = (
    Path(__file__).resolve().parents[2] / "ci/maven-publish/maven_central_publish.sh"
)
DEPLOYMENT = "00000000-0000-0000-0000-000000000001"
# Synthetic test values only. Never read an operator's publisher credentials.
AUTH = base64.b64encode(b"fixture-user:fixture-value").decode()


@contextlib.contextmanager
def service(mode="success", redirect=None):
    state = {"requests": [], "uploads": {}, "handed_off": False}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _reply(self, status, body=None, location=None, raw=None):
            self.send_response(status)
            if location is not None:
                self.send_header("Location", location)
            if status == 401 and self.path.startswith("/repository/"):
                self.send_header("WWW-Authenticate", 'Basic realm="LocalFixture"')
            payload = raw if raw is not None else json.dumps(body or {}).encode()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _request(self):
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            expected_auth = (
                "Basic "
                if self.command == "PUT" or parts.path.startswith("/repository/")
                else "Bearer "
            ) + AUTH
            state["requests"].append(
                (self.command, parts.path, query, self.headers.get("Authorization"))
            )
            if (
                mode == "auth-failure"
                or self.headers.get("Authorization") != expected_auth
            ):
                self._reply(401, {"error": "synthetic authentication rejection"})
            elif mode == "redirect":
                self._reply(307, location=redirect)
            elif mode in {"invalid-search", "null-search"}:
                self._reply(
                    200,
                    {"repositories": "invalid" if mode == "invalid-search" else None},
                )
            elif self.command == "GET" and parts.path.startswith("/repository/"):
                coordinate = (
                    "/service/local/staging/deploy/maven2/"
                    + parts.path.removeprefix("/repository/")
                )
                if state["handed_off"] and coordinate in state["uploads"]:
                    self._reply(200, raw=state["uploads"][coordinate])
                else:
                    self._reply(404, {"error": "dummy coordinate missing"})
            elif self.command == "GET" and parts.path in state["uploads"]:
                self._reply(200, raw=state["uploads"][parts.path])
            elif parts.path == "/manual/search/repositories":
                rows = []
                if state["uploads"] or mode == "preexisting":
                    rows = [
                        {
                            "key": "repo-key",
                            "state": "released" if state["handed_off"] else "open",
                            "portal_deployment_id": DEPLOYMENT
                            if state["handed_off"]
                            else "",
                        }
                    ]
                self._reply(200, {"repositories": rows})
            elif self.command == "PUT" and parts.path.startswith(
                "/service/local/staging/deploy/maven2/"
            ):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                state["uploads"][parts.path] = body
                self._reply(
                    400
                    if mode == "partial-upload" and len(state["uploads"]) == 2
                    else 201
                )
            elif (
                parts.path == "/manual/upload/repository/repo-key"
                and self.command == "POST"
            ):
                state["handed_off"] = True
                self._reply(200)
            elif parts.path == "/api/v1/publisher/status" and self.command == "POST":
                status = {
                    "timeout": "PENDING",
                    "published": "PUBLISHED",
                    "failed": "FAILED",
                }.get(mode, "VALIDATED")
                self._reply(200, {"deploymentState": status})
            elif self.command == "DELETE" and parts.path in {
                "/manual/drop/repository/repo-key",
                f"/api/v1/publisher/deployment/{DEPLOYMENT}",
            }:
                self._reply(200)
            else:
                self._reply(404, {"error": "unexpected fixture endpoint"})

        do_GET = _request
        do_POST = _request
        do_PUT = _request
        do_DELETE = _request

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


def package_files(tmp_path):
    artifact = tmp_path / "signed/ai/rapids/example/26.12.0"
    artifact.mkdir(parents=True)
    for suffix in (
        ".pom",
        ".pom.asc",
        ".jar",
        ".jar.asc",
        "-sources.jar",
        "-sources.jar.asc",
        "-javadoc.jar",
        "-javadoc.jar.asc",
    ):
        (artifact / f"example-26.12.0{suffix}").write_bytes(
            f"fixture {suffix}".encode()
        )
    return tmp_path / "signed"


def run(
    tmp_path,
    base_url,
    *,
    auto_drop=True,
    username="fixture-user",
    credential="fixture-value",
    use_prepared=False,
):
    if not use_prepared:
        package_files(tmp_path)
    environment = {
        "PATH": os.environ["PATH"],
        "MAVEN_DEPLOY_USERNAME": username,
        "MAVEN_DEPLOY_TOKEN": credential,
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
    }
    bundle = tmp_path / "bundle.zip"
    result = subprocess.run(  # nosec B603 -- fixed reviewed script; endpoint validated as loopback by the script.
        [
            shutil.which("bash"),
            str(SCRIPT),
            "--input",
            str(tmp_path / "signed"),
            "--group-id",
            "ai.rapids",
            "--artifact-id",
            "example",
            "--version",
            "26.12.0",
            "--output-bundle",
            str(bundle),
            "--auto-drop",
            str(auto_drop).lower(),
            "--local-contract-base-url",
            base_url,
            "--local-contract-poll-timeout",
            "2",
        ],
        env=environment,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    return result, bundle
