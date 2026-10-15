#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Creates a deployment bundle from a signed Maven repository tree, uploads its
# files through Sonatype's OSSRH staging compatibility API, and polls the
# Publisher Portal until the release is PUBLISHED. A retained deployment receipt
# allows an interrupted publication to resume without another upload.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
. "${SCRIPT_DIR}/argparse.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_utils.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_central_publish_steps.sh"

CENTRAL_PORTAL_URL="https://central.sonatype.com"
OSSRH_STAGING_API_URL="https://ossrh-staging-api.central.sonatype.com"
# Used by polling functions in the sourced step library.
# shellcheck disable=SC2034
POLL_INTERVAL_SEC=15
# shellcheck disable=SC2034
POLL_TIMEOUT_SEC=${POLL_TIMEOUT_SEC:-1800}

INPUT_DIR=""
GROUP_ID=""
PROFILE_ID=""
ARTIFACT_ID=""
VERSION=""
OUTPUT_BUNDLE=""
DEPLOYMENT_RECEIPT=""

print_help() {
  cat << EOF

Usage: maven_central_publish.sh --input <path> --group-id <g> \\
                                --artifact-id <a> --version <v> \\
                                --output-bundle <path> [OPTIONS]

Copies a signed Maven repository tree into scratch space, generates .md5 and
.sha1 sidecars, and creates a retained ZIP. It uploads the repository files
individually through Sonatype's OSSRH staging compatibility API, then hands the
repository to the Central Publisher Portal, validates, publishes, and waits for
PUBLISHED. The release tag authorizes publication.

REQUIRED:
    -i, --input                    Signed Maven repository directory.
    -g, --group-id                 Maven groupId, e.g. ai.rapids.
    -a, --artifact-id              Maven artifactId, e.g. cudf.
    -v, --version                  Release version, e.g. 26.08.0.
    -o, --output-bundle            Path for the retained deployment ZIP.

OPTIONS:
    --profile-id <p>                Sonatype staging profile to search, if different from
                                    --group-id (e.g. a verified parent namespace like
                                    com.nvidia for a com.nvidia.cuopt groupId). Defaults to
                                    --group-id.
    --deployment-receipt <path>   Save the deployment id before polling; if the
                                  receipt exists, resume that deployment.
    -h, --help                     Show this help message.

ENVIRONMENT VARIABLES:
    MAVEN_DEPLOY_USERNAME          Publisher Portal user token username.
    MAVEN_DEPLOY_TOKEN             Publisher Portal user token password.
    GITHUB_OUTPUT                  If set, receives STAGING_REPOSITORY_KEY and
                                   DEPLOYMENT_ID.

EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case $1 in
      -h|--help)
        print_help
        exit 0
        ;;
      -i|--input)
        require_value "$1" "${2:-}"
        INPUT_DIR=$2
        shift 2
        ;;
      -g|--group-id)
        require_value "$1" "${2:-}"
        GROUP_ID=$2
        shift 2
        ;;
      --profile-id)
        require_value "$1" "${2:-}"
        PROFILE_ID=$2
        shift 2
        ;;
      -a|--artifact-id)
        require_value "$1" "${2:-}"
        ARTIFACT_ID=$2
        shift 2
        ;;
      -v|--version)
        require_value "$1" "${2:-}"
        VERSION=$2
        shift 2
        ;;
      -o|--output-bundle)
        require_value "$1" "${2:-}"
        OUTPUT_BUNDLE=$2
        shift 2
        ;;
      --deployment-receipt)
        require_value "$1" "${2:-}"
        DEPLOYMENT_RECEIPT=$2
        shift 2
        ;;
      *)
        echo "Error: Unknown argument $1"
        print_help
        exit 1
        ;;
    esac
  done
}

parse_args "$@"

require_arg --input "${INPUT_DIR}"
require_arg --group-id "${GROUP_ID}"
require_arg --artifact-id "${ARTIFACT_ID}"
require_arg --version "${VERSION}"
require_arg --output-bundle "${OUTPUT_BUNDLE}"

require_maven_coordinates "${GROUP_ID}" "${ARTIFACT_ID}" "${VERSION}"
require_release_version "${VERSION}"
if [[ -n ${GITHUB_REF:-} ]]; then
  if [[ ! ${GITHUB_REF} =~ ^refs/tags/v([0-9]{2})\.([0-9]{2})\.([0-9]+)$ ]]; then
    fatal "automatic publication requires a release tag"
  fi
  TAG_MAJOR=${BASH_REMATCH[1]}
  TAG_MINOR=${BASH_REMATCH[2]}
  TAG_PATCH=$(printf '%s' "${BASH_REMATCH[3]}" | sed 's/^0*//')
  if [[ ${VERSION} != "${TAG_MAJOR}.${TAG_MINOR}.${TAG_PATCH:-0}" ]]; then
    fatal "Maven version '${VERSION}' does not match release tag '${GITHUB_REF}'"
  fi
fi
# A groupId nested under a verified parent namespace (e.g. com.nvidia.cuopt under the
# verified com.nvidia) still searches/creates its OSSRH staging repo under the parent's
# Sonatype profile, not the full groupId string -- so default to GROUP_ID, but let callers
# override it.
PROFILE_ID="${PROFILE_ID:-${GROUP_ID}}"
if [[ ! -d ${INPUT_DIR} ]]; then
  fatal "--input '${INPUT_DIR}' does not exist or is not a directory"
fi
: "${MAVEN_DEPLOY_USERNAME:?must be set}"
: "${MAVEN_DEPLOY_TOKEN:?must be set}"

