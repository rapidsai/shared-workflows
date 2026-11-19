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

## Caller

```yaml
java-publish:
  needs: java-gather
  permissions:
    actions: read
    contents: read
  uses: rapidsai/shared-workflows/.github/workflows/maven-publish.yaml@release/26.12
  secrets:
    GPG_PRIVATE_KEY: ${{ secrets.GPG_PRIVATE_KEY }}
    GPG_PASSPHRASE: ${{ secrets.GPG_PASSPHRASE }}
    MAVEN_DEPLOY_TOKEN: ${{ secrets.MAVEN_DEPLOY_TOKEN }}
  with:
    artifact-name: cudf_java_maven_repo
    source-git-sha: ${{ github.sha }}
```

Configure `vars.MAVEN_DEPLOY_USERNAME` with the Sonatype token username.
The reusable workflow reference follows the caller's release branch.
`profile-id` is accepted for compatibility but unused: the direct Central
Publisher Portal API checks the account's namespace access.
Release publication is automatic: callers must omit
the removed `stage-for-maven-central-publish` and `publish-to-maven-central` inputs.

For a reactor such as cuVS Lucene, include every supported versioned module and
the POM-only parent and relocation in one repository artifact. Set
`primary-artifact-id: cuvs-lucene` to select the primary workflow output coordinate.
All coordinates must have the same group and version. Preparation reads each
POM's coordinates and packaging, signs every artifact, and writes a separate
coordinate manifest outside the Maven tree. POM-only packages require a signed
POM; JAR packages also require signed primary, sources, and javadoc JARs.
Snapshot publishing deploys each coordinate with its declared packaging.

The release uploads one `USER_MANAGED` ZIP using the direct Portal API. Before
requesting publication it verifies that the Portal's complete package-URL set
matches the manifest, independent of ordering. Missing, extra, and duplicate
packages fail verification. Recovery receipts bind the deployment to the full
coordinate set; a rerun resumes that deployment without uploading again. The
`deployment-state: PUBLISHED` output covers the entire set. Tagged runs also
require the build source SHA to match the tagged commit, including on recovery.

Signing and Maven credentials must be backed by the team's Vault records and
made available to each caller through the existing secret synchronization path.
The workflow consumes `GPG_PRIVATE_KEY`, `GPG_PASSPHRASE`, and
`MAVEN_DEPLOY_TOKEN` as secrets, and `MAVEN_DEPLOY_USERNAME` as a repository/org
variable. Do not put a Vault token or credential value in workflow configuration.
Verify consumer enrollment and synchronization for the caller before cutover.

## Rollout and recovery

Merge this publisher before its cuDF and cuVS callers, then confirm signing
credentials and Sonatype namespace access before cutting the release tag.
Retire legacy manual publishing at cutover so only one path publishes each
release coordinate.

`deployment.json` records the Sonatype deployment ID and complete coordinate set
before polling. The workflow retains it for 90 days as
`maven-central-deployment-<artifact-name>-<run-id>`. Rerun a failed publisher job
in the same workflow run to restore the receipt and resume that deployment.
If the upload response was lost before retaining `deployment.json`, reconcile
the deployment in Sonatype first. A new workflow run does not automatically
restore another run's recovery receipt.

## Documentation

Javadoc JARs remain part of the signed Maven release. Website documentation is
published through each project's existing docs system: cuDF uses Sphinx, and
cuVS Java and Lucene use Fern. This workflow publishes Maven artifacts and
returns deployment outputs; website deployment does not depend on a separate
Maven-publication receipt or docs dispatcher.
