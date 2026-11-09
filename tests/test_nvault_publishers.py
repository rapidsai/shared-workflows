# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Credential boundary and executable failure-path checks for publishers."""

import os
import re
import subprocess  # nosec B404 -- execute reviewed workflow shell snippets with fixture environments
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PUBLISHERS = ["maven-publish", "conda-upload-packages", "wheels-publish"]
VAULT_ACTION = "hashicorp/vault-action@892a26828f195e65540a40b4768ae4571f51ebfc"


def workflow(name):
    document = yaml.safe_load((ROOT / f".github/workflows/{name}.yaml").read_text())
    # PyYAML uses YAML 1.1, which interprets GitHub's `on` key as a boolean.
    document["on"] = document.pop(True)
    return document


def publisher(name):
    return next(iter(workflow(name)["jobs"].values()))


@pytest.mark.parametrize("name", PUBLISHERS)
def test_vault_is_job_local_and_excludes_pr_events(name):
    document = workflow(name)
    job = publisher(name)
    condition = job["if"]
    assert "vars.NV_VAULT_ENABLED != 'true'" in condition
    assert "github.event_name != 'pull_request'" in condition
    assert "github.event_name != 'pull_request_target'" in condition
    assert "linux-amd64-cpu" in job["runs-on"]
    assert job.get("permissions", document["permissions"])["id-token"] == "write"
    assert "outputs" not in job
    assert "secrets" not in job
    for step in job["steps"]:
        if step.get("uses", "").startswith("hashicorp/vault-action@"):
            assert step["uses"] == VAULT_ACTION
            assert "vars.NV_VAULT_ENABLED == 'true'" in step["if"]
            assert "continue-on-error" not in step
            settings = step["with"]
            assert settings["method"] == "jwt"
            assert settings["url"] == "https://prod.vault.nvidia.com"
            assert settings["namespace"] == "swgpu-rapids"
            assert settings["jwtGithubAudience"] == "https://prod.vault.nvidia.com:443"
            assert settings["exportEnv"] is False
            assert settings["exportToken"] is False
            assert "token" not in settings
            assert "ignoreNotFound" not in settings


@pytest.mark.parametrize("name", PUBLISHERS)
def test_enabled_vault_never_falls_back_to_legacy_secrets(name):
    job = publisher(name)
    vault_steps = {
        step["id"]: index
        for index, step in enumerate(job["steps"])
        if step.get("uses") == VAULT_ACTION
    }
    observed = []
    for index, step in enumerate(job["steps"]):
        for value in step.get("env", {}).values():
            if "secrets." not in value:
                continue
            # The legacy branch must be first and explicitly disabled in Vault
            # mode. Reversing these operands would leak through to old secrets
            # when a Vault output is empty.
            match = re.fullmatch(
                r"\$\{\{ vars\.NV_VAULT_ENABLED != 'true' && secrets\.(\w+) \|\| steps\.([\w-]+)\.outputs\.\1 \}\}",
                value,
            )
            assert match, value
            assert vault_steps[match[2]] < index
            observed.append(match[1])
    assert observed


CONFIGURATIONS = [
    (name, step)
    for name in PUBLISHERS
    for step in publisher(name)["steps"]
    if step["name"] == "Validate Vault configuration"
]


@pytest.mark.parametrize(
    ("name", "step"),
    CONFIGURATIONS,
    ids=[f"{name}-{i}" for i, (name, _) in enumerate(CONFIGURATIONS)],
)
def test_configuration_fails_before_auth_without_disclosing_values(name, step):
    values = dict.fromkeys(step["env"], "private-config-sentinel")
    assert "NV_VAULT_JWT_PATH" in values
    for missing in values:
        environment = {**os.environ, **values, missing: ""}
        result = subprocess.run(  # nosec B603 -- reviewed workflow source, fixed executable and fixture values
            ["/bin/bash", "-c", step["run"]],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0, (name, missing)
        assert missing in result.stdout
        assert "private-config-sentinel" not in result.stdout + result.stderr
    result = subprocess.run(  # nosec B603 -- reviewed workflow source, fixed executable and fixture values
        ["/bin/bash", "-c", step["run"]],
        env={**os.environ, **values},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.parametrize("name", ["conda-upload-packages", "wheels-publish"])
def test_missing_upload_token_stops_before_any_tool_or_network_access(name):
    for step in publisher(name)["steps"]:
        for variable in ("RAPIDS_CONDA_TOKEN", "TWINE_PASSWORD"):
            if variable not in step.get("env", {}):
                continue
            # An empty PATH ensures success cannot depend on installed clients
            # or cached login state. The shell must fail on the missing token.
            result = subprocess.run(  # nosec B603 -- reviewed workflow source and empty fixture environment
                ["/bin/bash", "-c", step["run"]],
                env={"PATH": "", variable: ""},
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode != 0
            assert f"{variable}: must be set" in result.stderr
            assert "command not found" not in result.stderr


def test_maven_vault_callers_can_omit_github_secrets():
    document = workflow("maven-publish")
    assert (
        document["on"]["workflow_call"]["inputs"]["artifact-name"]["required"] is True
    )
    for definition in document["on"]["workflow_call"]["secrets"].values():
        assert definition["required"] is False


def test_conda_reads_only_the_selected_channel_and_preserves_dual_publish():
    steps = {
        step.get("id"): step for step in publisher("conda-upload-packages")["steps"]
    }
    assert (
        "steps.conda-upload-destination.outputs.is_release == 'true'"
        in steps["vault-release"]["if"]
    )
    assert (
        "steps.conda-upload-destination.outputs.is_release == 'false' || inputs.dual_publish"
        in steps["vault-nightly"]["if"]
    )
    assert "NV_VAULT_CONDA_RELEASE_PATH" in steps["vault-release"]["with"]["secrets"]
    assert "NV_VAULT_CONDA_NIGHTLY_PATH" in steps["vault-nightly"]["with"]["secrets"]


def test_pypi_credential_is_read_only_for_requested_release_upload():
    steps = {step.get("id"): step for step in publisher("wheels-publish")["steps"]}
    condition = steps["vault-pypi"]["if"]
    assert "inputs.publish_to_pypi" in condition
    assert "steps.check_if_release.outputs.is_release_build == 'true'" in condition
    assert "NV_VAULT_PYPI_ROLE" in steps["vault-pypi"]["with"]["role"]
    assert "NV_VAULT_WHEELS_ROLE" in steps["vault-wheels"]["with"]["role"]
