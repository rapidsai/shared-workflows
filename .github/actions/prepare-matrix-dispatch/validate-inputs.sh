#!/usr/bin/env bash
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

set -euo pipefail

if [[ ! "${ACTION_REPOSITORY:-}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "prepare-matrix-dispatch requires a full owner/repository name" >&2
  exit 1
fi
if [[ ! "${ACTION_REF:-}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "prepare-matrix-dispatch requires a full lowercase commit SHA" >&2
  exit 1
fi
