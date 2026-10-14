#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <output.json|--validate>" >&2
  exit 1
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_utils.sh"
require_maven_coordinates "${GROUP_ID}" "${ARTIFACT_ID}" "${VERSION}"
[[ ${DOCS_PROJECT} =~ ^[a-z][a-z0-9-]+$ ]] || fatal "invalid docs project"
[[ ${GITHUB_REPOSITORY} =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fatal "invalid source repository"
[[ ${GITHUB_REPOSITORY_ID} =~ ^[1-9][0-9]*$ ]] || fatal "invalid repository id"
[[ ${GITHUB_RUN_ID} =~ ^[1-9][0-9]*$ ]] || fatal "invalid workflow run id"
[[ ${GITHUB_RUN_ATTEMPT} =~ ^[1-9][0-9]*$ ]] || fatal "invalid workflow attempt"
[[ ${GITHUB_SHA} =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] || fatal "invalid source SHA"
[[ ${SOURCE_GIT_SHA} == "${GITHUB_SHA}" ]] || fatal "build source must match the tagged commit"
[[ ${GITHUB_REF} =~ ^refs/tags/v([0-9]{2})\.([0-9]{2})\.([0-9]+)$ ]] || fatal "release tag required"
major=${BASH_REMATCH[1]}
minor=${BASH_REMATCH[2]}
patch=$(printf '%s' "${BASH_REMATCH[3]}" | sed 's/^0*//')
[[ ${VERSION} == "${major}.${minor}.${patch:-0}" ]] || fatal "Maven version does not match the release tag"
if [[ $1 == --validate ]]; then
  exit 0
fi
[[ ${DEPLOYMENT_STATE} == PUBLISHED ]] || fatal "a completed publication is required"
[[ ${DEPLOYMENT_ID} =~ ^[A-Za-z0-9-]+$ ]] || fatal "invalid deployment id"
mkdir -p "$(dirname "$1")"
jq -n --arg project "${DOCS_PROJECT}" --arg group "${GROUP_ID}" \
  --arg artifact "${ARTIFACT_ID}" --arg version "${VERSION}" \
  --arg deployment "${DEPLOYMENT_ID}" --arg repository "${GITHUB_REPOSITORY}" \
  --argjson repository_id "${GITHUB_REPOSITORY_ID}" --arg ref "${GITHUB_REF}" \
  --arg sha "${GITHUB_SHA}" --argjson run_id "${GITHUB_RUN_ID}" \
  --argjson attempt "${GITHUB_RUN_ATTEMPT}" \
  '{schema_version: 1, state: "PUBLISHED", docs_project: $project,
    group_id: $group, artifact_id: $artifact, version: $version,
    deployment_id: $deployment, source_repository: $repository,
    source_repository_id: $repository_id, source_ref: $ref, source_sha: $sha,
    workflow_run_id: $run_id, workflow_run_attempt: $attempt}' > "$1"
