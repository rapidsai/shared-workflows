# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
"""ci-imgs release rows are keyed, reused and built from the train's base digests (rapids-work per-row-reuse.md, 4a).

The release file's reuse plan decides image rows first and passes only those that build, so each row checks itself
against the whole matrix, names its artifact from its image rather than its job index, gives dependency setup its
matrix row and identity for the key, and skips every build step when an earlier complete run supplies it.
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
SOURCE = "1" * 40
SKIP = "${{ steps.row-reuse.outputs.reused != 'true' }}"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def steps():
    return workflow()["jobs"]["build"]["steps"]


def step(name):
    matches = [s for s in steps() if s.get("name") == name or s.get("id") == name]
    assert len(matches) == 1, name
    return steps().index(matches[0]), matches[0]


def image_row(repo="ci-conda", cuda="13.4.3", arch="amd64"):
    return {
        "CUDA_VER": cuda,
        "PYTHON_VER": "3.12",
        "LINUX_VER": "rockylinux8",
        "IMAGE_REPO": repo,
        "DOCKERFILE": f"{repo}.Dockerfile",
        "DOCKER_TARGET": "",
        "IMAGE_NAME": f"rapidsai/{repo}:26.12-cuda{cuda}-rockylinux8-py3.12",
        "ARCH": arch,
    }


FULL = {"include": [image_row(arch=arch) for arch in ("amd64", "arm64")] + [image_row("ci-wheel")]}
KEY = "ci-conda:26.12-cuda13.4.3-rockylinux8-py3.12-amd64"
CONDA_BASES = {
    "CUDA_BASE_IMAGE": "docker.io/nvidia/cuda:13.4.3-base-rockylinux8@sha256:" + "e" * 64,
    "MINIFORGE_BASE_IMAGE": "docker.io/condaforge/miniforge3:26.9.4-0@sha256:" + "f" * 64,
}
BASES = {
    KEY: CONDA_BASES,
    KEY.replace("amd64", "arm64"): CONDA_BASES,
    "ci-wheel:26.12-cuda13.4.3-rockylinux8-py3.12-amd64": {
        "BASE_IMAGE": "docker.io/nvidia/cuda:13.4.3-devel-rockylinux8@sha256:" + "d" * 64
    },
}


def train(bases=BASES):
    value = {
        "source_revisions": {"ci-imgs": SOURCE},
        "release_units": [{"id": "container:ci-images", "source_revision": SOURCE}],
        "version_contract": {"package_versions": {"container": "26.12"}},
    }
    if bases is not None:
        value["candidate_image_bases"] = bases
    return value


def check_row(tmp_path, *, row=None, full=FULL, computed=FULL, bases=BASES):
    """Run the row check with a fake checkout, store and ci-imgs matrix; return (result, outputs)."""
    document = train(bases)
    canonical = subprocess.run(  # noqa: S603 # nosec B603 B607
        ["jq", "-cSj", "del(.train_sha256)"], input=json.dumps(document), capture_output=True, text=True, check=True
    ).stdout
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    (tmp_path / "train.json").write_text(json.dumps(document))
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    (bin_directory / "aws").write_text(f'#!/bin/bash\ncp "{tmp_path}/train.json" "$4"\n')
    (bin_directory / "git").write_text(f"#!/bin/bash\necho {SOURCE}\n")
    checkout = tmp_path / "checkout"
    (checkout / "ci").mkdir(parents=True)
    (checkout / "VERSION").write_text("26.12.00\n")
    (checkout / "ci/compute-candidate-matrix.sh").write_text(f"#!/bin/bash\necho '{json.dumps(computed)}'\n")
    for path in (bin_directory / "aws", bin_directory / "git", checkout / "ci/compute-candidate-matrix.sh"):
        path.chmod(0o755)
    _, check = step("row")
    row = row or image_row()
    output = tmp_path / "github-output"
    env = {
        **os.environ,
        "PATH": f"{bin_directory}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
        "TRAIN_SHA256": digest,
        "SOURCE_SHA": SOURCE,
        "ROW": json.dumps(row),
        "FULL_MATRIX": json.dumps(full),
        "IMAGE_NAME": row["IMAGE_NAME"],
        "IMAGE_REPO": row["IMAGE_REPO"],
        "ARCH": row["ARCH"],
        "RAPIDS_VERSION_MAJOR_MINOR": "26.12",
    }
    result = subprocess.run(  # noqa: S603 # nosec B603
        ["/bin/bash", "-e", "-c", check["run"]], env=env, cwd=checkout, capture_output=True, text=True, check=False
    )
    lines = output.read_text().splitlines() if output.exists() else []
    return result, dict(line.split("=", 1) for line in lines)


def test_a_row_names_its_artifact_from_its_image_and_takes_its_bases_from_the_train(tmp_path):
    result, outputs = check_row(tmp_path)
    assert result.returncode == 0, result.stderr
    assert outputs["artifact"] == "ci-images-ci-conda-26.12-cuda13.4.3-rockylinux8-py3.12-amd64"
    assert json.loads(outputs["base-images"]) == BASES[KEY]


def test_a_train_from_before_base_digests_passes_no_bases(tmp_path):
    result, outputs = check_row(tmp_path, bases=None)
    assert result.returncode == 0, result.stderr
    assert outputs["base-images"] == ""


def test_a_filtered_matrix_row_is_checked_against_the_whole_matrix(tmp_path):
    # The plan passes only the arm64 row; the row still has to be in ci-imgs' whole matrix.
    result, _ = check_row(tmp_path, row=image_row(arch="arm64"))
    assert result.returncode == 0, result.stderr


MISNAMED = {**image_row(), "IMAGE_NAME": "rapidsai/ci-wheel:26.12-cuda13.4.3-rockylinux8-py3.12"}


@pytest.mark.parametrize(
    "overrides",
    [
        # ci-imgs now produces another matrix than the release file's.
        {"computed": {"include": FULL["include"][:2]}},
        # A row that is not in the whole matrix.
        {"row": image_row(cuda="12.9.2")},
        # The train records bases, but not for this image.
        {"bases": {"ci-conda:other-amd64": CONDA_BASES}},
        # An image name that is not its repository's, even when ci-imgs produced it.
        {"row": MISNAMED, "full": {"include": [MISNAMED]}, "computed": {"include": [MISNAMED]}},
    ],
)
def test_a_row_that_does_not_match_fails_without_outputs(tmp_path, overrides):
    result, outputs = check_row(tmp_path, **overrides)
    assert result.returncode != 0
    assert outputs == {}


def test_dependency_setup_keys_the_row_on_its_matrix_row_and_identity():
    _, setup = step("Prepare frozen candidate build inputs")
    assert setup["with"]["row-entrypoint"] == "ci-images-build-release"
    assert setup["with"]["row-identity"] == "${{ inputs.candidate-row-identity }}"
    assert setup["with"]["image-row"] == "${{ toJSON(matrix) }}"
    inputs = workflow()[True]["workflow_call"]["inputs"]
    assert inputs["candidate-row-identity"]["required"] is True
    assert inputs["candidate-full-matrix"]["required"] is True


def test_a_reused_row_skips_every_build_step():
    index, reuse = step("row-reuse")
    setup, _ = step("Prepare frozen candidate build inputs")
    assert setup + 1 == index
    assert reuse["run"] == "candidate-row-reuse"
    assert reuse["if"] == "${{ env.RELEASE_CANDIDATE_ROW_REUSE_KEY != '' }}"
    for later in steps()[index + 1 :]:
        assert "steps.row-reuse.outputs.reused != 'true'" in later.get("if", ""), later.get("name") or later["uses"]


def test_the_build_takes_its_bases_from_the_row_check_and_the_catalog_its_artifact_name():
    _, arguments = step("args")
    assert arguments["env"]["CANDIDATE_BASE_IMAGES"] == "${{ steps.row.outputs.base-images }}"
    _, publish = step("Publish run-bound private image catalog")
    assert publish["with"]["source-artifact-name"] == "${{ steps.row.outputs.artifact }}"
    assert "job-index" not in WORKFLOW.read_text()
