# Maven publication

The release tag authorizes publication to Maven Central. Other builds publish to
Sonatype snapshots.

## Parts

| Code | Purpose |
| --- | --- |
| [maven-publish.yaml](../../.github/workflows/maven-publish.yaml) | Reusable workflow: download artifacts, sign, publish, and retain receipts. Defines caller inputs, secrets, and outputs. |
| [prepare_maven_bundle.sh](prepare_maven_bundle.sh) | Prepare and GPG-sign the Maven repository files. |
| [maven_central_publish.sh](maven_central_publish.sh) | Publish a release and wait for `PUBLISHED`; resume interrupted publication using `deployment.json`. |
| [maven_central_publish_steps.sh](maven_central_publish_steps.sh) | Sonatype requests, bundle checks, and publication polling. |
| [maven_snapshot_publish.sh](maven_snapshot_publish.sh) | Upload snapshot artifacts. |
| [write_maven_publication_receipt.sh](write_maven_publication_receipt.sh) | Record a completed release as `publication.json` so the docs dispatcher can start documentation deployment. |
| [dispatch-java-docs.yml](https://github.com/rapidsai/release-tracking/blob/main/.github/workflows/dispatch-java-docs.yml) | Discover completed releases and dispatch the docs workflow using the existing release App. |
| [deploy-java-docs.yaml](https://github.com/rapidsai/docs/blob/main/.github/workflows/deploy-java-docs.yaml) | Deploy released javadocs and open the stable-link update PR. |

## Caller

```yaml
java-publish:
  needs: java-gather
  permissions:
    actions: read
    contents: read
  uses: rapidsai/shared-workflows/.github/workflows/maven-publish.yaml@main
  secrets:
    NVSEC_SSA_CLIENT_ID: ${{ secrets.NVSEC_SSA_CLIENT_ID }}
    NVSEC_SSA_CLIENT_SECRET: ${{ secrets.NVSEC_SSA_CLIENT_SECRET }}
    NVSEC_GITLAB_TOKEN: ${{ secrets.NVSEC_GITLAB_TOKEN }}
    MAVEN_DEPLOY_TOKEN: ${{ secrets.MAVEN_DEPLOY_TOKEN }}
  with:
    artifact-name: cudf_java_maven_repo
    source-git-sha: ${{ github.sha }}
    docs-project: cudf-java
```

Configure `vars.MAVEN_DEPLOY_USERNAME` with the Sonatype token username.
Set `docs-project` for projects registered with the docs dispatcher; omit it for
publication without docs. `profile-id` is accepted for compatibility but unused:
the direct Central Publisher Portal API checks the account's namespace access.
Release publication is automatic: callers must omit
the removed `stage-for-maven-central-publish` and `publish-to-maven-central` inputs.

For a reactor such as cuVS Lucene, include every supported versioned module and
the POM-only parent and relocation in one repository artifact. Set
`primary-artifact-id: cuvs-lucene` to keep the existing docs receipt coordinate.
All coordinates must have the same group and version. Preparation reads each
POM's coordinates and packaging, signs every artifact, and writes a separate
coordinate manifest outside the Maven tree. POM-only packages require a signed
POM; JAR packages also require signed primary, sources, and javadoc JARs.
Snapshot publishing deploys each coordinate with its declared packaging.

The release uploads one `USER_MANAGED` ZIP using the direct Portal API. Before
requesting publication it verifies that the Portal's complete package-URL set
matches the manifest, independent of ordering. Missing, extra, and duplicate
packages fail verification. Recovery receipts bind the deployment to the full
coordinate set; a rerun resumes that deployment without uploading again. One
completed-publication receipt is emitted only after the entire set is `PUBLISHED`.
Its schema and primary coordinate remain compatible with the docs dispatcher.
The Lucene docs registration must fetch javadoc from a versioned module such as
`cuvs-lucene-10.2`; the relocation coordinate has no javadoc JAR. Verify that
registration separately before enabling docs deployment.

## Production 3s signing

Maven artifacts use the same production `RAPIDS_ADI_OPS` job and
`SIGNING_RAPIDS_ADI_OPS` scope as the cuVS/cuDF standalone builds. This removes
private GPG keys and passphrases from the publisher. Each POM and JAR is submitted
sequentially. Every returned detached signature is verified against the pinned
RAPIDS public key before preparation succeeds. Missing signatures, modified
bytes, unexpected keys, unknown states, and contradictory states stop publication.
There is no local-key or Dev fallback. Discard partial output after failure;
the original input remains read-only.

The public key in `keys/rapids-production.asc` has primary fingerprint
`565BF95475A2C617A5982D2E8B239C09A78134A9`, confirmed for the standalone
integration under [SGN-9460](https://nvidia.atlassian.net/browse/SGN-9460), linked
from [SGN-3310](https://nvidia.atlassian.net/browse/SGN-3310). Key rotation requires
reviewed updates to both the public key and pinned fingerprint.

Signing and Maven credentials must be backed by the team's Vault records and
made available to each caller through the existing secret synchronization path.
Enroll `NVSEC_SSA_CLIENT_ID` and `NVSEC_SSA_CLIENT_SECRET` with an approved
production `SIGNING_RAPIDS_ADI_OPS` grant for each consumer. `NVSEC_GITLAB_TOKEN`
needs read access to the nvsec package registry in NVIDIA GitLab project 25923.
`MAVEN_DEPLOY_TOKEN` and `vars.MAVEN_DEPLOY_USERNAME` continue to authorize
Sonatype publication. Do not put credential values in workflow configuration.

The workflow installs `nvsec==2.16.1`, matching the standalone integration, using
a disposable private `.netrc`. Signing uses an isolated, disposable token/log
home. Credentials are passed through the environment. The container prepares
the repository layout without receiving signing credentials. The host requires
`gpg` and `jq` as well as `nvsec`. `THREES_TIMEOUT` defaults to 1800 seconds per
artifact, capped at 3600 seconds.

Deploy the workflow and caller mappings together: the removed `GPG_PRIVATE_KEY`
and `GPG_PASSPHRASE` inputs are incompatible with old callers. Before cutover,
verify Vault synchronization and registry access from the Actions runner, then
sign a representative Maven artifact with each consumer identity. Confirm that
the production job accepts `{}` parameters and returns ASCII-armored signatures
accepted by Sonatype. Multipart upload does not bypass the per-file service
limit. Confirm public-key availability to Maven consumers through an approved
distribution channel. Local tests use disposable keys and simulated 3s responses;
they do not establish live SSA grants, runner connectivity, job acceptance, or
Sonatype acceptance.

## Completed-publication receipt

`publication.json` tells the dispatcher what was published and which source run
published it. The workflow retains it for 90 days in the Actions artifact
`maven-central-published-<docs-project>`. Example (illustrative deployment/run IDs):

```json
{
  "schema_version": 1,
  "state": "PUBLISHED",
  "docs_project": "cudf-java",
  "group_id": "ai.rapids",
  "artifact_id": "cudf",
  "version": "26.12.0",
  "deployment_id": "00000000-0000-0000-0000-000000000001",
  "source_repository": "NVIDIA/cudf",
  "source_repository_id": 90506918,
  "source_ref": "refs/tags/v26.12.00",
  "source_sha": "0123456789abcdef0123456789abcdef01234567",
  "workflow_run_id": 123456789,
  "workflow_run_attempt": 1
}
```

`deployment.json` serves a separate purpose: it records the Sonatype deployment
for publication recovery, including when publication has not completed.

## Rollout and recovery

See the dispatcher's [rollout and recovery guide](https://github.com/rapidsai/release-tracking/blob/main/docs/java-docs-dispatch.md)
for App access, deployment order, and docs retries. Rerun a failed publisher job to
resume its retained deployment within 90 days. If the upload response was lost
before retaining `deployment.json`, reconcile the deployment in Sonatype first.

## Signing-only Actions smoke test

The `Maven publisher tests` workflow has an opt-in `live-signing` dispatch input.
It runs the reusable [signing smoke workflow](../../.github/workflows/maven-signing-smoke.yaml)
on the configured self-hosted test runner. Supply `source-repository` (`NVIDIA/cuvs`
or `NVIDIA/cudf`), a successful trusted `source-run-id`, the exact Maven repository
`artifact-name`, and `primary-artifact-id` when the artifact contains multiple
POMs. Source runs must originate on `main` or `release/*` in that repository
with a push, manual, or scheduled event; pull-request outputs are rejected. Ordinary publisher tests
continue to use disposable keys unless live signing is explicitly selected.

The smoke test uses GitHub OIDC to authenticate directly to RAPIDS Vault.
Credentials remain in Vault and are fetched at runtime; no signing or registry
credential is enrolled as a GitHub repository or environment secret. The pinned
`hashicorp/vault-action` v4.0.0 uses Node 24, supported by the same runner class
that runs checkout v7. The action masks returned values and exposes each only
to the steps that need it; the Vault token is neither exported nor output.

The [Vault configuration](vault/README.md) grants a ten-minute session with read
access only to the temporary cuDF SSA identity and runtime registry token.
Exact repository/owner IDs, branch, event, caller workflow, reusable workflow,
and reviewed commit bind the role to this smoke test. This test deliberately
uses cuDF's approved production identity while approval of the intended
`rapids-pulse` client scope is pending. It does not establish access for that
future identity. Remove the temporary role and policy after verification.

A successful run retains the signed Maven tree, source-build provenance,
coordinate manifest, public key, and verification results for seven days.
`request-receipts.jsonl` records each completed request ID, file SHA-256,
expected fingerprint, and elapsed seconds. An independent verification pass
checks the retained signatures, matches their receipts, and compares artifact
bytes with the downloaded input. No credential files or raw client logs are
included. Use a representative repository including its largest expected JAR;
a small artifact alone does not establish production size or timing limits.

### Live runner check, November 18, 2026

[Smoke run 44384629121, attempt 2](https://github.com/rapidsai/shared-workflows/actions/runs/44384629121/attempts/2)
validated the successful cuDF source build, downloaded its 2.6 GB Maven
repository, and authenticated to Vault using GitHub OIDC. Vault returned the
approved temporary credentials without GitHub secret enrollment. The run stopped
before signing because the managed CPU runner could not resolve
`gitlab-master.nvidia.com`, the current nvsec package registry.

The NVIDIA Artifactory `sw-cloudsec-pypi` and `sw-nvsec-pypi` indexes were checked;
they contain releases only through 2.2.3, not the pinned 2.16.1. Do not downgrade
or treat their presence as proof of a current mirror. Before another full
Actions attempt, provide an approved runner-accessible distribution path for
the pinned client and its dependencies, or use a runner with corporate network
access. The installer checks connectivity first so DNS failures do not look
like missing package versions. The temporary test role, policy, and runner-pool
enrollment are removed after the attempt; reruns require fresh enrollment bound
to the reviewed commit. Production publisher enrollment remains separate.

A separate local test using the same pinned `nvsec==2.16.1`, signer, temporary
cuDF identity, and production job successfully signed a real cuVS build POM and
CUDA JAR from [build 44241143198](https://github.com/NVIDIA/cuvs/actions/runs/44241143198).
Both returned signatures verified against the pinned RAPIDS fingerprint, and
source bytes were preserved. This establishes the live job/signature contract
for that sample; it does not establish full-size cuDF package acceptance,
Sonatype acceptance, or a working end-to-end managed-runner installation path.

The follow-up smoke test targets a temporary Apple Silicon Mac runner labeled
`rapids-3s-smoke-mac-20261118`, explicitly selected because this host can install
the pinned client from corporate GitLab. The runner accepts one job, uses a
separate temporary home/work directory, and is not installed as a service.
Remove its registration and local runner credentials after the run, along with
the temporary Vault role/policy. Its macOS result does not establish registry
reachability from the NVIDIA-managed Linux CPU pool. Publication still requires
a supported runner/network route or an approved current-package mirror.
