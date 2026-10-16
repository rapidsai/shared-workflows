# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Simulated Portal worker/index; actual SDK and pip clients are exercised."""

from contextlib import contextmanager
import base64
import hashlib
import http.server
import json
import threading
from urllib.parse import urlsplit
import zipfile

import requests

UUID = "550e8400-e29b-41d4-a716-446655440000"
INDEX_AUTH = "Basic Zml4dHVyZS1pbmRleDpmaXh0dXJlLXZhbHVl"


def wheel(path):
    files = {
        "fixture_one/__init__.py": b'__version__ = "26.12.0"\ndef message():\n    return "local-rehearsal"\n',
        "fixture_one-26.12.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: fixture-one\nVersion: 26.12.0\nRequires-Python: >=3.8\n",
        "fixture_one-26.12.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: local-rehearsal\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = (
        "".join(
            name
            + ",sha256="
            + base64.urlsafe_b64encode(hashlib.sha256(content).digest())
            .decode()
            .rstrip("=")
            + ","
            + str(len(content))
            + "\n"
            for name, content in files.items()
        )
        + "fixture_one-26.12.0.dist-info/RECORD,,\n"
    )
    files["fixture_one-26.12.0.dist-info/RECORD"] = record.encode()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


@contextmanager
def service(package, expected_sha256, *, delivery_status="completed"):
    if package.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("synthetic wheel exceeds fixture size bound")
    state = {"events": [], "uuid": None, "delivery": None, "worker_simulated": True}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, content, kind="application/json"):
            body = (
                content if isinstance(content, bytes) else json.dumps(content).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            auth = self.headers.get("Authorization")
            if path.startswith("/api/v0/"):
                if auth != "Bearer fixture-value":
                    self.reply(401, {})
                elif path == "/api/v0/projects":
                    state["events"].append("sdk-project-discovery")
                    self.reply(200, [{"id": 1, "name": "fixture-one"}])
                elif path == "/api/v0/status/" + UUID and state["uuid"]:
                    state["events"].append("sdk-status")
                    self.reply(
                        200, {"uuid": UUID, "status": delivery_status, "jobs": []}
                    )
                else:
                    self.reply(404, {})
            elif path == "/artifactory/local/" + package.name:
                if auth != "Bearer fixture-source-value":
                    self.reply(401, {})
                else:
                    state["events"].append("simulated-worker-source-fetch")
                    self.reply(200, package.read_bytes(), "application/octet-stream")
            elif auth != INDEX_AUTH:
                self.reply(401, {})
            elif path == "/simple/fixture-one/" and state["delivery"] is not None:
                state["events"].append("pip-simple-index")
                html = f'<a href="/packages/{package.name}#sha256={expected_sha256}">{package.name}</a>'
                self.reply(200, html.encode(), "text/html")
            elif path == "/packages/" + package.name and state["delivery"] is not None:
                state["events"].append("pip-wheel-download")
                self.reply(200, state["delivery"], "application/octet-stream")
            else:
                self.reply(404, {})

        def do_POST(self):
            if (
                self.headers.get("Authorization") != "Bearer fixture-value"
                or self.path != "/api/v0/projects/1/releases"
            ):
                self.reply(401, {})
                return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 64 * 1024:
                self.reply(413, {})
                return
            body = json.loads(self.rfile.read(size))
            origin = f"http://127.0.0.1:{self.server.server_port}"
            expected_url = origin + "/artifactory/local/" + package.name
            jobs = body.get("payload")
            if (
                body.get("project_name") != "fixture-one"
                or not isinstance(jobs, list)
                or len(jobs) != 1
                or not isinstance(jobs[0], dict)
                or {key: jobs[0].get(key) for key in ("job_type", "url", "upload")}
                != {
                    "job_type": "wheel-release-job",
                    "url": expected_url,
                    "upload": True,
                }
            ):
                self.reply(400, {})
                return
            state["events"].append("sdk-release-post")
            # Deliberate simulation of an unimplemented hosted Kitmaker worker.
            # It cannot select another origin, and its input hash predates POST.
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    expected_url,
                    headers={"Authorization": "Bearer fixture-source-value"},
                    timeout=3,
                    allow_redirects=False,
                    stream=True,
                )
                data = bytearray()
                with response:
                    if response.status_code != 200:
                        self.reply(409, {})
                        return
                    for chunk in response.iter_content(64 * 1024):
                        data.extend(chunk)
                        if len(data) > package.stat().st_size:
                            self.reply(413, {})
                            return
                if hashlib.sha256(data).hexdigest() != expected_sha256:
                    self.reply(409, {})
                    return
                state["delivery"] = bytes(data)
            state["uuid"] = UUID
            self.reply(200, {"release_uuid": UUID})

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
            raise RuntimeError("local Kitmaker fixture did not stop")
