#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Shared helpers for the ci/maven-publish/ pipeline scripts (host and
# in-container). Sourced, not executed:
#   . "${SCRIPT_DIR}/maven_utils.sh"

fatal() {
  echo "Error: $*" >&2
  exit 1
}

require_cmds() {
  local cmd
  for cmd in "$@"; do
    if ! command -v "${cmd}" >/dev/null; then
      fatal "'${cmd}' not on PATH"
    fi
  done
}

require_release_version() {
  local version=$1
  if [[ ${version} == *-SNAPSHOT ]]; then
    fatal "release-shaped version required, got '${version}'"
  fi
}

require_snapshot_version() {
  local version=$1
  if [[ ${version} != *-SNAPSHOT ]]; then
    fatal "snapshot version (ending in '-SNAPSHOT') required, got '${version}'"
  fi
}

require_maven_coordinates() {
  local group_id=$1 artifact_id=$2 version=$3
  local coordinate_pattern='^[A-Za-z0-9][A-Za-z0-9_.+-]*$'
  if [[ ! ${group_id} =~ ${coordinate_pattern} ]]; then
    fatal "invalid Maven groupId '${group_id}'"
  fi
  if [[ ! ${artifact_id} =~ ${coordinate_pattern} ]]; then
    fatal "invalid Maven artifactId '${artifact_id}'"
  fi
  if [[ ! ${version} =~ ${coordinate_pattern} ]]; then
    fatal "invalid Maven version '${version}'"
  fi
}

# Maven groupId (a.b.c) -> filesystem path (a/b/c).
maven_group_path() {
  printf '%s\n' "$1" | tr '.' '/'
}

# The manifest is outside the Maven tree so it is never uploaded to Sonatype.
# Legacy single-JAR callers can omit it; new callers always supply one.
# These values are provided by the publishing script that sources this file.
# shellcheck disable=SC2153
load_publication_coordinates() {
  local manifest=$1 coordinates
  if [[ -n ${manifest} ]]; then
    coordinates=$(jq -ce . "${manifest}") || fatal "invalid coordinate manifest"
  else
    coordinates=$(jq -cn --arg group "${GROUP_ID}" --arg artifact "${ARTIFACT_ID}" \
      --arg version "${VERSION}" \
      '[{groupId: $group, artifactId: $artifact, version: $version, packaging: "jar"}]')
  fi
  jq -e --arg group "${GROUP_ID}" --arg artifact "${ARTIFACT_ID}" --arg version "${VERSION}" '
    type == "array" and length > 0
    and all(.[]; .groupId == $group and .version == $version
      and (.artifactId | type == "string" and test("^[A-Za-z0-9][A-Za-z0-9_.+-]*$"))
      and (.packaging == "jar" or .packaging == "pom"))
    and (map(.artifactId) | length == (unique | length))
    and any(.[]; .artifactId == $artifact)
  ' <<< "${coordinates}" >/dev/null || fatal "manifest does not match this release"
  jq -c 'sort_by(.groupId, .artifactId, .version)' <<< "${coordinates}"
}
