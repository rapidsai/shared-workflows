# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
"""ci-imgs release rows push each image to private GHCR by digest, with a GHCR layer cache.

Release builds will run in these images by digest instead of public tags. S3 keeps only a small image record naming
the pushed manifest by digest; collection reads that manifest back from GHCR instead of downloading an archive.
"""

import hashlib
import json
import os
import subprocess  # nosec B404
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci-images-build-release.yaml"
LOGIN = "docker/login-action@8aa5831fb8d48d8f76594c7ddde61ae58019f550"
TRAIN = "a" * 64


def job():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["build"]


def step(name):
    steps = job()["steps"]
    matches = [s for s in steps if s.get("name") == name or s.get("id") == name]
    assert len(matches) == 1, name
    return steps.index(matches[0]), matches[0]


def test_only_the_image_job_may_write_packages():
    assert job()["permissions"] == {"contents": "read", "id-token": "write", "packages": "write"}


def test_ghcr_login_uses_the_runs_own_token_after_docker_config_is_isolated():
    isolate, _ = step("Isolate Docker registry configuration")
    index, login = step("Log in to GHCR for candidate images")
    build, _ = step("build")
    assert isolate < index < build
    assert login["uses"].startswith(LOGIN)
    assert login["with"] == {
        "registry": "ghcr.io",
        "username": "${{ github.actor }}",
        "password": "${{ github.token }}",
    }


def test_the_build_writes_only_the_ghcr_image():
    # The image itself lives in GHCR; S3 keeps a small image record instead of an OCI archive body.
    _, build = step("build")
    outputs = [line.strip() for line in build["with"]["outputs"].strip().splitlines()]
    assert outputs == ["type=registry,name=${{ steps.ghcr.outputs.image }},oci-mediatypes=true"]
    assert build["with"]["push"] is False
    assert not [s for s in job()["steps"] if "prepare-candidate-image.sh" in str(s.get("run", ""))]


def test_the_build_can_push_only_to_the_private_candidate_namespace():
    # Release run 45474940796: with `tags` set, buildx pushed the registry export to the docker.io tag (the public
    # rapidsai/ci-conda image) instead of the GHCR name. Only Docker Hub's refusal stopped an overwrite.
    _, build = step("build")
    assert "tags" not in build["with"]
    registry = [line.strip() for line in build["with"]["outputs"].splitlines() if "type=registry" in line]
    assert registry == ["type=registry,name=${{ steps.ghcr.outputs.image }},oci-mediatypes=true"]
    assert all("docker.io" not in line for line in registry)


def test_the_layer_cache_lives_in_ghcr():
    _, build = step("build")
    assert build["with"]["cache-to"] == (
        "type=registry,ref=${{ steps.ghcr.outputs.cache }},mode=max,oci-mediatypes=true,image-manifest=true"
    )
    cache_from = [line.strip() for line in build["with"]["cache-from"].strip().splitlines()]
    assert cache_from[0] == "type=registry,ref=${{ steps.ghcr.outputs.cache }}"
    # The public main cache stays a second source until the GHCR cache is warm.
    assert cache_from[1] == "type=registry,ref=${{ matrix.MAIN_CACHE_IMAGE_NAME }}-buildcache-${{ matrix.ARCH }}"


def name_image(tmp_path, image_repo="ci-conda", image_name="rapidsai/ci-conda:26.12-cuda12.9.2-rockylinux8-py3.12",
               arch="amd64", train=TRAIN):
    _, naming = step("ghcr")
    output = tmp_path / "out"
    env = {**os.environ, **naming["env"], "GITHUB_OUTPUT": str(output)}
    env.update(IMAGE_REPO=image_repo, IMAGE_NAME=image_name, ARCH=arch, TRAIN_SHA256=train)
    result = subprocess.run(  # noqa: S603 # nosec B603
        ["/bin/bash", "-e", "-c", naming["run"]], env=env, capture_output=True, text=True, check=False
    )
    lines = output.read_text().splitlines() if output.exists() else []
    return result, dict(line.split("=", 1) for line in lines)


def test_image_and_cache_names_are_private_and_bound_to_the_train_and_row(tmp_path):
    result, out = name_image(tmp_path)
    assert result.returncode == 0, result.stderr
    assert out == {
        "image": f"ghcr.io/rapidsai/release-candidates/ci-conda:{TRAIN}-26.12-cuda12.9.2-rockylinux8-py3.12-amd64",
        "cache": "ghcr.io/rapidsai/release-candidates/buildcache:ci-conda-26.12-cuda12.9.2-rockylinux8-py3.12-amd64",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"image_repo": "ci-other"},
        {"image_repo": "ci-conda/../x"},
        {"image_name": "rapidsai/ci-conda"},
        {"image_name": "rapidsai/ci-conda:26.12 x"},
        {"arch": "riscv"},
        {"train": "abc"},
    ],
)
def test_unexpected_names_fail_without_output(tmp_path, overrides):
    result, out = name_image(tmp_path, **overrides)
    assert result.returncode != 0
    assert out == {}


