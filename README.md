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
- { ARCH: 'amd64', PY_VER: '3.12', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.13', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.12', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
- { ARCH: 'amd64', PY_VER: '3.13', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
# arm64
- { ARCH: 'arm64', PY_VER: '3.12', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'arm64', PY_VER: '3.13', CUDA_VER: '12.9.1', LINUX_VER: 'rockylinux8' }
- { ARCH: 'arm64', PY_VER: '3.12', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
- { ARCH: 'arm64', PY_VER: '3.13', CUDA_VER: '13.0.2', LINUX_VER: 'rockylinux8' }
"

MATRIX="$(
    yq -n -o json 'env(MATRIX)' | \
    jq -c "${MATRIX_FILTER} | if (. | length) > 0 then {include: .} else \"Error: Empty matrix\n\" | halt_error(1) end"
)"

echo "${MATRIX}" | jq
```

### Package artifacts

Conda C++ and Python build artifacts and wheel build artifacts are uploaded without
additional Zlib compression because the packages are already compressed. Compression of
the packages themselves is controlled by each project's build scripts.

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

### Docs preview status

`custom-job.yaml` exposes a `docs-preview-urls` output: a JSON object mapping each docs project to its PR preview URL, written by `rapids-upload-docs` (gha-tools) on pull-request builds.
`docs-preview-status.yaml` reads that output and posts a commit status on the PR head commit, with a **Details** link to the docs preview.

```yaml
docs-preview:
  needs: [docs-build]
  if: needs.docs-build.result == 'success'
  permissions:
    statuses: write
  uses: rapidsai/shared-workflows/.github/workflows/docs-preview-status.yaml@main
  with:
    docs-preview-urls: ${{ needs.docs-build.outputs.docs-preview-urls }}
    # docs-project: <name>   # only if the main docs project dir differs from the repo name
```

Do not add `docs-preview` to the `needs:` of `pr-builder`; the link is informational and should never block merges.
Instead, add `docs-preview` to the `ignored_pr_jobs` input of the `checks` job, so `rapids-check-pr-job-dependencies` doesn't flag it as missing from `pr-builder`.

### Release catalog

We add additional metadata files to our builds to help track what dependencies
were present at build time (a Software Bill of Materials, SBoM), as well as
keeping track of artifacts as we prepare for releases. The standard Conda and
wheel builders do this automatically and upload an additional
`release-catalog-<artifact-name>` GitHub Actions artifact for every package
bundle.

`custom-job.yaml` can be used to produce artifacts, but the generation of extra
metadata files are opt-in, not automatic. Supplying a non-empty
`release-catalog-config` causes the job to upload the additional
release catalog companion; leaving it empty uploads only the original artifact.

See the
[`shared-actions` release catalog documentation](https://github.com/rapidsai/shared-actions/tree/main/release-catalog)
for the companion layout, configuration schema, examples, and evidence
semantics.
