# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run the real Anaconda upload client with synthetic, origin-bound local access.

This is a local contract harness, not a private service or production publisher.
The transport guard confines all actual client HTTP calls to the fixture origin.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--download-path", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", args.base_url):
        raise ValueError("fixture URL must be http://127.0.0.1:<port> only")
    origin = urlsplit(args.base_url)
    try:
        port = origin.port
    except ValueError:
        raise ValueError("invalid fixture port") from None
    if not 1 <= port <= 65535:
        raise ValueError("invalid fixture port")
    if not re.fullmatch(r"[0-9a-f]{64}", args.sha256):
        raise ValueError("expected package SHA-256 is required")
    if hashlib.sha256(args.package.read_bytes()).hexdigest() != args.sha256:
        raise ValueError("input package does not match the expected SHA-256")
    if (
        not re.fullmatch(
            r"/download/fixture-user/[A-Za-z0-9_.+/-]+", args.download_path
        )
        or ".." in args.download_path
    ):
        raise ValueError("readback path must identify a fixture-user package")
    if importlib.metadata.version("anaconda-client") != "1.15.0":
        raise ValueError("local contracts require anaconda-client==1.15.0")

    original_send = requests.Session.send

    def origin_bound_send(session, request, **kwargs):
        target = urlsplit(request.url)
        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
            print("fixture transport rejected an unapproved origin", file=sys.stderr)
            raise ValueError("fixture transport rejected an unapproved origin")
        # Requests normally follows redirects, including stage/storage responses.
        # The harness rejects these responses instead of following any Location.
        kwargs["allow_redirects"] = False
        kwargs["proxies"] = {}
        kwargs["timeout"] = 5
        session.trust_env = False
        response = original_send(session, request, **kwargs)
        if 300 <= response.status_code < 400:
            response.close()
            print("fixture transport rejected HTTP redirect", file=sys.stderr)
            raise ValueError("fixture transport rejected HTTP redirect")
        return response

    requests.Session.send = origin_bound_send
    try:
        with tempfile.TemporaryDirectory(prefix="conda-local-contract-") as directory:
            config_root = Path(directory)
            (config_root / "data").mkdir()
            (config_root / "data/config.yaml").write_text(
                json.dumps({"sites": {"contract": {"url": args.base_url}}})
            )
            os.environ["BINSTAR_CONFIG_DIR"] = directory
            # Import after selecting an isolated config. Never load operator tokens.
            from binstar_client.scripts.cli import main as client_main  # noqa: PLC0415

            client_main(
                args=[
                    "--site",
                    "contract",
                    "-t",
                    "fixture-value",
                    "upload",
                    "--label",
                    "candidate",
                    "--skip-existing",
                    "--no-progress",
                    str(args.package),
                ],
                exit_=False,
                allow_plugin_main=False,
            )
            response = requests.get(
                args.base_url + args.download_path,
                headers={"Authorization": "token fixture-value"},
                timeout=5,
            )
            response.raise_for_status()
            actual = hashlib.sha256(response.content).hexdigest()
            if actual != args.sha256:
                raise ValueError(
                    "destination package digest conflict after upload or skip-existing"
                )
            if hashlib.sha256(args.package.read_bytes()).hexdigest() != args.sha256:
                raise ValueError("client changed the immutable input package")
            print(
                json.dumps(
                    {
                        "local_contract_only": True,
                        "sha256": actual,
                        "client_version": importlib.metadata.version("anaconda-client"),
                        "dependencies": {
                            dist.metadata["Name"]: dist.version
                            for dist in importlib.metadata.distributions()
                        },
                    },
                    sort_keys=True,
                )
            )
    finally:
        requests.Session.send = original_send


if __name__ == "__main__":
    main()
