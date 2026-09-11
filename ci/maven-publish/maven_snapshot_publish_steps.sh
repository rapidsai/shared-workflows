#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Sonatype snapshot upload helpers for maven_snapshot_publish.sh. Requires
# _sonatype_upload from maven_central_publish_steps.sh (sourced by the caller).

# require_snapshot_artifact_names DIR VERSION
# Fail if any file under DIR lacks "-SNAPSHOT" in its name.
require_snapshot_artifact_names() {
  local dir=$1 version=$2
  if [[ ${version} != *-SNAPSHOT ]]; then
    fatal "require_snapshot_artifact_names called with non-snapshot version '${version}'"
  fi

  local offenders
  readarray -t offenders < <(
    find "${dir}" -type f \
      ! -name '*-SNAPSHOT*' \
      ! -name 'maven-metadata.xml*'
  )

  if (( ${#offenders[@]} > 0 )); then
    echo "Error: snapshot bundle contains non-SNAPSHOT files:" >&2
    printf '  %s\n' "${offenders[@]}" >&2
    fatal "found ${#offenders[@]} file(s) missing '-SNAPSHOT' in their name under ${dir}"
  fi
}

# snapshot_upload_file LOCAL_PATH REPOSITORY_PATH
# PUT LOCAL_PATH to the snapshot repository at REPOSITORY_PATH.
snapshot_upload_file() {
  local file_path=$1 repository_path=$2
  # Maven coordinates use only [A-Za-z0-9._/-], so no URL encoding is needed.
  _sonatype_upload "${file_path}" "${SNAPSHOT_REPOSITORY_URL}/${repository_path}"
}

# upload_tree_to_snapshots ROOT
# PUT every file under ROOT to the Sonatype snapshot repository.
upload_tree_to_snapshots() {
  local root=$1
  echo "Uploading Maven repository files to the Sonatype snapshot repository"

  local files
  readarray -t files < <(find "${root}" -type f)

  local file
  for file in "${files[@]}"; do
    snapshot_upload_file "${file}" "${file#"${root}/"}"
  done
  echo "  uploaded files: ${#files[@]}"
}
