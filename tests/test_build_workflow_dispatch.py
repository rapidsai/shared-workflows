# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
"""Exercise dispatch validation and fail-closed candidate image selection offline."""

import json
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"
FAMILIES = ("conda-cpp-build", "conda-python-build", "wheels-build", "custom-job")


def load(name):
    return yaml.safe_load((WORKFLOWS / f"{name}.yaml").read_text())


def inputs(workflow):
    # PyYAML's YAML 1.1 resolver treats the GitHub 'on' key as a boolean.
    return workflow[True]["workflow_call"]["inputs"]


def default_values(workflow):
    return {
        key: (spec.get("default") or "")
        if spec["type"] == "string"
        else spec.get("default", False)
        for key, spec in inputs(workflow).items()
    }


def run_step(step, env, tmp_path):
    output = tmp_path / "github-output"
    output.write_text("")
    # Execute the checked-in workflow step, using inputs only through env.
    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(
        [bash, "-euo", "pipefail", "-c", step["run"]],
        env={**os.environ, **env, "GITHUB_OUTPUT": str(output)},
        text=True,
        capture_output=True,
        check=False,
    )  # nosec B603
    return result, dict(line.split("=", 1) for line in output.read_text().splitlines())


def validation_step(wrapper):
    action_dir = WORKFLOWS.parent / "actions/evaluate-build-policy"
    action = yaml.safe_load((action_dir / "action.yaml").read_text())
    step = action["runs"]["steps"][0]
    params = wrapper["jobs"]["validate"]["with"]
    step["env"] = {
        "ACTION_PATH": str(action_dir),
        "BUILD_TYPE_OVERRIDE": "",
        "CUSTOM_JOB": str(params.get("custom-job", False)).lower(),
        "VALIDATE_INPUTS": "true",
        "CANDIDATE_DEFAULTS_JSON": params["candidate-defaults-json"],
        "DISPATCH_BUILD_TYPE": "",
        "REF_TYPE": "branch",
        "REF_NAME": "main",
        "REF": "refs/heads/main",
        "HEAD_REF": "",
    }
    return step


