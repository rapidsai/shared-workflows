#!/usr/bin/env bash
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

set -euo pipefail

for name in RELEASE_CANDIDATE_TRAIN_SHA256 RELEASE_CANDIDATE_ARTIFACT_FAMILY RELEASE_CANDIDATE_REPOSITORY RELEASE_CANDIDATE_SOURCE_SHA RUNNER_TEMP GITHUB_ENV; do
  if [[ -z "${!name:-}" || "${!name}" == *$'\n'* || "${!name}" == *$'\r'* ]]; then
    echo "${name} must be a non-empty single-line string" >&2
    exit 1
  fi
done

if [[ ! "${RELEASE_CANDIDATE_TRAIN_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "RELEASE_CANDIDATE_TRAIN_SHA256 must be a lowercase SHA-256 digest" >&2
  exit 1
fi
if [[ "${RELEASE_CANDIDATE_ARTIFACT_FAMILY}" != "conda" && "${RELEASE_CANDIDATE_ARTIFACT_FAMILY}" != "wheel" ]]; then
  echo "unsupported release-candidate artifact family: ${RELEASE_CANDIDATE_ARTIFACT_FAMILY}" >&2
  exit 1
fi
if [[ ! "${RELEASE_CANDIDATE_REPOSITORY}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "RELEASE_CANDIDATE_REPOSITORY must be a full owner/repository name" >&2
  exit 1
fi
if [[ ! "${RELEASE_CANDIDATE_SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "RELEASE_CANDIDATE_SOURCE_SHA must be a full lowercase commit SHA" >&2
  exit 1
fi

train_path="${RUNNER_TEMP}/release-candidate-version-train.json"
aws s3 cp \
  "s3://rapids-release-candidates/train-state/${RELEASE_CANDIDATE_TRAIN_SHA256}/release-train.json" \
  "${train_path}"
actual_sha256="$(jq -cSj 'del(.train_sha256)' "${train_path}" | sha256sum | awk '{print $1}')"
if [[ "${actual_sha256}" != "${RELEASE_CANDIDATE_TRAIN_SHA256}" ]]; then
  echo "stored release train does not match RELEASE_CANDIDATE_TRAIN_SHA256" >&2
  exit 1
fi

repository_name="${RELEASE_CANDIDATE_REPOSITORY##*/}"
unit_id="${RELEASE_CANDIDATE_ARTIFACT_FAMILY}:${repository_name}"
candidate_version="$(jq -er \
  --arg family "${RELEASE_CANDIDATE_ARTIFACT_FAMILY}" \
  --arg repository "${repository_name}" \
  --arg github_repository "${RELEASE_CANDIDATE_REPOSITORY}" \
  --arg source_sha "${RELEASE_CANDIDATE_SOURCE_SHA}" \
  --arg unit_id "${unit_id}" \
  '. as $train
   | [$train.release_units[] | select(.id == $unit_id)] as $units
   | (if ($units | length) != 1 then error("release train must contain exactly one requested release unit")
      else $units[0]
      end) as $unit
   | if $unit.repository != $repository
       or $unit.artifact_family != $family
       or $unit.source_revision != $source_sha
       or $train.source_revisions[$repository] != $source_sha
       or $train.github_repositories[$repository] != $github_repository
     then error("release unit does not match the requested source identity")
     else ($train.version_contract.unit_package_versions[$unit_id]
           // $train.version_contract.package_versions[$unit.version_profile])
     end
   | select(type == "string" and test("^[0-9]+\\.[0-9]+\\.[0-9]+$"))' \
  "${train_path}")" || {
    echo "release train has no valid frozen version for ${unit_id} at the requested source revision" >&2
    exit 1
  }

printf 'RAPIDS_RELEASE_CANDIDATE_VERSION=%s\n' "${candidate_version}" >>"${GITHUB_ENV}"
