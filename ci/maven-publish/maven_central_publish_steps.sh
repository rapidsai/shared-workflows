#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Step functions used by maven_central_publish.sh. Sourced, not executed:
#   . "${SCRIPT_DIR}/maven_central_publish_steps.sh"
#
# Two layers:
#   * HTTP wrappers (artifactory_* / portal_*) - single-purpose curl calls.
#     Accept optional trailing `retries` and `retry_delay_sec` args (defaults
#     3 / 2 seconds).
#   * Step functions - workflow-level actions composed from the HTTP wrappers,
#     with logging and validation.
#
# Both layers rely on globals set by the calling script (ARTIFACTORY_*,
# MAVEN_DEPLOY_*, CENTRAL_PORTAL_URL, POLL_*, STAGING_URL, STAGING_SUBPATH,
# ARTIFACT_ID, VERSION, BUNDLE_DIR) and on `fatal` from maven_utils.sh.

# -----------------------------------------------------------------------------
# HTTP wrappers
# -----------------------------------------------------------------------------

# Base64 HTTP Basic value for the Publisher Portal (user token : password).
_portal_auth() {
  printf '%s:%s' "${MAVEN_DEPLOY_USERNAME}" "${MAVEN_DEPLOY_TOKEN}" | base64 -w0
}

# GET a JSON payload from Artifactory. Writes the response body to stdout.
#   $1 = repo-relative path (e.g. "api/storage/<repo>/<subpath>")
#   $2 = retries (default 3)
#   $3 = retry_delay_sec (default 2)
artifactory_get_json() {
  local path=$1
  local retries=${2:-3}
  local retry_delay_sec=${3:-2}
  curl -sS -f --retry "${retries}" --retry-delay "${retry_delay_sec}" \
    --user "${ARTIFACTORY_USERNAME}:${ARTIFACTORY_TOKEN}" \
    "${ARTIFACTORY_URL}/${path}"
}

# GET a single file from Artifactory and save it to disk.
#   $1 = full remote URL
#   $2 = local destination path
#   $3 = retries (default 3)
#   $4 = retry_delay_sec (default 2)
artifactory_download_file() {
  local remote_url=$1
  local local_path=$2
  local retries=${3:-3}
  local retry_delay_sec=${4:-2}
  curl -sS -f --retry "${retries}" --retry-delay "${retry_delay_sec}" \
    --user "${ARTIFACTORY_USERNAME}:${ARTIFACTORY_TOKEN}" \
    -o "${local_path}" \
    "${remote_url}"
}

# POST a signed bundle zip to the Publisher Portal upload endpoint. Writes the
# deployment id returned by the Portal to stdout.
#   $1 = bundle zip path (already assembled locally)
#   $2 = deployment name (unencoded; URL-encoded by this function)
#   $3 = retries (default 3)
#   $4 = retry_delay_sec (default 2)
portal_upload_bundle() {
  local bundle_path=$1
  local deployment_name=$2
  local retries=${3:-3}
  local retry_delay_sec=${4:-2}
  local url
  url="${CENTRAL_PORTAL_URL}/api/v1/publisher/upload?name=$(printf %s "${deployment_name}" | jq -sRr @uri)&publishingType=USER_MANAGED"
  curl -sS -f --retry "${retries}" --retry-delay "${retry_delay_sec}" \
    -H "Authorization: Bearer $(_portal_auth)" \
    -F "bundle=@${bundle_path}" \
    -X POST "${url}"
}

# POST to the Publisher Portal status endpoint. Writes the response body to
# stdout. On any curl failure, writes '{}' so callers can safely feed the
# result to `jq` without extra error handling.
#   $1 = deployment id
#   $2 = retries (default 3)
#   $3 = retry_delay_sec (default 2)
portal_get_status() {
  local deployment_id=$1
  local retries=${2:-3}
  local retry_delay_sec=${3:-2}
  local url="${CENTRAL_PORTAL_URL}/api/v1/publisher/status?id=${deployment_id}"
  curl -sS -f --retry "${retries}" --retry-delay "${retry_delay_sec}" \
    -H "Authorization: Bearer $(_portal_auth)" \
    -X POST "${url}" || echo '{}'
}

# DELETE a deployment from the Publisher Portal.
#   $1 = deployment id
#   $2 = retries (default 3)
#   $3 = retry_delay_sec (default 2)
portal_drop_deployment() {
  local deployment_id=$1
  local retries=${2:-3}
  local retry_delay_sec=${3:-2}
  curl -sS -f --retry "${retries}" --retry-delay "${retry_delay_sec}" \
    -H "Authorization: Bearer $(_portal_auth)" \
    -X DELETE "${CENTRAL_PORTAL_URL}/api/v1/publisher/deployment/${deployment_id}" \
    -o /dev/null
}