def evaluate_policy(tmp_path, values=None, **context):
    step = validation_step(load("custom-job"))
    env = {
        **step["env"],
        "VALIDATE_INPUTS": "false",
        "CUSTOM_JOB": "false",
        "BUILD_INPUTS_JSON": json.dumps({"build_type": "branch", **(values or {})}),
        **context,
    }
    completed, output = run_step(step, env, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert set(output.values()) <= {"true", "false"}
    return output


@pytest.mark.parametrize(
    "values,context,general,publish",
    [
        ({}, {}, True, True),
        ({"build_type": "release-candidate"}, {}, False, False),
        ({}, {"DISPATCH_BUILD_TYPE": "release-candidate"}, False, False),
        ({}, {"REF_NAME": "release-test-pilot"}, False, False),
        ({}, {"HEAD_REF": "release-test-pilot"}, False, False),
        ({"branch": "release-test-pilot"}, {}, True, False),
        ({"branch": "refs/heads/release-test-pilot"}, {}, True, False),
        # The existing guard treats release-test tags differently from branches.
        ({}, {"REF_NAME": "release-test-pilot", "REF_TYPE": "tag"}, True, True),
        ({"branch": "release-test"}, {}, True, True),
        ({}, {"HEAD_REF": "release-test"}, True, True),
        ({"branch": "feature/release-test-pilot"}, {}, True, True),
        ({"build_type": ""}, {}, True, True),
        ({"build_type": "Release-Candidate"}, {}, False, False),
        ({}, {"DISPATCH_BUILD_TYPE": "RELEASE-CANDIDATE"}, False, False),
        ({}, {"REF_NAME": "Release-Test-pilot"}, False, False),
        ({}, {"HEAD_REF": "Release-Test-pilot"}, False, False),
        ({"branch": "REFS/HEADS/Release-Test-pilot"}, {}, True, False),
    ],
)
def test_credential_and_publication_boundary_cases(
    tmp_path, values, context, general, publish
):
    output = evaluate_policy(tmp_path, values, **context)
    assert output["allow-general-ci"] == str(general).lower()
    assert output["allow-publication"] == str(publish).lower()


@pytest.mark.parametrize("location", ["REF", "HEAD_REF", "branch"])
@pytest.mark.parametrize("prefix", ["", "refs/heads/", "REFS/HEADS/"])
@pytest.mark.parametrize("suffix", ["", "-other"])
def test_publication_canary_exact_matches(tmp_path, location, prefix, suffix):
    ref = prefix + "agent/release-catalog-canary" + suffix
    values = {"branch": ref} if location == "branch" else {}
    context = {} if location == "branch" else {location: ref}
    output = evaluate_policy(tmp_path, values, **context)
    assert output["allow-general-ci"] == "true"
    assert output["allow-publication"] == str(bool(suffix)).lower()


@pytest.mark.parametrize("build_type", ["branch", "release-candidate"])
@pytest.mark.parametrize("event", ["branch", "release-candidate"])
@pytest.mark.parametrize("docs", [True, False])
@pytest.mark.parametrize(
    "script", ["ci/build_docs.sh", "CI/BUILD_DOCS.SH", "ci/build_other.sh"]
)
def test_custom_documentation_gate_and_exclusive_routing(
    tmp_path, build_type, event, docs, script
):
    values = {"build_type": build_type, "candidate-docs": docs, "script": script}
    output = evaluate_policy(
        tmp_path, values, CUSTOM_JOB="true", DISPATCH_BUILD_TYPE=event
    )
    allowed = (
        event != "release-candidate" or script.lower() != "ci/build_docs.sh" or docs
    )
    assert output["run-ci"] == str(allowed and build_type == "branch").lower()
    assert (
        output["run-candidate"]
        == str(allowed and build_type == "release-candidate").lower()
    )


@pytest.mark.parametrize(
    "field,decision",
    [
        ("candidate-image-train-json", "image-preflight"),
        ("candidate-image-preflight-json", "image-preflight"),
        ("candidate-image-preflight-sha256", "image-preflight"),
        ("candidate-image-consumer-id", "image-preflight"),
        ("cache-paths", "caller-cache"),
        ("cache-key-prefix", "caller-cache"),
        ("cache-key-files", "caller-cache"),
        ("cache-environment", "caller-cache"),
    ],
)
def test_each_optional_field_independently_activates_decision(
    tmp_path, field, decision
):
    assert evaluate_policy(tmp_path, {field: ""})[decision] == "false"
    assert evaluate_policy(tmp_path, {field: "nonempty"})[decision] == "true"


@pytest.mark.parametrize(
    "name,publishers",
    [
        ("conda-upload-packages", ["upload"]),
        ("wheels-publish", ["wheel-publish"]),
        ("maven-publish", ["maven-publish"]),
        ("publish-api-docs", ["publish-api-docs", "publish-versions-json"]),
    ],
)
def test_publication_policy_precedes_protected_environment(name, publishers):
    workflow = load(name)
    policy = workflow["jobs"]["policy"]
    assert policy["uses"] == "./.github/workflows/evaluate-build-policy.yaml"
    assert policy["permissions"] == {}
    assert "secrets" not in policy
    assert policy["with"]["inputs-json"] == "${{ toJSON(inputs) }}"
    for job_id in publishers:
        job = workflow["jobs"][job_id]
        needs = job["needs"]
        assert "policy" in ([needs] if isinstance(needs, str) else needs)
        assert job["if"] == "${{ needs.policy.outputs.allow-publication == 'true' }}"
        assert job["environment"] == "public-publication"


def test_policy_uses_the_current_workflow_revision_without_credentials():
    workflow = load("evaluate-build-policy")
    assert workflow["permissions"] == {}
    step = workflow["jobs"]["policy"]["steps"][0]
    assert step["uses"] == "$/.github/actions/evaluate-build-policy"
    assert set(workflow["jobs"]["policy"]["outputs"]) == {
        "run-ci",
        "run-candidate",
        "allow-general-ci",
        "allow-publication",
        "image-preflight",
        "caller-cache",
    }


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "mode", ["branch", "nightly", "pull-request", "release-candidate"]
)
def test_supported_modes_accept_defaults(family, mode, tmp_path):
    wrapper = load(family)
    step = validation_step(wrapper)
    values = {**default_values(wrapper), "build_type": mode}
    result, _ = run_step(
        step,
        {**step["env"], "BUILD_TYPE": mode, "BUILD_INPUTS_JSON": json.dumps(values)},
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("family", FAMILIES)
def test_every_candidate_option_rejected_on_ci_and_allowed_on_candidates(
    family, tmp_path
):
    wrapper = load(family)
    step = validation_step(wrapper)
    candidate_defaults = json.loads(step["env"]["CANDIDATE_DEFAULTS_JSON"])
    expected_defaults = {
        key: value
        for key, value in default_values(wrapper).items()
        if key.startswith("candidate-") or key == "release-catalog-config"
    }
    assert candidate_defaults == expected_defaults
    for key, default in candidate_defaults.items():
        value = not default if isinstance(default, bool) else "non-default"
        for mode in ("branch", "nightly", "pull-request", "release-candidate"):
            values = {**default_values(wrapper), key: value, "build_type": mode}
            result, _ = run_step(
                step,
                {
                    **step["env"],
                    "BUILD_TYPE": mode,
                    "BUILD_INPUTS_JSON": json.dumps(values),
                },
                tmp_path,
            )
            assert (result.returncode == 0) == (mode == "release-candidate"), (
                key,
                mode,
                result.stderr,
            )


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    "mode", ["", "release", "Release-Candidate", "NIGHTLY", "invalid; exit 0"]
)
def test_unknown_modes_fail_before_dispatch(family, mode, tmp_path):
    wrapper = load(family)
    step = validation_step(wrapper)
    result, _ = run_step(
        step,
        {
            **step["env"],
            "BUILD_TYPE": mode,
            "BUILD_INPUTS_JSON": json.dumps(default_values(wrapper)),
        },
        tmp_path,
    )
    assert result.returncode != 0


