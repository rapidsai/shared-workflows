# Synthetic four-family publication rehearsal

`run_local.py` joins the actual existing publication clients with owned loopback
protocol services. It prepares immutable input files and records their hashes
and the exact local client-source hashes before execution. A caller-supplied
plan digest is mandatory for `run`. The digest protects this local replay from
substitution; it is not a signature, admission decision or publication authority.
No remote URL, operator secret or live service option is accepted.

The driver runs clients, retrieval and installation directly. It does not invoke
pytest or treat test exit codes as family evidence. Conda/Maven's existing
fixture services were moved into reusable modules here; their established
negative tests continue using the same implementations.

| Family | Actual executable operations and receipt | Explicit limits |
| --- | --- | --- |
| Conda | Anaconda client 1.15.0 stage/object upload/commit, authenticated download, SHA-256 against the planned archive, identical retry with one storage upload | Synthetic API service; channel indexing and a Conda dependency solve/install are not exercised. |
| Python through Kitmaker | Exact reviewed SDK project discovery, release POST, UUID and completed-status calls; simulated worker fetches the planned wheel; actual pip reads an authenticated Simple index, downloads with `--require-hashes`, installs offline into a disposable venv, and imports/executes the fixture | The Portal, worker and Simple index are **simulated**, including the returned `completed` state. This is not evidence that hosted Kitmaker delivers to a private index. Synthetic wheel only; no sdist. Direct public PyPI is never used. |
| Maven | Existing shell/curl Central publisher uploads, hands off and reaches VALIDATED; independent authenticated GET hashes all 24 coordinate/checksum files, verifies original planned bytes and MD5/SHA-1 metadata, then drops the dummy deployment | Synthetic Central protocol; `.asc` files are synthetic bytes, not valid signatures. No snapshot deploy-plugin, hosted repository indexing or public release. SHA-256 is used for evidence; legacy checksums are compatibility metadata. |
| OCI | Planned gzip OCI input passes the existing real authenticated registry, guarded HTTP publisher, independent digest readback and ORAS baseline; ordered/repeated layer references retained, digest pull and owned-resource cleanup checked | Synthetic image and local registry. No hosted ACL/TLS acceptance, private managed-runner startup or full-train image coverage. |

Machine-readable evidence repeats `local_fixture_only: true`,
`production_authorization: false` and `kitmaker_worker_simulated: true`.
The Python family also records `worker_delivery: SIMULATED`, actual SDK source
revision, pip versions, wheel hash, index/download events and installed behavior.
Other family records include exact archive/manifest/config/layer or coordinate
hashes. Actual runtime/dependency versions are recorded; they are not a new
production lock or tooling release.

## Replay

Provide Python with the pinned dependencies in the `four-family-rehearsal`
pre-commit hook, plus the existing local Docker, ORAS 1.3.3, curl, jq, bash and
standard fixture prerequisites. The SDK comes from a separate clean checkout of
reviewed release-scripts `b95debb0b3310c4e4a86b8fa62e565b34af3683d`.
The driver verifies that exact HEAD and refuses dirty or untracked publisher
files, including ignored files. Git replacement objects are disabled for these
checks. Use a dedicated checkout with no Python caches and keep it exclusively
owned during replay; the driver never clones or modifies it. The supplied source
is checked before any publication client is run. Python children use the local
SDK harness that confines requests to the generated loopback origin.

Create an empty directory with mode 0700 at a canonical path, then run:

```text
python ci/publication-rehearsal/run_local.py prepare --directory /private/job/rehearsal
python ci/publication-rehearsal/run_local.py run \
  --directory /private/job/rehearsal \
  --plan-sha256 <digest printed by prepare> \
  --kitmaker-checkout /private/job/reviewed-release-scripts
```

The SDK checkout's production client defaults are unchanged. The local harness
supplies only synthetic fixture credentials. Child environments contain only
PATH and explicitly selected fixture variables; no production credentials are
inherited. The temporary pip netrc contains only the index's synthetic identity
and is removed on success or failure. Pip runs isolated with all config disabled
through `PIP_CONFIG_FILE=/dev/null`, no dependency resolution, no cache/version
check and a hash-pinned wheel. The later install uses `--no-index` with the local
verified download. No HOME reassignment or operator config changes occur.

Input files, the plan, per-family evidence and final completion are mode 0400.
Evidence uses atomic exclusive creation and never follows/replaces an existing
path or symlink. Partial runs retain their finished family receipts; completion
is created only after all four families finish and inputs/client-source hashes
are checked again. Completed/partial runs cannot be overwritten or automatically
retried: prepare a fresh directory. Local services stop in `finally` blocks,
network/client waits are bounded, and readback bodies are capped. Raw child
output, HTTP authorization values and server request logs are not persisted in
evidence. Local input/runtime directories remain for inspection; remove only
your own replay directory when finished. Docker resources are cleaned by the
existing fixture's ownership checks.

## Checks and opt-in SDK integration

The pre-commit hook always runs the plan/input/source-tamper, private-mode,
exclusive-write/symlink, body-bound and other local contract regressions.
Only the two tests that execute the cross-repository SDK opt in through:

```text
PUB01_KITMAKER_SOURCE=/private/job/reviewed-release-scripts pre-commit run --all-files
```

Without that explicit checkout the two integration cases skip with a precise
reason; this does not claim a four-family replay occurred. A supplied wrong SHA,
dirty checkout, failed/nonterminal SDK status or bad package hash is a failure,
never a skip or fallback. The standalone `run` command always requires the
reviewed checkout. Its actual completion receipt, rather than a standalone CI
skip count, is the evidence for a complete local replay.

Hosted service selection, live credentials/ACLs, downstream delivery, retained
repository metadata, signatures, authorization and deployment acceptance remain
separate work. This branch changes no production workflow, proof ref, Vault/IAM
policy, hosted infrastructure or public publication destination.
