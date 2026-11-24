# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
"""A train-generated build names the producer commit it checked out, not the calling repository's commit."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yaml"))
DEPENDENCIES = "rapidsai/shared-actions/release-candidate-dependencies@"
NAMED_PRODUCER = "inputs.candidate-producer-repository-id != ''"


def dependency_steps():
    for path in WORKFLOWS:
        document = yaml.safe_load(path.read_text())
        for job_name, job in (document.get("jobs") or {}).items():
            steps = job.get("steps") or []
            for index, step in enumerate(steps):
                if str(step.get("uses", "")).startswith(DEPENDENCIES):
                    yield pytest.param(path.name, steps, index, id=f"{path.name}:{job_name}:{index}")


STEPS = list(dependency_steps())


def test_dependency_steps_exist():
    # Guard against the parametrized test silently covering nothing.
    assert len(STEPS) >= 9


@pytest.mark.parametrize(("workflow", "steps", "index"), STEPS)
def test_named_producer_also_names_its_commit(workflow, steps, index):
    inputs = steps[index]["with"]
    if workflow == "ci-images-build-release.yaml":
        # This entrypoint only ever builds ci-imgs, at the commit the train names.
        assert inputs["producer-repository"] == "rapidsai/ci-imgs"
        assert inputs["producer-sha"] == "${{ inputs.sha }}"
        return
    source = "inputs.sha || github.sha" if workflow == "custom-job-candidate.yaml" else "env.RAPIDS_SHA"
    # The two inputs are gated on the same condition, so they are supplied together or not at all.
    assert inputs["producer-repository"] == f"${{{{ {NAMED_PRODUCER} && inputs.repo || '' }}}}"
    assert inputs["producer-sha"] == f"${{{{ {NAMED_PRODUCER} && ({source}) || '' }}}}"


@pytest.mark.parametrize(("workflow", "steps", "index"), STEPS)
def test_producer_commit_is_resolved_before_dependencies(workflow, steps, index):
    if workflow in ("custom-job-candidate.yaml", "ci-images-build-release.yaml"):
        pytest.skip("this entrypoint takes the producer commit from inputs.sha")
    earlier = steps[:index]
    sets_sha = any(
        "RAPIDS_SHA=" in str(step.get("run", "")) or "/rapids-github-info@" in str(step.get("uses", ""))
        for step in earlier
    )
    assert sets_sha, f"{workflow} passes env.RAPIDS_SHA before any step sets it"
