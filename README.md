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

### release-build-output

Release build-output companions are created inside the producer job by the
[`release-build-output-dispatch`](https://github.com/rapidsai/shared-actions/tree/main/release-build-output-dispatch)
shared action. Running beside the build keeps the producer's matrix,
source-artifact name, and original files authoritative and avoids a second
runner and artifact download.

The standard wheel and Conda builders create a companion for every uploaded
bundle. The release unit defaults to `wheel:<repository-name>` or
`conda:<repository-name>` and can be overridden with `release-unit` when the
release-platform catalog uses a different ID. The shared action reads exact
package metadata from the built files and uploads
`release-build-output-<artifact-name>`. No release-specific caller
configuration is required for the standard builders.

`custom-job.yaml` remains explicitly opt-in through `release-build-output` and
also requires `release-unit`, `release-output-directory`, `release-artifacts`,
and either `release-package` or
`release-package-file`. Descriptors may name producer-supplied SBOM,
provenance, and signature sidecars relative to the output directory. Each path
or glob must resolve to exactly one file; the action never guesses a release
artifact.

```yaml
cuvs-java-build:
  uses: rapidsai/shared-workflows/.github/workflows/custom-job.yaml@main
  with:
    # existing build inputs omitted
    artifact-name: cuvs-java-cuda12.9.1
    file_to_upload: java/cuvs-java/target/
    release-build-output: true
    release-output-directory: java/cuvs-java/target
    release-unit: maven:cuvs-java
    release-package-file: cuvs-java.release-package.json
    release-artifacts: '[{"path":"cuvs-java-*-x86_64-cuda*.jar"}]'
```

The release coordinator downloads both artifacts into the same directory, for
example `release-build-outputs/cuvs-java/cuda12.9.1/`. The resulting tree has
one `release-build-output.json` per producer job and is consumed directly by
`rapids-release shadow file`. It does not require Artifactory.

The companion artifact also carries `release-build-metadata.json`. It records
the artifact identity, manifest filename, GitHub build identity, and one
`metadata.artifacts` entry per primary artifact. Each entry explicitly sets
`sbom_kind` to `producer-dependency` or `generated-identity`. SBOM and
provenance paths remain authoritative in `release-build-output.json`; supplied
sidecars are copied under `release-evidence/` so the companion is independently
self-contained.

When no SBOM is selected, the action generates an SPDX artifact-identity
envelope containing package identity and the primary artifact SHA-256. It is
classified as `generated-identity`, contains no dependency inventory, and must
not be reported as a producer-supplied dependency SBOM. A descriptor-selected
producer SBOM is instead classified as `producer-dependency`.

The cross-repository enrollment inventory, blockers, and proposed PR sequence
are maintained in
[`rapidsai/build-infra#381`](https://github.com/rapidsai/build-infra/issues/381).

### matrix_filter

Several of the workflows in this project have matrices (combinations of workflow inputs) expressed in inline YAML/JSON.
Those workflows tend to offer an input, `matrix_filter`, for post-processing of that matrix.

For example, by default `wheels-build` has builds for all combinations of CPU architecture, Python version, CUDA major version, and operating system supported by RAPIDS.
Not all projects need that, so they sometimes invoke the workflow like this:

```yaml
wheel-build-nx-cugraph:
  secrets: inherit
  uses: rapidsai/shared-workflows/.github/workflows/wheels-build.yaml@main
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
- { ARCH: 'amd64', PY_VER: '3.11', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.12', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.13', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.11', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.12', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.13', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
# arm64
- { ARCH: 'arm64', PY_VER: '3.11', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'arm64', PY_VER: '3.12', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'arm64', PY_VER: '3.13', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
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

### Secrets

Some workflows support passing in arbitrary secrets and making them available as environment variables for the `script:` input.

For example, if you had the following:

* a repo secret called `PENGUINS_AND_POLAR_BEARS_DATASET_URI`
* a testing script that downloads whatever it finds in environment variable `BENCHMARK_DATASET_URI`

You could do something like the following:

```yaml
wheel-tests:
  uses: rapidsai/shared-workflows/.github/workflows/wheels-test.yaml@main
  with:
    script: ci/test_wheel.sh
  secrets:
    script-env-secret-1-key: BENCHMARK_DATASET_URI
    script-env-secret-1-value: ${{ secrets.PENGUINS_AND_POLAR_BEARS_DATASET_URI }}
```

Values passed through `secrets:` are redacted everywhere in the GitHub UI, including in logs, and in most cases are replaced with `***`.
