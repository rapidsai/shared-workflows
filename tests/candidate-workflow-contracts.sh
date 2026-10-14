#!/usr/bin/env bash
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workflows=(
  conda-cpp-build.yaml
  conda-python-build.yaml
  wheels-build.yaml
  custom-job.yaml
)
# The GitHub expression is intentionally a literal assertion value.
# shellcheck disable=SC2016
expected_role='arn:aws:iam::279114543810:role/gha-oidc-rc-producer-${{ github.repository_id }}'

for workflow in "${workflows[@]}"; do
  path="${root}/.github/workflows/${workflow}"
  if ! rg -Fq "${expected_role}" "${path}"; then
    echo "${workflow} has no caller-specific candidate producer role" >&2
    exit 1
  fi
  if rg -q 'gha-oidc-rapids-release-candidates|gha-oidc-rc-producer-[0-9]+' "${path}"; then
    echo "${workflow} contains a shared or hard-coded candidate producer role" >&2
    exit 1
  fi
  while IFS=: read -r line_number _; do
    start_line=$((line_number - 3))
    role_config="$(sed -n "${start_line},${line_number}p" "${path}")"
    if ! rg -Fq 'role-duration-seconds: 3600' <<<"${role_config}" ||
      rg -Fq 'role-duration-seconds: 43200' <<<"${role_config}"; then
      echo "${workflow} candidate producer role must request a 3,600-second session" >&2
      exit 1
    fi
  done < <(rg -n -F --no-heading "${expected_role}" "${path}" | cut -d: -f1)
done

candidate_action_paths=(
  "${root}/.github/workflows/conda-cpp-build.yaml"
  "${root}/.github/workflows/conda-python-build.yaml"
  "${root}/.github/workflows/wheels-build.yaml"
  "${root}/.github/workflows/custom-job.yaml"
  "${root}/.github/workflows/compute-matrix.yaml"
  "${root}/.github/actions/prepare-matrix-dispatch/action.yaml"
)
if rg -nP 'uses:\s+[^\s@]+@(?![a-f0-9]{40}(?:\s|$))[^\s]+' "${candidate_action_paths[@]}"; then
  echo "candidate build paths must pin external actions and workflows to full commit SHAs" >&2
  exit 1
fi
matrix_workflow="${root}/.github/workflows/compute-matrix.yaml"
matrix_dispatch_action="${root}/.github/actions/prepare-matrix-dispatch/action.yaml"
if ! rg -Fq 'action-repo: ${{ job.workflow_repository }}' "${matrix_workflow}" ||
  ! rg -Fq 'action-ref: ${{ job.workflow_sha }}' "${matrix_workflow}" ||
  rg -Fq "|| 'main'" "${matrix_dispatch_action}" ||
  ! rg -Fq 'name: Validate shared-workflows implementation source' "${matrix_dispatch_action}" ||
  ! rg -Fq 'ACTION_PATH: ${{ github.action_path }}' "${matrix_dispatch_action}"; then
  echo "candidate matrix checkout must use the exact reusable-workflow repository and SHA" >&2
  exit 1
fi
bash "${root}/tests/prepare-matrix-dispatch-pins.sh"

pins="$(rg -o 'uses: rapidsai/shared-actions/(release-candidate-[^@[:space:]]+|release-catalog-dispatch)@[[:alnum:]]+' "${root}/.github/workflows" \
  | sed 's/.*@//' | sort -u)"
