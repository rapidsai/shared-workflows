# Product CI for stopgap version-update PRs

Burndown activation requires exact-head pre-commit/candidate validation and human
approval. Product builds and tests are not activation gates. Skipping only those
PR jobs avoids unnecessary GPU/CPU work and dependency-availability failures while
keeping the checks that authorize the actual cutover.

The reusable `.github/workflows/burndown-pr.yaml` emits `skip-product-ci=true`
only when canonical GitHub PR metadata identifies an open, same-repository PR
targeting main, with a `version-update-YY.MM` head, both `non-breaking` and
`improvement` labels, the generated atomic-candidate body markers, a full head SHA,
and exactly one valid `Burndown-Base` and `Burndown-Release` commit trailer.
The head commit is read from GitHub with a read-only token. Metadata mismatch
returns false; an API failure fails the classifier job. A branch name alone is
insufficient. Main/tag events return false without attempting PR lookup.

This is CI selection, not cutover authorization. It neither validates the
candidate topology nor approves it. Keep ordinary pre-commit/style jobs and the
exact-head `burndown-candidate` job independent of this output. The stopgap must
still enforce both-tree validation, current-head review and all ref protections.

Consumers add a job calling this reusable workflow with only `actions: read`,
`contents: read`, and `pull-requests: read`. Product build/test jobs depend on that
job and combine the following predicate with their existing conditions:

```yaml
if: >-
  needs.burndown-pr.result == 'success' &&
  needs.burndown-pr.outputs.skip-product-ci != 'true'
```

Retain their original dependencies too. Add the classifier to the PR aggregation
job's dependencies so a classifier failure is visible. The standard `pr-builder`
accepts successfully skipped product jobs; it still rejects failed jobs. Do not
gate pre-commit, lint, security, label or candidate-validation jobs on the skip
output, or make them depend on a skipped product build.
The explicit classifier-result check also protects jobs with an existing
`always()` condition from running after an indeterminate classification.

Merge this reusable workflow before enrolling consumers, and ensure it exists on
the release branch used by generated workflow references. Existing rules must
allow the intended cutover actor; skipping a required product job does not itself
grant bypass authority. Candidate validation enrollment remains mandatory even
when a consumer's current ordinary pre-commit already runs.

Only PR execution changes. Main/release builds and publication continue normally;
do not disable an entire workflow through the GitHub API for a version bump.
Existing in-flight writers still need publication fencing or a verified hold.

Run `bash ci/test-burndown-pr.sh` to test the exact shell from the workflow against
normal, fork, malformed, incomplete and duplicate-trailer cases. The dedicated
classifier test workflow and pre-commit ShellCheck cover future changes.
