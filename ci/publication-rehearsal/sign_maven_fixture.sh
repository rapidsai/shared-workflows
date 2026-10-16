#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Reuse the production signing step with an already-created test-only keyring.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${script_dir}/../maven-publish/maven_utils.sh"
# shellcheck disable=SC1091
source "${script_dir}/../maven-publish/prepare_maven_bundle_steps.sh"

[[ $# == 2 && -d $1 && $2 =~ ^[A-F0-9]{40}$ ]]
: "${GNUPGHOME:?explicit ephemeral fixture keyring required}"
[[ ${GPG_PASSPHRASE:-} == pub01-fixture-passphrase ]]
ARTIFACTS=()
list_artifacts_to_sign "$1" ARTIFACTS
[[ ${#ARTIFACTS[@]} == 4 ]]
sign_artifacts_in_place ARTIFACTS "$2"
