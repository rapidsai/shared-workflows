#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "${TEST_ROOT}"' EXIT
export DOCS_PROJECT=cuvs-java GROUP_ID=com.nvidia.cuvs ARTIFACT_ID=cuvs-java VERSION=26.12.0
export GITHUB_REPOSITORY=NVIDIA/cuvs GITHUB_REPOSITORY_ID=721217347
export GITHUB_RUN_ID=42 GITHUB_RUN_ATTEMPT=1 GITHUB_REF=refs/tags/v26.12.00
export GITHUB_SHA=0123456789012345678901234567890123456789
export SOURCE_GIT_SHA=${GITHUB_SHA} DEPLOYMENT_STATE=PUBLISHED DEPLOYMENT_ID=deployment-1
"${SCRIPT_DIR}/write_maven_publication_receipt.sh" --validate
"${SCRIPT_DIR}/write_maven_publication_receipt.sh" "${TEST_ROOT}/publication.json"
jq -e '.state == "PUBLISHED" and .docs_project == "cuvs-java" and .version == "26.12.0"
  and .source_repository_id == 721217347 and .workflow_run_id == 42
  and .workflow_run_attempt == 1 and .source_ref == "refs/tags/v26.12.00"' \
  "${TEST_ROOT}/publication.json" >/dev/null
for state in VALIDATED PUBLISHING FAILED; do
  if DEPLOYMENT_STATE=${state} "${SCRIPT_DIR}/write_maven_publication_receipt.sh" "${TEST_ROOT}/blocked.json"; then
    echo "unpublished state ${state} must not create a receipt" >&2
    exit 1
  fi
  [[ ! -f ${TEST_ROOT}/blocked.json ]]
done
for ref in refs/heads/main refs/pull/1/merge refs/tags/v26.10.0; do
  if GITHUB_REF=${ref} "${SCRIPT_DIR}/write_maven_publication_receipt.sh" --validate; then
    echo 'a mismatched/non-release ref must fail validation' >&2
    exit 1
  fi
done
if SOURCE_GIT_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  "${SCRIPT_DIR}/write_maven_publication_receipt.sh" --validate; then
  echo 'a source override must not produce a trusted release receipt' >&2
  exit 1
fi
echo 'Completed-publication receipt tests passed'
