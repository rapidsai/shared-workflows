#!/bin/bash
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

set -euo pipefail

RESOLVED_MATRIX=${RESOLVED_MATRIX:-}
MATRIX_FILTER=${MATRIX_FILTER:-.}
PURE_WHEEL=${PURE_WHEEL:-false}
PURE_CONDA=${PURE_CONDA:-false}

case "$BUILD_TYPE" in
  branch|nightly|pull-request|release-candidate) ;;
  *) echo "::error::Invalid build_type: $BUILD_TYPE"; exit 1 ;;
esac

if [[ -n "$RESOLVED_MATRIX" ]]; then
  if ! jq -en '
    $ENV.RESOLVED_MATRIX | fromjson
    | type == "object" and (keys == ["include"])
      and (.include | type == "array" and length > 0
        and all(.[]; type == "object"))
  ' >/dev/null; then
    echo '::error::resolved-matrix must be a nonempty {"include": [...]} matrix'
    exit 1
  fi
  BASE_MATRIX=$(jq -cn '$ENV.RESOLVED_MATRIX | fromjson | .include')
else
  if ! jq -en '$ENV.MATRIX | fromjson | has($ENV.MATRIX_NAME)' >/dev/null; then
    echo "::error::Invalid matrix_name: $MATRIX_NAME"
    exit 1
  fi
  if [[ "$MATRIX_TYPE" == auto ]]; then
    case "$BUILD_TYPE" in
      branch|release-candidate) MATRIX_TYPE=nightly ;;
      *) MATRIX_TYPE=$BUILD_TYPE ;;
    esac
  fi
  export MATRIX_TYPES
  MATRIX_TYPES=$(jq -cn --arg types "$MATRIX_TYPE" '$types | split(",")')
  if ! jq -en '
    ($ENV.MATRIX | fromjson)[$ENV.MATRIX_NAME] as $matrix
    | $ENV.MATRIX_TYPES | fromjson
    | length > 0 and all(.[]; . as $type | $matrix | has($type))
  ' >/dev/null; then
    echo "::error::Invalid matrix_type: $MATRIX_TYPE"
    exit 1
  fi
  BASE_MATRIX=$(jq -cn '
    ($ENV.MATRIX | fromjson)[$ENV.MATRIX_NAME] as $matrix
    | [$ENV.MATRIX_TYPES | fromjson | .[] as $type | $matrix[$type][]]
    | reduce .[] as $entry ([]; if index($entry) == null then . + [$entry] else . end)
  ')
fi
export BASE_MATRIX

# Preserve the existing default selectors for build workflows.
if [[ "$MATRIX_FILTER" == . ]]; then
  if [[ "$PURE_WHEEL" == true ]]; then
    MATRIX_FILTER='map(select(.ARCH == "amd64")) | group_by(.CUDA_VER) | map(max_by(.PY_VER | split(".") | map(tonumber)))'
  elif [[ "$PURE_CONDA" == true ]]; then
    MATRIX_FILTER='map(select(.ARCH == "amd64")) | sort_by(.CUDA_VER, .PY_VER) | [last]'
  elif [[ "$PURE_CONDA" == cuda_major ]]; then
    MATRIX_FILTER='map(select(.ARCH == "amd64")) | group_by(.CUDA_VER|split(".")|map(tonumber)|.[0]) | map(max_by([(.PY_VER|split(".")|map(tonumber)), (.CUDA_VER|split(".")|map(tonumber))]))'
  fi
fi

if ! RESULT=$(jq -ecn "
  \$ENV.BASE_MATRIX | fromjson | ${MATRIX_FILTER}
  | if type == \"array\" and length > 0 and all(.[]; type == \"object\")
    then {include: .} else error(\"Empty or invalid matrix\") end
"); then
  echo '::error::Empty or invalid matrix'
  exit 1
fi

if [[ "$MATRIX_NAME" == wheels-test || "$MATRIX_NAME" == conda-python-tests ]]; then
  if ! jq -e '
    .include | all(.[];
      . as $entry | ["ARCH", "CUDA_VER", "PY_VER", "LINUX_VER", "GPU", "DRIVER", "DEPENDENCIES"]
      | all(.[]; $entry[.] | type == "string" and length > 0))
  ' <<< "$RESULT" >/dev/null; then
    echo '::error::Python test matrix entries must contain all runner and environment fields'
    exit 1
  fi
fi

echo "matrix=$RESULT" | tee --append "$GITHUB_OUTPUT"
