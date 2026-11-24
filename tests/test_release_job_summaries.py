# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
"""Release-build rows must not each add a job summary: a release run has one row per matrix entry."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*-release.yaml"))


def docker_build_steps():
    for path in RELEASE_WORKFLOWS:
        document = yaml.safe_load(path.read_text())
        for job_name, job in (document.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                if str(step.get("uses", "")).startswith("docker/build-push-action@"):
                    yield pytest.param(step, id=f"{path.name}:{job_name}:{index}")


STEPS = list(docker_build_steps())


def test_release_workflows_have_docker_builds():
    # Guard against the parametrized test silently covering nothing.
    assert STEPS


@pytest.mark.parametrize("step", STEPS)
def test_docker_build_adds_no_summary_or_build_record(step):
    env = step.get("env") or {}
    # build-push-action defaults both to true: a summary block and a .dockerbuild artifact per row.
    assert env.get("DOCKER_BUILD_SUMMARY") is False
    assert env.get("DOCKER_BUILD_RECORD_UPLOAD") is False
