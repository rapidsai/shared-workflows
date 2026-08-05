#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_utils.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/prepare_maven_bundle_steps.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_central_publish_steps.sh"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

ARTIFACT_ID=cudf
VERSION=26.08.0
ARTIFACT_DIR="${TEST_ROOT}/bundle/ai/rapids/${ARTIFACT_ID}/${VERSION}"
mkdir -p "${ARTIFACT_DIR}"

for suffix in \
  .pom .pom.asc .jar .jar.asc \
  -sources.jar -sources.jar.asc \
  -javadoc.jar -javadoc.jar.asc; do
  printf 'content for %s\n' "${suffix}" > "${ARTIFACT_DIR}/${ARTIFACT_ID}-${VERSION}${suffix}"
done

require_bundle_contents "${ARTIFACT_DIR}"

rm "${ARTIFACT_DIR}/${ARTIFACT_ID}-${VERSION}-javadoc.jar.asc"
if (require_bundle_contents "${ARTIFACT_DIR}") >/dev/null 2>&1; then
  fail "missing signatures must be rejected"
fi
printf 'javadoc signature\n' > "${ARTIFACT_DIR}/${ARTIFACT_ID}-${VERSION}-javadoc.jar.asc"

printf 'stale\n' > "${ARTIFACT_DIR}/${ARTIFACT_ID}-${VERSION}.jar.md5"
generate_bundle_checksums "${ARTIFACT_DIR}"
EXPECTED_MD5=$(md5sum "${ARTIFACT_DIR}/${ARTIFACT_ID}-${VERSION}.jar" | awk '{ print $1 }')
ACTUAL_MD5=$(< "${ARTIFACT_DIR}/${ARTIFACT_ID}-${VERSION}.jar.md5")
[[ ${ACTUAL_MD5} == "${EXPECTED_MD5}" ]] || fail "MD5 sidecar was not regenerated"
[[ -f ${ARTIFACT_DIR}/${ARTIFACT_ID}-${VERSION}.jar.asc.sha1 ]] \
  || fail "signature checksum was not generated"

BUNDLE_ZIP="${TEST_ROOT}/central-bundle.zip"
create_bundle_zip "${BUNDLE_ZIP}" "${TEST_ROOT}/bundle"
unzip -tq "${BUNDLE_ZIP}" >/dev/null || fail "deployment ZIP is invalid"

touch "${ARTIFACT_DIR}/stale.asc" "${ARTIFACT_DIR}/stale.sha512"
remove_generated_sidecars "${TEST_ROOT}/bundle"
[[ ! -e ${ARTIFACT_DIR}/stale.asc ]] || fail "old signatures were not removed"
[[ ! -e ${ARTIFACT_DIR}/stale.sha512 ]] || fail "old checksums were not removed"

require_maven_coordinates ai.rapids cudf 26.08.0
if (require_maven_coordinates 'ai.rapids\nBAD=1' cudf 26.08.0) >/dev/null 2>&1; then
  fail "invalid coordinates must be rejected"
fi

echo "Maven publish step tests passed"
