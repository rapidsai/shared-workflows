# shared-workflows

## Introduction

This repository contains [reusable GitHub Action workflows](https://docs.github.com/en/actions/using-workflows/reusing-workflows).

Reusable composite actions can be found in https://github.com/rapidsai/shared-actions.

See the articles below for a comparison between these two types of reusable GitHub Action components:

- https://wallis.dev/blog/composite-github-actions
- https://dev.to/n3wt0n/composite-actions-vs-reusable-workflows-what-is-the-difference-github-actions-11kd

## Folder Structure

Reusable workflows must be placed in the `.github/workflows` directory as mentioned in the community discussions below:

- https://github.com/community/community/discussions/10773
- https://github.com/community/community/discussions/9050

## Usage

### matrix_filter

Several of the workflows in this project have matrices (combinations of workflow inputs) expressed in inline YAML/JSON.
Those workflows tend to offer an input, `matrix_filter`, for post-processing of that matrix.

For example, by default `wheels-build` has builds for all combinations of CPU architecture, Python version, CUDA major version, and operating system supported by RAPIDS.
Not all projects need that, so they sometimes invoke the workflow like this:

```yaml
wheel-build-nx-cugraph:
  secrets: inherit
  uses: rapidsai/shared-workflows/.github/workflows/wheels-build.yaml@branch-25.10
  with:
    build_type: pull-request
    script: ci/build_wheel_nx-cugraph.sh
    # This selects "ARCH=amd64 + the latest supported Python, 1 job per major CUDA version".
    matrix_filter: map(select(.ARCH == "amd64")) | group_by(.CUDA_VER|split(".")|map(tonumber)|.[0]) | map(max_by([(.PY_VER|split(".")|map(tonumber)), (.CUDA_VER|split(".")|map(tonumber))]))
    package-name: nx-cugraph
    package-type: python
    pure-wheel: true
```

Something like the bash snippet below can be used to test those filters.

```bash
#!/bin/bash

export MATRIX_FILTER='map(select(.ARCH == "amd64")) | group_by(.CUDA_VER|split(".")|map(tonumber)|.[0]) | map(max_by([(.PY_VER|split(".")|map(tonumber)), (.CUDA_VER|split(".")|map(tonumber))]))'

export MATRIX="
# amd64
- { ARCH: 'amd64', PY_VER: '3.10', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.11', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.12', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.13', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.10', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.11', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.12', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.13', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
# arm64
- { ARCH: 'arm64', PY_VER: '3.10', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'arm64', PY_VER: '3.11', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'arm64', PY_VER: '3.12', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'arm64', PY_VER: '3.13', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'arm64', PY_VER: '3.10', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
- { ARCH: 'arm64', PY_VER: '3.11', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
- { ARCH: 'arm64', PY_VER: '3.12', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
- { ARCH: 'arm64', PY_VER: '3.13', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
"

MATRIX="$(
    yq -n -o json 'env(MATRIX)' | \
    jq -c "${MATRIX_FILTER} | if (. | length) > 0 then {include: .} else \"Error: Empty matrix\n\" | halt_error(1) end"
)"

echo "${MATRIX}" | jq
```
