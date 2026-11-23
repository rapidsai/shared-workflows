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
    assert {key: value for key, value in inputs.items() if key != "candidate-matrix"} == candidate[True][
        "workflow_call"
    ]["inputs"]
    # The policy runs first, then every original step in order.
    names = [step.get("name") or step.get("uses") for step in build["steps"]]
    assert names[:2] == [
        "Evaluate the build policy for this release build",
        "Require a release-build candidate without image preflight",
    ]
    assert len(build["steps"]) == len(original["steps"]) + 2
    assert "needs." not in (WORKFLOWS / f"{family}-release.yaml").read_text().split("jobs:", 1)[1]


def run_gate(family, run_candidate, image_preflight, role):
    step = yaml.safe_load((WORKFLOWS / f"{family}-release.yaml").read_text())["jobs"]["build"]["steps"][1]
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
