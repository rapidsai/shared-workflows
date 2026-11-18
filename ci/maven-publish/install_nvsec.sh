#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
: "${NVSEC_GITLAB_TOKEN:?must be set}"
: "${RUNNER_TEMP:?must be set}"
: "${GITHUB_PATH:?must be set}"
# The managed CPU pool can reach Vault but does not necessarily have corporate
# DNS/network access to the current nvsec registry. Distinguish this from an
# authentication failure or a nonexistent package version before invoking pip.
if ! curl --silent --show-error --output /dev/null --connect-timeout 10 --max-time 20 \
  https://gitlab-master.nvidia.com; then
  echo 'The nvsec registry requires NVIDIA corporate DNS/network access.' >&2
  echo 'Use an approved network route or a mirror containing nvsec 2.16.1 and its dependencies.' >&2
  exit 1
fi

install_home=$(mktemp -d)
trap 'rm -rf "${install_home}"' EXIT
chmod 700 "${install_home}"
printf 'machine gitlab-master.nvidia.com login oauth2 password %s\n' \
  "${NVSEC_GITLAB_TOKEN}" > "${install_home}/.netrc"
chmod 600 "${install_home}/.netrc"
python3 -m venv "${RUNNER_TEMP}/maven-nvsec"
HOME="${install_home}" "${RUNNER_TEMP}/maven-nvsec/bin/pip" install \
  nvsec==2.16.1 --index-url https://gitlab-master.nvidia.com/api/v4/projects/25923/packages/pypi/simple
printf '%s\n' "${RUNNER_TEMP}/maven-nvsec/bin" >> "${GITHUB_PATH}"
