#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Sign existing Maven build outputs and retain evidence without publication.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root=$(cd "${1:?usage: run_3s_smoke.sh <directory containing input/>}" && pwd)
export THREES_AUDIT_FILE=${root}/request-receipts.jsonl
: > "${THREES_AUDIT_FILE}"
prepare_args=(--input "${root}/input" --output "${root}/signed"
  --manifest-output "${root}/coordinates.json")
if [[ -n ${PRIMARY_ARTIFACT_ID:-} ]]; then
  prepare_args+=(--primary-artifact-id "${PRIMARY_ARTIFACT_ID}")
fi
bash "${SCRIPT_DIR}/prepare_maven_bundle.sh" "${prepare_args[@]}"

# Verify the retained bytes again with a fresh public-key-only keyring.
verify_home=$(mktemp -d)
trap 'rm -rf "${verify_home}"' EXIT
export GNUPGHOME=${verify_home}
gpg --batch --import "${SCRIPT_DIR}/keys/rapids-production.asc"
count=0
while IFS= read -r -d '' signature; do
  file=${signature%.asc}
  [[ -f ${file} && ! -L ${file} ]]
  status=$(gpg --batch --no-auto-key-retrieve --status-fd 1 --verify "${signature}" "${file}")
  awk '$1 == "[GNUPG:]" && $2 == "VALIDSIG" &&
    ($3 == "565BF95475A2C617A5982D2E8B239C09A78134A9" ||
     $NF == "565BF95475A2C617A5982D2E8B239C09A78134A9") {found=1}
    END {exit !found}' <<< "${status}"
  relative=${file#"${root}/signed/"}
  [[ -f ${root}/input/${relative} ]]
  cmp -- "${root}/input/${relative}" "${file}"
  digest=$(sha256sum "${file}")
  jq -se --arg file "${relative}" --arg sha256 "${digest%% *}" \
    '[.[] | select(.file == $file and .sha256 == $sha256 and .state == "Completed")]
     | length == 1' "${THREES_AUDIT_FILE}" >/dev/null
  count=$((count + 1))
done < <(find "${root}/signed" -type f -name '*.asc' -print0)
(( count > 0 ))
jq -se --argjson expected "${count}" 'length == $expected' "${THREES_AUDIT_FILE}" >/dev/null
jq -n --argjson count "${count}" \
  --arg fingerprint 565BF95475A2C617A5982D2E8B239C09A78134A9 \
  '{verified: true, signed_file_count: $count, fingerprint: $fingerprint,
    original_bytes_preserved: true, published: false}' > "${root}/verification.json"
if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then
  printf 'Verified %s Maven file signatures against RAPIDS key %s. No packages published.\n' \
    "${count}" 565BF95475A2C617A5982D2E8B239C09A78134A9 >> "${GITHUB_STEP_SUMMARY}"
fi
