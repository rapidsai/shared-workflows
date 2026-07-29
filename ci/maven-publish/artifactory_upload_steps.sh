#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Step functions used by artifactory_upload_in_container.sh. Sourced, not executed:
#   . "${SCRIPT_DIR}/artifactory_upload_steps.sh"
#
# Two layers:
#   * HTTP wrapper (artifactory_put_file) - single-purpose curl call. Accepts
#     optional trailing `retries` and `retry_delay_sec` args (defaults 3 / 2 s).
#   * Step functions - workflow-level actions composed on top, plus a couple
#     of `discover_*` / `read_*` helpers so the main script reads top-to-bottom
#     as an outline.
#
# Both layers rely on globals set by the calling script (ARTIFACTORY_*,
# GPG_PRIVATE_KEY, GPG_PASSPHRASE) and on `fatal` from maven_utils.sh.

# -----------------------------------------------------------------------------
# HTTP wrapper
# -----------------------------------------------------------------------------

# PUT a local file to an Artifactory URL. Response body is discarded.
#   $1 = local file path
#   $2 = full remote URL (may include Artifactory ;matrix=params)
#   $3 = retries (default 3)
#   $4 = retry_delay_sec (default 2)
artifactory_put_file() {
  local local_path=$1
  local remote_url=$2
  local retries=${3:-3}
  local retry_delay_sec=${4:-2}
  curl -sS -f --retry "${retries}" --retry-delay "${retry_delay_sec}" \
    --user "${ARTIFACTORY_USERNAME}:${ARTIFACTORY_TOKEN}" \
    -T "${local_path}" \
    -X PUT "${remote_url}" \
    -o /dev/null
}

# -----------------------------------------------------------------------------
# Container prep
# -----------------------------------------------------------------------------

# Base maven image lacks gpg and curl; install both on demand (no-op if already
# present, e.g. from an image rebuild that includes them).
install_container_deps() {
  if command -v gpg >/dev/null && command -v curl >/dev/null; then
    return 0
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -qq -y --no-install-recommends gnupg curl
}

# -----------------------------------------------------------------------------
# POM discovery and parsing
# -----------------------------------------------------------------------------

# Populate <out_pom_var> with the single POM found under <input_dir>. Fatals if
# 0 or >1 POMs are present.
discover_single_pom() {
  local input_dir=$1
  local -n out_pom_var=$2
  local -a candidates
  mapfile -t candidates < <(find "${input_dir}" -type f -name '*.pom' | sort)
  case ${#candidates[@]} in
    0) fatal "no *.pom found anywhere under ${input_dir}" ;;
    1) out_pom_var=${candidates[0]} ;;
    *)
      { echo "Error: expected exactly one POM under ${input_dir}, found:"
        printf '  %s\n' "${candidates[@]}"; } >&2
      exit 1
      ;;
  esac
}

# Read groupId / artifactId / version from <pom_file> into the three nameref
# out-vars via `mvn help:evaluate`. Fatals if any resolves empty.
read_pom_coordinates() {
  local pom_file=$1
  local -n out_group_var=$2
  local -n out_artifact_var=$3
  local -n out_version_var=$4
  out_group_var=$(mvn -q -B -f "${pom_file}" help:evaluate -Dexpression=project.groupId -DforceStdout)
  out_artifact_var=$(mvn -q -B -f "${pom_file}" help:evaluate -Dexpression=project.artifactId -DforceStdout)
  out_version_var=$(mvn -q -B -f "${pom_file}" help:evaluate -Dexpression=project.version -DforceStdout)
  if [[ -z ${out_group_var} || -z ${out_artifact_var} || -z ${out_version_var} ]]; then
    fatal "failed to read groupId/artifactId/version from ${pom_file}"
  fi
}

# -----------------------------------------------------------------------------
# GPG signing
# -----------------------------------------------------------------------------

