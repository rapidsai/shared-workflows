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

cp "${SCRIPT_DIR}/mock_central_curl.sh" "${MOCK_BIN}/curl"
chmod +x "${MOCK_BIN}/curl"
printf '#!/usr/bin/env bash\nexit 0\n' > "${MOCK_BIN}/sleep"
chmod +x "${MOCK_BIN}/sleep"

export PATH="${MOCK_BIN}:${PATH}"
export GITHUB_OUTPUT MOCK_STATE_DIR
export MAVEN_DEPLOY_USERNAME=test-user
export MAVEN_DEPLOY_TOKEN=test-token
export GITHUB_REF=refs/tags/v26.08.00

OUTPUT_BUNDLE="${TEST_ROOT}/central-bundle.zip"
"${SCRIPT_DIR}/maven_central_publish.sh" \
  --input "${TEST_ROOT}/signed" \
  --group-id ai.rapids \
  --artifact-id example \
  --version 26.08.0 \
  --output-bundle "${OUTPUT_BUNDLE}"

unzip -tq "${OUTPUT_BUNDLE}" >/dev/null
unzip -l "${OUTPUT_BUNDLE}" | grep -F 'example-26.08.0.jar.asc.sha1'
grep -Fx 'STAGING_REPOSITORY_KEY=repo-key' "${GITHUB_OUTPUT}"
grep -Fx 'DEPLOYMENT_ID=00000000-0000-0000-0000-000000000001' "${GITHUB_OUTPUT}"
[[ $(wc -l < "${MOCK_STATE_DIR}/uploads.log") -eq 24 ]]
grep -F '/ai/rapids/example/26.08.0/example-26.08.0.jar.asc.sha1' "${MOCK_STATE_DIR}/uploads.log"
grep -F 'manual/upload/repository/repo-key?publishing_type=portal_api' \
  "${MOCK_STATE_DIR}/curl.log"
if grep -Fq 'api/v1/publisher/upload' "${MOCK_STATE_DIR}/curl.log"; then
  echo "bundle upload endpoint must not be used" >&2
  exit 1
fi
[[ -f ${MOCK_STATE_DIR}/publish-requested && ! -f ${MOCK_STATE_DIR}/portal-dropped ]]
[[ ! -f ${MOCK_STATE_DIR}/staging-dropped ]]

rm -f "${MOCK_STATE_DIR}"/*
export PREEXISTING_OPEN=true
if "${SCRIPT_DIR}/maven_central_publish.sh" \
  --input "${TEST_ROOT}/signed" \
  --group-id ai.rapids \
  --artifact-id example \
  --version 26.08.0 \
  --output-bundle "${TEST_ROOT}/blocked-bundle.zip"; then
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
  --output-bundle "${TEST_ROOT}/failed-upload-bundle.zip"; then
  echo "an upload failure must fail publication" >&2
  exit 1
fi
[[ -f ${MOCK_STATE_DIR}/staging-dropped ]]

export FAIL_UPLOAD=false
export GITHUB_REF=refs/tags/v26.08.00
RECEIPT="${TEST_ROOT}/deployment.json"
run_publish() {
  "${SCRIPT_DIR}/maven_central_publish.sh" \
    --input "${TEST_ROOT}/signed" --group-id ai.rapids --artifact-id example \
    --version 26.08.0 --output-bundle "${TEST_ROOT}/automatic-bundle.zip" \
    --deployment-receipt "${RECEIPT}"
}

rm -f "${MOCK_STATE_DIR}"/*
export GITHUB_REF=refs/tags/v26.12.00
if run_publish; then
  echo 'a tag/POM mismatch must block publication' >&2
  exit 1
fi
[[ ! -f ${MOCK_STATE_DIR}/curl.log ]]
export GITHUB_REF=refs/heads/main
if run_publish; then
  echo 'a branch must not authorize automatic release publication' >&2
  exit 1
fi
[[ ! -f ${MOCK_STATE_DIR}/curl.log ]]
export GITHUB_REF=refs/tags/v26.08.00

rm -f "${MOCK_STATE_DIR}"/*
# A lost/failed HTTP response after acceptance must still complete successfully.
export UNCERTAIN_PUBLISH=true
run_publish
grep -Fx 'DEPLOYMENT_STATE=PUBLISHED' "${GITHUB_OUTPUT}"
[[ -f ${RECEIPT} && ! -f ${MOCK_STATE_DIR}/portal-dropped ]]
[[ $(wc -l < "${MOCK_STATE_DIR}/publish.log") -eq 1 ]]

# Rerunning the same release uses its receipt, even when the output ZIP exists.
rm -f "${MOCK_STATE_DIR}/uploads.log"
run_publish
[[ ! -f ${MOCK_STATE_DIR}/uploads.log ]]
[[ $(wc -l < "${MOCK_STATE_DIR}/publish.log") -eq 1 ]]

# Resume a VALIDATED deployment after a prior failed attempt.
rm -f "${MOCK_STATE_DIR}"/*
export UNCERTAIN_PUBLISH=false
run_publish
[[ ! -f ${MOCK_STATE_DIR}/uploads.log ]]
[[ $(wc -l < "${MOCK_STATE_DIR}/publish.log") -eq 1 ]]

# A mismatched coordinate must block promotion.
rm -f "${MOCK_STATE_DIR}"/*
export PORTAL_PURL='pkg:maven/ai.rapids/other@26.08.0'
if run_publish; then
  echo 'mismatched Portal coordinates must block publication' >&2
  exit 1
fi
[[ ! -f ${MOCK_STATE_DIR}/publish.log ]]
unset PORTAL_PURL

# A failed validation/publication must not emit the success output.
for state in FAILED VALIDATION_FAILED; do
  rm -f "${MOCK_STATE_DIR}"/*
  : > "${GITHUB_OUTPUT}"
  export PORTAL_STATE=${state}
  if run_publish; then
    echo "terminal state ${state} must fail publication" >&2
    exit 1
  fi
  if grep -Fq 'DEPLOYMENT_STATE=PUBLISHED' "${GITHUB_OUTPUT}"; then exit 1; fi
  [[ ! -f ${MOCK_STATE_DIR}/publish.log ]]
done
unset PORTAL_STATE

rm -f "${MOCK_STATE_DIR}"/*
export AFTER_PUBLISH_STATE=FAILED
if run_publish; then
  echo 'publication failure must fail the job' >&2
  exit 1
fi
if grep -Fq 'DEPLOYMENT_STATE=PUBLISHED' "${GITHUB_OUTPUT}"; then exit 1; fi
unset AFTER_PUBLISH_STATE

# A stalled deployment is recoverable without another upload or promotion.
rm -f "${MOCK_STATE_DIR}"/*
export PORTAL_STATE=PUBLISHING
export POLL_TIMEOUT_SEC=1
printf '#!/bin/bash\nexit 0\n' > "${MOCK_BIN}/sleep"
chmod +x "${MOCK_BIN}/sleep"
if run_publish; then
  echo 'publication timeout must fail the job' >&2
  exit 1
fi
[[ ! -f ${MOCK_STATE_DIR}/uploads.log && ! -f ${MOCK_STATE_DIR}/publish.log ]]
if grep -Fq 'DEPLOYMENT_STATE=PUBLISHED' "${GITHUB_OUTPUT}"; then exit 1; fi
unset PORTAL_STATE POLL_TIMEOUT_SEC

echo "Maven Central publish integration test passed"
