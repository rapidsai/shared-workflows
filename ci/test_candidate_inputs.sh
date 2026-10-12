#!/usr/bin/env bash
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
root="${CANDIDATE_TEST_ROOT:-${root}}"
workspace="$(mktemp -d)"
trap 'rm -rf "${workspace}"' EXIT
for workflow in conda-cpp-build conda-python-build wheels-build custom-job; do
  file="${root}/.github/workflows/${workflow}.yaml"
  if rg -q 'role/gha-oidc-rapids-release-candidates' "${file}"; then
    echo "candidate producer cannot use the controller role: ${file}" >&2
    exit 1
  fi
  rg -q 'role/gha-oidc-rc-producer-\$\{\{ github.repository_id \}\}' "${file}"
  rg -q 'uses: rapidsai/shared-actions/release-candidate-images@[0-9a-f]{40}' "${file}"
  rg -q 'fromJSON\(needs.candidate-inputs.outputs.images\)' "${file}"
  # Check every general-role and cache-action step, including their actual
  # guards. Retain exactly one ordinary-build occurrence of each integration.
  awk -v guard="inputs.build_type != 'release-candidate'" '
    /^      - / {condition=""}
    /if:/ {condition=$0}
    /role-to-assume:.*vars.AWS_ROLE_ARN/ {
      roles++
      if (!index(condition, guard) || index(condition, "||") || index(condition, "&&")) exit 1
    }
    /uses: rapidsai\/shared-actions\/setup-sccache-dist@/ {
      caches++
      if (!index(condition, guard) || index(condition, "||") || index(condition, "&&")) exit 1
    }
    END {if (roles != 1 || caches != 1) exit 1}
  ' "${file}"
  if rg -q 'name: Restore build credentials for sccache' "${file}"; then
    echo "candidate cannot restore general cache credentials: ${file}" >&2
    exit 1
  fi
  awk '
    /name: Require clean candidate cache policy/ {found=1}
    found && /run: \|/ {body=1; next}
    body && /^          / {sub(/^          /, ""); print; next}
    body && NF {exit}
  ' "${file}" >"${workspace}/cache-gate.sh"
  test -s "${workspace}/cache-gate.sh"
  export TRAIN_FILE="${workspace}/train.json"
  printf '{"candidate_build_policy":{"sccache":false}}\n' >"${TRAIN_FILE}"
  bash -e -o pipefail "${workspace}/cache-gate.sh"
  for rejected in true null; do
    printf '{"candidate_build_policy":{"sccache":%s}}\n' "${rejected}" >"${TRAIN_FILE}"
    if bash -e -o pipefail "${workspace}/cache-gate.sh" >/dev/null 2>&1; then
      echo "candidate cache gate accepted ${rejected}: ${file}" >&2
      exit 1
    fi
  done
done
if [[ -z "${CANDIDATE_TEST_ROOT:-}" ]]; then
  # A negative check must fail the whole test program, not merely return false
  # inside a shell expression that errexit can ignore.
  mkdir -p "${workspace}/mutant/.github/workflows"
  for workflow in conda-cpp-build conda-python-build wheels-build custom-job; do
    cp "${root}/.github/workflows/${workflow}.yaml" "${workspace}/mutant/.github/workflows/"
  done
  mutant="${workspace}/mutant/.github/workflows/conda-cpp-build.yaml"
  awk -v guard="inputs.build_type != 'release-candidate'" '
    !removed && /- if:/ && index($0, guard) {removed=1; drop=1; next}
    drop && /^        uses:/ {sub(/^        uses:/, "      - uses:"); drop=0}
    {print}
  ' "${mutant}" >"${workspace}/unguarded.yaml"
  mv "${workspace}/unguarded.yaml" "${mutant}"
  if CANDIDATE_TEST_ROOT="${workspace}/mutant" bash "${BASH_SOURCE[0]}" >/dev/null 2>&1; then
    echo "tests failed to reject an unguarded general producer role" >&2
    exit 1
  fi
fi
echo "scoped producer and validated-image wiring passed"
