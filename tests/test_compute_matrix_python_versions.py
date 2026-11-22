# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
"""Run compute-matrix's checked-in matrix step with an optional Python version restriction."""

import json
import os
import shutil
import subprocess  # nosec B404 - runs the checked-in workflow step with bash, no shell string from input
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/compute-matrix.yaml"
MATRIX = yaml.safe_load((ROOT / "matrix.yaml").read_text())
NOARCH_MAX = 'group_by([.ARCH, (.CUDA_VER|split(".")|map(tonumber)|.[0])]) | map(max_by(.PY_VER|split(".")|map(tonumber)))'


def compute(tmp_path, matrix_name, python_versions="", matrix_filter=".", pure_wheel="false", pure_conda="false"):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    step = next(
        step for step in workflow["jobs"]["compute-matrix"]["steps"] if step.get("id") == "compute-matrix"
    )
    output = tmp_path / "github-output"
    output.write_text("")
    bash = shutil.which("bash")
    assert bash is not None
    env = {
        **os.environ,
        "GITHUB_OUTPUT": str(output),
        "BUILD_TYPE": "release-candidate",
        "MATRIX_NAME": matrix_name,
        "MATRIX_TYPE": "auto",
        "MATRIX": json.dumps(MATRIX),
        "MATRIX_FILTER": matrix_filter,
        "PURE_WHEEL": pure_wheel,
        "PURE_CONDA": pure_conda,
        "PYTHON_VERSIONS": python_versions,
    }
    result = subprocess.run(
        [bash, "-c", step["run"]], env=env, text=True, capture_output=True, check=False
    )  # nosec B603
    lines = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    include = json.loads(lines["matrix"])["include"] if "matrix" in lines else None
    return result, include


def test_no_restriction_keeps_every_python(tmp_path):
    result, include = compute(tmp_path, "conda-python-build")
    assert result.returncode == 0, result.stderr
    assert {entry["PY_VER"] for entry in include} == {"3.12", "3.13", "3.14"}


def test_restriction_applies_before_the_project_filter(tmp_path):
    # A max_by filter applied first would pick 3.14 and then filter to nothing.
    result, include = compute(tmp_path, "conda-python-build", "3.12", NOARCH_MAX)
    assert result.returncode == 0, result.stderr
    assert include and {entry["PY_VER"] for entry in include} == {"3.12"}
    assert len(include) == 4  # one per architecture and CUDA major


def test_restriction_keeps_pure_package_defaults(tmp_path):
    result, include = compute(tmp_path, "wheels-build", "3.12", pure_wheel="true")
    assert result.returncode == 0, result.stderr
    assert {entry["ARCH"] for entry in include} == {"amd64"}
    assert {entry["PY_VER"] for entry in include} == {"3.12"}
    assert len(include) == len({entry["CUDA_VER"] for entry in include})


def test_multiple_versions_are_allowed(tmp_path):
    result, include = compute(tmp_path, "conda-python-build", "3.12,3.13")
    assert result.returncode == 0, result.stderr
    assert {entry["PY_VER"] for entry in include} == {"3.12", "3.13"}


@pytest.mark.parametrize("value", ["3.12;id", "3", "3.12,", " 3.12", "3.12 3.13", "$(id)"])
def test_malformed_restriction_fails_without_a_matrix(value, tmp_path):
    result, include = compute(tmp_path, "conda-python-build", value)
    assert result.returncode != 0
    assert "python_versions" in result.stdout + result.stderr
    assert include is None


def test_restriction_matching_nothing_fails(tmp_path):
    result, include = compute(tmp_path, "conda-python-build", "3.99")
    assert result.returncode != 0
    assert include is None


@pytest.mark.parametrize("family", ["conda-cpp-build", "conda-python-build", "wheels-build"])
def test_candidate_workflows_pass_the_restriction_to_their_matrix(family):
    child = yaml.safe_load((ROOT / f".github/workflows/{family}-candidate.yaml").read_text())
    inputs = child[True]["workflow_call"]["inputs"]
    assert inputs["candidate-python-versions"]["default"] == ""
    matrix_job = child["jobs"]["compute-matrix"]
    assert matrix_job["with"]["python_versions"] == "${{ inputs.candidate-python-versions }}"
