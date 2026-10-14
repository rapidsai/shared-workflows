# Maven release publication

The reusable workflow exposes `deployment-state`, `deployment-id`, and `version`
outputs. A dependent Maven coordinate can wait for `deployment-state` to equal
`PUBLISHED`, even if a subsequent docs dispatch fails. cuVS publishes Java before
Lucene because the Lucene POM depends on the matching Java release.

The release tag authorizes publication. Java release callers enable
`publish-to-maven-central: true` to build, sign, validate, publish, and start docs
without another release approval. Non-release builds publish snapshots.

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
    DOCS_DISPATCH_APP_PRIVATE_KEY: ${{ secrets.DOCS_DISPATCH_APP_PRIVATE_KEY }}
  with:
    artifact-name: cudf_java_maven_repo
    source-git-sha: ${{ github.sha }}
    publish-to-maven-central: true
    docs-project: cudf-java
```

The Maven version must match the tag, allowing a zero-padded patch component
(`v26.12.00` authorizes `26.12.0`). The Portal deployment's package URL must match
the Maven coordinates before promotion. Docs dispatch occurs only after
`PUBLISHED`, with the version extracted from the artifact's POM.

## Credentials and rollout

The Sonatype user token and GPG credentials use the existing configuration.
For docs dispatch, configure `DOCS_DISPATCH_APP_ID` as a repository or organization
variable and `DOCS_DISPATCH_APP_PRIVATE_KEY` as a secret available to each caller.
The GitHub App must be installed on `rapidsai/docs` with Actions write permission.
The workflow requests an installation token scoped to that repository and
permission. Docs dispatch configuration is checked before publication; token
issuance and the remote workflow still require a live integration check.

Deploy `rapidsai/docs`'s `deploy-java-docs.yaml` and register the project there
before enabling its caller. Enable cuDF for the 26.12 release branch. Other Java
projects use the same publisher inputs and add their own docs registry entry.
Projects without hosted Java docs can omit `docs-project` and the App secret.

Existing callers that do not enable automatic publication retain the validation
and staging options. `stage-for-maven-central-publish: false` validates and drops;
`true` leaves a validated deployment. Automatic publication overrides that input.

## Recovery

The publisher records the deployment ID and Maven coordinates before polling,
then retains the receipt as a run-specific Actions artifact, including on failure.
Rerun the failed workflow within the 90-day artifact retention period to resume
the same deployment. `VALIDATED` can be promoted, `PUBLISHING` is polled, and
`PUBLISHED` succeeds without another upload or publish request. An uncertain
publish response is followed by status polling rather than HTTP-level retries.
The publisher serializes attempts for the same caller and artifact without
cancelling publication in progress.

If the docs dispatch job fails after publication, rerun the failed job. A docs
deployment that was accepted but later failed can be rerun in `rapidsai/docs`.
The docs workflow waits for Maven Central download availability, uploads the
javadoc, and opens the stable-version PR. The PR follows the docs repository's
existing merge process; publication and the S3 upload are automatic.

An interrupted staging handoff before a receipt is retained still requires
checking the Portal and recovering the identified deployment. Do not restart
the build as a new release or drop a publishing deployment.
