# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
"""A wheel build whose script stages several wheels in one job is collected whole (rapids-work per-row-reuse.md).

rmm's ``ci/build_wheel.sh`` builds librmm and then rmm, which installs the librmm wheel just built, in one job. It
leaves each package's wheels in its own directory and reports it as the step output ``<package>_output_dir``. The
release build collects every reported directory into the row's one catalog artifact; a script that reports none
leaves its wheels in ``RAPIDS_WHEEL_BLD_OUTPUT_DIR``, as before.
"""

import json
import os
import shutil
import subprocess  # nosec B404
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
# The build containers' bash; macOS's /bin/bash 3.2 has no mapfile.
BASH = shutil.which("bash") or "bash"
WORKFLOWS = [ROOT / ".github/workflows/wheels-build-release.yaml", ROOT / ".github/workflows/wheels-build-candidate.yaml"]


def step(workflow, name):
    jobs = yaml.safe_load(workflow.read_text())["jobs"]
    return next(entry for entry in jobs["build"]["steps"] if entry.get("name") == name)


def run(tmp_path, name, outputs, *, workflow=WORKFLOWS[0], default_directory="wheelhouse"):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    selected = step(workflow, name)
    assert selected["env"]["BUILD_OUTPUTS"] == "${{ toJSON(steps.build-wheel.outputs) }}"
    env = {
        "PATH": os.environ["PATH"],
        "GITHUB_WORKSPACE": str(workspace),
        "BUILD_OUTPUTS": json.dumps(outputs),
        "WHEEL_OUTPUT_DIR": str(workspace / default_directory),
    }
    result = subprocess.run(  # noqa: S603 # nosec B603
        [BASH, "-e", "-c", selected["run"]], env=env, cwd=workspace, capture_output=True, text=True, check=False
    )
    staged = workspace / ".release-candidate-artifacts/wheels"
    files = sorted(path.relative_to(staged).as_posix() for path in staged.rglob("*.whl")) if staged.exists() else []
    return result, files, workspace


def wheel(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(b"wheel")


STAGE = "Stage candidate wheels for the release catalog"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=["release", "candidate"])
def test_a_staged_build_contributes_every_package_it_reports(tmp_path, workflow):
    workspace = tmp_path / "workspace"
    wheel(workspace / "wheel-output/librmm", "librmm_cu13-26.12.0-py3-none-manylinux_2_28_x86_64.whl")
    wheel(workspace / "wheel-output/rmm", "rmm_cu13-26.12.0-cp312-abi3-manylinux_2_28_x86_64.whl")
    outputs = {
        "librmm_artifact_name": "rmm_wheel_cpp_librmm_x86_64_cu13",
        "librmm_output_dir": str(workspace / "wheel-output/librmm"),
        "rmm_artifact_name": "rmm_wheel_python_rmm_x86_64_abi3_cu13",
        "rmm_output_dir": str(workspace / "wheel-output/rmm"),
    }
    result, files, _ = run(tmp_path, STAGE, outputs, workflow=workflow)
    assert result.returncode == 0, result.stderr
    assert files == [
        "librmm/librmm_cu13-26.12.0-py3-none-manylinux_2_28_x86_64.whl",
        "rmm/rmm_cu13-26.12.0-cp312-abi3-manylinux_2_28_x86_64.whl",
    ]
    shown, _, _ = run(tmp_path, "Show files to be uploaded", outputs, workflow=workflow)
    assert shown.returncode == 0, shown.stderr
    assert "librmm_cu13" in shown.stdout and "rmm_cu13" in shown.stdout


def test_a_single_package_build_still_uses_its_output_directory(tmp_path):
    wheel(tmp_path / "workspace/wheelhouse/sub", "rapids_logger-0.3.0-py3-none-manylinux_2_28_x86_64.whl")
    result, files, _ = run(tmp_path, STAGE, {"rapids-package-name": ""})
    assert result.returncode == 0, result.stderr
    assert files == ["sub/rapids_logger-0.3.0-py3-none-manylinux_2_28_x86_64.whl"]


@pytest.mark.parametrize(
    ("make", "message"),
    [
        # A reported directory without wheels: a package is missing from the row, so nothing is staged.
        (lambda ws: (ws / "wheel-output/rmm").mkdir(parents=True), "no wheels found under"),
        # A reported directory outside the checkout.
        (lambda ws: None, "outside the workspace"),
        # A package key that could name another path.
        (lambda ws: None, "invalid staged package"),
    ],
    ids=["empty", "outside", "bad-key"],
)
def test_a_staged_build_that_reports_a_bad_directory_stages_nothing(tmp_path, make, message):
    workspace = tmp_path / "workspace"
    wheel(workspace / "wheel-output/librmm", "librmm_cu13-26.12.0-py3-none-manylinux_2_28_x86_64.whl")
    make(workspace)
    outside = tmp_path / "elsewhere"
    wheel(outside, "rmm_cu13-26.12.0-cp312-abi3-manylinux_2_28_x86_64.whl")
    second = {
        "empty": ("rmm", str(workspace / "wheel-output/rmm")),
        "outside": ("rmm", str(outside)),
        "bad-key": ("../rmm", str(workspace / "wheel-output/librmm")),
    }[{"no wheels found under": "empty", "outside the workspace": "outside", "invalid staged package": "bad-key"}[message]]
    outputs = {"librmm_output_dir": str(workspace / "wheel-output/librmm"), f"{second[0]}_output_dir": second[1]}
    result, files, _ = run(tmp_path, STAGE, outputs)
    assert result.returncode != 0
    assert message in result.stderr
    assert files == []
