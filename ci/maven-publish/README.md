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
publication without docs. Use `profile-id` when Sonatype's verified namespace
differs from the Maven group. Release publication is automatic: callers must omit
the removed `stage-for-maven-central-publish` and `publish-to-maven-central` inputs.

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
resume its retained deployment within 90 days. If the staging handoff failed
before retaining `deployment.json`, reconcile the deployment in Sonatype first.