@pytest.mark.parametrize("family", FAMILIES)
def test_dispatch_preserves_input_contracts_and_permissions(family):
    wrapper = load(family)
    jobs = wrapper["jobs"]
    for mode in ("ci", "candidate"):
        call = jobs[mode]
        child = load(f"{family}-{mode}")
        assert call["needs"] == "validate"
        assert call["if"] == f"${{{{ needs.validate.outputs.run-{mode} == 'true' }}}}"
        assert call["uses"] == f"./.github/workflows/{family}-{mode}.yaml"
        assert call["secrets"] == "inherit"
        assert child["permissions"] == wrapper["permissions"]
        assert set(call["with"]) == set(inputs(child))
        for key, spec in inputs(child).items():
            assert call["with"][key] == f"${{{{ inputs.{key} }}}}"
            for field in ("type", "required", "default"):
                assert spec.get(field) == inputs(wrapper)[key].get(field)


@pytest.mark.parametrize("family", FAMILIES)
def test_candidate_and_ci_authorities_are_separate(family):
    ci = load(f"{family}-ci")
    candidate = load(f"{family}-candidate")
    assert not any(key.startswith("candidate-") for key in inputs(ci))
    assert "build_type" not in inputs(candidate)
    ci_text = (WORKFLOWS / f"{family}-ci.yaml").read_text()
    candidate_text = (WORKFLOWS / f"{family}-candidate.yaml").read_text()
    assert "gha-oidc-rc-producer" not in ci_text
    assert "release-catalog-dispatch@" not in ci_text
    assert "vars.AWS_ROLE_ARN" not in candidate_text
    assert "setup-sccache-dist@" not in candidate_text
    assert "inputs.build_type" not in candidate_text.replace(
        "github.event.inputs.build_type", ""
    )
    assert ci["jobs"]["build"]["if"] == "${{ needs.policy.outputs.run-ci == 'true' }}"
    for step in ci["jobs"]["build"]["steps"]:
        if "vars.AWS_ROLE_ARN" in str(step) or "setup-sccache-dist@" in str(step):
            assert (
                step["if"] == "${{ needs.policy.outputs.allow-general-ci == 'true' }}"
            )
    for workflow in (ci, candidate):
        assert (
            workflow["jobs"]["policy"]["uses"]
            == "./.github/workflows/evaluate-build-policy.yaml"
        )
        assert workflow["jobs"]["policy"]["permissions"] == {}
        needs = workflow["jobs"]["build"]["needs"]
        assert "policy" in ([needs] if isinstance(needs, str) else needs)


@pytest.mark.parametrize("family", ["conda-python-build", "wheels-build"])
def test_source_scripts_keep_build_type_and_candidate_version(family):
    for mode in ("ci", "candidate"):
        workflow = load(f"{family}-{mode}")
        step_id = "python-build" if family == "conda-python-build" else "build-wheel"
        build = next(
            step
            for step in workflow["jobs"]["build"]["steps"]
            if step.get("id") == step_id
        )
        expected = (
            "release-candidate" if mode == "candidate" else "${{ inputs.build_type }}"
        )
        assert build["env"]["BUILD_TYPE"] == expected
        assert ("SETUPTOOLS_SCM_PRETEND_VERSION" in build["run"]) == (
            mode == "candidate"
        )


def select_matrix(tmp_path, **overrides):
    step = yaml.safe_load(
        (WORKFLOWS.parent / "actions/select-candidate-matrix/action.yaml").read_text()
    )["runs"]["steps"][0]
    env = {
        "ACTION_PATH": str(WORKFLOWS.parent / "actions/select-candidate-matrix"),
        "PREFLIGHT_REQUESTED": "false",
        "PREFLIGHT_RESULT": "skipped",
        "PREFLIGHT_MATRIX": "",
        "PREFLIGHT_SHA256": "",
        "LEGACY_MATRIX": json.dumps(
            {
                "include": [
                    {
                        "CUDA_VER": "13.3.0",
                        "LINUX_VER": "ubuntu24.04",
                        "PY_VER": "3.12",
                        "ARCH": "amd64",
                    }
                ]
            }
        ),
        **overrides,
    }
    return run_step(step, env, tmp_path)


