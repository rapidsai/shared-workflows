#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Sonatype HTTP wrappers and local bundle helpers for maven_central_publish.sh.

_portal_auth() {
  printf '%s:%s' "${MAVEN_DEPLOY_USERNAME}" "${MAVEN_DEPLOY_TOKEN}" \
    | base64 | tr -d '\n'
}

# _sonatype_error_log METHOD URL STATUS BODY_FILE
_sonatype_error_log() {
  echo "Sonatype request failed"
  echo "  method: $1"
  echo "  URL:    $2"
  echo "  status: $3"
  echo "  body:   $(cat "$4")"
}

# _sonatype_call METHOD URL
# Bearer auth, no request body. Prints response body on 2xx; logs failure and
# returns 1 on non-2xx.
_sonatype_call() {
  local method=$1 url=$2 retries=${3:-3}
  local body_file status
  body_file=$(mktemp)
  status=$(curl -sS --connect-timeout 15 --max-time 60 --retry "${retries}" --retry-delay 2 \
    -H "Authorization: Bearer $(_portal_auth)" \
    -X "${method}" -o "${body_file}" -w '%{http_code}' "${url}") \
    || status="transport-error"
  if [[ ${status} != 2* ]]; then
    _sonatype_error_log "${method}" "${url}" "${status}" "${body_file}" >&2
    rm -f "${body_file}"
    return 1
  fi
  cat "${body_file}"
  rm -f "${body_file}"
}

# portal_upload_bundle ZIP NAME PUBLISHING_TYPE
# Bearer auth, multipart POST of the bundle. Prints the deployment id on 201;
# logs failure and returns 1 otherwise.
portal_upload_bundle() {
  local zip_path=$1 name=$2 publishing_type=$3
  local body_file status url
  url="${CENTRAL_PORTAL_URL}/api/v1/publisher/upload?publishingType=${publishing_type}&name=$(printf %s "${name}" | jq -sRr @uri)"
  body_file=$(mktemp)
  status=$(curl -sS --retry 3 --retry-delay 2 \
    -H "Authorization: Bearer $(_portal_auth)" \
    --form "bundle=@${zip_path};type=application/octet-stream" \
    -X POST -o "${body_file}" -w '%{http_code}' "${url}") \
    || status="transport-error"
  if [[ ${status} != 201 ]]; then
    _sonatype_error_log POST "${url}" "${status}" "${body_file}" >&2
    rm -f "${body_file}"
    return 1
  fi
  tr -d '[:space:]' < "${body_file}"
  rm -f "${body_file}"
}

portal_get_status() {
  local deployment_id=$1
  _sonatype_call POST \
    "${CENTRAL_PORTAL_URL}/api/v1/publisher/status?id=${deployment_id}" \
    || echo '{}'
}

portal_publish_deployment() {
  _sonatype_call POST \
    "${CENTRAL_PORTAL_URL}/api/v1/publisher/deployment/$1" 0 >/dev/null
}

# Resume the same deployment after an uncertain publish response or a timeout.
# Never upload another bundle merely because publication is still in progress.
publish_deployment() {
  local deployment_id=$1 response state elapsed=0 requested=false
  local expected_purls
  expected_purls=$(jq -c '[.[] | "pkg:maven/\(.groupId)/\(.artifactId)@\(.version)"] | sort' <<< "${EXPECTED_COORDINATES}")
  echo "Publishing deployment ${deployment_id}"
  while (( elapsed < POLL_TIMEOUT_SEC )); do
    response=$(portal_get_status "${deployment_id}")
    state=$(jq -r '.deploymentState // "UNKNOWN"' <<< "${response}")
    echo "  [${elapsed}s] state=${state}"
    case "${state}" in
      VALIDATED|PUBLISHING|PUBLISHED)
        jq -e --argjson expected "${expected_purls}" \
          '(.purls | sort) == $expected' <<< "${response}" >/dev/null \
          || fatal "Portal deployment does not match the complete release coordinate set"
        ;;
    esac
    case "${state}" in
      VALIDATED)
        if [[ ${requested} == false ]]; then
          # A failed response may follow an accepted request. Check state before
          # attempting another promotion, including after a workflow rerun.
          portal_publish_deployment "${deployment_id}" \
            || echo "Publish response uncertain; checking deployment state" >&2
          requested=true
        fi
        ;;
      PUBLISHED)
        if [[ -n ${GITHUB_OUTPUT:-} ]]; then
          echo "DEPLOYMENT_STATE=PUBLISHED" >> "${GITHUB_OUTPUT}"
        fi
        return 0
        ;;
      FAILED|VALIDATION_FAILED)
        jq . <<< "${response}" >&2 || true
        fatal "Publisher Portal reported terminal state ${state}"
        ;;
      PENDING|VALIDATING|PUBLISHING|UNKNOWN) ;;
      *) fatal "unexpected Portal deployment state '${state}'" ;;
    esac
    sleep "${POLL_INTERVAL_SEC}"
    elapsed=$((elapsed + POLL_INTERVAL_SEC))
  done
  fatal "timed out waiting for PUBLISHED; resume deployment ${deployment_id}, last state '${state}'"
}

