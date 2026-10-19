#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# HTTP test double used by the Central integration test. No network requests.
set -euo pipefail
printf '%s\n' "$*" >> "${MOCK_STATE_DIR}/curl.log"
body_file=""
url=""
method=GET
while [[ $# -gt 0 ]]; do
  case $1 in
    -o) body_file=$2; shift 2 ;;
    -X) method=$2; shift 2 ;;
    --upload-file) method=PUT; shift 2 ;;
    https://*) url=$1; shift ;;
    *) shift ;;
  esac
done
status=200
body=""
case "${url}" in
  *manual/search/repositories*state=open*)
    if [[ ${PREEXISTING_OPEN:-false} == true || -f ${MOCK_STATE_DIR}/uploaded ]]; then
      body='{"repositories":[{"key":"repo-key","state":"open","portal_deployment_id":"","warnings":[]}]}'
    else
      body='{"repositories":[]}'
    fi
    ;;
  */service/local/staging/deploy/maven2/*)
    touch "${MOCK_STATE_DIR}/uploaded"
    printf '%s\n' "${url}" >> "${MOCK_STATE_DIR}/uploads.log"
    upload_count=$(wc -l < "${MOCK_STATE_DIR}/uploads.log")
    if [[ ${FAIL_UPLOAD:-false} == true && ${upload_count} -gt 1 ]]; then
      status=500
    fi
    ;;
  *manual/upload/repository/repo-key*publishing_type=portal_api*)
    touch "${MOCK_STATE_DIR}/handed-off"
    ;;
  *manual/search/repositories*)
    [[ -f ${MOCK_STATE_DIR}/handed-off ]]
    body='{"repositories":[{"key":"repo-key","state":"released","portal_deployment_id":"00000000-0000-0000-0000-000000000001","warnings":[]}]}'
    ;;
  *publisher/status*)
    state=${PORTAL_STATE:-VALIDATED}
    if [[ -f ${MOCK_STATE_DIR}/publish-requested ]]; then
      state=${AFTER_PUBLISH_STATE:-PUBLISHED}
    fi
    body=$(jq -n --arg state "${state}" --arg purl "${PORTAL_PURL:-pkg:maven/ai.rapids/example@26.08.0}" \
      '{deploymentState: $state, purls: [$purl]}')
    ;;
  *publisher/deployment/*)
    if [[ ${method} == DELETE ]]; then
      touch "${MOCK_STATE_DIR}/portal-dropped"
    else
      printf '%s\n' "${url}" >> "${MOCK_STATE_DIR}/publish.log"
      touch "${MOCK_STATE_DIR}/publish-requested"
      if [[ ${UNCERTAIN_PUBLISH:-false} == true ]]; then
        status=500
      fi
    fi
    ;;
  *manual/drop/repository/*) touch "${MOCK_STATE_DIR}/staging-dropped" ;;
  *) echo "unexpected curl invocation: ${url}" >&2; exit 1 ;;
esac
printf '%s' "${body}" > "${body_file}"
printf '%s' "${status}"
