#!/bin/bash
# Copyright (c) 2019-2023, NVIDIA CORPORATION.
###########################################
# shared-action-workflows Version Updater #
###########################################

## Usage
# bash update-version.sh <new_version>

# Format is YY.MM.PP - no leading 'v' or trailing 'a'
NEXT_FULL_TAG=$1

#Get <major>.<minor> for next version
NEXT_MAJOR=$(echo $NEXT_FULL_TAG | awk '{split($0, a, "."); print a[1]}')
NEXT_MINOR=$(echo $NEXT_FULL_TAG | awk '{split($0, a, "."); print a[2]}')
NEXT_SHORT_TAG=${NEXT_MAJOR}.${NEXT_MINOR}

echo "Updating repository for $NEXT_FULL_TAG"

# Inplace sed replace; workaround for Linux and Mac
function sed_runner() {
    sed -i.bak ''"$1"'' $2 && rm -f ${2}.bak
}

for FILE in .github/workflows/*.yaml; do
  sed_runner "/rapidsai\/shared-action-workflows/ s/@.*/@branch-${NEXT_SHORT_TAG}/g" "${FILE}"
done
