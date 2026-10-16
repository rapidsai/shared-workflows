# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real indexed-channel solve/install for one synthetic loopback Conda package.

The service is owned here, not an arbitrary hosted endpoint. This is not an
egress policy for a general Conda client or a hosted-service authorization gate.
"""

import base64
from contextlib import contextmanager
import hashlib
import http.server
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess  # nosec B404 -- explicitly selected local clients, fixed argv.
import tempfile
import threading


PACKAGE = "conda-fixture-26.12.00-h0_0.conda"
PAYLOAD_SHA256 = hashlib.sha256(b"immutable fixture").hexdigest()
BASIC = "Basic " + base64.b64encode(b"fixture-user:fixture-value").decode()


class ChannelError(ValueError):
    """Sanitized local-client/metadata failure; no raw output or URLs escape."""


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def executable(value):
    if not isinstance(value, (str, Path)):
        raise ChannelError("an explicit absolute executable path is required")
    path = Path(value)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ChannelError("an explicit absolute executable path is required")
    return str(path)


def command(argv, *, environment=None):
    try:
        result = subprocess.run(  # nosec B603 -- fixed local indexer/Pixi argv; no shell or ambient credentials.
            [str(value) for value in argv],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
            env={"PATH": os.environ["PATH"], **(environment or {})},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ChannelError("local channel client could not complete") from None
    if result.returncode:
        raise ChannelError("local channel client failed")
    return result.stdout


def target_subdir():
    target = {
        ("Darwin", "arm64"): "osx-arm64",
        ("Darwin", "x86_64"): "osx-64",
        ("Linux", "x86_64"): "linux-64",
        ("Linux", "aarch64"): "linux-aarch64",
    }.get((platform.system(), platform.machine()))
    if target is None:
        raise ChannelError("unsupported local target for the synthetic install")
    return target


def index(package, expected_sha256, root, python):
    python = executable(python)
    package, root = Path(package), Path(root)
    if (
        package.name != PACKAGE
        or package.is_symlink()
        or not package.is_file()
        or not isinstance(expected_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)
        or digest(package) != expected_sha256
    ):
        raise ChannelError("planned Conda archive bytes differ")
    channel = root / "channel"
    (channel / "noarch").mkdir(parents=True)
    (channel / target_subdir()).mkdir()
    destination = channel / "noarch" / PACKAGE
    shutil.copyfile(package, destination)
    if digest(destination) != expected_sha256:
        raise ChannelError("channel archive copy differs")
    versions = json.loads(
        command(
            [
                python,
                "-I",
                "-B",
                "-c",
                "import importlib.metadata as m,json; print(json.dumps({k:m.version(k) for k in ('conda-index','conda')}))",
            ]
        )
    )
    command(
        [
            python,
            "-I",
            "-B",
            "-m",
            "conda_index",
            channel,
            "--no-current-repodata",
            "--no-zst",
            "--no-bz2",
            "--no-write-shards",
            "--no-html-dependencies",
            "--threads",
            "1",
        ]
    )
    repodata = channel / "noarch/repodata.json"
    record = json.loads(repodata.read_bytes())["packages.conda"][PACKAGE]
    if (
        record["sha256"] != expected_sha256
        or record["size"] != destination.stat().st_size
        or (record["name"], record["version"], record["build"], record["subdir"])
        != ("conda-fixture", "26.12.00", "h0_0", "noarch")
    ):
        raise ChannelError("real index metadata differs from the planned archive")
    return channel, {"versions": versions, "repodata_sha256": digest(repodata)}


@contextmanager
def service(channel):
    """Serve only indexed local files with synthetic Basic authentication."""
    channel = Path(channel).resolve()
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            authenticated = self.headers.get("Authorization") == BASIC
            calls.append({"path": self.path, "authenticated": authenticated})
            status, body = 401, b""
            if authenticated:
                path = self.path.removeprefix("/")
                source = channel / path
                if (
                    re.fullmatch(
                        r"(?:noarch|osx-arm64|osx-64|linux-64|linux-aarch64)/[a-zA-Z0-9_.-]+",
                        path,
                    )
                    and source.is_file()
                    and not source.is_symlink()
                    and source.resolve().is_relative_to(channel)
                    and source.stat().st_size <= 2 * 1024 * 1024
                ):
                    status, body = 200, source.read_bytes()
                else:
                    status = 404
            if status == 200:
                calls[-1]["sha256"] = hashlib.sha256(body).hexdigest()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            if status == 401:
                self.send_header("WWW-Authenticate", 'Basic realm="LocalFixture"')
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise ChannelError("authenticated channel service did not stop")


def solve_install(origin, root, pixi, expected_sha256, *, authentication="valid"):
    """Real one-channel solver/installer; all credentials are fixed fixture data."""
    pixi, root = executable(pixi), Path(root)
    if (
        not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", origin)
        or int(origin.rsplit(":", 1)[1]) > 65535
    ):
        raise ChannelError("channel origin must be owned IPv4 loopback")
    if authentication not in {"valid", "wrong", "anonymous"}:
        raise ChannelError("unsupported synthetic authentication case")
    root.mkdir(mode=0o700)
    auth_file = root / "auth.json"
    auth_file.write_text("{}\n")
    auth_file.chmod(0o600)
    authority = "fixture-user:" + (
        "fixture-value" if authentication == "valid" else "wrong-fixture-value"
    )
    channel = (
        origin
        if authentication == "anonymous"
        else origin.replace("http://", "http://" + authority + "@")
    )
    (root / "pixi.toml").write_text(
        '[workspace]\nname="pub01-conda-fixture"\n'
        + "channels="
        + json.dumps([channel])
        + "\n"
        + "platforms="
        + json.dumps([target_subdir()])
        + "\n"
        + '[dependencies]\nconda-fixture={version="==26.12.00",build="h0_0"}\n'
    )
    try:
        version = command([pixi, "--version"]).strip()
        command(
            [
                pixi,
                "install",
                "--manifest-path",
                root / "pixi.toml",
                "--no-config",
                "--auth-file",
                auth_file,
                "--no-progress",
                "--concurrent-downloads",
                "1",
            ],
            environment={"PIXI_CACHE_DIR": str(root / "cache")},
        )
    finally:
        auth_file.unlink()
    prefix = root / ".pixi/envs/default"
    records = list((prefix / "conda-meta").glob("*.json"))
    if len(records) != 1:
        raise ChannelError("installed set is not the exact synthetic package")
    record = json.loads(records[0].read_bytes())
    if (
        record["sha256"] != expected_sha256
        or (record["name"], record["version"], record["build"], record["subdir"])
        != ("conda-fixture", "26.12.00", "h0_0", "noarch")
        or digest(prefix / "payload.txt") != PAYLOAD_SHA256
    ):
        raise ChannelError("installed archive identity or payload differs")
    # The caller independently hashes the authenticated archive response and
    # compares it with this installed package record and payload.
    return {
        "pixi_version": version,
        "target_subdir": target_subdir(),
        "installed_payload_sha256": digest(prefix / "payload.txt"),
        "installed_archive_sha256": record["sha256"],
        "installed_identity": {
            k: record[k] for k in ("name", "version", "build", "subdir")
        },
        "lock_sha256": digest(root / "pixi.lock"),
    }


def rehearse(package, expected_sha256, root, *, python, pixi):
    """Index verified readback bytes, then solve/install from authenticated HTTP."""
    with tempfile.TemporaryDirectory(prefix="channel-install-", dir=root) as temporary:
        directory = Path(temporary)
        channel, report = index(package, expected_sha256, directory, python)
        with service(channel) as (origin, calls):
            installed = solve_install(
                origin, directory / "consumer", pixi, expected_sha256
            )
        if not any(
            item["path"] == "/noarch/" + PACKAGE
            and item["authenticated"]
            and item.get("sha256") == expected_sha256
            for item in calls
        ):
            raise ChannelError("no authenticated archive retrieval was observed")
        return {
            **report,
            **installed,
            "archive_sha256": expected_sha256,
            "channel_index_solve_install": "verified",
            "one_loopback_channel_only": True,
            "requests": calls,
            "limits": [
                "synthetic authenticated channel; no hosted ACL/indexing acceptance"
            ],
        }
