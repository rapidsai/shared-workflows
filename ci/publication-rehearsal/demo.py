# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""One command: publish to four local dummy services, retrieve, install and use."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

SPEC = importlib.util.spec_from_file_location(
    "demo_driver", Path(__file__).with_name("run_local.py")
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)
PAYLOAD = DRIVER.load("demo_payload", "ci/publication-rehearsal/usable_payloads.py")


def rehearse(
    output,
    *,
    checkout=None,
    maven_cache=None,
    runtime_image="python:3.12-slim",
    conda_python=None,
    pixi=None,
):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise DRIVER.RehearsalError("demo output must be a new directory")
    output = output.absolute().parent.resolve(strict=True) / output.name
    output.mkdir(mode=0o700)
    output = DRIVER.private_root(output)
    for executable in (
        "git",
        "mvn",
        "java",
        "javac",
        "jar",
        "docker",
        "oras",
        "gpg",
        "gpgv",
        "gpgconf",
        "htpasswd",
        "curl",
        "jq",
        "bash",
    ):
        if shutil.which(executable) is None:
            raise DRIVER.RehearsalError(
                "missing demo tool: " + executable + "; use the supplied Pixi manifest"
            )
    if checkout is None:
        checkout = output / "kitmaker-source"
        if shutil.which("gh") is None:
            raise DRIVER.RehearsalError(
                "private SDK bootstrap requires authenticated gh or --kitmaker-checkout"
            )
        source_environment = {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
        # A supplied read-only token is available only during the fixed source GET.
        if os.environ.get("GH_TOKEN"):
            source_environment["GH_TOKEN"] = os.environ["GH_TOKEN"]
        DRIVER.execute(["git", "init", checkout], environment=source_environment)
        DRIVER.execute(
            [
                "git",
                "-C",
                checkout,
                "remote",
                "add",
                "origin",
                "https://github.com/rapidsai/release-scripts.git",
            ],
            environment=source_environment,
        )
        DRIVER.execute(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "credential.helper=!gh auth git-credential",
                "-C",
                checkout,
                "fetch",
                "--depth=1",
                "origin",
                DRIVER.KITMAKER_REVISION,
            ],
            environment=source_environment,
            timeout=180,
        )
        DRIVER.execute(
            ["git", "-C", checkout, "checkout", "--detach", "FETCH_HEAD"],
            environment=source_environment,
        )
    checkout = DRIVER.verify_kitmaker(checkout)
    if maven_cache is None:
        maven_cache = PAYLOAD.bootstrap_maven(output / "maven-tool-bootstrap")
    with tempfile.TemporaryDirectory(prefix="dummy-client-") as temporary:
        client = PAYLOAD.docker_client(Path(temporary))
        for image in (
            runtime_image,
            "dockerhub.nvidia.com/library/registry@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e",
        ):
            # Read-only image dependency bootstrap; no registry credential inherited.
            try:
                DRIVER.execute(client + ["image", "inspect", image])
            except DRIVER.RehearsalError:
                DRIVER.execute(
                    client + ["pull", "--platform=linux/amd64", image], timeout=240
                )
        source_image = json.loads(
            DRIVER.execute(client + ["image", "inspect", runtime_image])
        )[0]["Id"]
    directory = output / "rehearsal"
    directory.mkdir(mode=0o700)
    plan_sha = DRIVER.prepare(directory, usable_image=source_image)
    result = DRIVER.run(
        directory,
        plan_sha,
        checkout,
        conda_python=conda_python or Path(sys.executable),
        pixi=pixi or Path(shutil.which("pixi")),
        maven_cache=Path(maven_cache),
    )
    if (
        not result["families"]["conda"]["channel_install_exercised"]
        or not result["families"]["maven"]["usability"]
        or not result["families"]["oci"]["image_execution"]
    ):
        raise DRIVER.RehearsalError(
            "dummy publish demo lacks installation/use evidence"
        )
    return {
        "completion": str(directory / "completion.json"),
        "completion_sha256": DRIVER.sha(directory / "completion.json"),
        "plan_sha256": plan_sha,
        "local_dummy_endpoints_only": True,
        "kitmaker_worker_simulated": True,
        "runtime_source_image_id": source_image,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--kitmaker-checkout", type=Path)
    parser.add_argument("--maven-cache", type=Path)
    parser.add_argument("--runtime-image", default="python:3.12-slim")
    parser.add_argument("--conda-python", type=Path)
    parser.add_argument("--pixi", type=Path)
    args = parser.parse_args()
    try:
        result = rehearse(
            args.output,
            checkout=args.kitmaker_checkout,
            maven_cache=args.maven_cache,
            runtime_image=args.runtime_image,
            conda_python=args.conda_python,
            pixi=args.pixi,
        )
    except (ValueError, OSError) as error:
        parser.exit(1, "dummy publication rehearsal failed: " + str(error) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
