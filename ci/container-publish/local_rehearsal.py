# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real Docker publication roundtrip against an ephemeral authenticated registry."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404 -- fixed Docker/htpasswd argv, no shell or operator credentials.
import tempfile
import time
from urllib.parse import urlsplit
import uuid

import requests

REGISTRY_IMAGE = "dockerhub.nvidia.com/library/registry@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e"
AUTH_PAIR = ("fixture-user", "fixture-value")
MEDIA = "application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json"


def local_reference(reference):
    if not re.fullmatch(
        r"127\.0\.0\.1:[1-9][0-9]{0,4}/pub01-[a-f0-9]+/fixture(?::[a-z0-9-]+|@sha256:[a-f0-9]{64})",
        reference,
    ):
        raise ValueError(
            "only explicitly named loopback fixture references are allowed"
        )
    if urlsplit("http://" + reference).port > 65535:
        raise ValueError("invalid fixture port")
    return reference


def command(argv, *, text=None, check=True):
    result = subprocess.run(  # nosec B603 -- fixed executables/argv constructed within this module.
        argv,
        input=text,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
        env={"PATH": os.environ["PATH"]},
    )
    if check and result.returncode:
        raise RuntimeError("local Docker/tool operation failed: " + result.stderr)
    return result


def registry_request(origin, path, *, auth=AUTH_PAIR):
    if (
        not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", origin)
        or urlsplit(origin).port > 65535
    ):
        raise ValueError("registry origin must be loopback only")
    if not path.startswith("/v2/") or ".." in path or "?" in path or "#" in path:
        raise ValueError("invalid registry API path")
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            origin + path,
            auth=auth,
            headers={"Accept": MEDIA},
            timeout=5,
            allow_redirects=False,
        )
    if 300 <= response.status_code < 400:
        raise ValueError("registry API redirects are rejected")
    return response


