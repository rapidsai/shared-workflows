#!/bin/bash
# Copyright (c) 2019-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
###########################################
# shared-workflows Version Updater #
###########################################

## Usage
# Primary interface:   bash update-version.sh <new_version> [--run-context=main|release]
# Fallback interface:  [RAPIDS_RUN_CONTEXT=main|release] bash update-version.sh <new_version>
# CLI arguments take precedence over environment variables
# Defaults to main when no run-context is specified

set -euo pipefail

# Verify we're running from the repository root
if [[ ! -d ".git" ]]; then
    echo "Error: This script must be run from the repository root directory."
    echo "Expected to find: .git/"
    echo "Current directory: $(pwd)"
    exit 1
fi

# Parse command line arguments
CLI_RUN_CONTEXT=""
VERSION_ARG=""

for arg in "$@"; do
    case $arg in
        --run-context=*)
            CLI_RUN_CONTEXT="${arg#*=}"
            shift
            ;;
        *)
            if [[ -z "$VERSION_ARG" ]]; then
                VERSION_ARG="$arg"
            fi
            ;;
    esac
done

# Format is YY.MM.PP - no leading 'v' or trailing 'a'
NEXT_FULL_TAG="$VERSION_ARG"

# Determine RUN_CONTEXT with CLI precedence over environment variable, defaulting to main
if [[ -n "$CLI_RUN_CONTEXT" ]]; then
    RUN_CONTEXT="$CLI_RUN_CONTEXT"
    echo "Using run-context from CLI: $RUN_CONTEXT"
elif [[ -n "${RAPIDS_RUN_CONTEXT}" ]]; then
    RUN_CONTEXT="$RAPIDS_RUN_CONTEXT"
    echo "Using run-context from environment: $RUN_CONTEXT"
else
    RUN_CONTEXT="main"
    echo "No run-context provided, defaulting to: $RUN_CONTEXT"
fi

# Validate RUN_CONTEXT value
if [[ "${RUN_CONTEXT}" != "main" && "${RUN_CONTEXT}" != "release" ]]; then
    echo "Error: Invalid run-context value '${RUN_CONTEXT}'"
    echo "Valid values: main, release"
    exit 1
fi

# Validate version argument
if [[ -z "$NEXT_FULL_TAG" ]]; then
    echo "Error: Version argument is required"
    echo "Usage: $0 <new_version> [--run-context=<context>]"
    echo "   or: [RAPIDS_RUN_CONTEXT=<context>] $0 <new_version>"
    echo "Note: Defaults to main when run-context is not specified"
    exit 1
fi

#Get <major>.<minor> for next version
NEXT_MAJOR=$(echo "$NEXT_FULL_TAG" | awk '{split($0, a, "."); print a[1]}')
NEXT_MINOR=$(echo "$NEXT_FULL_TAG" | awk '{split($0, a, "."); print a[2]}')
NEXT_SHORT_TAG=${NEXT_MAJOR}.${NEXT_MINOR}

# RAPIDS advances on a two-month cadence. Keep versioned workflow examples
# current so the release tooling's stale-version check distinguishes examples
# maintained by this script from references that require manual review.
if ((10#${NEXT_MINOR} <= 2)); then
    PREVIOUS_MAJOR=$((10#${NEXT_MAJOR} - 1))
    PREVIOUS_MINOR=$((10#${NEXT_MINOR} + 10))
else
    PREVIOUS_MAJOR=$((10#${NEXT_MAJOR}))
    PREVIOUS_MINOR=$((10#${NEXT_MINOR} - 2))
fi
printf -v PREVIOUS_SHORT_TAG '%02d.%02d' "${PREVIOUS_MAJOR}" "${PREVIOUS_MINOR}"

# Set branch references based on RUN_CONTEXT
if [[ "${RUN_CONTEXT}" == "main" ]]; then
    WORKFLOW_BRANCH_REF="main"
    echo "Preparing development branch update for $NEXT_FULL_TAG (targeting main branch)"
elif [[ "${RUN_CONTEXT}" == "release" ]]; then
    WORKFLOW_BRANCH_REF="release/${NEXT_SHORT_TAG}"
    echo "Preparing release branch update for $NEXT_FULL_TAG (targeting release/${NEXT_SHORT_TAG} branch)"
fi

# Inplace sed replace; workaround for Linux and Mac
function sed_runner() {
    sed -i.bak ''"$1"'' "$2" && rm -f "${2}.bak"
}

for FILE in .github/workflows/*.yaml; do
  # Update shared-workflows references
  sed_runner "/rapidsai\/shared-workflows/ s|@release/[0-9]\+\.[0-9]\+|@${WORKFLOW_BRANCH_REF}|g" "${FILE}"
  sed_runner "/rapidsai\/shared-workflows/ s|@\\<main\\>|@${WORKFLOW_BRANCH_REF}|g" "${FILE}"

  # Update CI image tags
  sed_runner "/rapidsai\/ci.*:[0-9\.]*-/ s/:[0-9\.]*-/:${NEXT_SHORT_TAG}-/g" "${FILE}"
done

# publish-api-docs contains illustrative release versions outside image tags.
PUBLISH_API_DOCS_FILE=".github/workflows/publish-api-docs.yaml"
sed_runner "/JSON map of tag to library version/ s|{\"stable\": \"[0-9][0-9]\.[0-9][0-9]\", \"nightly\": \"[0-9][0-9]\.[0-9][0-9]\"}|{\"stable\": \"${PREVIOUS_SHORT_TAG}\", \"nightly\": \"${NEXT_SHORT_TAG}\"}|g" "${PUBLISH_API_DOCS_FILE}"
sed_runner "/Keep only tags matching/ s|v[0-9][0-9]\.[0-9][0-9]\.[0-9][0-9]|v${NEXT_FULL_TAG}|g" "${PUBLISH_API_DOCS_FILE}"
sed_runner "/# (e.g. \"[0-9][0-9]\.[0-9][0-9]\"), and collect/ s|\"[0-9][0-9]\.[0-9][0-9]\"|\"${NEXT_SHORT_TAG}\"|g" "${PUBLISH_API_DOCS_FILE}"

# README example
sed_runner "/shared-workflows.*\.yaml@/ s|@release/[0-9]\+\.[0-9]\+|@${WORKFLOW_BRANCH_REF}|g" README.md
sed_runner "/shared-workflows.*\.yaml@/ s|@\\<main\\>|@${WORKFLOW_BRANCH_REF}|g" README.md
