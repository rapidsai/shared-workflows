#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Publish into a disposable local repository using the real Maven deploy plugin.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ${1:-} != --in-container ]]; then
  exec docker run --rm --volume "${SCRIPT_DIR}:/scripts:ro" \
    maven:3-eclipse-temurin-17 bash /scripts/test_maven_snapshot_publish_integration.sh --in-container
fi

# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_utils.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_snapshot_publish_steps.sh"

TEST_ROOT=$(mktemp -d)
trap 'rm -rf "${TEST_ROOT}"' EXIT
export MAVEN_DEPLOY_USERNAME=test-user MAVEN_DEPLOY_TOKEN=test-token
write_maven_settings "${TEST_ROOT}/settings.xml"
VERSION=26.12.0-SNAPSHOT
printf 'release fixture\n' > "${TEST_ROOT}/payload"

for artifact in example parent; do
  target="${TEST_ROOT}/${artifact}"
  mkdir -p "${target}"
  packaging=jar
  if [[ ${artifact} == parent ]]; then packaging=pom; fi
  cat > "${target}/${artifact}-${VERSION}.pom" <<EOF
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>ai.rapids</groupId>
  <artifactId>${artifact}</artifactId>
  <version>${VERSION}</version>
  <packaging>${packaging}</packaging>
</project>
EOF
  if [[ ${packaging} == jar ]]; then
    for suffix in .jar -sources.jar -javadoc.jar -x86_64-cuda13.jar; do
      jar --create --file "${target}/${artifact}-${VERSION}${suffix}" -C "${TEST_ROOT}" payload
    done
  fi
  deploy_snapshot_with_mvn "${target}" "${artifact}" "${VERSION}" \
    "file://${TEST_ROOT}/repository" "${TEST_ROOT}/settings.xml" "${TEST_ROOT}/deploy.log" "${packaging}"

  published="${TEST_ROOT}/repository/ai/rapids/${artifact}/${VERSION}"
  mapfile -t poms < <(find "${published}" -maxdepth 1 -name '*.pom')
  [[ ${#poms[@]} == 1 ]] || fatal "expected one published POM for ${artifact}"
  cmp "${target}/${artifact}-${VERSION}.pom" "${poms[0]}"
  if [[ ${packaging} == jar ]]; then
    base=${poms[0]%.pom}
    for suffix in .jar -sources.jar -javadoc.jar -x86_64-cuda13.jar; do
      cmp "${target}/${artifact}-${VERSION}${suffix}" "${base}${suffix}"
    done
  else
    [[ -z $(find "${published}" -name '*.jar' -print -quit) ]] \
      || fatal "POM-only publication must not contain JARs"
  fi
done
echo 'Maven snapshot publication integration test passed'
