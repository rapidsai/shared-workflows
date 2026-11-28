# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
"""The release-build entrypoints are each candidate workflow's build job alone, generated, never edited."""

import os
import shutil
import subprocess  # nosec B404 - runs a generated workflow step with bash and fixed test inputs
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ci"))
import release_build_entrypoints as entrypoints  # noqa: E402

WORKFLOWS = ROOT / ".github/workflows"


def test_checked_in_entrypoints_are_current():
    assert entrypoints.main(["--check"]) == 0


@pytest.mark.parametrize("family", entrypoints.FAMILIES)
def test_entrypoint_is_the_candidate_build_job_with_an_input_matrix(family):
    candidate = yaml.safe_load((WORKFLOWS / f"{family}-candidate.yaml").read_text())
    release = yaml.safe_load((WORKFLOWS / f"{family}-release.yaml").read_text())
    assert list(release["jobs"]) == ["build"]
    build, original = release["jobs"]["build"], candidate["jobs"]["build"]
    assert "needs" not in build and "if" not in build
    assert build["strategy"]["matrix"] == "${{ fromJSON(inputs.candidate-matrix) }}"
    assert build.get("permissions") == original.get("permissions")
    assert release["permissions"] == candidate["permissions"]
    inputs = release[True]["workflow_call"]["inputs"]
    assert inputs["candidate-matrix"]["required"] is True
    assert inputs["candidate-image-map"]["required"] is True
    assert inputs["candidate-row-identity"]["required"] is True
    added = {"candidate-matrix", "candidate-image-map", "candidate-row-identity"}
    assert {key: value for key, value in inputs.items() if key not in added} == candidate[True]["workflow_call"][
        "inputs"
    ]
    # The image check runs first, then the policy, then every original step in order.
    names = [step.get("name") or step.get("uses") for step in build["steps"]]
    assert names[:3] == [
        "Require the train's verified candidate image",
        "Evaluate the build policy for this release build",
        "Require a release-build candidate without image preflight",
    ]
    assert len(build["steps"]) == len(original["steps"]) + 4
    assert "needs." not in (WORKFLOWS / f"{family}-release.yaml").read_text().split("jobs:", 1)[1]


