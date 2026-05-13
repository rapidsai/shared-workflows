# Security Policy

`shared-workflows` is a repository of reusable GitHub Actions workflows
used across the RAPIDS organization. Caller workflows reference them as
`uses: rapidsai/shared-workflows/.github/workflows/<name>.yaml@<ref>` and
pass build, test, and publish work into them. The workflows here drive
conda and pip wheel builds and tests, PR validation, devcontainer image
builds, change-set detection, project-board updates, and the upload-to-
anaconda / upload-to-PyPI publishing steps for every RAPIDS package.

Because these workflows accept caller-supplied shell scripts, container
images, build matrices, and publishing tokens, the repository's security
posture is dominated by how callers reference and parameterize the
workflows — and by the workflows' own handling of those caller inputs.

## Reporting a Vulnerability

Please report security vulnerabilities privately through one of the channels
below. **Do not open a public GitHub issue, PR, or discussion** for a
suspected vulnerability.

1. **NVIDIA Vulnerability Disclosure Program (preferred)**
   <https://www.nvidia.com/en-us/security/>
   Submit through the NVIDIA PSIRT web form. This is the fastest path to
   triage and tracking.

2. **Email NVIDIA PSIRT**
   psirt@nvidia.com — encrypt sensitive reports with the
   [NVIDIA PSIRT PGP key](https://www.nvidia.com/en-us/security/pgp-key).

3. **GitHub Private Vulnerability Reporting**
   Use the **Security** tab on this repository → *Report a vulnerability*.

Please include, where possible:

- Affected workflow (e.g. `wheels-publish.yaml`, `pr-builder.yaml`,
  `conda-cpp-build.yaml`, `custom-job.yaml`)
- Whether the issue is in this repo's workflow source, in how a caller
  consumes the workflow, or in a third-party action referenced from it
- Reproduction (workflow snippet + inputs + observed behavior)
- Impact assessment (secret leak, code execution in the runner,
  supply-chain weakness, publishing-credential exposure)
- Any relevant CWE / CVE identifiers

NVIDIA PSIRT will acknowledge receipt and coordinate triage, fix
development, and coordinated disclosure. More on NVIDIA's response
process: <https://www.nvidia.com/en-us/security/psirt-policies/>.

## Security Architecture & Context

**Classification:** CI / build-tooling library (reusable GitHub Actions
workflows). Distributed as YAML in this repository and consumed via the
`workflow_call` trigger from caller workflows in other RAPIDS repos.

**Primary security responsibility:** Provide reusable build / test /
publish pipelines that behave predictably given trusted inputs from a
calling workflow, without amplifying that workflow's trust assumptions —
i.e. without exposing secrets to unrelated steps, dispatching to
unintended container images, or letting PR-controlled text reach shell
contexts unsanitized.

**Workflows (in `.github/workflows/`):**

- **Build / test pipelines** — `conda-cpp-build`, `conda-cpp-tests`,
  `conda-cpp-post-build-checks`, `conda-python-build`,
  `conda-python-tests`, `wheels-build`, `wheels-test`,
  `build-in-devcontainer`, `compute-matrix`, `custom-job`, `checks`.
- **Publishing** — `conda-upload-packages` (anaconda.org),
  `wheels-publish` (anaconda.org / PyPI). These accept
  `CONDA_RAPIDSAI_WHEELS_NIGHTLY_TOKEN`, `RAPIDSAI_PYPI_TOKEN`, and
  related secrets explicitly declared in the workflow's `secrets:` block.
- **PR / repo state** — `pr-builder`, `changed-files`,
  `breaking-change-alert`, `update-latest-branch`.
- **Project-board management** — `project-get-item-id`,
  `project-get-set-iteration-field`,
  `project-get-set-single-select-field`,
  `project-set-text-date-numeric-field`,
  `project-update-linked-issues`.

**Caller-controlled inputs that materially affect security:**

| Input | Workflows | Effect |
| --- | --- | --- |
| `script` | most build/test workflows | Shell code executed in a step (inside the chosen container). Documented as: "Ideally this should just invoke a script managed in the repo the workflow runs from, like `ci/build_wheel.sh`." |
| `container_image` | most build/test workflows | Container image URI used to run the job |
| `container-options` | most build/test workflows | String inlined into the `docker run` command |
| `matrix_filter` | matrix-using workflows | jq expression post-processing the build matrix |
| `repo`, `sha`, `branch` | most workflows | What the workflow checks out |
| `alternative-gh-token-secret-name` | some build workflows | Name of a repo secret to use in place of `github.token` |

**Out of scope for this policy:** vulnerabilities in GitHub Actions
itself, the third-party actions and reusable workflows referenced from
here (`actions/checkout`, `actions/upload-artifact`,
`nv-gha-runners/*`, conda / pip / anaconda.org tooling), the upstream
container images consumed via `container_image`, or the `gha-tools` and
`shared-actions` projects. Vulnerabilities in *how this repo composes
those upstreams* — interpolation of caller inputs, secret declaration
scope, default refs, PR-validation safeguards — are in scope.

## Threat Model

The threats below trace to specific workflows and patterns in this
repository. The
[RAPIDS Security Audit](https://github.com/orgs/rapidsai/projects/207)
remediated the critical `${{ }}` template-injection finding (#2)
against this repo and several others.

1. **`@main` consumption.**
   The README's usage examples reference workflows as
   `uses: rapidsai/shared-workflows/.github/workflows/<name>.yaml@main`.
   `main` is mutable: every CI run on a caller repo executes the
   *current* state of `main` at trigger time. Any compromise of `main`
   in this repo, or a maintainer account that pushes a malicious
   change, flows immediately into every caller. The audit-level
   remediation for the org pinned external action references to
   SHAs; the same discipline applies here, and callers should pin to
   a commit SHA or a SHA-pinned release tag, not `@main`.

2. **`script` input executes caller-supplied shell.**
   The `script` input to `conda-*-build`, `wheels-build`, `custom-job`,
   and others is shell code executed in the runner (typically inside
   the workflow's container). This is the documented integration
   point — caller repos pass `ci/build_wheel.sh` or similar — but it
   also means anything that reaches `script` gets executed.
   Workflows that compose `script` from PR-controlled text (titles,
   branch names, fork-supplied inputs) yield arbitrary command
   execution in the runner with the workflow's secret scope.

3. **`container_image` and `container-options` inputs.**
   Workflows that accept a `container_image` URI run subsequent
   steps inside that image. A caller that lets PR-controlled text
   reach this input can dispatch the job into an attacker-chosen
   container. `container-options` is inlined into `docker run`, so
   PR-controlled values reaching it can inject Docker flags
   (additional volume mounts, env vars, security options) into the
   command. Callers should treat both as workflow-fixed values, not
   pass-throughs from request data.

4. **`matrix_filter` is jq evaluated server-side.**
   The README documents `matrix_filter` as a jq expression that
   post-processes the build matrix. jq is a programming language;
   the workflow pipes the caller's expression through `jq -c` to
   shape the matrix output. A caller that lets PR-controlled text
   reach `matrix_filter` lets that text run jq against the matrix
   data — bounded by what jq itself exposes, but not a constant.

5. **Publishing-credential blast radius.**
   `wheels-publish.yaml` and `conda-upload-packages.yaml` accept
   `RAPIDSAI_PYPI_TOKEN`, `CONDA_RAPIDSAI_WHEELS_NIGHTLY_TOKEN`,
   and related publishing tokens via explicit `secrets:` blocks.
   Compromise of any of those — through a leaking workflow run,
   the `script` execution surface above, or a misconfigured
   caller — yields the ability to publish arbitrary packages to
   the RAPIDS namespaces on PyPI / anaconda.org. The audit-level
   move toward explicit secret declarations (versus
   `secrets: inherit`) bounds blast radius; preserving that posture
   is ongoing.

6. **`alternative-gh-token-secret-name` and the `pr-builder` guard.**
   Some build workflows accept `alternative-gh-token-secret-name` so
   that a caller can elevate beyond the default `github.token` for
   operations that need additional permissions. The `pr-builder`
   workflow contains a defensive check that fails PR validation if
   any workflow under `.github/` references this input — preventing
   inadvertent merging of changes that elevate token scope. This
   guard is load-bearing: relaxing or bypassing it (e.g. via a
   different filename pattern, a renamed input) reintroduces the
   class of risk it exists to catch.

7. **`${{ }}` expression interpolation.**
   GitHub Actions evaluates `${{ ... }}` expressions before passing
   text to `run:` blocks. Historical workflow content interpolated
   values derived from PR metadata directly into shell context, which
   is the canonical GitHub-Actions injection class (audit #2,
   closed). The risk class recurs on new workflow contributions; any
   `run:` block reading from `${{ github.event.* }}`,
   `${{ inputs.* }}`, or similar should consume via `env:` rather
   than direct interpolation.

8. **`actions/checkout` token persistence.**
   `actions/checkout` defaults to leaving `GITHUB_TOKEN` in
   `.git/config`. Workflows here that need post-checkout git
   operations should set `persist-credentials: false` explicitly
   (`pr-builder.yaml` already does this); new checkout usage
   should follow the same pattern.

## Critical Security Assumptions

The following are assumed of caller workflows and the runners they
execute on. These are load-bearing — violating them turns documented
behavior into a vulnerability.

- **Callers reference workflows by commit SHA, not `@main`.**
  The README's `@main` examples are illustrative; production
  callers should pin to a commit SHA or a SHA-pinned release tag.
  Treat shared-workflows like any third-party reusable workflow for
  supply-chain purposes.

- **Caller workflows control what reaches `script`, `container_image`,
  `container-options`, and `matrix_filter`.**
  These inputs are functionally arbitrary code execution and
  configuration. Caller workflows must keep them as workflow-fixed
  values or values sourced from the repo's own trusted files — never
  from PR titles, branch names, comment bodies, or other
  attacker-influenced text.

- **Publishing credentials are scoped tightly and used only on
  intended branches.**
  Anaconda and PyPI tokens passed to `wheels-publish` /
  `conda-upload-packages` should be scoped to the specific channels
  / packages those workflows publish, and the caller workflow's
  `if:` conditions should restrict invocation to release / nightly
  branches and tags.

- **`secrets:` are passed explicitly, not inherited.**
  Caller workflows should declare the exact `secrets:` shared-workflows
  needs, not `secrets: inherit`. This bounds the blast radius of any
  bug in this repo or in a downstream action it dispatches to.

- **Caller workflows declare minimal top-level `permissions:`.**
  GitHub's default `GITHUB_TOKEN` permissions are broader than most
  jobs need. Workflows that invoke shared-workflows should declare a
  minimal top-level `permissions:` block and only grant per-job
  elevations where required.

- **The `pr-builder` guard against `alternative-gh-token-secret-name`
  is enforced.**
  The defensive grep in `pr-builder.yaml` is part of this repo's
  security posture; do not introduce paths that bypass it (e.g.,
  workflows that elevate token scope through other input names).

- **Reviewers verify workflow-YAML changes with the same care as
  application code.**
  History across the RAPIDS org has shown template-injection bugs
  and credential-leak patterns recurring on otherwise innocuous
  workflow changes. Treat YAML diffs as security-relevant on every
  PR.

- **Runners are not actively malicious.**
  shared-workflows assumes runners are stock GitHub-hosted or
  trusted self-hosted runners under the
  [`nv-gha-runners`](https://github.com/nv-gha-runners) controls.
  Shared-runner reuse across PR jobs from forks violates this
  assumption and should not be used for jobs that consume RAPIDS
  publishing credentials.

## Supported Versions

Workflows follow a rolling-`main` model with periodic tagged releases.
Callers should pin to commit SHAs. Security fixes ship to `main` and
the next tag; there is no formal back-port policy.

## Dependency Security

shared-workflows depends on a small set of upstream GitHub Actions
(`actions/checkout`, `actions/upload-artifact`, `actions/download-artifact`,
the `nv-gha-runners/*` runner group, and others), on the `gha-tools`
shell utilities, on `shared-actions` composite actions, and on the
upstream container images selected via `container_image`. Upstream
CVE-driven updates are applied as commit-SHA bumps in this repo;
high-severity advisories in any of those projects may trigger an
out-of-band update.
