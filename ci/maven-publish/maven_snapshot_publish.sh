#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Publishes a signed Maven repository tree to the Sonatype snapshot
# repository (https://central.sonatype.com/repository/maven-snapshots/).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
. "${SCRIPT_DIR}/argparse.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_utils.sh"
# Reuse copy_bundle, require_bundle_contents, generate_bundle_checksums,
# create_bundle_zip, and _sonatype_upload from the Central helper library.
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_central_publish_steps.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_snapshot_publish_steps.sh"

SNAPSHOT_REPOSITORY_URL="https://central.sonatype.com/repository/maven-snapshots"

INPUT_DIR=""
GROUP_ID=""
ARTIFACT_ID=""
VERSION=""
OUTPUT_BUNDLE=""

print_help() {
  cat << EOF

Usage: maven_snapshot_publish.sh --input <path> --group-id <g> \\
                                 --artifact-id <a> --version <v> \\
                                 --output-bundle <path>

Copies a signed Maven repository tree into scratch space, generates .md5 and
.sha1 sidecars, creates a retained ZIP for provenance, and PUTs every file to
the Sonatype snapshot repository. VERSION must end with '-SNAPSHOT'.

REQUIRED:
    -i, --input                    Signed Maven repository directory.
    -g, --group-id                 Maven groupId, e.g. ai.rapids.
    -a, --artifact-id              Maven artifactId, e.g. cudf.
    -v, --version                  Snapshot version, e.g. 26.12.0-SNAPSHOT.
    -o, --output-bundle            Path for the retained snapshot ZIP.

OPTIONS:
    -h, --help                     Show this help message.

ENVIRONMENT VARIABLES:
    MAVEN_DEPLOY_USERNAME          Publisher Portal user token username.
    MAVEN_DEPLOY_TOKEN             Publisher Portal user token password.

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
require_snapshot_version "${VERSION}"

if [[ ! -d ${INPUT_DIR} ]]; then
  fatal "--input '${INPUT_DIR}' does not exist or is not a directory"
fi
: "${MAVEN_DEPLOY_USERNAME:?must be set}"
: "${MAVEN_DEPLOY_TOKEN:?must be set}"

require_cmds base64 curl jq zip md5sum sha1sum

INPUT_DIR="$(cd "${INPUT_DIR}" && pwd)"
OUTPUT_BUNDLE_PARENT="$(dirname "${OUTPUT_BUNDLE}")"
mkdir -p "${OUTPUT_BUNDLE_PARENT}"
OUTPUT_BUNDLE_PARENT="$(cd "${OUTPUT_BUNDLE_PARENT}" && pwd)"
OUTPUT_BUNDLE="${OUTPUT_BUNDLE_PARENT}/$(basename "${OUTPUT_BUNDLE}")"
if [[ -e ${OUTPUT_BUNDLE} ]]; then
  fatal "--output-bundle '${OUTPUT_BUNDLE}' already exists"
fi

echo "Sonatype snapshot upload"
echo "  coordinates:   ${GROUP_ID}:${ARTIFACT_ID}:${VERSION}"
echo "  input dir:     ${INPUT_DIR}"
echo "  output bundle: ${OUTPUT_BUNDLE}"
echo "  target repo:   ${SNAPSHOT_REPOSITORY_URL}"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

BUNDLE_DIR="${WORK_DIR}/bundle"
BUNDLE_ARTIFACT_DIR="${BUNDLE_DIR}/$(maven_group_path "${GROUP_ID}")/${ARTIFACT_ID}/${VERSION}"
mkdir -p "${BUNDLE_DIR}"

copy_bundle "${INPUT_DIR}" "${BUNDLE_DIR}"
# Reject if any release shaped file is present in the snapshot bundle.
require_snapshot_artifact_names "${BUNDLE_DIR}" "${VERSION}"
# Require the POM, primary/sources/javadoc jars, and their signatures.
require_bundle_contents "${BUNDLE_ARTIFACT_DIR}"
generate_bundle_checksums "${BUNDLE_ARTIFACT_DIR}"

BUNDLE_ZIP="${WORK_DIR}/${ARTIFACT_ID}-${VERSION}.zip"
create_bundle_zip "${BUNDLE_ZIP}" "${BUNDLE_DIR}"
mv "${BUNDLE_ZIP}" "${OUTPUT_BUNDLE}"

upload_tree_to_snapshots "${BUNDLE_DIR}"

echo "Snapshot upload complete."
echo "  SNAPSHOT is immediately available at:"
echo "    ${SNAPSHOT_REPOSITORY_URL}/$(maven_group_path "${GROUP_ID}")/${ARTIFACT_ID}/${VERSION}/"
