#!/usr/bin/env bash
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validator="${root}/.github/actions/prepare-matrix-dispatch/validate-inputs.sh"

ACTION_REPOSITORY=rapidsai/shared-workflows \
  ACTION_REF=0123456789abcdef0123456789abcdef01234567 \
  bash "${validator}"

for invalid_repository in "" rapidsai "../evil/repository" "rapidsai/shared workflows"; do
  if ACTION_REPOSITORY="${invalid_repository}" \
    ACTION_REF=0123456789abcdef0123456789abcdef01234567 \
    bash "${validator}" >/dev/null 2>&1; then
    echo "validator accepted invalid repository: ${invalid_repository}" >&2
    exit 1
  fi
done

for invalid_ref in "" main 0123456789abcdef0123456789abcdef0123456G 0123456789abcdef0123456789abcdef0123456789; do
  if ACTION_REPOSITORY=rapidsai/shared-workflows \
    ACTION_REF="${invalid_ref}" \
    bash "${validator}" >/dev/null 2>&1; then
    echo "validator accepted invalid ref: ${invalid_ref}" >&2
    exit 1
  fi
done

echo "Prepare-matrix dispatch pin tests passed."
