#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Creates a deployment bundle from a signed Maven repository tree, uploads it
# through the Central Publisher Portal API, and polls the
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
MANIFEST=""

print_help() {
  cat << EOF

Usage: maven_central_publish.sh --input <path> --group-id <g> \\
                                --artifact-id <a> --version <v> \\
                                --output-bundle <path> [OPTIONS]

Copies a signed Maven repository tree into scratch space, generates .md5 and
.sha1 sidecars, and creates a retained ZIP. It uploads the ZIP through the
Central Publisher Portal API, validates the complete coordinate set, publishes, and waits for
PUBLISHED. The release tag authorizes publication.

REQUIRED:
    -i, --input                    Signed Maven repository directory.
    -g, --group-id                 Maven groupId, e.g. ai.rapids.
    -a, --artifact-id              Maven artifactId, e.g. cudf.
    -v, --version                  Release version, e.g. 26.08.0.
    -o, --output-bundle            Path for the retained deployment ZIP.

OPTIONS:
    --profile-id <p>                Accepted for caller compatibility; unused by the Portal API.
    --manifest <path>              Coordinate manifest from bundle preparation.
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
      --manifest)
        require_value "$1" "${2:-}"
        MANIFEST=$2
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
if [[ ! -d ${INPUT_DIR} ]]; then
  fatal "--input '${INPUT_DIR}' does not exist or is not a directory"
fi
: "${MAVEN_DEPLOY_USERNAME:?must be set}"
: "${MAVEN_DEPLOY_TOKEN:?must be set}"

require_cmds base64 curl jq zip md5sum sha1sum
EXPECTED_COORDINATES=$(load_publication_coordinates "${MANIFEST}")
# Validate even on resume: a receipt may never authorize a changed package set.
validate_publication_tree "${INPUT_DIR}" "${EXPECTED_COORDINATES}"

if [[ -n ${DEPLOYMENT_RECEIPT} && -f ${DEPLOYMENT_RECEIPT} ]]; then
  DEPLOYMENT_ID=$(jq -er --arg group "${GROUP_ID}" --arg artifact "${ARTIFACT_ID}" \
    --arg version "${VERSION}" --argjson coordinates "${EXPECTED_COORDINATES}" \
    'select(.groupId == $group and .artifactId == $artifact and .version == $version)
     | select((.coordinates // [{groupId: $group, artifactId: $artifact, version: $version, packaging: "jar"}]) == $coordinates)
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
# profile-id is retained only for backward compatibility.
: "${PROFILE_ID}"
echo "  input dir:     ${INPUT_DIR}"
echo "  output bundle: ${OUTPUT_BUNDLE}"
echo "  target portal: ${CENTRAL_PORTAL_URL}"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT
BUNDLE_DIR="${WORK_DIR}/bundle"
mkdir -p "${BUNDLE_DIR}"
copy_bundle "${INPUT_DIR}" "${BUNDLE_DIR}"
validate_publication_tree "${BUNDLE_DIR}" "${EXPECTED_COORDINATES}"
generate_bundle_checksums "${BUNDLE_DIR}"

BUNDLE_ZIP="${WORK_DIR}/${ARTIFACT_ID}-${VERSION}.zip"
create_bundle_zip "${BUNDLE_ZIP}" "${BUNDLE_DIR}"
mv "${BUNDLE_ZIP}" "${OUTPUT_BUNDLE}"

# USER_MANAGED prevents an upload from publishing before identity validation.
DEPLOYMENT_ID=$(portal_upload_bundle "${OUTPUT_BUNDLE}" "${GROUP_ID}:${ARTIFACT_ID}:${VERSION}" USER_MANAGED)
[[ ${DEPLOYMENT_ID} =~ ^[A-Za-z0-9-]+$ ]] || fatal "invalid Portal deployment id"
if [[ -n ${DEPLOYMENT_RECEIPT} ]]; then
  jq -n --arg deployment "${DEPLOYMENT_ID}" --arg group "${GROUP_ID}" \
    --arg artifact "${ARTIFACT_ID}" --arg version "${VERSION}" \
    --argjson coordinates "${EXPECTED_COORDINATES}" \
    '{deploymentId: $deployment, groupId: $group, artifactId: $artifact, version: $version, coordinates: $coordinates}' \
    > "${DEPLOYMENT_RECEIPT}"
fi
if [[ -n ${GITHUB_OUTPUT:-} ]]; then
  echo "DEPLOYMENT_ID=${DEPLOYMENT_ID}" >> "${GITHUB_OUTPUT}"
fi

publish_deployment "${DEPLOYMENT_ID}"
