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
    --form)
      bundle=${2#bundle=@}
      cp "${bundle%%;*}" "${MOCK_STATE_DIR}/uploaded.zip"
      shift 2
      ;;
    https://*) url=$1; shift ;;
    *) shift ;;
  esac
done
status=200
body=""
[[ ${method} == POST ]] || { echo "unexpected Portal method: ${method}" >&2; exit 1; }
case "${url}" in
  *publisher/upload*)
    printf '%s\n' "${url}" >> "${MOCK_STATE_DIR}/uploads.log"
    status=201
    body=00000000-0000-0000-0000-000000000001
    if [[ ${FAIL_UPLOAD:-false} == true ]]; then status=400; fi
    ;;
  *publisher/status*)
    state=${PORTAL_STATE:-VALIDATED}
    if [[ -f ${MOCK_STATE_DIR}/publish-requested ]]; then
      state=${AFTER_PUBLISH_STATE:-PUBLISHED}
    fi
    purls=${PORTAL_PURLS:-}
    if [[ -z ${purls} ]]; then
      purls=$(jq -cn --arg purl "${PORTAL_PURL:-pkg:maven/ai.rapids/example@26.08.0}" '[$purl]')
    fi
    body=$(jq -n --arg state "${state}" --argjson purls "${purls}" \
      '{deploymentState: $state, purls: $purls}')
    ;;
  *publisher/deployment/*)
    printf '%s\n' "${url}" >> "${MOCK_STATE_DIR}/publish.log"
    touch "${MOCK_STATE_DIR}/publish-requested"
    if [[ ${UNCERTAIN_PUBLISH:-false} == true ]]; then
      status=500
    fi
    ;;
  *) echo "unexpected curl invocation: ${url}" >&2; exit 1 ;;
esac
printf '%s' "${body}" > "${body_file}"
printf '%s' "${status}"
