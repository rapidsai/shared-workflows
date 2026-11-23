# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
"""A candidate build names its release catalog unit after the producer it builds, not the calling repository."""

import os
import shutil
import subprocess  # nosec B404 - runs a checked-in step with bash and fixed test inputs
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = {"conda-cpp-build": "conda", "conda-python-build": "conda", "wheels-build": "wheel"}
NAMING_STEP = "catalog-repository"


def steps(workflow):
    document = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    return document["jobs"]["build"]["steps"]


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("kind", ["candidate", "release"])
def test_catalog_key_reads_the_producer_name(family, kind):
    build_steps = steps(f"{family}-{kind}.yaml")
    names = [step.get("id") for step in build_steps]
    companion = next(index for index, step in enumerate(build_steps) if "release catalog companion" in step.get("name", ""))
    naming = names.index(NAMING_STEP)
    assert naming < companion
    config = build_steps[companion]["with"]["config"]
    assert "github.event.repository.name" not in config
    assert f"format('{FAMILIES[family]}:{{0}}', steps.{NAMING_STEP}.outputs.name)" in config
    # A named producer is built from inputs.repo; otherwise the caller is the producer, as before.
    assert build_steps[naming]["env"]["REPOSITORY"] == (
        "${{ inputs.candidate-producer-repository-id != '' && inputs.repo || github.repository }}"
    )


@pytest.mark.parametrize(
    ("repository", "expected"),
    [("rapidsai/rapids-logger", "rapids-logger"), ("NVIDIA/cuvs", "cuvs")],
)
def test_naming_step_outputs_the_repository_name(tmp_path, repository, expected):
    step = next(step for step in steps("wheels-build-candidate.yaml") if step.get("id") == NAMING_STEP)
    output = tmp_path / "output"
    output.write_text("")
    result = subprocess.run(  # nosec B603 - checked-in step, test-controlled environment
        [shutil.which("bash"), "-c", step["run"]],
        env={**os.environ, "REPOSITORY": repository, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text() == f"name={expected}\n"


@pytest.mark.parametrize("repository", ["", "rapids-logger", "rapidsai/rapids logger", "rapidsai/x/y"])
def test_naming_step_refuses_a_malformed_repository(tmp_path, repository):
    step = next(step for step in steps("wheels-build-candidate.yaml") if step.get("id") == NAMING_STEP)
    output = tmp_path / "output"
    output.write_text("")
    result = subprocess.run(  # nosec B603 - checked-in step, test-controlled environment
        [shutil.which("bash"), "-c", step["run"]],
        env={**os.environ, "REPOSITORY": repository, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert output.read_text() == ""
