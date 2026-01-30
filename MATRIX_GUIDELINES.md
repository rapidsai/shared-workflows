# CI Test Matrix Guidelines

## Overview

This document describes the principles and practices for determining the CI test matrix configuration in `.github/workflows/` test jobs. These guidelines balance comprehensive coverage against limited GPU resources to maximize confidence in code quality while keeping CI time and cost manageable.

## Core Constraint

**Limited GPU resources are the primary constraint that forces us to keep the CI matrix limited.** GPU runners are a shared, finite resource across all RAPIDS projects. Every matrix entry that requires a GPU runner consumes time on this shared pool. We must strive for maximum coverage within the GPU resource budget.

## Matrix Dimensions

Test matrices span multiple dimensions:

- **CPU Architecture** (`ARCH`): `amd64`, `arm64`
- **CUDA Version** (`CUDA_VER`): e.g., `12.2.2`, `12.9.1`, `13.0.2`
- **Python Version** (`PY_VER`): e.g., `3.11`, `3.12`, `3.13`
- **GPU Architecture** (`GPU`): `l4`, `a100`, `h100`
- **Driver Version** (`DRIVER`): `earliest`, `latest`
- **Linux Distribution** (`LINUX_VER`): `rockylinux8`, `ubuntu22.04`, `ubuntu24.04`
- **Dependencies** (`DEPENDENCIES`): `oldest`, `latest`

## Two-Tier Matrix Strategy

### Pull Request (PR) Matrix
**Goal**: Fast feedback with focused coverage

- **Minimal size** to provide quick CI results
- Try to keep the matrix size constant when adding new versions
  - e.g. if adding a new CUDA or new Python, rearrange existing jobs to maximize coverage while keeping the number of jobs constant
- Focus on:
  - Endpoints
    - Latest versions of everything (newest Python, CUDA, driver, dependencies)
    - Earliest supported versions of everything (oldest Python, CUDA, driver, dependencies)
  - "Off-diagonal" elements
    - We want to test combinations like "oldest CUDA, newest Python" and vice versa
    - Use different matrices for conda and wheel CI jobs -- we often use wheel jobs to hit some of these "edge" configurations
  - Broad coverage
    - Both CPU architectures (`amd64` and `arm64`)
    - Cover all possible GPU architectures, while respecting the relative pool sizes of the runners
      - Allocate fewer CI jobs to the pools with fewer runners
    - Cover oldest and latest drivers, but make sure the driver/CUDA versions are supported by the desired operating system

### Nightly Matrix
**Goal**: Comprehensive coverage across all supported configurations

- **Expanded matrix** to catch edge cases and combinations not tested in PRs
- We have a weak preference for nightlies to be a *superset* of the PR matrix, meaning it is the same with additional elements
- Otherwise, same rules as for the PR matrix: hit the endpoints, off-diagonal elements, and shoot for broad coverage

## Coverage Priorities

When trading off coverage for resource utilization, prioritize in this order:

### 1. CPU Architecture Coverage
**Goal**: Every PR and nightly build must test both `amd64` and `arm64`

- Both architectures must be represented in PR tests
- Failures on either architecture are equally important

### 2. CUDA Version Coverage
**Goal**: Test minimum supported, the latest stable version, and another intermediate version

- **Previous major, minimum supported version**
- **Latest major.minor version**
- **Latest major, earliest minor version**
  - e.g. if 13.1 is the latest, use 13.0
  - If this is the same as the latest major.minor, use the latest minor of the previous major (e.g. if 13.0 is the latest, use 12.9)
- If resources allow, also test the latest minor of the previous major

### 3. Driver Version Coverage
**Goal**: Validate compatibility across the driver support range

- **Earliest supported driver**: Always test with oldest CUDA version (typically PR and nightly)
- **Latest driver**: Test with all CUDA versions (PR and nightly)
- These combinations catch driver compatibility issues and forward compatibility

### 4. Python Version Coverage
**Goal**: Test oldest and newest, sample intermediate versions in nightly

- **Oldest supported Python**
- **Newest supported Python**
- **Intermediate versions**: lower priority for coverage than oldest/newest, sprinkle these through the matrix

### 5. GPU Architecture Coverage
Different GPU families should be distributed across the matrix to validate portability.
Make sure to test on CUDA versions new enough to support that hardware.

### 6. Dependency Version Coverage
**Goal**: Validate against both oldest and latest dependencies

- **Oldest dependencies**: Use at least once in the matrix
- **Latest dependencies**: Use latest dependencies in most jobs
- It is up to each repository to use `rapids-dependency-file-generator` and `dependencies.yaml` to define its oldest supported dependencies, this just sets an environment variable that can be interpreted by the CI scripts

### 7. Linux Distribution Coverage
**Goal**: Validate across enterprise and modern distributions

- Use operating systems with a mixture of glibc versions from oldest to newest
- Linux distribution diversity is secondary to other factors

## Rollout Strategy for Major Changes

### Adding new versions to the matrix

When making matrix changes (new CUDA/Python/OS versions, runner type changes):
1. **Create a long-lived branch**: Create a feature branch in `shared-workflows`. 1. **Create a long-lived branch**: Create a feature branch in `shared-workflows`.
> [!IMPORTANT]
> You must use the `rapidsai/shared-workflows` repo and not a fork.
> Using a fork in downstream repositories will not allow actions to run, for security reasons.
2. **Add the new versions**: Modify build/test matrices to use the new version in some jobs
3. **Update RAPIDS repos**: Update projects one-by-one to use `@feature-branch` in their `.github/workflows/*.yaml` files.
4. **Merge and switch back**: Merge feature branch, then update projects back to `@main`

This allows incremental matrix expansion across RAPIDS and provides rollback capability if issues arise.

See examples in:
- [PR #413 (Add CUDA 13.0)](https://github.com/rapidsai/shared-workflows/pull/413)
- [PR #412 (Add conda CUDA 13 workflows)](https://github.com/rapidsai/shared-workflows/pull/412)

### Modifying or removing versions from the matrix

When modifying or removing matrix elements, it may not be necessary to do the full rollout procedure above.
That process is really only needed for adding new builds that don't yet exist, and need to be created in RAPIDS dependency order.

1. **Announce deprecation**: Publish a RAPIDS Support Notice if needed (e.g., [RSN 54](https://docs.rapids.ai/notices/rsn0054/))
2. **Update the matrix**: Modify/remove build and test matrices
3. **Validate one repo**: Open a test PR downstream in representative project like rmm or cudf.
> [!NOTE]
> This ensures the workflow syntax is valid, the requested CI runners are operational, and the requested CI images exist
4. **Merge matrix changes**: Merge the workflow changes and close the test PR

See examples in:
- [PR #431 (Drop CUDA 12.0)](https://github.com/rapidsai/shared-workflows/pull/431)
