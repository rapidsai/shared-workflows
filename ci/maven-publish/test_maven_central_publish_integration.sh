#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "${TEST_ROOT}"' EXIT

ARTIFACT_DIR="${TEST_ROOT}/signed/ai/rapids/example/26.08.0"
MOCK_BIN="${TEST_ROOT}/bin"
MOCK_STATE_DIR="${TEST_ROOT}/state"
GITHUB_OUTPUT="${TEST_ROOT}/github-output"
mkdir -p "${ARTIFACT_DIR}" "${MOCK_BIN}" "${MOCK_STATE_DIR}"

for suffix in \
  .pom .pom.asc .jar .jar.asc \
  -sources.jar -sources.jar.asc \
  -javadoc.jar -javadoc.jar.asc; do
  printf 'content for %s\n' "${suffix}" > "${ARTIFACT_DIR}/example-26.08.0${suffix}"
done

cat > "${MOCK_BIN}/curl" <<'EOF'
#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >> "${MOCK_STATE_DIR}/curl.log"
out=/dev/null
method=GET
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[i]}" in
    -o) out=${args[i + 1]} ;;
    -X) method=${args[i + 1]} ;;
    --form)
      bundle=${args[i + 1]#bundle=@}
      cp "${bundle%%;*}" "${MOCK_STATE_DIR}/uploaded.zip"
      ;;
  esac
done
case "$*" in
  *publisher/upload*)
    if [[ ${FAIL_UPLOAD:-false} == true ]]; then
      echo '{"error":"bad bundle"}' > "${out}"
      printf 400
    else
      echo 00000000-0000-0000-0000-000000000001 > "${out}"
      printf 201
    fi
    ;;
  *publisher/status*)
    echo "{\"deploymentState\":\"${DEPLOYMENT_STATE:-VALIDATED}\"}" > "${out}"
    printf 200
    ;;
  *publisher/deployment/*)
    [[ ${method} == DELETE ]] && touch "${MOCK_STATE_DIR}/dropped"
    : > "${out}"
    printf 204
    ;;
  *)
    echo "unexpected curl invocation: $*" >&2
    exit 1
    ;;
esac
EOF
chmod +x "${MOCK_BIN}/curl"

export PATH="${MOCK_BIN}:${PATH}"
export GITHUB_OUTPUT MOCK_STATE_DIR
export MAVEN_DEPLOY_USERNAME=test-user
export MAVEN_DEPLOY_TOKEN=test-token

publish() {
  "${SCRIPT_DIR}/maven_central_publish.sh" \
    --input "${TEST_ROOT}/signed" \
    --group-id ai.rapids \
    --artifact-id example \
    --version 26.08.0 \
    --output-bundle "${TEST_ROOT}/bundle-$1.zip" \
    --auto-drop "$2"
}

reset_state() {
  rm -rf "${MOCK_STATE_DIR:?}"/*
  : > "${GITHUB_OUTPUT}"
}

reset_state
publish drop true

unzip -tq "${TEST_ROOT}/bundle-drop.zip" >/dev/null
unzip -l "${TEST_ROOT}/bundle-drop.zip" | grep -F 'example-26.08.0.jar.asc.sha1'
grep -Fx 'DEPLOYMENT_ID=00000000-0000-0000-0000-000000000001' "${GITHUB_OUTPUT}"
grep -F 'api/v1/publisher/upload?publishingType=USER_MANAGED' "${MOCK_STATE_DIR}/curl.log"
grep -F 'Authorization: Bearer dGVzdC11c2VyOnRlc3QtdG9rZW4=' "${MOCK_STATE_DIR}/curl.log"
grep -F 'bundle=@' "${MOCK_STATE_DIR}/curl.log"
unzip -l "${MOCK_STATE_DIR}/uploaded.zip" | grep -F 'ai/rapids/example/26.08.0/example-26.08.0.jar'
if grep -Fq 'ossrh-staging-api' "${MOCK_STATE_DIR}/curl.log"; then
  echo "the legacy OSSRH staging API must not be used" >&2
  exit 1
fi
[[ -f ${MOCK_STATE_DIR}/dropped ]]

reset_state
publish keep false
[[ ! -f ${MOCK_STATE_DIR}/dropped ]]

reset_state
export FAIL_UPLOAD=true
if publish failed-upload true; then
  echo "an upload failure must fail publication" >&2
  exit 1
fi
[[ ! -f ${MOCK_STATE_DIR}/dropped ]]
unset FAIL_UPLOAD

reset_state
export DEPLOYMENT_STATE=FAILED
if publish failed-validation true; then
  echo "a FAILED deployment must fail publication" >&2
  exit 1
fi
[[ ! -f ${MOCK_STATE_DIR}/dropped ]]

echo "Maven Central publish integration test passed"
