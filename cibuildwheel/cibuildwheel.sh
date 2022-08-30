#!/usr/bin/env bash

set -eoxu pipefail

git config --global --add safe.directory $(readlink -f ./)

outdir=./dist
code_dir=./"${1}"

arch=$(uname -m)

mkdir -p "${outdir}"

sh -c "${CIBW_BEFORE_ALL_LINUX}"

for pyver in ${RAPIDS_PY_VER}; do
        deactivate || true

        pybuild="cp${pyver//./}-cp${pyver//./}"

        /opt/python/$pybuild/bin/python -m venv /cibw-build-venv-${pyver}

        . /cibw-build-venv-${pyver}/bin/activate

        curl -sS https://bootstrap.pypa.io/get-pip.py | python3

        sh -c "${CIBW_BEFORE_BUILD_LINUX}"

        rm -rf /tmp/cibuildwheel/built_wheel
        mkdir -p /tmp/cibuildwheel/built_wheel

        python -m pip wheel "${code_dir}" --wheel-dir=/tmp/cibuildwheel/built_wheel --no-deps -vvv

        if [ "${AUDITWHEEL_SKIP_REPAIR}" == "true" ]; then
                cp /tmp/cibuildwheel/built_wheel/*.whl ./dist/
        else
                auditwheel --verbose repair -w /tmp/cibuildwheel/repaired_wheel --plat manylinux_2_17_${arch} /tmp/cibuildwheel/built_wheel/*.whl ||\
                auditwheel --verbose repair -w /tmp/cibuildwheel/repaired_wheel --plat manylinux_2_24_${arch} /tmp/cibuildwheel/built_wheel/*.whl ||\
                auditwheel --verbose repair -w /tmp/cibuildwheel/repaired_wheel --plat manylinux_2_27_${arch} /tmp/cibuildwheel/built_wheel/*.whl ||\
                auditwheel --verbose repair -w /tmp/cibuildwheel/repaired_wheel --plat manylinux_2_28_${arch} /tmp/cibuildwheel/built_wheel/*.whl ||\
                auditwheel --verbose repair -w /tmp/cibuildwheel/repaired_wheel --plat manylinux_2_31_${arch} /tmp/cibuildwheel/built_wheel/*.whl

                cp /tmp/cibuildwheel/repaired_wheel/*.whl ./dist/
        fi
done

python -m pip install awscli
rapids-upload-wheels-to-s3 ./dist