copy_bundle() {
  local source_dir=$1 destination_dir=$2
  echo "Copying signed Maven tree into bundle scratch space"
  cp -a "${source_dir}/." "${destination_dir}/"
}

require_bundle_contents() {
  local artifact_dir=$1
  local packaging=${2:-jar}
  local pattern desc entry
  local expected=(
    "${ARTIFACT_ID}-${VERSION}.pom|the POM"
    "${ARTIFACT_ID}-${VERSION}.pom.asc|the POM signature"
  )
  if [[ ${packaging} == jar ]]; then
    expected+=(
    "${ARTIFACT_ID}-${VERSION}.jar|the unclassified primary jar"
    "${ARTIFACT_ID}-${VERSION}.jar.asc|the unclassified primary jar signature"
    "${ARTIFACT_ID}-${VERSION}-sources.jar|the sources jar"
    "${ARTIFACT_ID}-${VERSION}-sources.jar.asc|the sources jar signature"
    "${ARTIFACT_ID}-${VERSION}-javadoc.jar|the javadoc jar"
    "${ARTIFACT_ID}-${VERSION}-javadoc.jar.asc|the javadoc jar signature"
    )
  fi
  for entry in "${expected[@]}"; do
    IFS='|' read -r pattern desc <<< "${entry}"
    if [[ ! -f ${artifact_dir}/${pattern} ]]; then
      fatal "bundle is missing ${desc} (${pattern})"
    fi
  done
}

validate_publication_tree() {
  local root=$1 coordinates=$2 artifact packaging directory file suffix
  local allowed_dirs="" count=0 actual_count
  while IFS=$'\t' read -r artifact packaging; do
    local ARTIFACT_ID=${artifact}
    directory="${root}/$(maven_group_path "${GROUP_ID}")/${artifact}/${VERSION}"
    require_bundle_contents "${directory}" "${packaging}"
    allowed_dirs+="${directory}"$'\n'
    count=$((count + 1))
  done < <(jq -r '.[] | [.artifactId, .packaging] | @tsv' <<< "${coordinates}")
  actual_count=$(find "${root}" -type f -name '*.pom' | wc -l)
  (( actual_count == count )) || fatal "POM inventory does not match manifest"
  [[ -z $(find "${root}" -type l -print -quit) ]] || fatal "symlinks are not publishable"
  while IFS= read -r -d '' file; do
    directory=$(dirname "${file}")
    grep -Fxq "${directory}" <<< "${allowed_dirs}" || fatal "unexpected publication path: ${file}"
    suffix=${file##*/}
    case ${suffix} in
      *.pom|*.jar)
        [[ -f ${file}.asc ]] || fatal "missing signature: ${file}"
        ;;
      *.pom.asc|*.jar.asc|*.md5|*.sha1|*.sha256|*.sha512) ;;
      *) fatal "unexpected publication file: ${file}" ;;
    esac
  done < <(find "${root}" -type f -print0)
}

generate_bundle_checksums() {
  local root=$1 file
  find "${root}" -type f \( \
    -name '*.md5' -o -name '*.sha1' -o -name '*.sha256' -o -name '*.sha512' \
  \) -delete
  while IFS= read -r file; do
    md5sum "${file}" | awk '{ print $1 }' > "${file}.md5"
    sha1sum "${file}" | awk '{ print $1 }' > "${file}.sha1"
  done < <(find "${root}" -type f ! -name '*.md5' ! -name '*.sha1' \
    ! -name '*.sha256' ! -name '*.sha512' | sort)
}

create_bundle_zip() {
  local zip_path=$1 bundle_dir=$2
  echo "Zipping bundle at ${zip_path}"
  (cd "${bundle_dir}" && zip -qr "${zip_path}" .)
}

