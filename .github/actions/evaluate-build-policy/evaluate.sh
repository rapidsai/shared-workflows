#!/usr/bin/env bash
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
set -euo pipefail

inputs="${BUILD_INPUTS_JSON}"
build_type="${BUILD_TYPE_OVERRIDE:-}"
if [[ -z "${build_type}" ]]; then
  build_type="$(jq -er '.build_type // "" | strings' <<<"${inputs}")"
fi

if [[ "${VALIDATE_INPUTS}" == true ]]; then
  case "${build_type}" in
    release-candidate) ;;
    branch|nightly|pull-request)
      if ! jq -en --argjson inputs "${inputs}" --argjson defaults "${CANDIDATE_DEFAULTS_JSON}" \
        '$defaults | to_entries | all(.[]; $inputs[.key] == .value)' >/dev/null; then
        echo "Candidate inputs are valid only for release-candidate builds" >&2
        exit 1
      fi
      ;;
    *) echo "Invalid build_type: ${build_type}" >&2; exit 1 ;;
  esac
fi

# GitHub expression equality and startsWith are case-insensitive. Keep that
# behavior for security guards after the entry point's exact input validation.
shopt -s nocasematch

candidate=false
[[ "${build_type}" != release-candidate ]] || candidate=true

# Keep the dispatch-event and ref checks independent of the caller's build type.
allow_general_ci=true
if [[ "${candidate}" == true || "${DISPATCH_BUILD_TYPE}" == release-candidate ]]; then
  allow_general_ci=false
fi
if [[ "${REF_TYPE}" == branch && "${REF_NAME}" == release-test-* ]]; then
  allow_general_ci=false
fi
if [[ "${HEAD_REF}" == release-test-* ]]; then
  allow_general_ci=false
fi

allow_publication="${allow_general_ci}"
branch="$(jq -er '.branch // "" | strings' <<<"${inputs}")"
if [[ "${branch}" == release-test-* || "${branch}" == refs/heads/release-test-* ]]; then
  allow_publication=false
fi
for ref in "${REF}" "${HEAD_REF}" "${branch}"; do
  case "${ref}" in
    agent/release-catalog-canary|refs/heads/agent/release-catalog-canary)
      allow_publication=false
      ;;
  esac
done

run_custom=true
if [[ "${CUSTOM_JOB}" == true && "${DISPATCH_BUILD_TYPE}" == release-candidate ]]; then
  script="$(jq -er '.script // "" | strings' <<<"${inputs}")"
  candidate_docs="$(jq -r '."candidate-docs" // false' <<<"${inputs}")"
  if [[ "${script}" == ci/build_docs.sh && "${candidate_docs}" != true ]]; then
    run_custom=false
  fi
fi

run_ci=false
run_candidate=false
if [[ "${run_custom}" == true ]]; then
  if [[ "${candidate}" == true ]]; then
    run_candidate=true
  else
    run_ci=true
  fi
fi

# Any supplied preflight field activates validation; partial opt-ins cannot
# silently fall back to the tagged image path.
image_preflight="$(jq -r '[
  ."candidate-image-train-json", ."candidate-image-preflight-json",
  ."candidate-image-preflight-sha256", ."candidate-image-consumer-id"
] | any(. != null and . != "")' <<<"${inputs}")"
caller_cache="$(jq -r '[
  ."cache-paths", ."cache-key-prefix", ."cache-key-files", ."cache-environment"
] | any(. != null and . != "")' <<<"${inputs}")"

{
  echo "run-ci=${run_ci}"
  echo "run-candidate=${run_candidate}"
  echo "allow-general-ci=${allow_general_ci}"
  echo "allow-publication=${allow_publication}"
  echo "image-preflight=${image_preflight}"
  echo "caller-cache=${caller_cache}"
} >>"${GITHUB_OUTPUT}"
