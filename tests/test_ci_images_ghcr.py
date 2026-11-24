# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
"""ci-imgs release rows also push each image to private GHCR by digest, with a GHCR layer cache.

Release builds will run in these images by digest instead of public tags. Until consumers switch, the OCI archive
upload to S3 stays, and the pushed image must be the same image as the archive.
"""

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


def test_the_build_writes_the_archive_and_the_ghcr_image_in_one_build():
    _, build = step("build")
    outputs = [line.strip() for line in build["with"]["outputs"].strip().splitlines()]
    assert outputs == [
        "type=oci,dest=.release-candidate-artifacts/container/image.tar,"
        "name=docker.io/${{ matrix.IMAGE_NAME }}-${{ matrix.ARCH }},oci-mediatypes=true",
        "type=registry,name=${{ steps.ghcr.outputs.image }},oci-mediatypes=true",
    ]
    assert build["with"]["push"] is False


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


def test_the_pushed_image_is_read_back_and_matches_the_archive():
    index, readback = step("Check the GHCR image is the archived image")
    build, _ = step("build")
    assert index > build
    assert readback["env"]["DIGEST"] == "${{ steps.build.outputs.digest }}"
    assert readback["env"]["IMAGE"] == "${{ steps.ghcr.outputs.image }}"
    run = readback["run"]
    assert 'docker buildx imagetools inspect "${IMAGE%:*}@${DIGEST}"' in run
    assert "index.json" in run