def run_gate(family, run_candidate, image_preflight, role):
    step = yaml.safe_load((WORKFLOWS / f"{family}-release.yaml").read_text())["jobs"]["build"]["steps"][2]
    env = {**os.environ, "RUN_CANDIDATE": run_candidate, "IMAGE_PREFLIGHT": image_preflight, "CANDIDATE_ROLE": role}
    return subprocess.run(  # nosec B603 - checked-in step, test-controlled environment
        [shutil.which("bash"), "-c", step["run"]], env=env, capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("family", entrypoints.FAMILIES)
def test_gate_admits_only_release_build_candidates_without_preflight(family):
    assert run_gate(family, "true", "false", "release-build").returncode == 0
    for refused in (("false", "false", "release-build"), ("true", "true", "release-build"), ("true", "false", "producer")):
        result = run_gate(family, *refused)
        assert result.returncode != 0
        assert "only release-build candidates" in result.stderr


def test_changed_candidate_shape_fails_instead_of_guessing():
    text = (WORKFLOWS / "wheels-build-candidate.yaml").read_text()
    with pytest.raises(entrypoints.EntrypointError, match="changed shape"):
        entrypoints.generate(text.replace("    needs: [compute-matrix, policy]\n", ""), "wheels-build")
    with pytest.raises(entrypoints.EntrypointError, match="reads another job"):
        entrypoints.generate(text.replace("${{ inputs.node_type }}", "${{ needs.other.outputs.x }}", 1), "wheels-build")


IMAGE_EXPRESSION = "${{ fromJSON(inputs.candidate-image-map)[matrix.candidate_image_key] }}"
# maven-build names its image family by input; these tests give it cudf's, ci-wheel.
FAMILY_IMAGES = {
    "conda-cpp-build": "ci-conda",
    "conda-python-build": "ci-conda",
    "wheels-build": "ci-wheel",
    "maven-build": "ci-wheel",
}
FAMILY_INPUT = "${{ inputs.container-image-family }}"


def step_environment(family, step):
    """The image step's env as GitHub would pass it, with maven-build's family input resolved."""
    return {key: FAMILY_IMAGES[family] if value == FAMILY_INPUT else value for key, value in step["env"].items()}
DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize("family", entrypoints.FAMILIES)
def test_every_release_build_runs_in_its_trains_image_from_private_ghcr(family):
    build = yaml.safe_load((WORKFLOWS / f"{family}-release.yaml").read_text())["jobs"]["build"]
    assert build["container"]["image"] == IMAGE_EXPRESSION
    assert build["container"]["credentials"] == {"username": "${{ github.actor }}", "password": "${{ github.token }}"}
    # No public tag is left anywhere in the job.
    assert "rapidsai/ci-" not in yaml.safe_dump(build)


def run_image_check(family, image):
    step = yaml.safe_load((WORKFLOWS / f"{family}-release.yaml").read_text())["jobs"]["build"]["steps"][0]
    assert step["env"]["IMAGE"] == IMAGE_EXPRESSION
    env = {
        **os.environ,
        **{key: value for key, value in step_environment(family, step).items() if key != "IMAGE"},
        "IMAGE": image,
        "GITHUB_ENV": os.devnull,
    }
    return subprocess.run(  # nosec B603 - checked-in step, test-controlled environment
        [shutil.which("bash"), "-c", step["run"]], env=env, capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("family", entrypoints.FAMILIES)
def test_the_image_check_admits_only_a_digest_pinned_candidate_of_the_right_family(family):
    image = FAMILY_IMAGES[family]
    other = "ci-wheel" if image == "ci-conda" else "ci-conda"
    assert run_image_check(family, f"ghcr.io/rapidsai/release-candidates/{image}@{DIGEST}").returncode == 0
    for refused in (
        "",  # a missing map key: GitHub would run the job on the host
        f"rapidsai/{image}:26.12-cuda12.9.2-rockylinux8-py3.12",
        f"ghcr.io/rapidsai/release-candidates/{image}:latest",
        f"ghcr.io/rapidsai/release-candidates/{other}@{DIGEST}",
        f"ghcr.io/rapidsai/release-candidates/{image}@sha256:abc",
        f"ghcr.io/other/release-candidates/{image}@{DIGEST}",
    ):
        result = run_image_check(family, refused)
        assert result.returncode != 0, refused
        assert "verified candidate image" in result.stderr



@pytest.mark.parametrize("family", entrypoints.FAMILIES)
def test_the_image_check_tells_dependency_setup_which_image_the_row_runs_in(family, tmp_path):
    # The row's reuse key covers its exact image; setup reads it from RELEASE_CANDIDATE_RUNTIME_IMAGE.
    step = yaml.safe_load((WORKFLOWS / f"{family}-release.yaml").read_text())["jobs"]["build"]["steps"][0]
    image = f"ghcr.io/rapidsai/release-candidates/{FAMILY_IMAGES[family]}@{DIGEST}"
    env_file = tmp_path / "github-env"
    env = {**os.environ, **step_environment(family, step), "IMAGE": image, "GITHUB_ENV": str(env_file)}
    result = subprocess.run(  # nosec B603 - checked-in step, test-controlled environment
        [shutil.which("bash"), "-c", step["run"]], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert env_file.read_text() == f"RELEASE_CANDIDATE_RUNTIME_IMAGE={image}\n"



def test_a_maven_row_runs_only_in_a_ci_conda_or_ci_wheel_image():
    step = yaml.safe_load((WORKFLOWS / "maven-build-release.yaml").read_text())["jobs"]["build"]["steps"][0]
    assert step["env"]["FAMILY"] == FAMILY_INPUT
    env = {**os.environ, **step["env"], "FAMILY": "ci-imgs", "IMAGE": f"ghcr.io/rapidsai/release-candidates/ci-imgs@{DIGEST}"}
    result = subprocess.run(  # nosec B603 - checked-in step, test-controlled environment
        [shutil.which("bash"), "-c", step["run"]], env={**env, "GITHUB_ENV": os.devnull}, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "ci-conda or ci-wheel image" in result.stderr


REUSED = "steps.row-reuse.outputs.reused != 'true'"
SETUP = "Prepare candidate dependencies and build policy"


@pytest.mark.parametrize("family", entrypoints.FAMILIES)
def test_a_row_tries_reuse_right_after_dependency_setup_and_then_skips_its_build(family):
    candidate = yaml.safe_load((WORKFLOWS / f"{family}-candidate.yaml").read_text())["jobs"]["build"]["steps"]
    steps = yaml.safe_load((WORKFLOWS / f"{family}-release.yaml").read_text())["jobs"]["build"]["steps"]
    names = [step.get("name") or step.get("uses") for step in steps]
    setup = names.index(SETUP)
    reuse = steps[setup + 1]
    assert reuse == {
        "name": "Reuse this row from an earlier complete run",
        "id": "row-reuse",
        "if": "${{ env.RELEASE_CANDIDATE_ROW_REUSE_KEY != '' }}",
        "run": "candidate-row-reuse",
    }
    # Every step up to dependency setup runs as before; every later one skips when the row was reused.
    for step in steps[: setup + 1]:
        assert REUSED not in str(step.get("if", ""))
    originals = {step.get("name") or step.get("uses"): step for step in candidate}
    for step in steps[setup + 2 :]:
        condition = step["if"]
        assert condition.startswith("${{ ") and condition.endswith(" }}")
        assert REUSED in condition
        original = originals[step.get("name") or step.get("uses")].get("if")
        if original:
            inner = original.removeprefix("${{").removesuffix("}}").strip()
            assert f"({inner})" in condition


@pytest.mark.parametrize("family", entrypoints.FAMILIES)
def test_dependency_setup_names_the_build_each_row_runs(family):
    # A repository's C++ and Python rows differ only in the build they run, so setup puts it in the reuse key
    # (release run 45540771695 gave kvikio's C++ and Python rows one key). The build is the entrypoint and the
    # release file's static with: values, which the generator passes as candidate-row-identity: toJSON(inputs) also
    # carried run- and train-bound values (release runs 45664183656 and 45690532489), and the reuse plan can't see it.
    steps = yaml.safe_load((WORKFLOWS / f"{family}-release.yaml").read_text())["jobs"]["build"]["steps"]
    setups = [step for step in steps if "/release-candidate-dependencies@" in step.get("uses", "")]
    assert setups
    for step in setups:
        assert step["with"]["row-entrypoint"] == f"{family}-release"
        assert step["with"]["row-identity"] == "${{ inputs.candidate-row-identity }}"
        assert "row-inputs" not in step["with"]
    candidate = yaml.safe_load((WORKFLOWS / f"{family}-candidate.yaml").read_text())["jobs"]["build"]["steps"]
    for step in candidate:
        assert "row-entrypoint" not in (step.get("with") or {})

def test_a_step_condition_the_generator_cannot_extend_fails():
    text = (WORKFLOWS / "wheels-build-candidate.yaml").read_text()
    changed = text.replace(
        "        if: ${{ env.RAPIDS_RELEASE_CANDIDATE_SCCACHE == 'true' }}",
        "        if: >-\n          ${{ env.RAPIDS_RELEASE_CANDIDATE_SCCACHE == 'true' }}",
        1,
    )
    assert changed != text
    with pytest.raises(entrypoints.EntrypointError, match="condition"):
        entrypoints.generate(changed, "wheels-build")