def rehearse():
    docker = shutil.which("docker")
    htpasswd = shutil.which("htpasswd")
    if not docker or not htpasswd:
        raise RuntimeError("Docker and bcrypt-capable htpasswd are required")
    host = command(
        [
            docker,
            "context",
            "inspect",
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ]
    ).stdout.strip()
    if not host.startswith("unix://"):
        raise ValueError("only a local Unix Docker socket is allowed")
    plugins = json.loads(
        command(
            [docker, "--host", host, "info", "--format", "{{json .ClientInfo.Plugins}}"]
        ).stdout
    )
    buildx = next(item for item in plugins if item["Name"] == "buildx")
    buildx_path = Path(buildx["Path"]).resolve(strict=True)
    identifier = "pub01-" + uuid.uuid4().hex
    refs = []
    network_created = container_created = False
    receipt = None
    with tempfile.TemporaryDirectory(prefix="pub01-oci-") as directory:
        root = Path(directory)
        config = root / "client"
        config.mkdir()
        (config / "cli-plugins").mkdir()
        (config / "cli-plugins/docker-buildx").symlink_to(buildx_path)
        client = [docker, "--host", host, "--config", str(config)]
        command(
            client + ["image", "inspect", REGISTRY_IMAGE]
        )  # Fail instead of pulling an unreviewed image.
        try:
            command(client + ["network", "create", identifier])
            network_created = True
            auth_file = root / "htpasswd"
            auth_file.write_text(command([htpasswd, "-bnB", *AUTH_PAIR]).stdout)
            auth_file.chmod(0o600)
            command(
                client
                + [
                    "run",
                    "-d",
                    "--pull=never",
                    "--name",
                    identifier,
                    "--network",
                    identifier,
                    "--publish",
                    "127.0.0.1::5000",
                    "--tmpfs",
                    "/var/lib/registry:rw,size=16777216",
                    "--mount",
                    f"type=bind,src={auth_file},dst=/auth/htpasswd,readonly",
                    "-e",
                    "REGISTRY_AUTH=htpasswd",
                    "-e",
                    "REGISTRY_AUTH_HTPASSWD_REALM=LocalFixture",
                    "-e",
                    "REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd",
                    "-e",
                    "REGISTRY_STORAGE_REDIRECT_DISABLE=true",
                    "-e",
                    "OTEL_TRACES_EXPORTER=none",
                    REGISTRY_IMAGE,
                ]
            )
            container_created = True
            for _attempt in range(30):
                mapping = json.loads(
                    command(
                        client
                        + [
                            "inspect",
                            identifier,
                            "--format",
                            "{{json .NetworkSettings.Ports}}",
                        ]
                    ).stdout
                )
                if mapping.get("5000/tcp"):
                    binding = mapping["5000/tcp"][0]
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError(
                    "registry did not receive a loopback port: "
                    + command(client + ["logs", identifier]).stderr
                )
            if binding["HostIp"] != "127.0.0.1":
                raise ValueError("registry is not exclusively bound to loopback")
            endpoint = "127.0.0.1:" + binding["HostPort"]
            origin = "http://" + endpoint
            for _attempt in range(30):
                try:
                    ready = registry_request(origin, "/v2/", auth=None)
                    if ready.status_code == 401:
                        break
                except requests.ConnectionError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("authenticated registry did not become ready")
            if registry_request(origin, "/v2/", auth=None).status_code != 401:
                raise ValueError("anonymous API request unexpectedly allowed")
            if (
                registry_request(
                    origin, "/v2/", auth=("fixture-user", "invalid-fixture-value")
                ).status_code
                != 401
            ):
                raise ValueError("wrong registry authentication unexpectedly allowed")
            failed_login = command(
                client
                + ["login", endpoint, "--username", AUTH_PAIR[0], "--password-stdin"],
                text="invalid-fixture-value\n",
                check=False,
            )
            if failed_login.returncode == 0:
                raise ValueError("wrong registry credential unexpectedly authenticated")
            if not any(
                marker in failed_login.stderr.lower()
                for marker in ("unauthorized", "401")
            ):
                raise ValueError(
                    "wrong credential failed without an authentication rejection"
                )
            command(
                client
                + ["login", endpoint, "--username", AUTH_PAIR[0], "--password-stdin"],
                text=AUTH_PAIR[1] + "\n",
            )
            repo = identifier + "/fixture"
            target = local_reference(endpoint + "/" + repo + ":multi")
            context = root / "context"
            context.mkdir()
            (context / "Dockerfile").write_text(
                "FROM scratch\nCOPY payload.txt /payload.txt\n"
            )
            (context / "payload.txt").write_text(identifier)
            for arch in ("amd64", "arm64"):
                reference = local_reference(endpoint + "/" + repo + ":" + arch)
                refs.append(reference)
                command(
                    client
                    + [
                        "build",
                        "--network=none",
                        "--pull=false",
                        "--provenance=false",
                        "--sbom=false",
                        "--platform",
                        "linux/" + arch,
                        "-t",
                        reference,
                        str(context),
                    ]
                )
                command(client + ["push", reference])
            command(client + ["manifest", "create", "--insecure", target, *refs])
            command(client + ["manifest", "push", "--insecure", target])
            response = registry_request(origin, f"/v2/{repo}/manifests/multi")
            response.raise_for_status()
            digest = "sha256:" + hashlib.sha256(response.content).hexdigest()
            if response.headers["Docker-Content-Digest"] != digest:
                raise ValueError(
                    "registry index digest does not match the retrieved bytes"
                )
            index = response.json()
            platforms = sorted(
                item["platform"]["architecture"] for item in index["manifests"]
            )
            if platforms != ["amd64", "arm64"]:
                raise ValueError("multiarchitecture manifest is incomplete")
            blob_digests = set()
            for item in index["manifests"]:
                manifest = registry_request(
                    origin, f"/v2/{repo}/manifests/{item['digest']}"
                )
                manifest.raise_for_status()
                if (
                    "sha256:" + hashlib.sha256(manifest.content).hexdigest()
                    != item["digest"]
                ):
                    raise ValueError("child manifest digest mismatch")
                for blob in [manifest.json()["config"], *manifest.json()["layers"]]:
                    result = registry_request(
                        origin, f"/v2/{repo}/blobs/{blob['digest']}"
                    )
                    result.raise_for_status()
                    if (
                        "sha256:" + hashlib.sha256(result.content).hexdigest()
                        != blob["digest"]
                    ):
                        raise ValueError("registry blob digest mismatch")
                    blob_digests.add(blob["digest"])
            anonymous = registry_request(
                origin, f"/v2/{repo}/manifests/multi", auth=None
            )
            if anonymous.status_code != 401:
                raise ValueError("anonymous manifest access unexpectedly allowed")
            missing = registry_request(
                origin, f"/v2/{repo}/manifests/sha256:" + "0" * 64
            )
            if (
                missing.status_code != 404
                or missing.json()["errors"][0]["code"] != "MANIFEST_UNKNOWN"
            ):
                raise ValueError("unknown digest did not receive MANIFEST_UNKNOWN/404")
            bad_digest = command(
                client + ["pull", endpoint + "/" + repo + "@sha256:" + "0" * 64],
                check=False,
            )
            if bad_digest.returncode == 0:
                raise ValueError("unknown digest unexpectedly pulled")
            for item in index["manifests"]:
                by_digest = local_reference(
                    endpoint + "/" + repo + "@" + item["digest"]
                )
                refs.append(by_digest)
                command(
                    client
                    + [
                        "pull",
                        "--platform",
                        "linux/" + item["platform"]["architecture"],
                        by_digest,
                    ]
                )
            versions = json.loads(
                command(client + ["version", "--format", "{{json .}}"]).stdout
            )
            native_arch = versions["Server"]["Arch"]
            by_index = local_reference(endpoint + "/" + repo + "@" + digest)
            refs.append(by_index)
            command(client + ["pull", "--platform", "linux/" + native_arch, by_index])
            command(client + ["manifest", "push", "--insecure", target])
            retry = registry_request(origin, f"/v2/{repo}/manifests/multi")
            if retry.content != response.content:
                raise ValueError("identical manifest retry changed registry bytes")
            receipt = {
                "local_contract_only": True,
                "synthetic_images_only": True,
                "registry_image": REGISTRY_IMAGE,
                "docker_versions": versions,
                "index_pull_architecture": native_arch,
                "buildx_version": command(
                    client + ["buildx", "version"]
                ).stdout.strip(),
                "index_digest": digest,
                "platforms": platforms,
                "blob_digests": sorted(blob_digests),
                "anonymous_read": "denied401",
                "wrong_auth": "denied",
                "wrong_digest": "rejected",
                "wrong_digest_api": "MANIFEST_UNKNOWN/404",
                "digest_pulls": "passed",
                "identical_retry": "unchanged",
            }
            return receipt
        finally:
            for reference in refs:
                command(client + ["image", "rm", reference], check=False)
            if container_created:
                command(client + ["rm", "-f", "-v", identifier], check=False)
            if network_created:
                command(client + ["network", "rm", identifier], check=False)
            if (
                container_created
                and command(client + ["inspect", identifier], check=False).returncode
                == 0
            ):
                raise RuntimeError("owned registry container cleanup failed")
            if (
                network_created
                and command(
                    client + ["network", "inspect", identifier], check=False
                ).returncode
                == 0
            ):
                raise RuntimeError("owned fixture network cleanup failed")
            if any(
                command(client + ["image", "inspect", ref], check=False).returncode == 0
                for ref in refs
            ):
                raise RuntimeError("owned fixture image reference cleanup failed")
            if receipt is not None:
                receipt["cleanup_verified"] = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():
        raise ValueError("receipt output already exists")
    receipt = rehearse()
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        args.output.chmod(0o600)
    else:
        print(json.dumps(receipt, indent=2, sort_keys=True))
