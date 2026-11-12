#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# In-container worker for prepare_maven_bundle.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
. "${SCRIPT_DIR}/maven_utils.sh"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/prepare_maven_bundle_steps.sh"

INPUT_DIR=/input
BUNDLE_DIR=/bundle
METADATA_DIR=/metadata

: "${GPG_PRIVATE_KEY:?must be set}"
: "${GPG_PASSPHRASE:?must be set}"
: "${HOST_UID:?must be set}"
: "${HOST_GID:?must be set}"

trap 'chown -R "${HOST_UID}:${HOST_GID}" "${BUNDLE_DIR}" "${METADATA_DIR}" 2>/dev/null || true' EXIT

install_container_deps
cp -a "${INPUT_DIR}/." "${BUNDLE_DIR}/"
remove_generated_sidecars "${BUNDLE_DIR}"

mapfile -t POMS < <(find "${BUNDLE_DIR}" -type f -name '*.pom' | sort)
(( ${#POMS[@]} > 0 )) || fatal "no POMs found"
if (( ${#POMS[@]} > 1 )) && [[ -z ${PRIMARY_ARTIFACT_ID:-} ]]; then
  fatal "multiple coordinates require --primary-artifact-id for receipt compatibility"
fi
COORDINATES='[]'
for POM_FILE in "${POMS[@]}"; do
  GROUP_ID=""
  ARTIFACT_ID=""
  VERSION=""
  read_pom_coordinates "${POM_FILE}" GROUP_ID ARTIFACT_ID VERSION
  require_maven_coordinates "${GROUP_ID}" "${ARTIFACT_ID}" "${VERSION}"
  EXPECTED_ARTIFACT_DIR="${BUNDLE_DIR}/$(maven_group_path "${GROUP_ID}")/${ARTIFACT_ID}/${VERSION}"
  [[ ${POM_FILE} == "${EXPECTED_ARTIFACT_DIR}/${ARTIFACT_ID}-${VERSION}.pom" ]] \
    || fatal "POM does not match Maven repository layout: ${POM_FILE}"
  PACKAGING=$(mvn -q -B -f "${POM_FILE}" help:evaluate -Dexpression=project.packaging -DforceStdout)
  [[ ${PACKAGING} == jar || ${PACKAGING} == pom ]] || fatal "unsupported packaging: ${PACKAGING}"
  COORDINATES=$(jq -c --arg group "${GROUP_ID}" --arg artifact "${ARTIFACT_ID}" \
    --arg version "${VERSION}" --arg packaging "${PACKAGING}" \
    '. + [{groupId: $group, artifactId: $artifact, version: $version, packaging: $packaging}]' <<< "${COORDINATES}")
done
PRIMARY_ARTIFACT_ID=${PRIMARY_ARTIFACT_ID:-${ARTIFACT_ID}}
PRIMARY=$(jq -cer --arg artifact "${PRIMARY_ARTIFACT_ID}" \
  '[.[] | select(.artifactId == $artifact)] | select(length == 1) | .[0]' <<< "${COORDINATES}") \
  || fatal "primary artifact is not in the publication set"
GROUP_ID=$(jq -r .groupId <<< "${PRIMARY}")
ARTIFACT_ID=${PRIMARY_ARTIFACT_ID}
VERSION=$(jq -r .version <<< "${PRIMARY}")
printf '%s\n' "${COORDINATES}" > "${METADATA_DIR}/coordinates.json"
COORDINATES=$(load_publication_coordinates "${METADATA_DIR}/coordinates.json")
printf '%s\n' "${COORDINATES}" > "${METADATA_DIR}/coordinates.json"

GPG_KEY_ID=""
import_gpg_signing_key GPG_KEY_ID
echo "GPG signing key: ${GPG_KEY_ID}"

# Populated through a nameref in list_artifacts_to_sign.
# shellcheck disable=SC2034
ARTIFACTS=()
for POM_FILE in "${POMS[@]}"; do
  list_artifacts_to_sign "$(dirname "${POM_FILE}")" ARTIFACTS
  sign_artifacts_in_place ARTIFACTS "${GPG_KEY_ID}"
done

write_bundle_metadata "${METADATA_DIR}/bundle_metadata.env" \
  "${GROUP_ID}" "${ARTIFACT_ID}" "${VERSION}"

echo "Signing complete for ${GROUP_ID}:${ARTIFACT_ID}:${VERSION}"
