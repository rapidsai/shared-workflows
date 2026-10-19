# shared-workflows

## Introduction

This repository contains [reusable GitHub Action workflows](https://docs.github.com/en/actions/using-workflows/reusing-workflows).

For cross-repository contributor responsibilities and the tested local workflow, see the [release-system contributor guide](https://github.com/rapidsai/release-tracking/blob/codex/release-system/docs/guide/README.md). That link uses the temporary `codex/release-system` branch while the guide awaits adoption into `main`; the [work map](https://github.com/rapidsai/release-tracking/blob/codex/release-system/WORK_PLAN.md) tracks current source and activation status.

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

### Build mode dispatch

Keep calling `conda-cpp-build.yaml`, `conda-python-build.yaml`,
`wheels-build.yaml`, or `custom-job.yaml`. These entry points validate inputs
and select a `-ci.yaml` or `-candidate.yaml` implementation at the same workflow
revision. Branch, nightly, and pull-request builds share the CI implementation;
release candidates use the candidate implementation. The implementation files
are internal; callers should use the existing entry points.

The split keeps candidate version selection, dependency preparation, producer
credentials, and private catalog uploads together, so reviewers can follow the
candidate path without tracing build-mode conditions through ordinary CI.
Candidate workflows cannot assume the general CI role or enable its distributed
compiler cache. CI workflows retain the dispatch-event and release-test ref
guards on that role. Shared setup still lives in pinned actions; the workflow
files own execution order and permissions.

`evaluate-build-policy` evaluates routing, CI credential eligibility, publication
eligibility, image-preflight opt-in, and caller-cache opt-in. Workflows compare
its named boolean outputs instead of repeating compound expressions. The
`evaluate-build-policy.yaml` reusable workflow evaluates job-level decisions on
an unprivileged host before the selected build or protected publication job
starts. The action comes from the workflow's own revision; consumer source
checkout and caller secrets are not needed to evaluate these rules.

`select-candidate-matrix` separately checks preflight completion and selects the
final C++ candidate matrix. Both actions use standalone shell scripts, so their
decision cases are tested directly and checked by ShellCheck.

The entry points retain their input names, types, and defaults. Candidate-only
options must retain their defaults on CI builds; non-default candidate options
and unsupported build types fail validation before either implementation runs.
Secrets are forwarded explicitly at the additional reusable-workflow boundary.
Custom candidate documentation and artifact options still control their own
steps within the candidate implementation.

The extra reusable-workflow call adds a level to displayed job/check names.
Required checks or automation that matches complete job names must be reviewed
when adopting this revision. Validate this mapping in hosted CI before cutover;
the offline checks do not establish hosted check names or OIDC authorization.

For C++ candidates, host preparation selects one final matrix with explicit
container references. Empty image-preflight inputs preserve the existing tagged
image; an opted-in preflight must succeed and produce both a matrix and document
hash. Failed or incomplete validation never falls back to tagged images.

### Candidate C++ digest-image opt-in

`conda-cpp-build.yaml` accepts an optional pre-start CI-image preflight for
release-candidate runs. Callers provide the canonical release-train JSON, its
expected `candidate-train-sha256`, the preflight JSON, its independently
expected canonical SHA-256, and the selected consumer ID together. Each JSON
input is limited to 120 KiB; the computed matrix is also limited to 120 KiB so
it fits runner environment transport. A partial set fails the host preflight
and prevents build-container startup. When all inputs are empty, the existing
workflow path and image tag are unchanged.

The host job checks that the supplied documents agree with the exact source,
unit, and computed `ARCH`/`CUDA_VER`/`PY_VER`/`LINUX_VER` matrix, and resolves
only digest-pinned image references. The build job writes the same preflight
bytes, checks their raw SHA against the host job, and passes them to the
reviewed shared-actions BuildInput binder. This is consistency validation of
caller-supplied documents, not authentication: the caller must authenticate
the train, preflight, and expected hashes before invoking the workflow. It
does not grant private registry access or provide credentials required by
GitHub to pull a private container before a job starts. Those caller/provider
and pre-pull access requirements must be established before using this path.