require_cmds base64 curl jq zip md5sum sha1sum

if [[ -n ${DEPLOYMENT_RECEIPT} && -f ${DEPLOYMENT_RECEIPT} ]]; then
  DEPLOYMENT_ID=$(jq -er --arg group "${GROUP_ID}" --arg artifact "${ARTIFACT_ID}" \
    --arg version "${VERSION}" \
    'select(.groupId == $group and .artifactId == $artifact and .version == $version)
     | .deploymentId | select(type == "string" and test("^[A-Za-z0-9-]+$"))' \
    "${DEPLOYMENT_RECEIPT}") || fatal "deployment receipt does not match this release"
  if [[ -n ${GITHUB_OUTPUT:-} ]]; then
    echo "DEPLOYMENT_ID=${DEPLOYMENT_ID}" >> "${GITHUB_OUTPUT}"
  fi
  publish_deployment "${DEPLOYMENT_ID}"
  exit 0
fi

INPUT_DIR="$(cd "${INPUT_DIR}" && pwd)"
OUTPUT_BUNDLE_PARENT="$(dirname "${OUTPUT_BUNDLE}")"
mkdir -p "${OUTPUT_BUNDLE_PARENT}"
OUTPUT_BUNDLE_PARENT="$(cd "${OUTPUT_BUNDLE_PARENT}" && pwd)"
OUTPUT_BUNDLE="${OUTPUT_BUNDLE_PARENT}/$(basename "${OUTPUT_BUNDLE}")"
if [[ -e ${OUTPUT_BUNDLE} ]]; then
  fatal "--output-bundle '${OUTPUT_BUNDLE}' already exists"
fi
echo "Maven Central upload"
echo "  coordinates:   ${GROUP_ID}:${ARTIFACT_ID}:${VERSION}"
echo "  profile:       ${PROFILE_ID}"
echo "  input dir:     ${INPUT_DIR}"
echo "  output bundle: ${OUTPUT_BUNDLE}"
echo "  staging API:   ${OSSRH_STAGING_API_URL}"
echo "  target portal: ${CENTRAL_PORTAL_URL}"

WORK_DIR="$(mktemp -d)"
STAGING_REPOSITORY_KEY=""
STAGING_FILE_UPLOADED="false"
STAGING_HANDED_OFF="false"

cleanup() {
  local status=$?
  if (( status != 0 )) \
    && [[ ${STAGING_FILE_UPLOADED} == "true" ]] \
    && [[ ${STAGING_HANDED_OFF} != "true" ]]; then
    if [[ -z ${STAGING_REPOSITORY_KEY} ]]; then
      STAGING_REPOSITORY_KEY=$(staging_search_repositories open 2>/dev/null \
        | jq -r 'if (.repositories | length) == 1 then .repositories[0].key else empty end' \
        2>/dev/null) || true
    fi
    if [[ -n ${STAGING_REPOSITORY_KEY} ]]; then
      echo "Cleaning up open OSSRH staging repository ${STAGING_REPOSITORY_KEY}" >&2
      staging_drop_repository "${STAGING_REPOSITORY_KEY}" \
        || echo "Warning: could not drop staging repository ${STAGING_REPOSITORY_KEY}; remove it before retrying" >&2
    fi
  fi
  rm -rf "${WORK_DIR}"
  return "${status}"
}
trap cleanup EXIT

BUNDLE_DIR="${WORK_DIR}/bundle"
BUNDLE_ARTIFACT_DIR="${BUNDLE_DIR}/$(maven_group_path "${GROUP_ID}")/${ARTIFACT_ID}/${VERSION}"
mkdir -p "${BUNDLE_DIR}"

require_no_open_staging_repository
copy_bundle "${INPUT_DIR}" "${BUNDLE_DIR}"
require_bundle_contents "${BUNDLE_ARTIFACT_DIR}"
generate_bundle_checksums "${BUNDLE_ARTIFACT_DIR}"

BUNDLE_ZIP="${WORK_DIR}/${ARTIFACT_ID}-${VERSION}.zip"
create_bundle_zip "${BUNDLE_ZIP}" "${BUNDLE_DIR}"
mv "${BUNDLE_ZIP}" "${OUTPUT_BUNDLE}"

DEPLOYMENT_ID=""
upload_tree_to_staging "${BUNDLE_DIR}"
find_open_staging_repository
echo "Handing staging repository to the Publisher Portal"
staging_handoff_repository "${STAGING_REPOSITORY_KEY}"
wait_for_portal_handoff "${STAGING_REPOSITORY_KEY}"
STAGING_HANDED_OFF="true"
if [[ -n ${DEPLOYMENT_RECEIPT} ]]; then
  jq -n --arg deployment "${DEPLOYMENT_ID}" --arg group "${GROUP_ID}" \
    --arg artifact "${ARTIFACT_ID}" --arg version "${VERSION}" \
    '{deploymentId: $deployment, groupId: $group, artifactId: $artifact, version: $version}' \
    > "${DEPLOYMENT_RECEIPT}"
fi
if [[ -n ${GITHUB_OUTPUT:-} ]]; then
  echo "STAGING_REPOSITORY_KEY=${STAGING_REPOSITORY_KEY}" >> "${GITHUB_OUTPUT}"
  echo "DEPLOYMENT_ID=${DEPLOYMENT_ID}" >> "${GITHUB_OUTPUT}"
fi

publish_deployment "${DEPLOYMENT_ID}"
