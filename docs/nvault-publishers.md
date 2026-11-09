# Publisher credentials from nVault

The Maven, Conda and wheel publishing workflows can retrieve their credentials
directly from nVault using GitHub's per-job OIDC identity. This avoids distributing
additional copies of persistent service credentials into GitHub secrets. It uses
the same workload-authentication model as the Jenkins scanning pipelines, with
GitHub-specific roles and claim restrictions.

This integration is opt-in. Until `NV_VAULT_ENABLED` is exactly `true`, existing
callers continue to use their named GitHub secrets. Once enabled, a failed Vault
login or missing secret fails the job; the workflow never retries with the old
GitHub credential. Retrieved values are masked step outputs passed only to the
consuming step's environment. The Vault token is not exported, and credentials
are not passed between jobs or retained in artifacts.

## Configure a caller

Use an approved NVIDIA runner with network access to `https://prod.vault.nvidia.com`.
The publishers already use the managed Linux CPU runners. Connectivity and JWT
authentication must be tested from the actual job and container, rather than
inferred from a developer login or a Jenkins build.

Provision the credential paths and GitHub JWT roles first. The source change
does not install Vault roles, import credentials, enable repository variables,
or change any existing secret. Use namespace `swgpu-rapids` and the audience
`https://prod.vault.nvidia.com:443`.

Set these repository configuration variables:

| Variable | Purpose |
| --- | --- |
| `NV_VAULT_ENABLED` | Set to `true` after provisioning and validation. Unset or `false` keeps the legacy path. |
| `NV_VAULT_JWT_PATH` | GitHub JWT auth mount, without the `auth/` prefix. Do not use a Jenkins issuer mount. |
| `NV_VAULT_MAVEN_ROLE` | Role allowed to read Maven signing and deployment credentials. |
| `NV_VAULT_MAVEN_SIGNING_PATH` | Full KV v2 API path with fields `private_key` and `passphrase`. |
| `NV_VAULT_MAVEN_DEPLOY_PATH` | Full KV v2 API path with field `token`. |
| `NV_VAULT_CONDA_RELEASE_ROLE` | Role for Conda release uploads. |
| `NV_VAULT_CONDA_RELEASE_PATH` | Full KV v2 API path with field `token`. |
| `NV_VAULT_CONDA_NIGHTLY_ROLE` | Separate role for Conda nightly uploads. |
| `NV_VAULT_CONDA_NIGHTLY_PATH` | Full KV v2 API path with field `token`. |
| `NV_VAULT_WHEELS_ROLE` | Role for Anaconda wheel publishing. |
| `NV_VAULT_WHEELS_NIGHTLY_PATH` | Full KV v2 API path with field `token`. |
| `NV_VAULT_PYPI_ROLE` | Separate role for PyPI publishing. |
| `NV_VAULT_PYPI_PATH` | Full KV v2 API path with field `token`. |

Only configure roles for workflows and destinations the caller uses. Conda reads
the release token only for a release upload and the nightly token only for a
nightly or dual upload. The PyPI role/path are required
only when `publish_to_pypi` requests an actual release upload. Maven's existing
`MAVEN_DEPLOY_USERNAME` variable remains the non-secret Sonatype username.

For example, a KV v2 path looks like
`kv2/rapids-team/data/rapids/github/maven/signing`. Use the canonical existing
runtime secret where its field schema matches, or agree on a path with the
credential owner. A KV path's ACL grants access to the whole secret, so keep
credentials with different readers in separate paths. Do not point readers at
administrative rotation credentials.

Call the reviewed workflow at a full immutable commit and grant OIDC permission:

```yaml
jobs:
  publish:
    permissions:
      actions: read
      contents: read
      id-token: write
    uses: rapidsai/shared-workflows/.github/workflows/maven-publish.yaml@<reviewed-full-commit-sha>
    with:
      artifact-name: maven-repository
```

Vault-enabled Maven callers can omit the GitHub signing/deployment secrets.
Existing callers that have not enabled Vault must still supply them. There is
no need to add `secrets: inherit` for Vault authentication. Caller permissions
must include the other permissions declared by the chosen reusable workflow;
reusable workflows cannot elevate their caller's permissions.
All Maven callers adopting this revision must grant `id-token: write`, including
those temporarily retaining GitHub secrets. GitHub checks the reusable job's
permission requirements before evaluating its Vault step conditions.

## Bind access to the workload

Create separate GitHub roles from the Jenkins roles, even when both read the
same canonical credential. Bind immutable repository and owner IDs, the exact
reviewed reusable workflow identity (`job_workflow_ref` and, where available,
`job_workflow_sha`), allowed branch/tag refs, event types and audience. Use
short-lived Vault tokens with read-only policies restricted to the required
credential paths. Where protected environments are required, configure the job
environment and bind its claim before enabling the integration.

An OIDC permission or runner label does not authorize a credential read.
Repository variables choose a role/path; Vault's policy and bound claims enforce
the boundary. In particular, do not give every repository or arbitrary workflow
code access to the shared publishing token. Review who can modify the workflow,
configure the repository and invoke credential-bearing publishing jobs.

Vault-enabled publishing jobs skip both `pull_request` and
`pull_request_target`, including same-repository PRs. Other events and refs must
be restricted by the role. Existing release-candidate publication guards remain
in place. Normal publishing destination and approval behavior is unchanged.

## Validate and cut over

1. Import or issue credentials directly into their approved Vault paths. GitHub
   cannot return the plaintext value of an existing stored secret.
2. Install and read back the reviewed GitHub roles and policies. Confirm a job
   on the approved runner can authenticate and read only its intended paths.
3. Verify wrong repository, ref, workflow revision and audience are denied.
   Test missing configuration, denied/missing secrets and cancellation without
   disclosing values or silently using legacy credentials.
4. Enable `NV_VAULT_ENABLED=true` for a reviewed pilot and verify the actual
   signing/upload result at its intended destination. Local tests establish
   workflow behavior, not live Vault or publisher success.
5. Migrate the remaining consumers, including active release branches, before
   removing obsolete GitHub mappings and revoking duplicated credentials.

Keep AWS's direct OIDC federation, GitHub's generated `GITHUB_TOKEN` and the
existing short-lived sccache token exchange. This integration covers stored
publisher credentials. Candidate cache policy, build helpers, infrastructure
provisioning and bootstrap image-pull/checkout credentials are separate changes.
