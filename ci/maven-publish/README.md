# Maven release publication

The release tag authorizes publication. Release builds sign, validate, and
publish automatically, then wait for `PUBLISHED`. Non-release builds publish snapshots.

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

The Maven version must match the tag, allowing a zero-padded patch component
(`v26.12.00` authorizes `26.12.0`). The Portal deployment's package URL must match
the Maven coordinates before promotion. `deployment-state`, `deployment-id`,
and `version` outputs allow a dependent coordinate to wait for `PUBLISHED`.
cuVS publishes Java before Lucene because Lucene depends on that Java release.

## Publication receipts and docs

An optional `docs-project` labels a completed publication. The source build SHA
must equal the tagged workflow commit. After confirming `PUBLISHED`, the workflow
retains `publication.json` in an artifact named
`maven-central-published-<docs-project>`. Each workflow run has its own artifact;
reruns preserve an existing receipt. Receipts are retained for 90 days.

Schema version 1 records `state`, `docs_project`, `group_id`, `artifact_id`,
`version`, `deployment_id`, `source_repository`, `source_repository_id`,
`source_ref`, `source_sha`, `workflow_run_id`, and `workflow_run_attempt`.
Only a completed publication emits this receipt. It is distinct from the
deployment receipt retained before polling for recovery.

The standalone `dispatch-java-docs.yml` workflow in `rapidsai/release-tracking`
checks for these receipts every five minutes. It validates repository identity,
coordinates, workflow event/path, run metadata, and the current release tag's
commit before dispatching `rapidsai/docs`. Source publishers need no GitHub App
credentials. The existing release dispatcher App remains in release-tracking's
protected `release-dispatch` environment; no release train/controller is needed.

## Configuration and rollout

Sonatype user tokens and GPG credentials use the existing configuration. Deploy
the docs workflow and project registrations, then the standalone dispatcher,
then enable the shared publisher and project callers on the release branch.
The dispatcher registry pins the canonical NVIDIA/cuDF and NVIDIA/cuVS repository
IDs and Maven coordinates. Its existing App needs source repository reads and
Actions write access to `rapidsai/docs`. No App key or PAT belongs in a publisher.

Release publication has one path: validate, publish, and wait for `PUBLISHED`.
The former `stage-for-maven-central-publish` and `publish-to-maven-central` inputs
are removed; callers must omit them when adopting this workflow version.

## Recovery

The publisher records the deployment ID and coordinates before polling, then
retains that recovery receipt as a run-specific artifact, including on failure.
Rerun failed jobs within the 90-day retention period to resume the same deployment.
`VALIDATED` can be promoted, `PUBLISHING` is polled, and `PUBLISHED` succeeds without
another upload or publish request. An uncertain publish response is followed by
status polling rather than HTTP-level retries. Publication is serialized without
cancelling an active release.

If completed receipt upload fails after publication, rerun the failed publisher
job: it resumes the published deployment and retries receipt creation. An
interrupted staging handoff before a recovery receipt is retained requires
Portal-side reconciliation before retrying.

The docs dispatcher skips active and successful docs runs. Failed docs runs are
retried up to three total attempts; after repairing the failure, manually run the
dispatcher with the project selected and `retry-failed` enabled. `dry-run` checks
evidence and reports intended dispatches without starting docs. GitHub schedules
can be delayed; manual dispatch provides immediate recovery.

Docs upload and the existing stable-link PR occur in the docs repository. That
PR follows its existing merge process. A failed docs deployment can be rerun
directly for an already published Maven version, without republishing artifacts.