# Import GPG_PRIVATE_KEY into a fresh, per-run GNUPGHOME (isolated from any
# ambient state). Populate <out_key_id> with the imported secret key's long ID.
import_gpg_signing_key() {
  local -n out_key_id=$1
  export GNUPGHOME
  GNUPGHOME="$(mktemp -d)"
  chmod 700 "${GNUPGHOME}"
  echo "${GPG_PRIVATE_KEY}" | gpg --batch --yes --pinentry-mode loopback \
    --passphrase "${GPG_PASSPHRASE}" --import
  out_key_id=$(gpg --list-secret-keys --keyid-format=long --with-colons \
    | awk -F: '$1=="sec" { print $5; exit }')
  if [[ -z ${out_key_id} ]]; then
    fatal "no GPG secret key was imported"
  fi
}

# Populate <out_files> with every file directly under <artifact_dir> that is
# NOT itself a signature or checksum (those would be signed-signature / signed-
# checksum garbage). Fatals if the result would be empty.
list_artifacts_to_sign() {
  local artifact_dir=$1
  local -n out_files=$2
  mapfile -t out_files < <(find "${artifact_dir}" -maxdepth 1 -type f \
    ! -name '*.asc' ! -name '*.md5' ! -name '*.sha1' ! -name '*.sha256' \
    ! -name '*.sha512' | sort)
  if (( ${#out_files[@]} == 0 )); then
    fatal "no artifacts found under ${artifact_dir} to sign"
  fi
}

# Produce a detached .asc signature next to every file in <files_ref>, using
# the imported key. The promoter byte-forwards these signatures unmodified.
sign_artifacts_in_place() {
  local -n files_ref=$1
  local key_id=$2
  local file
  for file in "${files_ref[@]}"; do
    gpg --batch --yes --pinentry-mode loopback \
      --passphrase "${GPG_PASSPHRASE}" \
      --local-user "${key_id}" \
      --armor --detach-sign \
      --output "${file}.asc" \
      "${file}"
  done
}

# -----------------------------------------------------------------------------
# Upload
# -----------------------------------------------------------------------------

# DELETE anything already at <path>. Logs "OVERRIDE:" when executed.
clear_destination_if_exists() {
  local path=$1
  local status
  status=$(curl -sS -o /dev/null -w '%{http_code}' \
    --user "${ARTIFACTORY_USERNAME}:${ARTIFACTORY_TOKEN}" \
    "${ARTIFACTORY_URL}/api/storage/${ARTIFACTORY_REPOSITORY}/${path}")
  case ${status} in
    200)
      echo "OVERRIDE: existing content found at ${path}. Deleting before re-upload"
      curl -sS -f --retry 3 --retry-delay 2 \
        --user "${ARTIFACTORY_USERNAME}:${ARTIFACTORY_TOKEN}" \
        -X DELETE "${ARTIFACTORY_URL}/${ARTIFACTORY_REPOSITORY}/${path}" \
        -o /dev/null
      ;;
    404)
      echo "Destination ${path} is empty. Proceeding with fresh upload"
      ;;
    *)
      fatal "unexpected HTTP ${status} probing ${path}"
      ;;
  esac
}

# Upload every file in <files_ref> plus its .asc sibling to Artifactory under
# <staging_url>, tacking <prop_matrix> onto each PUT so Artifactory stores the
# matrix params as queryable properties.
upload_artifacts_to_staging() {
  local -n files_ref=$1
  local staging_url=$2
  local staging_path=$3
  local prop_matrix=$4
  local file candidate remote_name
  for file in "${files_ref[@]}"; do
    for candidate in "${file}" "${file}.asc"; do
      remote_name=$(basename "${candidate}")
      echo "  PUT ${remote_name} -> ${staging_path}/${remote_name}"
      artifactory_put_file "${candidate}" "${staging_url}/${remote_name}${prop_matrix}"
    done
  done
}

# -----------------------------------------------------------------------------
# Handoff to host script
# -----------------------------------------------------------------------------

# Write resolved coordinates to a env-file for artifactory_upload.sh to source.
write_upload_metadata() {
  local out_path=$1
  local group_id=$2
  local artifact_id=$3
  local version=$4
  {
    echo "GROUP_ID=${group_id}"
    echo "ARTIFACT_ID=${artifact_id}"
    echo "VERSION=${version}"
  } > "${out_path}"
}