MANIFEST = (
    b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json",'
    b'"config":{"mediaType":"application/vnd.oci.image.config.v1+json","digest":"sha256:'
    + b"c" * 64
    + b'","size":7},"layers":[]}'
)
MANIFEST_DIGEST = "sha256:" + hashlib.sha256(MANIFEST).hexdigest()
IMAGE = f"ghcr.io/rapidsai/release-candidates/ci-conda:{TRAIN}-26.12-cuda12.9.2-rockylinux8-py3.12-amd64"


def write_record(tmp_path, *, manifest=MANIFEST, digest=MANIFEST_DIGEST, trailing_newline=False, **overrides):
    _, record = step("record")
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    (tmp_path / "manifest").write_bytes(manifest + (b"\n" if trailing_newline else b""))
    fake.write_text('#!/bin/bash\nprintf "%s\\n" "$*" > "$DOCKER_ARGS"\ncat "$MANIFEST_FILE"\n')
    fake.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    env = {
        **os.environ,
        **record["env"],
        "PATH": f"{fake.parent}:{os.environ['PATH']}",
        "DOCKER_ARGS": str(tmp_path / "docker-args"),
        "MANIFEST_FILE": str(tmp_path / "manifest"),
        "RUNNER_TEMP": str(tmp_path),
        "IMAGE": IMAGE,
        "DIGEST": digest,
        "IMAGE_REPO": "ci-conda",
        "IMAGE_NAME": "rapidsai/ci-conda:26.12-cuda12.9.2-rockylinux8-py3.12",
        "ARCH": "amd64",
        "RAPIDS_VERSION_MAJOR_MINOR": "26.12",
    }
    env.update(overrides)
    result = subprocess.run(  # noqa: S603 # nosec B603
        ["/bin/bash", "-e", "-c", record["run"]], env=env, cwd=work, capture_output=True, text=True, check=False
    )
    directory = work / ".release-candidate-artifacts" / "container"
    return result, directory


def test_the_record_names_the_pushed_manifest_by_its_exact_bytes(tmp_path):
    result, directory = write_record(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "docker-args").read_text().split() == [
        "buildx", "imagetools", "inspect", f"ghcr.io/rapidsai/release-candidates/ci-conda@{MANIFEST_DIGEST}", "--raw"
    ]
    record = json.loads((directory / "image-ci-conda-26.12-cuda12.9.2-rockylinux8-py3.12-amd64.oci-image.json").read_text())
    assert record == {
        "schema_version": 1,
        "registry": "ghcr.io",
        "repository": "rapidsai/release-candidates/ci-conda",
        "digest": MANIFEST_DIGEST,
        "media_type": "application/vnd.oci.image.manifest.v1+json",
        "config_digest": "sha256:" + "c" * 64,
        "platform": "linux/amd64",
        "size": len(MANIFEST),
    }
    # The same package identity ci-imgs' prepare-candidate-image.sh wrote for the archive.
    assert json.loads((directory / "package-identity.json").read_text()) == {
        "ecosystem": "container",
        "name": "docker.io/rapidsai/ci-conda",
        "version": "26.12",
        "build": "26.12-cuda12.9.2-rockylinux8-py3.12-amd64",
        "platform": "linux/amd64",
    }
    assert sorted(path.name for path in directory.iterdir()) == [
        "image-ci-conda-26.12-cuda12.9.2-rockylinux8-py3.12-amd64.oci-image.json",
        "package-identity.json",
    ]


def test_one_trailing_newline_from_the_inspect_output_is_not_part_of_the_manifest(tmp_path):
    result, directory = write_record(tmp_path, trailing_newline=True)
    assert result.returncode == 0, result.stderr
    [record] = directory.glob("*.oci-image.json")
    assert json.loads(record.read_text())["size"] == len(MANIFEST)


@pytest.mark.parametrize(
    "overrides",
    [
        {"digest": "sha256:" + "0" * 64},
        {"digest": "latest"},
        {"manifest": MANIFEST.replace(b"image.manifest", b"image.index")},
        {"manifest": b"not json"},
        {"IMAGE_REPO": "ci-other"},
        {"IMAGE_NAME": "rapidsai/ci-conda:27.02-cuda12.9.2-rockylinux8-py3.12"},
        {"ARCH": "riscv"},
        {"IMAGE": "docker.io/rapidsai/ci-conda:26.12"},
    ],
)
def test_a_manifest_that_is_not_the_pushed_image_writes_no_record(tmp_path, overrides):
    if "manifest" in overrides and "digest" not in overrides:
        overrides["digest"] = "sha256:" + hashlib.sha256(overrides["manifest"]).hexdigest()
    keyword = {key: overrides.pop(key) for key in ("manifest", "digest") if key in overrides}
    result, directory = write_record(tmp_path, **keyword, **overrides)
    assert result.returncode != 0
    assert not list(directory.glob("*.oci-image.json")) if directory.exists() else True


def test_the_catalog_carries_the_image_record():
    _, dispatch = step("Publish run-bound private image catalog")
    config = json.loads(dispatch["with"]["config"])
    assert config["artifacts"] == [{"path": "*.oci-image.json", "package_identity_file": "package-identity.json"}]
    record, _ = step("record")
    publish, _ = step("Publish run-bound private image catalog")
    build, _ = step("build")
    assert build < record < publish