def test_legacy_candidate_matrix_gets_explicit_tagged_image(tmp_path):
    result, output = select_matrix(tmp_path)
    assert result.returncode == 0, result.stderr
    row = json.loads(output["matrix"])["include"][0]
    assert (
        row["candidate_image_reference"]
        == "rapidsai/ci-conda:26.12-cuda13.3.0-ubuntu24.04-py3.12"
    )
    assert row["ARCH"] == "amd64"
    assert output["preflight-document-sha256"] == ""


def test_digest_candidate_matrix_and_document_hash_pass_through(tmp_path):
    matrix = json.dumps(
        {"include": [{"candidate_image_reference": "example@sha256:" + "a" * 64}]}
    )
    result, output = select_matrix(
        tmp_path,
        PREFLIGHT_REQUESTED="true",
        PREFLIGHT_RESULT="success",
        PREFLIGHT_MATRIX=matrix,
        PREFLIGHT_SHA256="b" * 64,
    )
    assert result.returncode == 0, result.stderr
    assert output == {"matrix": matrix, "preflight-document-sha256": "b" * 64}


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "success"])
def test_incomplete_preflight_never_falls_back(tmp_path, result):
    completed, output = select_matrix(
        tmp_path, PREFLIGHT_REQUESTED="true", PREFLIGHT_RESULT=result
    )
    assert completed.returncode != 0
    assert output == {}


@pytest.mark.parametrize("missing", ["PREFLIGHT_MATRIX", "PREFLIGHT_SHA256"])
def test_each_missing_validated_output_blocks_build(tmp_path, missing):
    env = {
        "PREFLIGHT_REQUESTED": "true",
        "PREFLIGHT_RESULT": "success",
        "PREFLIGHT_MATRIX": '{"include":[]}',
        "PREFLIGHT_SHA256": "b" * 64,
    }
    env[missing] = ""
    completed, output = select_matrix(tmp_path, **env)
    assert completed.returncode != 0
    assert output == {}


@pytest.mark.parametrize("result", ["success", "failure", "cancelled"])
def test_unrequested_preflight_must_be_skipped(tmp_path, result):
    completed, output = select_matrix(tmp_path, PREFLIGHT_RESULT=result)
    assert completed.returncode != 0
    assert output == {}


def test_invalid_legacy_matrix_cannot_start_build(tmp_path):
    completed, output = select_matrix(tmp_path, LEGACY_MATRIX="not-json")
    assert completed.returncode != 0
    assert output == {}


@pytest.mark.parametrize("decision", ["", "unknown", "TRUE"])
def test_missing_or_invalid_policy_never_falls_back_to_tagged_images(
    tmp_path, decision
):
    completed, output = select_matrix(tmp_path, PREFLIGHT_REQUESTED=decision)
    assert completed.returncode != 0
    assert output == {}


def test_matrix_preparation_blocks_container_start_and_binds_document():
    workflow = load("conda-cpp-build-candidate")
    prepare = workflow["jobs"]["prepare-build-matrix"]
    build = workflow["jobs"]["build"]
    assert prepare["needs"] == ["compute-matrix", "candidate-image-preflight", "policy"]
    assert "!cancelled()" in prepare["if"]
    assert "needs.compute-matrix.result == 'success'" in prepare["if"]
    assert "needs.policy.result == 'success'" in prepare["if"]
    assert prepare["steps"][0]["uses"] == "$/.github/actions/select-candidate-matrix"
    assert (
        prepare["steps"][0]["with"]["preflight-requested"]
        == "${{ needs.policy.outputs.image-preflight }}"
    )
    assert (
        workflow["jobs"]["candidate-image-preflight"]["if"]
        == "${{ needs.policy.outputs.image-preflight == 'true' }}"
    )
    assert build["needs"] == ["prepare-build-matrix", "policy"]
    assert (
        build["if"]
        == "${{ !cancelled() && needs.prepare-build-matrix.result == 'success' }}"
    )
    assert (
        build["strategy"]["matrix"]
        == "${{ fromJSON(needs.prepare-build-matrix.outputs.matrix) }}"
    )
    assert build["container"]["image"] == "${{ matrix.candidate_image_reference }}"
    verify = next(
        step
        for step in build["steps"]
        if step.get("name", "").startswith("Write the same image")
    )
    assert (
        verify["env"]["EXPECTED_DOCUMENT_SHA256"]
        == "${{ needs.prepare-build-matrix.outputs.preflight-document-sha256 }}"
    )
