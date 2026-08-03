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
GPG_PASSPHRASE=test-passphrase
gpg --batch --yes --pinentry-mode loopback \
  --passphrase "${GPG_PASSPHRASE}" \
  --quick-generate-key 'Maven Publish Test <maven-publish-test@nvidia.com>' \
  rsa2048 sign 1d
GPG_PRIVATE_KEY=$(gpg --batch --yes --pinentry-mode loopback \
  --passphrase "${GPG_PASSPHRASE}" --armor --export-secret-keys)

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

export GPG_PRIVATE_KEY GPG_PASSPHRASE GITHUB_OUTPUT
"${SCRIPT_DIR}/prepare_maven_bundle.sh" \
  --input "${INPUT_DIR}" \
  --output "${OUTPUT_DIR}" \
  --publication-type rc

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

echo "Maven bundle preparation integration test passed"