# -----------------------------------------------------------------------------
# Workflow step functions
# -----------------------------------------------------------------------------

list_staged_files() {
  artifactory_get_json "api/storage/${ARTIFACTORY_REPOSITORY}/${STAGING_SUBPATH}" \
    | jq -r '.children[] | select(.folder == false) | .uri | ltrimstr("/")'
}

# Fail early with a clear message rather than an opaque Central VALIDATION_FAILED.
require_bundle_contents() {
  local -n files_ref=$1
  local pattern desc file found entry
  local expected=(
    "${ARTIFACT_ID}-${VERSION}.pom|the POM"
    "${ARTIFACT_ID}-${VERSION}.pom.asc|the POM signature"
    "${ARTIFACT_ID}-${VERSION}.jar|the unclassified primary jar"
    "${ARTIFACT_ID}-${VERSION}.jar.asc|the unclassified primary jar signature"
    "${ARTIFACT_ID}-${VERSION}-sources.jar|the sources jar"
    "${ARTIFACT_ID}-${VERSION}-javadoc.jar|the javadoc jar"
  )
  for entry in "${expected[@]}"; do
    IFS='|' read -r pattern desc <<< "${entry}"
    found=0
    for file in "${files_ref[@]}"; do
      if [[ ${file} == "${pattern}" ]]; then
        found=1
        break
      fi
    done
    if (( ! found )); then
      fatal "staged bundle is missing ${desc} (${pattern})"
    fi
  done
}

download_bundle() {
  local -n files_ref=$1
  local dest=$2
  local file
  echo "Downloading ${#files_ref[@]} files from Artifactory"
  for file in "${files_ref[@]}"; do
    echo "  GET ${file}"
    artifactory_download_file "${STAGING_URL}/${file}" "${dest}/${file}"
  done
}

# Write .md5 and .sha1 sidecars next to every non-checksum file under
# <root>. Sonatype Central rejects deployments missing either checksum
# (https://central.sonatype.org/publish/requirements/); Artifactory doesn't
# list them as children (it stores client-declared checksums as file
# metadata) so we generate at promote time.
generate_bundle_checksums() {
  local root=$1
  local file
  while IFS= read -r file; do
    md5sum  "${file}" | awk '{ print $1 }' > "${file}.md5"
    sha1sum "${file}" | awk '{ print $1 }' > "${file}.sha1"
  done < <(find "${root}" -type f ! -name '*.md5' ! -name '*.sha1' \
             ! -name '*.sha256' ! -name '*.sha512')
}

create_bundle_zip() {
  local zip_path=$1
  echo "Zipping bundle at ${zip_path}"
  (cd "${BUNDLE_DIR}" && zip -qr "${zip_path}" .)
}

# Publisher Portal API: https://central.sonatype.org/publish/publish-portal-api/
upload_bundle_to_portal() {
  local zip_path=$1
  local deployment_name=$2
  local -n out_deployment_id=$3
  echo "Uploading bundle to Publisher Portal"
  out_deployment_id=$(portal_upload_bundle "${zip_path}" "${deployment_name}")
  if [[ -z ${out_deployment_id} ]]; then
    fatal "Publisher Portal did not return a deployment id"
  fi
  echo "  deployment id: ${out_deployment_id}"
}

wait_for_validated() {
  local deployment_id=$1
  local response state elapsed=0
  echo "Polling status (interval=${POLL_INTERVAL_SEC}s, timeout=${POLL_TIMEOUT_SEC}s)"
  while (( elapsed < POLL_TIMEOUT_SEC )); do
    response=$(portal_get_status "${deployment_id}")
    state=$(jq -r '.deploymentState // "UNKNOWN"' <<< "${response}")
    echo "  [${elapsed}s] state=${state}"
    case "${state}" in
      VALIDATED) return 0 ;;
      FAILED|VALIDATION_FAILED)
        jq . <<< "${response}" >&2 || true
        fatal "Publisher Portal reported terminal state ${state}"
        ;;
      PUBLISHED)
        fatal "unexpected PUBLISHED state (USER_MANAGED deployments should never auto-publish)"
        ;;
    esac
    sleep "${POLL_INTERVAL_SEC}"
    elapsed=$((elapsed + POLL_INTERVAL_SEC))
  done
  fatal "timed out waiting for VALIDATED, last state was '${state}'"
}

drop_deployment() {
  local deployment_id=$1
  echo "Dropping deployment ${deployment_id} (--auto-drop true)"
  portal_drop_deployment "${deployment_id}"
  echo "Deployment dropped. Nothing was published to Maven Central."
}
