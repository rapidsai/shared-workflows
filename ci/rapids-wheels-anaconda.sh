#!/bin/bash
# A utility script to upload Python wheel packages to Anaconda repository using anaconda-client.
# Positional Arguments:
#   1) wheel name
#   2) package type (one of: 'cpp', 'python'). If not provided, defaults to 'python' for compatibility with older code where python was the only behavior.
#
# [usage]
#
#   # upload any wheels found in CI artifacts with names like '*wheel_python_sparkly-unicorn*.tar.gz'
#   rapids-wheels-anaconda 'sparkly-unicorn' 'python'
#
set -eou pipefail
source rapids-constants
export RAPIDS_SCRIPT_NAME="rapids-wheels-anaconda"
WHEEL_NAME="$1"
PKG_TYPE="${2:-python}"
WHEEL_DIR="./dist"
_rapids-wheels-prepare "${WHEEL_NAME}" "${PKG_TYPE}"
export RAPIDS_RETRY_SLEEP=180
# shellcheck disable=SC2086
# rapids-retry anaconda \
#     -t "${RAPIDS_CONDA_TOKEN}" \
#     upload \
#     --skip-existing \
#     --no-progress \
#     "${WHEEL_DIR}"/*.whl

echo "all good!"
