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

# Maven groupId (a.b.c) -> filesystem path (a/b/c).
maven_group_path() {
  echo "${1//./\/}"
}

# Repo-relative Maven path: <groupPath>/<artifactId>/<version>.
# Re-uploading the same X.Y.Z overwrites in place. RC iteration is not
# part of the path. Artifactory lastModified + sha256 give per-file
# provenance. source.git-sha (matrix property) records the commit.
maven_release_path() {
  local group_id=$1 artifact_id=$2 version=$3
  echo "$(maven_group_path "${group_id}")/${artifact_id}/${version}"
}
