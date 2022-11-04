#!/usr/bin/env bash

set -eoxu pipefail

export RAPIDS_PY_WHEEL_NAME="${RAPIDS_PY_WHEEL_NAME:-}"
export CIBW_TEST_EXTRAS="${CIBW_TEST_EXTRAS:-}"
export CIBW_TEST_COMMAND="${CIBW_TEST_COMMAND:-}"
export RAPIDS_BEFORE_TEST_COMMANDS_AMD64="${RAPIDS_BEFORE_TEST_COMMANDS_AMD64:-}"
export RAPIDS_BEFORE_TEST_COMMANDS_ARM64="${RAPIDS_BEFORE_TEST_COMMANDS_ARM64:-}"

mkdir -p ./dist

arch=$(uname -m)

pybin="python-${RAPIDS_CPYTHON_VERSION}"

$pybin -m pip install awscli
rapids-download-wheels-from-s3 ./dist

if [ "${arch}" == "x86_64" ]; then
        sh -c "${RAPIDS_BEFORE_TEST_COMMANDS_AMD64}"
elif [ "${arch}" == "aarch64" ]; then
        sh -c "${RAPIDS_BEFORE_TEST_COMMANDS_ARM64}"
fi

# see: https://cibuildwheel.readthedocs.io/en/stable/options/#test-extras
extra_requires_suffix=''
if [ "${CIBW_TEST_EXTRAS}" != "" ]; then
        extra_requires_suffix="[${CIBW_TEST_EXTRAS}]"
fi

# echo to expand wildcard before adding `[extra]` requires for pip
$pybin -m pip install --verbose $(echo ./dist/${RAPIDS_PY_WHEEL_NAME}*.whl)$extra_requires_suffix

$pybin -m pip check

sh -c "${CIBW_TEST_COMMAND}"
