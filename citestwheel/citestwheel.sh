#!/usr/bin/env bash

set -eoxu pipefail

export RAPIDS_WHEEL_VER_OVERRIDE="${RAPIDS_WHEEL_VER_OVERRIDE:-}"
export RAPIDS_WHEEL_VER_OVERRIDE_WITH_ARCH="${RAPIDS_WHEEL_VER_OVERRIDE_WITH_ARCH:-}"
export CIBW_TEST_EXTRAS="${CIBW_TEST_EXTRAS:-}"
export CIBW_TEST_REQUIRES="${CIBW_TEST_REQUIRES:-}"
export RAPIDS_WHEEL_SMOKETEST_COMMAND="${RAPIDS_WHEEL_SMOKETEST_COMMAND:-}"

rm -rf ./dist
mkdir -p ./dist

first='yes'
arch=$(uname -m)

for pyver in ${RAPIDS_PY_VER}; do
        deactivate || true

        pyunittest="cp${pyver//./}-cp${pyver//./}"

        /opt/python/$pyunittest/bin/python -m venv /cibw-unittest-venv-${pyver}
        . /cibw-unittest-venv-${pyver}/bin/activate

        curl -sS https://bootstrap.pypa.io/get-pip.py | python3

        if [ "${first}" == "yes" ]; then
                python -m pip install awscli
                rapids-download-wheels-from-s3 ./dist
                first='no'
        fi

        # see: https://cibuildwheel.readthedocs.io/en/stable/options/#test-extras
        extra_requires_suffix=''
        if [ "${CIBW_TEST_EXTRAS}" != "" ]; then
                extra_requires_suffix="[${CIBW_TEST_EXTRAS}]"
        fi

        # echo to expand wildcard before adding `[extra]` requires for pip
        if [ "${RAPIDS_WHEEL_VER_OVERRIDE}" != "" ]; then
                python3 -m pip install --verbose $(echo ./dist/${RAPIDS_PY_WHEEL_NAME}*-${RAPIDS_WHEEL_VER_OVERRIDE}.whl)$extra_requires_suffix
        elif [ "${RAPIDS_WHEEL_VER_OVERRIDE_WITH_ARCH}" != "" ]; then
                python3 -m pip install --verbose $(echo ./dist/${RAPIDS_PY_WHEEL_NAME}*-${RAPIDS_WHEEL_VER_OVERRIDE_WITH_ARCH}_$(uname -m).whl)$extra_requires_suffix
        else
                python3 -m pip install --verbose $(echo ./dist/${RAPIDS_PY_WHEEL_NAME}*-cp${pyver//./}-cp${pyver//./}*_$(uname -m).whl)$extra_requires_suffix
        fi

        python3 -m pip check

        if [ "${arch}" == "x86_64" ]; then
                sh -c "${RAPIDS_BEFORE_TEST_COMMANDS_AMD64}"
        elif [ "${arch}" == "aarch64" ]; then
                sh -c "${RAPIDS_BEFORE_TEST_COMMANDS_ARM64}"
        fi

        if [ "${CIBW_TEST_REQUIRES}" != "" ]; then
                pip install ${CIBW_TEST_REQUIRES}
        fi

        sh -c "${CIBW_TEST_COMMAND/_venv_placeholder/$VIRTUAL_ENV}"

        python3 -c "${RAPIDS_WHEEL_SMOKETEST_COMMAND}"
done
