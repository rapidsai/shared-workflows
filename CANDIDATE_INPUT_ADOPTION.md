# Candidate producer and input enrollment

Candidate producers use `gha-oidc-rc-producer-${{ github.repository_id }}`.
GitHub supplies that ID for the caller repository; it is not a workflow input.
Enroll each repository in the producer-role infrastructure and retain the
controller role exclusively for release-tracking. Do not broaden controller trust
to recover a failing product workflow.

Before executing a build container, the candidate-inputs job reads the selected
train with its scoped producer identity, checks the canonical train digest, and
validates the train's `candidate_container_images` map. The keys are the existing
image names used by each matrix cell or custom job; the values are registry image
references ending in `@sha256:<64 lowercase hex digits>`. Each architecture must
be covered by a reviewed image or multi-architecture index digest.

The planner accepts `--candidate-container-images reviewed-images.json`. Include
every selected Conda, wheel, and custom-job image. Resolve and review digests
before planning; build jobs never resolve tags to choose an image. Store the
reviewed map with the planning inputs. The complete map participates in the train
digest and candidate build-input record, conservatively preventing reuse when
any enrolled image changes. Missing maps, malformed digests, modified trains,
caller overrides, and absent matrix keys prevent candidate execution.

Caller workflows may optionally supply `candidate-container-images`, but it must
equal the map in the immutable train. Existing callers that use a controller run
binding need no new image input. Ordinary CI retains its existing tagged images.

Before publication, integrate the prepared shared-actions changes, replace the
explicit zero-SHA context placeholders and all candidate action references with
that reviewed commit, and enroll the same commit in the train's implementation
revisions. Publish those action revisions before publishing workflows that use
them. The pinned telemetry, cache setup, proxy, and information actions use
verified remote commit IDs; their references are captured by the frozen workflow
revision. Review their transitive executable dependencies when admitting new
action revisions. A parent action SHA alone does not freeze downloads performed
inside that action, remote cache services, or build scripts that fetch tools.

Candidate trains must use `--no-sccache`. The runner preflight rejects enabled or
missing cache policies before starting build containers. Candidate jobs skip the
general build role and distributed cache setup entirely. Ordinary CI retains its
existing credentials and cache integration. Enabling candidate compiler caching
requires a separately reviewed cache-purpose identity and explicit admission
policy; restoring ordinary build credentials is prohibited.

Validation before live enrollment:

1. Confirm the authenticated caller ID selects the intended producer role.
2. Exercise denied writes to another repository and controller-owned records.
3. Exercise a valid train, a missing image key, an overridden map, and a mutable
   image; only the complete reviewed train should start a candidate container.
4. Regenerate the train using the final published workflow/action revisions.
5. Confirm all candidate artifact families, including custom jobs, record the
   approved image map in their build-input evidence.

These local changes do not prove deployed IAM behavior, registry availability,
image provenance, cache isolation, or successful end-to-end candidate builds.
