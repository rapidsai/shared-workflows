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
    GPG_PRIVATE_KEY: ${{ secrets.GPG_PRIVATE_KEY }}
    GPG_PASSPHRASE: ${{ secrets.GPG_PASSPHRASE }}
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

Signing and Maven credentials must be backed by the team's Vault records and
made available to each caller through the existing secret synchronization path.
The workflow consumes `GPG_PRIVATE_KEY`, `GPG_PASSPHRASE`, and
`MAVEN_DEPLOY_TOKEN` as secrets, and `MAVEN_DEPLOY_USERNAME` as a repository/org
variable. Do not put a Vault token or credential value in workflow configuration.
Verify consumer enrollment and synchronization for the caller before cutover.

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
