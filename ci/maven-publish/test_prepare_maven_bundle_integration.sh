#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

GNUPGHOME="${TEST_ROOT}/gnupg"
export GNUPGHOME
mkdir -m 700 "${GNUPGHOME}"
GPG_PASSPHRASE=
gpg --batch --yes --pinentry-mode loopback \
  --passphrase "${GPG_PASSPHRASE}" \
  --quick-generate-key 'Maven Publish Test <maven-publish-test@nvidia.com>' \
  rsa2048 sign 1d
# Exercise the production wrapper using a copy with disposable trust material.
cp -R "${SCRIPT_DIR}" "${TEST_ROOT}/scripts"
TEST_SCRIPT_DIR="${TEST_ROOT}/scripts"
gpg --armor --export > "${TEST_SCRIPT_DIR}/keys/rapids-production.asc"
fingerprint=$(gpg --with-colons --list-keys | awk -F: '$1=="fpr" {print $10; exit}')  # codespell:ignore fpr
sed -i.bak "s/565BF95475A2C617A5982D2E8B239C09A78134A9/${fingerprint}/g" \
  "${TEST_SCRIPT_DIR}/prepare_maven_bundle.sh" "${TEST_SCRIPT_DIR}/run_3s_smoke.sh"
mkdir "${TEST_ROOT}/bin"
cp "${SCRIPT_DIR}/mock_nvsec.sh" "${TEST_ROOT}/bin/nvsec"
chmod +x "${TEST_ROOT}/bin/nvsec"
export PATH="${TEST_ROOT}/bin:${PATH}"
export MOCK_GPG_HOME=${GNUPGHOME}
export NVSEC_SSA_CLIENT_ID=test NVSEC_SSA_CLIENT_SECRET=test
export NVSEC_SSA_SCOPE=SIGNING_RAPIDS_ADI_OPS

INPUT_DIR="${TEST_ROOT}/input"
ARTIFACT_DIR="${INPUT_DIR}/ai/rapids/example/26.08.0"
OUTPUT_DIR="${TEST_ROOT}/signed"
GITHUB_OUTPUT="${TEST_ROOT}/github-output"
mkdir -p "${ARTIFACT_DIR}"

printf '%s\n' \
  '<project xmlns="http://maven.apache.org/POM/4.0.0">' \
  '  <modelVersion>4.0.0</modelVersion>' \
  '  <groupId>ai.rapids</groupId>' \
  '  <artifactId>example</artifactId>' \
  '  <version>26.08.0</version>' \
  '</project>' \
  > "${ARTIFACT_DIR}/example-26.08.0.pom"
printf 'primary jar\n' > "${ARTIFACT_DIR}/example-26.08.0.jar"
printf 'sources jar\n' > "${ARTIFACT_DIR}/example-26.08.0-sources.jar"
printf 'javadoc jar\n' > "${ARTIFACT_DIR}/example-26.08.0-javadoc.jar"
printf 'stale checksum\n' > "${ARTIFACT_DIR}/example-26.08.0.jar.sha1"

export GITHUB_OUTPUT
bash "${TEST_SCRIPT_DIR}/run_3s_smoke.sh" "${TEST_ROOT}"
jq -e '.verified and .signed_file_count == 4 and .published == false' "${TEST_ROOT}/verification.json"

SIGNED_DIR="${OUTPUT_DIR}/ai/rapids/example/26.08.0"
for artifact in \
  example-26.08.0.pom \
  example-26.08.0.jar \
  example-26.08.0-sources.jar \
  example-26.08.0-javadoc.jar; do
  gpg --verify "${SIGNED_DIR}/${artifact}.asc" "${SIGNED_DIR}/${artifact}"
done

[[ ! -e ${SIGNED_DIR}/example-26.08.0.jar.sha1 ]]
grep -Fx 'GROUP_ID=ai.rapids' "${GITHUB_OUTPUT}"
grep -Fx 'ARTIFACT_ID=example' "${GITHUB_OUTPUT}"
grep -Fx 'VERSION=26.08.0' "${GITHUB_OUTPUT}"

# A parent and relocation carry only POMs, with no JAR requirements.
for artifact in example-parent example-relocation; do
  target="${INPUT_DIR}/ai/rapids/${artifact}/26.08.0"
  mkdir -p "${target}"
  printf '%s\n' \
    '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>' \
    "<groupId>ai.rapids</groupId><artifactId>${artifact}</artifactId><version>26.08.0</version>" \
    '<packaging>pom</packaging></project>' > "${target}/${artifact}-26.08.0.pom"
done
"${TEST_SCRIPT_DIR}/prepare_maven_bundle.sh" \
  --input "${INPUT_DIR}" --output "${TEST_ROOT}/mixed-signed" \
  --primary-artifact-id example --manifest-output "${TEST_ROOT}/coordinates.json"
jq -e 'length == 3 and ([.[] | select(.packaging == "pom")] | length == 2)' "${TEST_ROOT}/coordinates.json"
for artifact in example-parent example-relocation; do
  target="${TEST_ROOT}/mixed-signed/ai/rapids/${artifact}/26.08.0/${artifact}-26.08.0.pom"
  gpg --verify "${target}.asc" "${target}"
done

echo "Maven bundle preparation integration test passed"
