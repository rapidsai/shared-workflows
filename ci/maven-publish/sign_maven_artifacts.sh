#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Sourced by the preparation wrapper. Credentials are read by nvsec from the
# environment; no private GPG key is imported into the publisher.
validate_3s_environment() {
  : "${NVSEC_SSA_CLIENT_ID:?must be set}"
  : "${NVSEC_SSA_CLIENT_SECRET:?must be set}"
  [[ ${NVSEC_SSA_SCOPE:-} == SIGNING_RAPIDS_ADI_OPS ]] || {
    echo 'Production signing requires SIGNING_RAPIDS_ADI_OPS' >&2
    return 1
  }
  local timeout=${THREES_TIMEOUT:-1800}
  if [[ ! ${timeout} =~ ^[1-9][0-9]{0,3}$ ]] || (( timeout > 3600 )); then
    echo 'THREES_TIMEOUT must be between 1 and 3600 seconds' >&2
    return 1
  fi
  command -v nvsec >/dev/null
  command -v gpg >/dev/null
  command -v jq >/dev/null
}

sign_maven_tree() (
  set -euo pipefail
  local directory=$1 public_key=$2 fingerprint=$3
  local signing_home file result request_id status primary signer started digest
  validate_3s_environment
  signing_home=$(mktemp -d)
  trap 'rm -rf "${signing_home}"' EXIT
  export HOME=${signing_home} GNUPGHOME=${signing_home}/gpg
  mkdir -m 700 "${GNUPGHOME}"
  gpg --batch --import "${public_key}"
  local count=0
  while IFS= read -r -d '' file; do
    started=${SECONDS}
    result=$(mktemp -d "${signing_home}/request.XXXXXX")
    nvsec 3s submit --auth ssa --job_type RAPIDS_ADI_OPS \
      --input_file "${file}" --parameters '{}' \
      --description "RAPIDS Maven package signing" \
      --timeout "${THREES_TIMEOUT:-1800}" --multi_part_upload \
      --request_id_file "${result}/request.json" \
      --download --result_dir "${result}" --result_filename signature.asc
    request_id=$(jq -er '.requestId | select(type == "number" and . > 0 and floor == .)' "${result}/request.json")
    nvsec 3s request-info --auth ssa --request_id "${request_id}" \
      --output_path "${result}/status.json"
    jq -e '[to_entries[] | select(.key == "statusCode" or
      .key == "statusName" or .key == "status") | .value] |
      length > 0 and all(. == 40 or . == "Completed")' "${result}/status.json" >/dev/null
    [[ -f ${result}/signature.asc && ! -L ${result}/signature.asc && -s ${result}/signature.asc ]]
    [[ $(head -n 1 "${result}/signature.asc") == '-----BEGIN PGP SIGNATURE-----' ]]
    status=$(gpg --batch --no-auto-key-retrieve --status-fd 1 \
      --verify "${result}/signature.asc" "${file}")
    # VALIDSIG identifies the signing subkey first and its primary key last.
    signer=$(awk '$1 == "[GNUPG:]" && $2 == "VALIDSIG" {print $3}' <<< "${status}")
    primary=$(awk '$1 == "[GNUPG:]" && $2 == "VALIDSIG" {print $NF}' <<< "${status}")
    [[ ${signer} == "${fingerprint}" || ${primary} == "${fingerprint}" ]] || {
      echo "Unexpected signing key for ${file}" >&2
      return 1
    }
    cp "${result}/signature.asc" "${file}.asc"
    if [[ -n ${THREES_AUDIT_FILE:-} ]]; then
      digest=$(sha256sum "${file}")
      jq -nc --arg file "${file#"${directory}"/}" --arg sha256 "${digest%% *}" \
        --arg fingerprint "${fingerprint}" --argjson request_id "${request_id}" \
        --argjson elapsed_seconds "$((SECONDS - started))" \
        '{file: $file, sha256: $sha256, fingerprint: $fingerprint,
          request_id: $request_id, state: "Completed", elapsed_seconds: $elapsed_seconds}' \
        >> "${THREES_AUDIT_FILE}"
    fi
    rm -rf "${result}"
    count=$((count + 1))
    echo "Verified ${file}: 3s request ${request_id}"
  done < <(find "${directory}" -type f \
    ! -name '*.asc' ! -name '*.md5' ! -name '*.sha1' \
    ! -name '*.sha256' ! -name '*.sha512' -print0)
  (( count > 0 )) || { echo 'No Maven artifacts to sign' >&2; return 1; }
)