if [[ "$(printf '%s\n' "${pins}" | wc -l | tr -d ' ')" != 1 ]] ||
  [[ ! "${pins}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "candidate implementation actions must share one full commit SHA; found: ${pins}" >&2
  exit 1
fi

for workflow in conda-cpp-build.yaml conda-python-build.yaml wheels-build.yaml; do
  path="${root}/.github/workflows/${workflow}"
  if ! rg -Fq "if: \${{ inputs.upload-artifacts && inputs.build_type != 'release-candidate' }}" "${path}"; then
    echo "${workflow} must not upload public GitHub artifacts for candidates" >&2
    exit 1
  fi
done

declare -A candidate_version_families=(
  [conda-cpp-build.yaml]=conda
  [conda-python-build.yaml]=conda
  [wheels-build.yaml]=wheel
)
for workflow in "${!candidate_version_families[@]}"; do
  path="${root}/.github/workflows/${workflow}"
  version_step="$(rg -n -F 'uses: $/.github/actions/select-candidate-version' "${path}" | cut -d: -f1)"
  dependencies_step="$(rg -n -F 'name: Prepare candidate dependencies and build policy' "${path}" | cut -d: -f1)"
  if [[ -z "${version_step}" || -z "${dependencies_step}" || "${version_step}" -ge "${dependencies_step}" ]] ||
    ! rg -Fq "artifact-family: ${candidate_version_families[${workflow}]}" "${path}" ||
    ! rg -Fq 'repository: ${{ inputs.repo || github.repository }}' "${path}" ||
    ! rg -Fq 'source-sha: ${{ env.RAPIDS_SHA }}' "${path}" ||
    rg -Fq 'release-candidate source checkout has no VERSION file' "${path}"; then
    echo "${workflow} must select its version from the frozen train before candidate dependencies are installed" >&2
    exit 1
  fi
done
for workflow in conda-python-build.yaml wheels-build.yaml; do
  path="${root}/.github/workflows/${workflow}"
  if ! rg -Fq 'export SETUPTOOLS_SCM_PRETEND_VERSION="${RAPIDS_RELEASE_CANDIDATE_VERSION}"' "${path}"; then
    echo "${workflow} must pass the frozen version to its source build only" >&2
    exit 1
  fi
done
bash "${root}/tests/select-candidate-version.sh"

declare -A candidate_catalog_directories=(
  [conda-cpp-build.yaml]=.release-candidate-artifacts/conda
  [conda-python-build.yaml]=.release-candidate-artifacts/conda
  [wheels-build.yaml]=.release-candidate-artifacts/wheels
)
for workflow in "${!candidate_catalog_directories[@]}"; do
  path="${root}/.github/workflows/${workflow}"
  artifact_directory="${candidate_catalog_directories[${workflow}]}"
  if ! rg -Fq "\"artifact_directory\": \"${artifact_directory}\"" "${path}" ||
    ! rg -Fq 'cd "${GITHUB_WORKSPACE}"' "${path}" ||
    ! rg -Fq 'find "${source_directory}" -type f' "${path}" ||
    ! rg -Fq 'cp -- "${package}" "${destination}"' "${path}" &&
      ! rg -Fq 'cp -- "${wheel}" "${destination}"' "${path}"; then
    echo "${workflow} must stage only regular package files into its workspace-relative release catalog directory" >&2
    exit 1
  fi
  if rg -Fq '"artifact_directory": ${{ toJSON(steps.package-name.outputs.' "${path}"; then
    echo "${workflow} must not pass the absolute package-manager output directory to the catalog" >&2
    exit 1
  fi
done

custom="${root}/.github/workflows/custom-job.yaml"
rg -Fq "steps.candidate-context.outputs.custom-mode == 'artifact'" "${custom}"
rg -Fq "inputs.script != 'ci/build_docs.sh' || inputs.candidate-docs" "${custom}"
if rg -Fq 'name: Restore build credentials' "${custom}"; then
  echo "custom-job.yaml must not restore the general CI role for candidate scripts" >&2
  exit 1
fi
rg -Fq "inputs.build_type == 'release-candidate' && inputs.release-catalog-config != ''" "${custom}"
rg -Fq 'candidate-dependency-family:' "${custom}"
rg -Fq 'candidate-skip-github-artifact-upload:' "${custom}"
rg -Fq 'inputs.candidate-skip-github-artifact-upload &&' "${custom}"
rg -Fq 'candidate-artifact-name:' "${custom}"
rg -Fq 'source-artifact-name: ${{ inputs.candidate-artifact-name || inputs.artifact-name }}' "${custom}"
rg -Fq 'candidate-docs-directory:' "${custom}"
rg -Fq 'candidate-matrix-arch: ${{ inputs.arch }}' "${custom}"
rg -Fq 'candidate-matrix-cuda-version: ${{ inputs.candidate-cuda-version }}' "${custom}"
rg -Fq 'candidate-matrix-python-version: ${{ inputs.candidate-python-version }}' "${custom}"
rg -Fq 'astral-sh/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86' "${custom}"
rg -Fq 'RAPIDS_DOCS_DIR="${DOCS_DIRECTORY}" rapids-upload-docs' "${custom}"
rg -Fq 'RAPIDS_DOCS_PUBLISH:' "${custom}"
rg -Fq "inputs.candidate-docs && 'false'" "${custom}"

for workflow in conda-cpp-build.yaml conda-python-build.yaml wheels-build.yaml custom-job.yaml; do
  path="${root}/.github/workflows/${workflow}"
  telemetry_steps="$(rg -c 'uses: rapidsai/shared-actions/telemetry-dispatch-(setup|stash-job-artifacts)@' "${path}")"
  telemetry_pins="$(rg -c 'SHARED_ACTIONS_REF: 83a68283134b22b0e7e7a072194358aaf3d3d90b' "${path}")"
  if [[ "${telemetry_steps}" != 2 || "${telemetry_pins}" != 2 ]]; then
    echo "${workflow} telemetry runtime checkout must use the same pinned shared-actions revision" >&2
    exit 1
  fi
  role_line="$(rg -n -F 'role-to-assume: ${{ vars.AWS_ROLE_ARN }}' "${path}" | cut -d: -f1)"
  role_guard="$(sed -n "$((role_line - 3)),$((role_line - 1))p" "${path}")"
  if ! rg -Fq "inputs.build_type != 'release-candidate' && github.event.inputs.build_type != 'release-candidate'" <<<"${role_guard}" ||
    ! rg -Fq "startsWith(github.ref_name, 'release-test-')" <<<"${role_guard}" ||
    ! rg -Fq "startsWith(github.head_ref, 'release-test-')" <<<"${role_guard}"; then
    echo "${workflow} general CI role must be unavailable for candidate and release-test refs" >&2
    exit 1
  fi
  if rg -Fq 'name: Restore build credentials' "${path}" ||
    ! rg -n -A2 'name: Setup sccache-dist' "${path}" |
      rg -Fq "if: \${{ inputs.build_type != 'release-candidate' && github.event.inputs.build_type != 'release-candidate' && !(github.ref_type == 'branch' && startsWith(github.ref_name, 'release-test-')) && !startsWith(github.head_ref, 'release-test-') }}"; then
    echo "${workflow} must not restore the general role or enable sccache for candidates" >&2
    exit 1
  fi
done

for workflow in conda-cpp-tests.yaml conda-python-tests.yaml wheels-test.yaml conda-cpp-post-build-checks.yaml; do
  path="${root}/.github/workflows/${workflow}"
  if ! rg -Fq "if: \${{ inputs.build_type != 'release-candidate' && github.event.inputs.build_type != 'release-candidate' }}" "${path}"; then
    echo "${workflow} must skip unsupported candidate jobs before assuming the general CI role" >&2
    exit 1
  fi
  role_line="$(rg -n -F 'role-to-assume: ${{ vars.AWS_ROLE_ARN }}' "${path}" | cut -d: -f1)"
  role_guard="$(sed -n "$((role_line - 3)),$((role_line - 1))p" "${path}")"
  if ! rg -Fq "startsWith(github.ref_name, 'release-test-')" <<<"${role_guard}" ||
    ! rg -Fq "startsWith(github.head_ref, 'release-test-')" <<<"${role_guard}"; then
    echo "${workflow} general CI role must be unavailable on release-test refs" >&2
    exit 1
  fi
  if [[ "${workflow}" != conda-cpp-post-build-checks.yaml ]] &&
    ! rg -n -A2 'name: Setup sccache-dist' "${path}" |
      rg -Fq "if: \${{ !(github.ref_type == 'branch' && startsWith(github.ref_name, 'release-test-')) && !startsWith(github.head_ref, 'release-test-') }}"; then
    echo "${workflow} must not use the general sccache role on release-test refs" >&2
    exit 1
  fi
done

declare -A expected_public_guards=(
  [conda-upload-packages.yaml]=1
  [wheels-publish.yaml]=1
  [publish-api-docs.yaml]=2
  [maven-publish.yaml]=1
)
for workflow in "${!expected_public_guards[@]}"; do
  path="${root}/.github/workflows/${workflow}"
  if ! rg -Fq "inputs.build_type != 'release-candidate'" "${path}" ||
    ! rg -Fq "github.event.inputs.build_type != 'release-candidate'" "${path}"; then
    echo "${workflow} must reject candidate mode from both caller input and dispatch event" >&2
    exit 1
  fi
  if ! rg -Fq "startsWith(github.ref_name, 'release-test-')" "${path}" ||
    ! rg -Fq "startsWith(github.head_ref, 'release-test-')" "${path}"; then
    echo "${workflow} must suppress public publishing on release-test branches and PR heads" >&2
    exit 1
  fi
  if ! rg -Fq "startsWith(inputs.branch, 'release-test-')" "${path}" ||
    ! rg -Fq "startsWith(inputs.branch, 'refs/heads/release-test-')" "${path}"; then
    echo "${workflow} must suppress publishing when the caller branch input names a release-test ref" >&2
    exit 1
  fi

  mapfile -t publisher_conditions < <(
    rg -F 'if:' "${path}" | rg -F "github.ref != 'agent/release-catalog-canary'"
  )
  if [[ "${#publisher_conditions[@]}" -ne "${expected_public_guards[${workflow}]}" ]]; then
    echo "${workflow} has ${#publisher_conditions[@]} publisher jobs guarded against the canary ref; expected ${expected_public_guards[${workflow}]}" >&2
    exit 1
  fi
  for publisher_condition in "${publisher_conditions[@]}"; do
    for ref_check in \
      "github.ref != 'refs/heads/agent/release-catalog-canary'" \
      "github.head_ref != 'agent/release-catalog-canary'" \
      "github.head_ref != 'refs/heads/agent/release-catalog-canary'"; do
      if ! rg -Fq "${ref_check}" <<<"${publisher_condition}"; then
        echo "${workflow} publisher condition is missing canary ref check: ${ref_check}" >&2
        exit 1
      fi
    done
    if ! rg -Fq "inputs.branch != 'agent/release-catalog-canary'" <<<"${publisher_condition}" ||
      ! rg -Fq "inputs.branch != 'refs/heads/agent/release-catalog-canary'" <<<"${publisher_condition}"; then
      echo "${workflow} publisher condition is missing caller branch input canary checks" >&2
      exit 1
    fi
  done
done

for workflow in conda-upload-packages.yaml wheels-publish.yaml publish-api-docs.yaml maven-publish.yaml; do
  path="${root}/.github/workflows/${workflow}"
  if rg -Fq '    secrets:' "${path}"; then
    echo "${workflow} must source publisher credentials from the protected environment, not workflow_call secrets" >&2
    exit 1
  fi
  if ! rg -Fq '    environment: public-publication' "${path}"; then
    echo "${workflow} publisher jobs must use the protected public-publication environment" >&2
    exit 1
  fi
done

wheels_workflow="${root}/.github/workflows/wheels-publish.yaml"
if ! rg -Fq 'Deprecated compatibility input; PyPI publishing is handled by Kitmaker. true is rejected.' "${wheels_workflow}" ||
  ! rg -Fq 'name: Reject retired direct PyPI publishing' "${wheels_workflow}" ||
  ! rg -Fq 'Direct PyPI publishing is retired; publish PyPI packages through Kitmaker.' "${wheels_workflow}" ||
  rg -Fq 'TWINE_PASSWORD' "${wheels_workflow}" ||
  rg -Fq 'RAPIDSAI_PYPI_TOKEN' "${wheels_workflow}" ||
  rg -Fq 'python3 -m twine upload' "${wheels_workflow}"; then
  echo "wheels-publish.yaml must reject the retired direct PyPI path and contain no PyPI publish credential or command" >&2
  exit 1
fi

maven_workflow="${root}/.github/workflows/maven-publish.yaml"
if [[ $(rg -Fc 'MAVEN_DEPLOY_USERNAME: ${{ secrets.MAVEN_DEPLOY_USERNAME }}' "${maven_workflow}") -ne 2 ]] ||
  rg -Fq 'MAVEN_DEPLOY_USERNAME: ${{ vars.MAVEN_DEPLOY_USERNAME }}' "${maven_workflow}"; then
  echo "maven-publish.yaml must read the deploy username from protected environment secrets for both destinations" >&2
  exit 1
fi

for job in publish-api-docs publish-versions-json; do
  if ! rg -n -A4 "^  ${job}:" "${root}/.github/workflows/publish-api-docs.yaml" | rg -Fq '    environment: public-publication'; then
    echo "publish-api-docs.yaml job ${job} must use the protected public-publication environment" >&2
    exit 1
  fi
done

if rg -Fq 'role-to-assume: ${{ vars.AWS_ROLE_ARN }}' "${root}/.github/workflows/publish-api-docs.yaml" ||
  ! rg -Fq 'role-to-assume: ${{ vars.PUBLICATION_AWS_ROLE_ARN }}' "${root}/.github/workflows/publish-api-docs.yaml"; then
  echo "API docs publishing must use the isolated publication AWS role" >&2
  exit 1
fi

for workflow in conda-upload-packages.yaml wheels-publish.yaml publish-api-docs.yaml maven-publish.yaml; do
  path="${root}/.github/workflows/${workflow}"
  if ! rg -Fq "startsWith(inputs.branch, 'release-test-')" "${path}" ||
    ! rg -Fq "startsWith(inputs.branch, 'refs/heads/release-test-')" "${path}"; then
    echo "${workflow} must guard its caller-supplied branch input" >&2
    exit 1
  fi
done

echo "Candidate workflow contracts passed."
