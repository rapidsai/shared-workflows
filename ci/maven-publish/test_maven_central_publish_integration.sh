#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

ARTIFACT_DIR="${TEST_ROOT}/signed/ai/rapids/example/26.08.0"
MOCK_BIN="${TEST_ROOT}/bin"
MOCK_STATE_DIR="${TEST_ROOT}/state"
GITHUB_OUTPUT="${TEST_ROOT}/github-output"
mkdir -p "${ARTIFACT_DIR}" "${MOCK_BIN}" "${MOCK_STATE_DIR}"

for suffix in \
  .pom .pom.asc .jar .jar.asc \
  -sources.jar -sources.jar.asc \
  -javadoc.jar -javadoc.jar.asc; do
  printf 'content for %s\n' "${suffix}" > "${ARTIFACT_DIR}/example-26.08.0${suffix}"
done

# This is the literal contents of the mock curl executable; its variables must
# expand only when that executable runs.
# shellcheck disable=SC2016
MOCK_CURL='#!/bin/bash
set -euo pipefail
printf "%s\n" "$*" >> "${MOCK_STATE_DIR}/curl.log"
case "$*" in
  *manual/search/repositories*state=open*)
    if [[ ${PREEXISTING_OPEN:-false} == true || -f ${MOCK_STATE_DIR}/uploaded ]]; then
      printf "%s\n" '\''{"repositories":[{"key":"repo-key","state":"open","portal_deployment_id":"","warnings":[]}]} '\''
    else
      printf "%s\n" '\''{"repositories":[]}'\''
    fi
    ;;
  *--upload-file*)
    touch "${MOCK_STATE_DIR}/uploaded"
    printf "%s\n" "$*" >> "${MOCK_STATE_DIR}/uploads.log"
    upload_count=$(wc -l < "${MOCK_STATE_DIR}/uploads.log")
    if [[ ${FAIL_UPLOAD:-false} == true && ${upload_count} -gt 1 ]]; then
      exit 22
    fi
    ;;
  *manual/upload/repository/repo-key*publishing_type=portal_api*)
    touch "${MOCK_STATE_DIR}/handed-off"
    ;;
  *manual/search/repositories*)
    if [[ -f ${MOCK_STATE_DIR}/handed-off ]]; then
      printf "%s\n" '\''{"repositories":[{"key":"repo-key","state":"released","portal_deployment_id":"00000000-0000-0000-0000-000000000001","warnings":[]}]} '\''
    else
      echo "repository searched before handoff" >&2
      exit 1
    fi
    ;;
  *publisher/status*) printf "%s\n" '\''{"deploymentState":"VALIDATED"}'\'' ;;
  *publisher/deployment/*) touch "${MOCK_STATE_DIR}/portal-dropped" ;;
  *manual/drop/repository/*) touch "${MOCK_STATE_DIR}/staging-dropped" ;;
  *) echo "unexpected curl invocation: $*" >&2; exit 1 ;;
esac'
printf '%s\n' "${MOCK_CURL}" > "${MOCK_BIN}/curl"
chmod +x "${MOCK_BIN}/curl"

export PATH="${MOCK_BIN}:${PATH}"
export GITHUB_OUTPUT MOCK_STATE_DIR
export MAVEN_DEPLOY_USERNAME=test-user
export MAVEN_DEPLOY_TOKEN=test-token

OUTPUT_BUNDLE="${TEST_ROOT}/central-bundle.zip"
"${SCRIPT_DIR}/maven_central_publish.sh" \
  --input "${TEST_ROOT}/signed" \
  --group-id ai.rapids \
  --artifact-id example \
  --version 26.08.0 \
  --output-bundle "${OUTPUT_BUNDLE}" \
  --auto-drop true

unzip -tq "${OUTPUT_BUNDLE}" >/dev/null
unzip -l "${OUTPUT_BUNDLE}" | grep -F 'example-26.08.0.jar.asc.sha1'
grep -Fx 'STAGING_REPOSITORY_KEY=repo-key' "${GITHUB_OUTPUT}"
grep -Fx 'DEPLOYMENT_ID=00000000-0000-0000-0000-000000000001' "${GITHUB_OUTPUT}"
[[ $(wc -l < "${MOCK_STATE_DIR}/uploads.log") -eq 24 ]]
grep -F -- '--upload-file' "${MOCK_STATE_DIR}/uploads.log" \
  | grep -F '/ai/rapids/example/26.08.0/example-26.08.0.jar.asc.sha1'
grep -F 'manual/upload/repository/repo-key?publishing_type=portal_api' \
  "${MOCK_STATE_DIR}/curl.log"
if grep -Fq 'api/v1/publisher/upload' "${MOCK_STATE_DIR}/curl.log"; then
  echo "bundle upload endpoint must not be used" >&2
  exit 1
fi
[[ -f ${MOCK_STATE_DIR}/portal-dropped ]]
[[ ! -f ${MOCK_STATE_DIR}/staging-dropped ]]

rm -f "${MOCK_STATE_DIR}"/*
export PREEXISTING_OPEN=true
if "${SCRIPT_DIR}/maven_central_publish.sh" \
  --input "${TEST_ROOT}/signed" \
  --group-id ai.rapids \
  --artifact-id example \
  --version 26.08.0 \
  --output-bundle "${TEST_ROOT}/blocked-bundle.zip" \
  --auto-drop true; then
  echo "a pre-existing open staging repository must block publication" >&2
  exit 1
fi
[[ ! -e ${MOCK_STATE_DIR}/uploads.log ]]
[[ ! -e ${MOCK_STATE_DIR}/staging-dropped ]]

rm -f "${MOCK_STATE_DIR}"/*
export PREEXISTING_OPEN=false
export FAIL_UPLOAD=true
if "${SCRIPT_DIR}/maven_central_publish.sh" \
  --input "${TEST_ROOT}/signed" \
  --group-id ai.rapids \
  --artifact-id example \
  --version 26.08.0 \
  --output-bundle "${TEST_ROOT}/failed-upload-bundle.zip" \
  --auto-drop true; then
  echo "an upload failure must fail publication" >&2
  exit 1
fi
[[ -f ${MOCK_STATE_DIR}/staging-dropped ]]

echo "Maven Central publish integration test passed"
