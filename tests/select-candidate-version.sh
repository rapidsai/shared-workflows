#!/usr/bin/env bash
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary_directory="$(mktemp -d)"
trap 'rm -rf "${temporary_directory}"' EXIT

mkdir -p "${temporary_directory}/bin" "${temporary_directory}/runner-temp"
cat >"${temporary_directory}/bin/aws" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$#" != 4 || "$1" != "s3" || "$2" != "cp" ]]; then
  echo "unexpected aws invocation" >&2
  exit 2
fi
if [[ "$3" != "s3://rapids-release-candidates/train-state/${RELEASE_CANDIDATE_TRAIN_SHA256}/release-train.json" ]]; then
  echo "selector requested a train outside its digest-bound object key" >&2
  exit 2
fi
cp "${CANDIDATE_TRAIN_FIXTURE}" "$4"
EOF
chmod +x "${temporary_directory}/bin/aws"

make_train() {
  local unit_version="${1:-}"
  local base
  base="$(jq -cn --arg unit_version "${unit_version}" '
    "0123456789abcdef0123456789abcdef01234567" as $cli_sha
    | "abcdef0123456789abcdef0123456789abcdef01" as $dask_sha
    | {
      schema_version: 2,
      version_contract: {
        package_versions: {conda: "26.12.00", wheel: "26.12.0"},
        unit_package_versions: (if $unit_version == "" then {} else {"conda:rapids-cli": $unit_version} end)
      },
      source_revisions: {"rapids-cli": $cli_sha, "rapids-dask-dependency": $dask_sha},
      github_repositories: {"rapids-cli": "rapidsai/rapids-cli", "rapids-dask-dependency": "rapidsai/rapids-dask-dependency"},
      release_units: [
        {id: "conda:rapids-cli", repository: "rapids-cli", artifact_family: "conda", version_profile: "conda", source_revision: $cli_sha},
        {id: "wheel:rapids-cli", repository: "rapids-cli", artifact_family: "wheel", version_profile: "wheel", source_revision: $cli_sha},
        {id: "conda:rapids-dask-dependency", repository: "rapids-dask-dependency", artifact_family: "conda", version_profile: "conda", source_revision: $dask_sha},
        {id: "wheel:rapids-dask-dependency", repository: "rapids-dask-dependency", artifact_family: "wheel", version_profile: "wheel", source_revision: $dask_sha}
      ]
    }')"
  local train_sha
  train_sha="$(jq -cSj . <<<"${base}" | sha256sum | awk '{print $1}')"
  jq -cn --argjson base "${base}" --arg train_sha "${train_sha}" '$base + {train_sha256: $train_sha}' \
    >"${temporary_directory}/train.json"
  printf '%s' "${train_sha}"
}

run_selector() {
  local digest="$1"
  local family="$2"
  local env_file="$3"
  local repository="${4:-rapids-cli}"
  local source_sha="${5:-0123456789abcdef0123456789abcdef01234567}"
  PATH="${temporary_directory}/bin:${PATH}" \
    CANDIDATE_TRAIN_FIXTURE="${temporary_directory}/train.json" \
    RELEASE_CANDIDATE_TRAIN_SHA256="${digest}" \
    RELEASE_CANDIDATE_ARTIFACT_FAMILY="${family}" \
    RELEASE_CANDIDATE_REPOSITORY="rapidsai/${repository}" \
    RELEASE_CANDIDATE_SOURCE_SHA="${source_sha}" \
    RUNNER_TEMP="${temporary_directory}/runner-temp" \
    GITHUB_ENV="${env_file}" \
    bash "${root}/.github/actions/select-candidate-version/select.sh"
}

# No source VERSION file exists in this test workspace. The family-specific
# values come from the verified immutable train's version contract.
expected_digest="$(make_train)"
: >"${temporary_directory}/environment"
run_selector "${expected_digest}" conda "${temporary_directory}/environment"
grep -Fxq 'RAPIDS_RELEASE_CANDIDATE_VERSION=26.12.00' "${temporary_directory}/environment"

: >"${temporary_directory}/environment"
run_selector "${expected_digest}" wheel "${temporary_directory}/environment"
grep -Fxq 'RAPIDS_RELEASE_CANDIDATE_VERSION=26.12.0' "${temporary_directory}/environment"

# A second repository exercises family-profile selection against its own
# frozen source identity, rather than assuming the selector is CLI-specific.
: >"${temporary_directory}/environment"
run_selector "${expected_digest}" conda "${temporary_directory}/environment" \
  rapids-dask-dependency abcdef0123456789abcdef0123456789abcdef01
grep -Fxq 'RAPIDS_RELEASE_CANDIDATE_VERSION=26.12.00' "${temporary_directory}/environment"

# A unit override must take precedence over its family's default version.
expected_digest="$(make_train 0.9.0)"
: >"${temporary_directory}/environment"
run_selector "${expected_digest}" conda "${temporary_directory}/environment"
grep -Fxq 'RAPIDS_RELEASE_CANDIDATE_VERSION=0.9.0' "${temporary_directory}/environment"

# Reject altered train bytes before writing an environment value.
jq '.version_contract.package_versions.conda = "99.99.99"' \
  "${temporary_directory}/train.json" >"${temporary_directory}/tampered.json"
mv "${temporary_directory}/tampered.json" "${temporary_directory}/train.json"
: >"${temporary_directory}/environment"
if run_selector "${expected_digest}" conda "${temporary_directory}/environment"; then
  echo "selector accepted a train whose bytes do not match its digest" >&2
  exit 1
fi
if [[ -s "${temporary_directory}/environment" ]]; then
  echo "selector wrote a candidate version before digest validation" >&2
  exit 1
fi

# A valid digest cannot authorize a different selected source revision.
expected_digest="$(make_train)"
: >"${temporary_directory}/environment"
if RELEASE_CANDIDATE_SOURCE_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  PATH="${temporary_directory}/bin:${PATH}" \
  CANDIDATE_TRAIN_FIXTURE="${temporary_directory}/train.json" \
  RELEASE_CANDIDATE_TRAIN_SHA256="${expected_digest}" \
  RELEASE_CANDIDATE_ARTIFACT_FAMILY=conda \
  RELEASE_CANDIDATE_REPOSITORY=rapidsai/rapids-cli \
  RUNNER_TEMP="${temporary_directory}/runner-temp" \
  GITHUB_ENV="${temporary_directory}/environment" \
  bash "${root}/.github/actions/select-candidate-version/select.sh" >/dev/null 2>&1; then
  echo "selector accepted a source SHA that differs from the train" >&2
  exit 1
fi
if [[ -s "${temporary_directory}/environment" ]]; then
  echo "selector wrote a candidate version for the mismatched source SHA" >&2
  exit 1
fi

# A train without the requested unit must also fail closed.
base="$(jq '.release_units = []' "${temporary_directory}/train.json" | jq 'del(.train_sha256)')"
expected_digest="$(jq -cSj . <<<"${base}" | sha256sum | awk '{print $1}')"
jq -cn --argjson base "${base}" --arg train_sha "${expected_digest}" '$base + {train_sha256: $train_sha}' \
  >"${temporary_directory}/train.json"
: >"${temporary_directory}/environment"
if run_selector "${expected_digest}" conda "${temporary_directory}/environment"; then
  echo "selector accepted a train without the requested artifact unit" >&2
  exit 1
fi
if [[ -s "${temporary_directory}/environment" ]]; then
  echo "selector wrote a candidate version for a missing artifact unit" >&2
  exit 1
fi

echo "Candidate train version selection tests passed."
