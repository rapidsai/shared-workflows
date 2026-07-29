#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# In-container worker for artifactory_upload.sh. Signs files under /input and
# uploads them to Artifactory at <groupPath>/<artifactId>/<version>/, deleting
# any prior content at that coordinate first (logged as "OVERRIDE:"); latest
# wins. source.git-sha is set as a matrix property for audit. Writes
# coordinates to /output/upload_metadata.env.
#
# TODO: add the nightly path (staging/nightly/<date>/...) when nightly
# support lands.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_utils.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/artifactory_upload_steps.sh"

INPUT_DIR=/input
OUTPUT_DIR=/output

: "${ARTIFACTORY_URL:?must be set}"
: "${ARTIFACTORY_REPOSITORY:?must be set}"
: "${ARTIFACTORY_USERNAME:?must be set}"
: "${ARTIFACTORY_TOKEN:?must be set}"
: "${GPG_PRIVATE_KEY:?must be set}"
: "${GPG_PASSPHRASE:?must be set}"
: "${HOST_UID:?must be set}"
: "${HOST_GID:?must be set}"

mkdir -p "${OUTPUT_DIR}"
trap 'chown -R "${HOST_UID}:${HOST_GID}" "${INPUT_DIR}" "${OUTPUT_DIR}" 2>/dev/null || true' EXIT

install_container_deps

# -----------------------------------------------------------------------------
# Resolve the artifact we are publishing.
# -----------------------------------------------------------------------------
POM_FILE=""
discover_single_pom "${INPUT_DIR}" POM_FILE
ARTIFACT_DIR="$(dirname "${POM_FILE}")"

GROUP_ID=""
ARTIFACT_ID=""
VERSION=""
read_pom_coordinates "${POM_FILE}" GROUP_ID ARTIFACT_ID VERSION

EXPECTED_ARTIFACT_DIR="${INPUT_DIR}/$(maven_group_path "${GROUP_ID}")/${ARTIFACT_ID}/${VERSION}"
if [[ ${ARTIFACT_DIR} != "${EXPECTED_ARTIFACT_DIR}" ]]; then
  fatal "POM location ${ARTIFACT_DIR} does not match expected Maven repository layout ${EXPECTED_ARTIFACT_DIR}"
fi
require_release_version "${VERSION}"

# -----------------------------------------------------------------------------
# Compute the Artifactory destination.
# -----------------------------------------------------------------------------
STAGING_PATH=$(maven_release_path "${GROUP_ID}" "${ARTIFACT_ID}" "${VERSION}")
STAGING_URL="${ARTIFACTORY_URL}/${ARTIFACTORY_REPOSITORY}/${STAGING_PATH}"

# Log the repo-relative path.
echo "Destination: ${STAGING_PATH}"

# -----------------------------------------------------------------------------
# Sign every file under the POM's directory.
# -----------------------------------------------------------------------------
GPG_KEY_ID=""
import_gpg_signing_key GPG_KEY_ID
echo "GPG signing key: ${GPG_KEY_ID}"

ARTIFACTS=()
echo "Signing artifacts under ${ARTIFACT_DIR}"
list_artifacts_to_sign "${ARTIFACT_DIR}" ARTIFACTS
sign_artifacts_in_place ARTIFACTS "${GPG_KEY_ID}"

# -----------------------------------------------------------------------------
# Upload the signed bundle. Matrix params get stored as Artifactory properties.
# -----------------------------------------------------------------------------
PROP_MATRIX=""
if [[ -n ${SOURCE_GIT_SHA} ]]; then
  PROP_MATRIX=";source.git-sha=${SOURCE_GIT_SHA}"
fi

clear_destination_if_exists "${STAGING_PATH}"

echo "Uploading to ${STAGING_PATH}"
upload_artifacts_to_staging ARTIFACTS "${STAGING_URL}" "${STAGING_PATH}" "${PROP_MATRIX}"

# -----------------------------------------------------------------------------
# Hand resolved coordinates back to the host script.
# -----------------------------------------------------------------------------
write_upload_metadata "${OUTPUT_DIR}/upload_metadata.env" \
  "${GROUP_ID}" "${ARTIFACT_ID}" "${VERSION}"

echo "Upload complete for ${GROUP_ID}:${ARTIFACT_ID}:${VERSION} at ${STAGING_PATH}"
