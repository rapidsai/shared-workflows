# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Prepare/run a hash-bound synthetic four-family publication rehearsal.

No operator secrets, destination overrides or hosted authorization are accepted.
The separately reviewed Kitmaker checkout is verified before any client runs.
"""

import argparse
import hashlib
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess  # nosec B404 -- fixed local clients/git, no shell or inherited credentials.
import sys
import tempfile
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[2]
KITMAKER_REVISION = "b95debb0b3310c4e4a86b8fa62e565b34af3683d"
CLIENTS = (
    "ci/publication-rehearsal/run_local.py",
    "ci/publication-rehearsal/conda_fixture.py",
    "ci/publication-rehearsal/conda_channel_install.py",
    "ci/publication-rehearsal/maven_fixture.py",
    "ci/publication-rehearsal/maven_signatures.py",
    "ci/publication-rehearsal/sign_maven_fixture.sh",
    "ci/maven-publish/prepare_maven_bundle_steps.sh",
    "ci/maven-publish/maven_utils.sh",
    "ci/publication-rehearsal/kitmaker_fixture.py",
    "ci/conda-publish/local_rehearsal.py",
    "ci/maven-publish/maven_central_publish.sh",
    "ci/container-publish/local_rehearsal.py",
    "ci/container-publish/hosted_preparation.py",
    "ci/container-publish/guarded_oci_transport.py",
    "ci/container-publish/guarded_oci_bearer.py",
    "ci/container-publish/http_deadline.py",
)


class RehearsalError(ValueError):
    pass


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def write_once(path, value):
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix="evidence-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
            temporary.chmod(0o400)
            os.link(temporary, path)  # Atomic create only, including existing symlinks.
        finally:
            temporary.unlink(missing_ok=True)


def execute(argv, *, environment=None, timeout=90):
    try:
        result = subprocess.run(  # nosec B603 -- fixed client argv; local inputs/origins validated by callers.
            list(map(str, argv)),
            env={"PATH": os.environ["PATH"], **(environment or {})},
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RehearsalError("local client timed out; child output withheld") from None
    if result.returncode:
        # Child output can contain auth, URLs or contact fields; never persist it.
        raise RehearsalError(
            "local client failed with status " + str(result.returncode)
        )
    return result.stdout


def verify_kitmaker(checkout):
    checkout = Path(checkout).resolve(strict=True)
    environment = {"GIT_NO_REPLACE_OBJECTS": "1"}
    actual = execute(
        ["git", "-C", checkout, "rev-parse", "HEAD"], environment=environment
    ).strip()
    if actual != KITMAKER_REVISION:
        raise RehearsalError("Kitmaker checkout does not match the reviewed revision")
    dirty = execute(
        [
            "git",
            "-C",
            checkout,
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "k2-publisher",
        ],
        environment=environment,
    )
    untracked = execute(
        ["git", "-C", checkout, "ls-files", "--others", "--", "k2-publisher"],
        environment=environment,
    )
    if (
        dirty
        or untracked
        or not (checkout / "k2-publisher/local_rehearsal.py").is_file()
    ):
        raise RehearsalError(
            "Kitmaker checkout must have clean reviewed publisher files"
        )
    return checkout


def private_root(path):
    path = Path(path)
    if (
        not path.is_dir()
        or path.is_symlink()
        or path.absolute() != path.resolve()
        or path.stat().st_mode & 0o777 != 0o700
    ):
        raise RehearsalError(
            "rehearsal root must be canonical, non-symlink and mode0700"
        )
    return path


def prepare(root):
    root = private_root(root)
    if any(root.iterdir()):
        raise RehearsalError("prepare requires an empty private directory")
    conda = load("local_conda_fixture", "ci/publication-rehearsal/conda_fixture.py")
    maven = load("local_maven_fixture", "ci/publication-rehearsal/maven_fixture.py")
    kitmaker = load(
        "local_kitmaker_fixture", "ci/publication-rehearsal/kitmaker_fixture.py"
    )
    oci = load("local_oci_fixture", "ci/container-publish/local_rehearsal.py")
    for family in ("conda", "python", "maven", "oci"):
        (root / family).mkdir(mode=0o700)
    conda.package(root / "conda")
    kitmaker.wheel(root / "python/fixture_one-26.12.0-py3-none-any.whl")
    maven.package_files(root / "maven")
    signatures = load(
        "local_maven_signatures", "ci/publication-rehearsal/maven_signatures.py"
    )
    signatures.prepare(root / "maven")
    oci.write_producer_shaped_single_platform_archive(
        root / "oci/input.tar.gz",
        architecture="amd64",
        image_tag="26.12-cuda12.9.2-rockylinux8-py3.12-amd64",
        image_name="rapidsai/ci-conda",
        repeat_layer=True,
    )
    inputs = {
        str(path.relative_to(root)): sha(path)
        for path in root.rglob("*")
        if path.is_file() and "source" not in path.relative_to(root).parts
    }
    for relative in inputs:
        (root / relative).chmod(0o400)
    plan = {
        "schema_version": 1,
        "mode": "synthetic-loopback-only",
        "families": ["conda", "python-kitmaker", "maven", "oci"],
        "kitmaker_revision": KITMAKER_REVISION,
        "clients": {relative: sha(ROOT / relative) for relative in CLIENTS},
        "inputs": inputs,
    }
    write_once(root / "plan.json", plan)
    return hashlib.sha256(canonical(plan)).hexdigest()


def validate(root, expected_sha256):
    root = private_root(root)
    plan_path = root / "plan.json"
    if (
        plan_path.is_symlink()
        or not plan_path.is_file()
        or plan_path.stat().st_mode & 0o777 != 0o400
        or not isinstance(expected_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)
        or sha(plan_path) != expected_sha256
    ):
        raise RehearsalError("local plan differs from caller-supplied digest")
    plan = json.loads(plan_path.read_bytes())
    if (
        not isinstance(plan, dict)
        or set(plan)
        != {
            "schema_version",
            "mode",
            "families",
            "kitmaker_revision",
            "clients",
            "inputs",
        }
        or type(plan["schema_version"]) is not int
        or plan["schema_version"] != 1
        or plan["mode"] != "synthetic-loopback-only"
        or plan["families"] != ["conda", "python-kitmaker", "maven", "oci"]
        or plan["kitmaker_revision"] != KITMAKER_REVISION
    ):
        raise RehearsalError("unsupported local rehearsal plan")
    if plan["clients"] != {relative: sha(ROOT / relative) for relative in CLIENTS}:
        raise RehearsalError("local client source changed after planning")
    expected_paths = {
        "conda/conda-fixture-26.12.00-h0_0.conda",
        "python/fixture_one-26.12.0-py3-none-any.whl",
        "oci/input.tar.gz",
        "maven/signer-public.asc",
    }
    expected_paths.update(
        "maven/signed/ai/rapids/example/26.12.0/example-26.12.0" + suffix
        for suffix in (
            ".pom",
            ".pom.asc",
            ".jar",
            ".jar.asc",
            "-sources.jar",
            "-sources.jar.asc",
            "-javadoc.jar",
            "-javadoc.jar.asc",
        )
    )
    if not isinstance(plan["inputs"], dict) or set(plan["inputs"]) != expected_paths:
        raise RehearsalError("plan does not cover the exact four-family input set")
    for relative, digest in plan["inputs"].items():
        path = root / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root)
            or path.stat().st_mode & 0o777 != 0o400
            or sha(path) != digest
        ):
            raise RehearsalError("immutable rehearsal input changed")
    return plan


def fetch(
    origin, path, expected, *, auth, destination=None, maximum_bytes=4 * 1024 * 1024
):
    if (
        not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", origin)
        or not path.startswith("/")
        or ".." in path
        or "?" in path
    ):
        raise RehearsalError("readback is not a fixed loopback fixture path")
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            origin + path,
            headers={"Authorization": auth},
            timeout=5,
            allow_redirects=False,
            stream=True,
        )
        data = bytearray()
        with response:
            if response.status_code != 200:
                raise RehearsalError("family readback HTTP response rejected")
            declared = response.headers.get("Content-Length")
            if declared is not None and (
                not declared.isdigit() or int(declared) > maximum_bytes
            ):
                raise RehearsalError("family readback exceeds fixture size bound")
            for chunk in response.iter_content(64 * 1024):
                data.extend(chunk)
                if len(data) > maximum_bytes:
                    raise RehearsalError("family readback exceeds fixture size bound")
        if hashlib.sha256(data).hexdigest() != expected:
            raise RehearsalError("family readback digest mismatch")
        if destination:
            Path(destination).write_bytes(data)
    return expected


def conda_run(root, plan, *, conda_python=None, pixi=None):
    fixture = load("unified_conda", "ci/publication-rehearsal/conda_fixture.py")
    relative = "conda/conda-fixture-26.12.00-h0_0.conda"
    package = root / relative
    download = root / "conda" / "readback.conda"
    with fixture.service() as (origin, state):
        for _retry in range(2):
            result = fixture.run(package, origin, expected=plan["inputs"][relative])
            if result.returncode:
                raise RehearsalError("actual Anaconda fixture client failed")
        receipt = json.loads(result.stdout.splitlines()[-1])
        fetch(
            origin,
            "/download/fixture-user/conda-fixture/26.12.00/noarch/" + package.name,
            plan["inputs"][relative],
            auth="token fixture-value",
            maximum_bytes=package.stat().st_size,
            destination=download,
        )
        installed = None
        if conda_python is not None:
            channel = load(
                "unified_conda_channel",
                "ci/publication-rehearsal/conda_channel_install.py",
            )
            # Keep the exact conventional filename for indexing the readback bytes.
            install_input = root / "conda" / "readback"
            install_input.mkdir(mode=0o700)
            verified = install_input / package.name
            verified.write_bytes(download.read_bytes())
            installed = channel.rehearse(
                verified, plan["inputs"][relative], root, python=conda_python, pixi=pixi
            )
        return {
            "sha256": receipt["sha256"],
            "client": "anaconda-client",
            "version": receipt["client_version"],
            "upload_readback": "verified",
            "identical_retry_upload_count": state["storage_posts"],
            "dependencies": receipt["dependencies"],
            "channel_install": installed,
            "channel_install_exercised": installed is not None,
            "limits": ["synthetic API service"]
            + ([] if installed else ["channel indexing/Conda solve not exercised"]),
        }


def maven_run(root, plan):
    fixture = load("unified_maven", "ci/publication-rehearsal/maven_fixture.py")
    readback = root / "maven/readback"
    readback.mkdir(mode=0o700)
    downloaded = {}
    with fixture.service() as (origin, state):
        result, bundle = fixture.run(
            root / "maven", origin, auto_drop=False, use_prepared=True
        )
        if result.returncode:
            raise RehearsalError("actual Maven shell client failed")
        hashes = {}
        with zipfile.ZipFile(bundle) as archive:
            for path in state["uploads"]:
                relative = path.removeprefix("/service/local/staging/deploy/maven2/")
                expected = hashlib.sha256(archive.read(relative)).hexdigest()
                destination = readback / str(len(downloaded))
                downloaded[relative] = destination
                hashes[relative] = fetch(
                    origin,
                    path,
                    expected,
                    auth="Bearer " + fixture.AUTH,
                    maximum_bytes=len(archive.read(relative)),
                    destination=destination,
                )
            originals = {
                name.removeprefix("maven/signed/"): digest
                for name, digest in plan["inputs"].items()
                if name.startswith("maven/signed/")
            }
            required = {
                name + suffix for name in originals for suffix in ("", ".md5", ".sha1")
            }
            if set(hashes) != required or any(
                hashes[name] != digest for name, digest in originals.items()
            ):
                raise RehearsalError(
                    "Maven readback does not cover exact planned coordinates"
                )
            for name in originals:
                body = archive.read(name)
                for algorithm in ("md5", "sha1"):
                    checksum = hashlib.new(
                        algorithm, body, usedforsecurity=False
                    ).hexdigest()
                    if (
                        archive.read(name + "." + algorithm).decode().strip()
                        != checksum
                    ):
                        raise RehearsalError(
                            "Maven client checksum metadata differs from artifact bytes"
                        )
        signatures = load(
            "unified_maven_signatures", "ci/publication-rehearsal/maven_signatures.py"
        )
        verified = signatures.verify(
            root / "maven/signer-public.asc",
            {
                name: (downloaded[name], downloaded[name + ".asc"])
                for name in originals
                if not name.endswith(".asc")
            },
        )
        with requests.Session() as session:
            session.trust_env = False
            response = session.delete(
                origin + "/api/v1/publisher/deployment/" + fixture.DEPLOYMENT,
                headers={"Authorization": "Bearer " + fixture.AUTH},
                timeout=5,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise RehearsalError("Maven dummy deployment cleanup failed")
        return {
            "coordinate": "ai.rapids:example:26.12.0",
            "artifact_sha256s": hashes,
            "bundle_sha256": sha(bundle),
            "state": "VALIDATED-and-dropped",
            "signatures": verified,
            "limits": [
                "simulated Central service",
                "ephemeral host fixture signing; production signer/wrapper not exercised",
                "snapshot deploy-plugin not exercised",
            ],
        }


def python_run(root, plan, checkout):
    fixture = load("unified_kitmaker", "ci/publication-rehearsal/kitmaker_fixture.py")
    package = root / "python/fixture_one-26.12.0-py3-none-any.whl"
    expected = plan["inputs"][str(package.relative_to(root))]
    directory = root / "python"
    with fixture.service(package, expected) as (origin, state):
        manifest = directory / "wheel-release-artifacts.json"
        manifest.write_text(
            json.dumps(
                {
                    "artifacts": [
                        {
                            "packageName": "fixture-one",
                            "wheelName": package.name,
                            "artifactoryUrl": origin
                            + "/artifactory/local/"
                            + package.name,
                        }
                    ]
                }
            )
        )
        ids = directory / "release-uuids.json"
        base = [
            sys.executable,
            "-I",
            "-B",
            checkout / "k2-publisher/local_rehearsal.py",
            "--base-url",
            origin + "/api/v0",
            "--package",
            "fixture-one",
        ]
        execute([*base, "publish-wheels", manifest, "--uuids", ids])
        execute([*base, "check-release-status", "--uuids", ids])
        netrc = directory / "fixture.netrc"
        netrc.write_text(
            "machine 127.0.0.1 login fixture-index password fixture-value\n"
        )
        netrc.chmod(0o600)
        requirements = directory / "requirements.txt"
        requirements.write_text("fixture-one==26.12.0 --hash=sha256:" + expected + "\n")
        download = directory / "download"
        download.mkdir(mode=0o700)
        environment = {"PIP_CONFIG_FILE": os.devnull, "NETRC": str(netrc)}
        try:
            execute(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-m",
                    "pip",
                    "--isolated",
                    "download",
                    "--disable-pip-version-check",
                    "--no-cache-dir",
                    "--no-deps",
                    "--only-binary=:all:",
                    "--index-url",
                    origin + "/simple/",
                    "--require-hashes",
                    "-r",
                    requirements,
                    "--dest",
                    download,
                ],
                environment=environment,
            )
        finally:
            netrc.unlink(missing_ok=True)
        downloaded = download / package.name
        if sha(downloaded) != expected:
            raise RehearsalError("pip downloaded different wheel bytes")
        target = directory / "runtime"
        execute([sys.executable, "-I", "-B", "-m", "venv", target])
        python = target / "bin/python"
        execute(
            [
                python,
                "-I",
                "-B",
                "-m",
                "pip",
                "--isolated",
                "install",
                "--disable-pip-version-check",
                "--no-index",
                "--no-deps",
                "--require-hashes",
                "-r",
                requirements,
                "--find-links",
                download,
            ],
            environment={"PIP_CONFIG_FILE": os.devnull},
        )
        value = execute(
            [
                python,
                "-I",
                "-B",
                "-c",
                "import fixture_one; print(fixture_one.__version__ + ':' + fixture_one.message())",
            ]
        ).strip()
        if value != "26.12.0:local-rehearsal":
            raise RehearsalError("installed synthetic wheel behavior differs")
        return {
            "wheel_sha256": expected,
            "kitmaker_source_revision": KITMAKER_REVISION,
            "sdk_status": "completed",
            "worker_delivery": "SIMULATED",
            "worker_simulated": True,
            "pip_hash_required_download": "verified",
            "pip_offline_install_import": value,
            "pip_download_version": importlib.metadata.version("pip"),
            "pip_install_version": execute(
                [python, "-I", "-B", "-m", "pip", "--version"]
            ).split()[1],
            "uuid": json.loads(ids.read_text())["fixture-one"],
            "events": state["events"],
            "limits": [
                "simulated worker/private Simple index; not hosted Kitmaker delivery",
                "synthetic wheel only; no sdist",
            ],
        }


def run(root, expected_sha256, kitmaker_checkout, *, conda_python=None, pixi=None):
    if (conda_python is None) != (pixi is None):
        raise RehearsalError("Conda install requires both explicit runtime tools")
    root = private_root(root)
    plan = validate(root, expected_sha256)
    if conda_python is not None:
        tools = load(
            "unified_conda_tools", "ci/publication-rehearsal/conda_channel_install.py"
        )
        conda_python, pixi = tools.executable(conda_python), tools.executable(pixi)
    checkout = verify_kitmaker(kitmaker_checkout)
    if any(
        path.exists() or path.is_symlink()
        for path in (root / "completion.json", root / "results")
    ):
        raise RehearsalError(
            "use a fresh planned directory; completed/partial runs are preserved"
        )
    results = root / "results"
    results.mkdir(mode=0o700)
    reports = {}
    for family, operation in (
        (
            "conda",
            lambda: conda_run(root, plan, conda_python=conda_python, pixi=pixi)
            if conda_python
            else conda_run(root, plan),
        ),
        ("python-kitmaker", lambda: python_run(root, plan, checkout)),
        ("maven", lambda: maven_run(root, plan)),
    ):
        reports[family] = operation()
        write_once(results / (family + ".json"), reports[family])
        validate(root, expected_sha256)
    oci = load("unified_oci", "ci/container-publish/local_rehearsal.py")
    result = oci.rehearse(
        single_archive_input=root / "oci/input.tar.gz",
        single_archive_sha256=plan["inputs"]["oci/input.tar.gz"],
    )
    reports["oci"] = {
        "archive": result["single_platform_archive"],
        "guarded_transport": result["guarded_http_transport"],
        "oras_version": result["oras_version"],
        "cleanup_verified": result["cleanup_verified"],
        "limits": [
            "synthetic authenticated loopback registry",
            "no hosted service or runner startup",
        ],
    }
    write_once(results / "oci.json", reports["oci"])
    validate(root, expected_sha256)
    completion = {
        "schema_version": 1,
        "local_fixture_only": True,
        "production_authorization": False,
        "plan_sha256": expected_sha256,
        "families": reports,
        "result_sha256s": {path.name: sha(path) for path in results.iterdir()},
        "kitmaker_worker_simulated": True,
        "all_local_clients_completed": True,
        "runtime": {
            "python": sys.version.split()[0],
            "requests": importlib.metadata.version("requests"),
        },
    }
    write_once(root / "completion.json", completion)
    return completion


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run"))
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--kitmaker-checkout", type=Path)
    parser.add_argument("--conda-python", type=Path)
    parser.add_argument("--pixi", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        print(prepare(args.directory))
    else:
        if not args.plan_sha256 or not args.kitmaker_checkout:
            parser.error("run requires --plan-sha256 and --kitmaker-checkout")
        print(
            json.dumps(
                run(
                    args.directory,
                    args.plan_sha256,
                    args.kitmaker_checkout,
                    conda_python=args.conda_python,
                    pixi=args.pixi,
                ),
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
