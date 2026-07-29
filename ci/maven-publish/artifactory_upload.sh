#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Host wrapper that runs artifactory_upload_in_container.sh in the Maven
# image; see --help for behavior and arguments.
#
# TODO: add --nightly-date + NIGHTLY_DATE env plumbing when nightly support
# lands.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
. "${SCRIPT_DIR}/argparse.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_utils.sh"

INPUT_DIR=""
PUBLICATION_TYPE=""
ARTIFACTORY_URL=""
ARTIFACTORY_REPOSITORY=""
SOURCE_GIT_SHA=""
IMAGE="maven:3-eclipse-temurin-17"

print_help() {
  cat << EOF

Usage: artifactory_upload.sh --input <path> --publication-type rc [OPTIONS]

Signs every file under a Maven repository directory and uploads the result
to an internal Artifactory repository. Any prior upload at the same
coordinate is deleted first (logged as "OVERRIDE:").

REQUIRED:
    -i, --input                Maven repository directory to sign and upload
                               (e.g. <input>/<groupPath>/<artifactId>/<version>/*).
    -t, --publication-type     Only "rc" is currently supported. Nightly is
                               a future addition. The flag is retained so
                               callers do not have to change when it lands.
    -u, --artifactory-url      Base URL of the Artifactory server (no
                               trailing slash).
    -r, --artifactory-repository
                               Artifactory repository name to upload into.

OPTIONS:
    -s, --source-git-sha       Git SHA to attach as the source.git-sha
                               Artifactory property (default: empty).
    -h, --help                 Show this help message.

ENVIRONMENT VARIABLES:
    GPG_PRIVATE_KEY            Armored GPG private key (required).
    GPG_PASSPHRASE             Passphrase for the GPG private key (required).
    ARTIFACTORY_USERNAME       Artifactory account with write access (required).
    ARTIFACTORY_TOKEN          Auth token for ARTIFACTORY_USERNAME (required).
    GITHUB_OUTPUT              If set, the emitted key=value lines (see
                               OUTPUTS below) are also appended here.

OUTPUTS:
    Writes key=value lines to stdout (also to \$GITHUB_OUTPUT, so GHA
    steps can read them as \`steps.<id>.outputs.<key>\`):

        GROUP_ID       Maven groupId, from the POM.
        ARTIFACT_ID    Maven artifactId, from the POM.
        VERSION        Maven version, from the POM.

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
        require_value "$1" "$2"
        INPUT_DIR=$2
        shift 2
        ;;
      -t|--publication-type)
        require_value "$1" "$2"
        PUBLICATION_TYPE=$2
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
      -s|--source-git-sha)
        require_value "$1" "$2"
        SOURCE_GIT_SHA=$2
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

require_arg --input                  "${INPUT_DIR}"
require_arg --publication-type       "${PUBLICATION_TYPE}"
require_arg --artifactory-url        "${ARTIFACTORY_URL}"
require_arg --artifactory-repository "${ARTIFACTORY_REPOSITORY}"

if [[ ${PUBLICATION_TYPE} != "rc" ]]; then
  fatal "only --publication-type rc is supported for now; nightly is a future addition (got '${PUBLICATION_TYPE}')"
fi
if [[ ! -d ${INPUT_DIR} ]]; then
  fatal "--input '${INPUT_DIR}' does not exist or is not a directory"
fi

# Fail fast before launching the container.
: "${GPG_PRIVATE_KEY:?must be set}"
: "${GPG_PASSPHRASE:?must be set}"
: "${ARTIFACTORY_USERNAME:?must be set}"
: "${ARTIFACTORY_TOKEN:?must be set}"

INPUT_DIR="$(cd "${INPUT_DIR}" && pwd)"

OUTPUT_SCRATCH="$(mktemp -d)"
trap 'rm -rf "${OUTPUT_SCRATCH}"' EXIT

echo "Artifactory upload"
echo "  image:                ${IMAGE}"
echo "  publication type:     ${PUBLICATION_TYPE}"
echo "  input dir:            ${INPUT_DIR}"
echo "  metadata scratch:     ${OUTPUT_SCRATCH}"

DOCKER_ARGS=(
  --rm
  --volume "${INPUT_DIR}:/input"
  --volume "${OUTPUT_SCRATCH}:/output"
  --workdir /input
  --env ARTIFACTORY_URL="${ARTIFACTORY_URL}"
  --env ARTIFACTORY_REPOSITORY="${ARTIFACTORY_REPOSITORY}"
  --env ARTIFACTORY_USERNAME="${ARTIFACTORY_USERNAME}"
  --env ARTIFACTORY_TOKEN="${ARTIFACTORY_TOKEN}"
  --env GPG_PRIVATE_KEY="${GPG_PRIVATE_KEY}"
  --env GPG_PASSPHRASE="${GPG_PASSPHRASE}"
  --env SOURCE_GIT_SHA="${SOURCE_GIT_SHA}"
  --env HOST_UID="$(id -u)"
  --env HOST_GID="$(id -g)"
  --volume "${SCRIPT_DIR}:/scripts:ro"
)

docker run "${DOCKER_ARGS[@]}" "${IMAGE}" \
  bash /scripts/artifactory_upload_in_container.sh

METADATA_FILE="${OUTPUT_SCRATCH}/upload_metadata.env"
if [[ ! -f ${METADATA_FILE} ]]; then
  fatal "worker did not produce ${METADATA_FILE}"
fi

# shellcheck disable=SC1090
. "${METADATA_FILE}"

if [[ -z ${GROUP_ID} || -z ${ARTIFACT_ID} || -z ${VERSION} ]]; then
  cat "${METADATA_FILE}" >&2 || true
  fatal "worker metadata is missing GROUP_ID / ARTIFACT_ID / VERSION"
fi

{
  echo "GROUP_ID=${GROUP_ID}"
  echo "ARTIFACT_ID=${ARTIFACT_ID}"
  echo "VERSION=${VERSION}"
} | tee -a "${GITHUB_OUTPUT:-/dev/null}"

echo "Artifactory upload completed for ${GROUP_ID}:${ARTIFACT_ID}:${VERSION}"
