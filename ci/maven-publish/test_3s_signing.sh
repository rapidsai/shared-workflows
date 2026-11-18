#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "${TEST_ROOT}"' EXIT
export GNUPGHOME=${TEST_ROOT}/gpg MOCK_GPG_HOME=${TEST_ROOT}/gpg
mkdir -m 700 "${GNUPGHOME}"
gpg --batch --pinentry-mode loopback --passphrase '' \
  --quick-generate-key 'Maven Signing Test <test@nvidia.com>' ed25519 sign 1d
gpg --armor --export > "${TEST_ROOT}/key.asc"
fingerprint=$(gpg --with-colons --list-keys | awk -F: '$1=="fpr" {print $10; exit}')  # codespell:ignore fpr
mkdir "${TEST_ROOT}/bin" "${TEST_ROOT}/artifacts"
cp "${SCRIPT_DIR}/mock_nvsec.sh" "${TEST_ROOT}/bin/nvsec"
chmod +x "${TEST_ROOT}/bin/nvsec"
export PATH="${TEST_ROOT}/bin:${PATH}"
export NVSEC_SSA_CLIENT_ID=test NVSEC_SSA_CLIENT_SECRET=test
export NVSEC_SSA_SCOPE=SIGNING_RAPIDS_ADI_OPS
run_signer() {
  bash -euo pipefail -c 'source "$1"; sign_maven_tree "$2" "$3" "$4"' \
    test "${SCRIPT_DIR}/sign_maven_artifacts.sh" "${TEST_ROOT}/artifacts" \
    "${TEST_ROOT}/key.asc" "${1:-${fingerprint}}"
}
expect_failure() {
  rm -f "${TEST_ROOT}/artifacts/example.jar.asc"
  if run_signer "${1:-${fingerprint}}" > "${TEST_ROOT}/failure.log" 2>&1; then
    echo 'Expected signing to fail' >&2
    exit 1
  fi
  [[ ! -e ${TEST_ROOT}/artifacts/example.jar.asc ]]
}
printf 'jar bytes\n' > "${TEST_ROOT}/artifacts/example.jar"
run_signer
gpg --verify "${TEST_ROOT}/artifacts/example.jar.asc" "${TEST_ROOT}/artifacts/example.jar"
for state in '{"status":"Pending"}' '{"statusCode":50}' \
  '{"statusCode":40,"statusName":"Failed"}' \
  '{"statusCode":40,"statusName":null}' '{}'; do
  export MOCK_3S_STATUS=${state}
  expect_failure
done
unset MOCK_3S_STATUS
expect_failure 0000000000000000000000000000000000000000
export MOCK_BAD_SIGNATURE=true
expect_failure
unset MOCK_BAD_SIGNATURE
export NVSEC_SSA_SCOPE=SIGNING_DEV
expect_failure
export NVSEC_SSA_SCOPE=SIGNING_RAPIDS_ADI_OPS THREES_TIMEOUT=3601
expect_failure
unset THREES_TIMEOUT NVSEC_SSA_CLIENT_SECRET
expect_failure
echo '3s signing tests passed'
