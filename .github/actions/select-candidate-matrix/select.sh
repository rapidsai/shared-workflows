#!/usr/bin/env bash
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
set -euo pipefail
case "${PREFLIGHT_REQUESTED}" in
  true|false) ;;
  *) echo "Missing or invalid preflight policy decision" >&2; exit 1 ;;
esac
if [[ "${PREFLIGHT_REQUESTED}" == true ]]; then
  # A failed or incomplete opt-in must never fall back to tagged images.
  if [[ "${PREFLIGHT_RESULT}" != success || -z "${PREFLIGHT_MATRIX}" || -z "${PREFLIGHT_SHA256}" ]]; then
    echo "Candidate image preflight did not produce a validated matrix and document hash" >&2
    exit 1
  fi
  matrix="${PREFLIGHT_MATRIX}"
else
  if [[ "${PREFLIGHT_RESULT}" != skipped ]]; then
    echo "Unexpected candidate image preflight result" >&2
    exit 1
  fi
  matrix="$(jq -ce '.include |= map(. + {
    candidate_image_reference: ("rapidsai/ci-conda:26.12-cuda" + .CUDA_VER + "-" + .LINUX_VER + "-py" + .PY_VER)
  })' <<<"${LEGACY_MATRIX}")"
fi
printf 'matrix=%s\n' "${matrix}" >>"${GITHUB_OUTPUT}"
printf 'preflight-document-sha256=%s\n' "${PREFLIGHT_SHA256}" >>"${GITHUB_OUTPUT}"
