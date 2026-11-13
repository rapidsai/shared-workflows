#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Check deploy-file arguments without contacting Sonatype.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_utils.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_snapshot_publish_steps.sh"
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "${TEST_ROOT}"' EXIT
mkdir -p "${TEST_ROOT}/bin" "${TEST_ROOT}/artifact"
cat > "${TEST_ROOT}/bin/mvn" <<'EOF'
#!/bin/bash
printf '%s\n' "$@" > "${MOCK_MVN_ARGS}"
EOF
chmod +x "${TEST_ROOT}/bin/mvn"
export PATH="${TEST_ROOT}/bin:${PATH}"
export MOCK_MVN_ARGS="${TEST_ROOT}/args"
target="${TEST_ROOT}/artifact"
touch "${target}/example-26.12.0-SNAPSHOT.pom"
deploy_snapshot_with_mvn "${target}" example 26.12.0-SNAPSHOT \
  https://example.invalid "${TEST_ROOT}/settings.xml" "${TEST_ROOT}/deploy.log" pom
grep -Fx -- '-Dpackaging=pom' "${MOCK_MVN_ARGS}"
grep -Fx -- "-Dfile=${target}/example-26.12.0-SNAPSHOT.pom" "${MOCK_MVN_ARGS}"
if grep -Fq -- '-Dclassifiers=' "${MOCK_MVN_ARGS}"; then
  fatal "POM-only deployment must not attach a nonexistent JAR"
fi
for suffix in .jar -sources.jar -javadoc.jar -x86_64-cuda13.jar; do
  touch "${target}/example-26.12.0-SNAPSHOT${suffix}"
done
deploy_snapshot_with_mvn "${target}" example 26.12.0-SNAPSHOT \
  https://example.invalid "${TEST_ROOT}/settings.xml" "${TEST_ROOT}/deploy.log" jar
grep -Fx -- '-Dpackaging=jar' "${MOCK_MVN_ARGS}"
grep -Fx -- '-Dclassifiers=x86_64-cuda13' "${MOCK_MVN_ARGS}"
grep -Fx -- "-Dsources=${target}/example-26.12.0-SNAPSHOT-sources.jar" "${MOCK_MVN_ARGS}"
echo 'Snapshot deploy argument tests passed'
