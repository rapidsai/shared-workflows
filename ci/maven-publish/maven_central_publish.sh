#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Uploads a bundle to the Sonatype Publisher Portal and polls until it
# passes validation. Never calls the /publish endpoint - promoting to
# Maven Central always requires a human click in the Portal UI.
#
# After validation, --auto-drop decides what to do:
#   true  -> drop the deployment (no PENDING entry left in the Portal)
#   false -> leave it PENDING for a human to Publish or Drop
#
# Runs on the host (no docker) - only needs curl, jq, zip.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
. "${SCRIPT_DIR}/argparse.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_utils.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_central_publish_steps.sh"

CENTRAL_PORTAL_URL="https://central.sonatype.com"
POLL_INTERVAL_SEC=15
POLL_TIMEOUT_SEC=900

GROUP_ID=""
ARTIFACT_ID=""
VERSION=""
ARTIFACTORY_URL=""
ARTIFACTORY_REPOSITORY=""
AUTO_DROP="true"

print_help() {
  cat << EOF

Usage: maven_central_publish.sh --group-id <g> --artifact-id <a> --version <v> \\
                                --artifactory-url <url> \\
                                --artifactory-repository <repo> [OPTIONS]

Byte-forwards the signed bundle currently staged in Artifactory at
<groupPath>/<artifactId>/<version>/ (whatever the latest upload of that
coordinate is) to the Sonatype Central Publisher Portal in USER_MANAGED
mode. .md5 / .sha1 sidecars are generated locally before upload.
Sonatype requires them per file.

REQUIRED:
    -g, --group-id                 Maven groupId, e.g. ai.rapids.
    -a, --artifact-id              Maven artifactId, e.g. cudf.
    -v, --version                  Release version, e.g. 26.08.0.
    -u, --artifactory-url          Base URL of the Artifactory server.
    -r, --artifactory-repository   Artifactory repository to download from.

OPTIONS:
    --auto-drop <true|false>       Drop after VALIDATED (default: true).
    -h, --help                     Show this help message.

ENVIRONMENT VARIABLES:
    ARTIFACTORY_USERNAME           Read-access account on Artifactory (required).
    ARTIFACTORY_TOKEN              Auth token for ARTIFACTORY_USERNAME (required).
    MAVEN_DEPLOY_USERNAME          Publisher Portal user token username (required).
    MAVEN_DEPLOY_TOKEN             Publisher Portal user token password (required).

EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case $1 in
      -h|--help)
        print_help
        exit 0
        ;;
      -g|--group-id)
        require_value "$1" "$2"
        GROUP_ID=$2
        shift 2
        ;;
      -a|--artifact-id)
        require_value "$1" "$2"
        ARTIFACT_ID=$2
        shift 2
        ;;
      -v|--version)
        require_value "$1" "$2"
        VERSION=$2
        shift 2
        ;;
      -u|--artifactory-url)
        require_value "$1" "$2"
        ARTIFACTORY_URL=$2
        shift 2
        ;;
      -r|--artifactory-repository)
        require_value "$1" "$2"
        ARTIFACTORY_REPOSITORY=$2
        shift 2
        ;;
      --auto-drop)
        require_value "$1" "$2"
        AUTO_DROP=$2
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

require_arg --group-id               "${GROUP_ID}"
require_arg --artifact-id            "${ARTIFACT_ID}"
require_arg --version                "${VERSION}"
require_arg --artifactory-url        "${ARTIFACTORY_URL}"
require_arg --artifactory-repository "${ARTIFACTORY_REPOSITORY}"

if [[ ${AUTO_DROP} != "true" && ${AUTO_DROP} != "false" ]]; then
  fatal "--auto-drop must be 'true' or 'false' (got '${AUTO_DROP}')"
fi
require_release_version "${VERSION}"

: "${ARTIFACTORY_USERNAME:?must be set}"
: "${ARTIFACTORY_TOKEN:?must be set}"
: "${MAVEN_DEPLOY_USERNAME:?must be set}"
: "${MAVEN_DEPLOY_TOKEN:?must be set}"

require_cmds curl jq zip

STAGING_SUBPATH=$(maven_release_path "${GROUP_ID}" "${ARTIFACT_ID}" "${VERSION}")
STAGING_URL="${ARTIFACTORY_URL}/${ARTIFACTORY_REPOSITORY}/${STAGING_SUBPATH}"

echo "Maven Central promote"
echo "  coordinates:   ${GROUP_ID}:${ARTIFACT_ID}:${VERSION}"
echo "  auto-drop:     ${AUTO_DROP}"
echo "  source path:   ${STAGING_SUBPATH}"
echo "  target portal: ${CENTRAL_PORTAL_URL}"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

BUNDLE_DIR="${WORK_DIR}/bundle"
BUNDLE_ARTIFACT_DIR="${BUNDLE_DIR}/$(maven_group_path "${GROUP_ID}")/${ARTIFACT_ID}/${VERSION}"
mkdir -p "${BUNDLE_ARTIFACT_DIR}"

# -----------------------------------------------------------------------------
# Main flow.
# -----------------------------------------------------------------------------

echo "Listing staged files at ${STAGING_SUBPATH}"
mapfile -t STAGED_FILES < <(list_staged_files)
if (( ${#STAGED_FILES[@]} == 0 )); then
  fatal "no files found at ${STAGING_SUBPATH} to promote"
fi

require_bundle_contents STAGED_FILES
download_bundle STAGED_FILES "${BUNDLE_ARTIFACT_DIR}"
generate_bundle_checksums "${BUNDLE_ARTIFACT_DIR}"

BUNDLE_ZIP="${WORK_DIR}/${ARTIFACT_ID}-${VERSION}.zip"
create_bundle_zip "${BUNDLE_ZIP}"

DEPLOYMENT_ID=""
upload_bundle_to_portal "${BUNDLE_ZIP}" "${ARTIFACT_ID}-${VERSION}" DEPLOYMENT_ID

wait_for_validated "${DEPLOYMENT_ID}"

if [[ ${AUTO_DROP} == "true" ]]; then
  drop_deployment "${DEPLOYMENT_ID}"
else
  cat <<EOF
Deployment left in PENDING state.
  Deployment id:  ${DEPLOYMENT_ID}
  Portal UI:      ${CENTRAL_PORTAL_URL}/publishing/deployments
A human must review the deployment and click Publish to release it to
Maven Central.
EOF
fi
