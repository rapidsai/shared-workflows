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
| Conda | Anaconda client 1.15.0 stage/object upload/commit, authenticated download, SHA-256 against the planned archive, identical retry with one storage upload; with explicit indexer/Pixi tools, real channel indexing and authenticated solver/install of those readback bytes, installed package identity and payload hash | Synthetic API/channel services. The no-dependency noarch package tests retrieval and installation, not RAPIDS dependency compatibility or hosted ACL/indexing. Without both tools, the receipt explicitly retains the unexercised install gap. |
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

### Local HTTPS acceptance

`tests/test_guarded_oci_tls.py` additionally exercises native TLS on the same
digest-pinned distribution registry image used by the OCI rehearsal. It generates
an ephemeral test CA and server certificates through the installed OpenSSL,
binds only to `127.0.0.1`, and publishes/reads back the exact synthetic OCI bytes
using the existing explicit `ca_file` API. Identical retry preserves the manifest
and archive digests. The real registry rejects wrong Basic credentials; its CA
is not trusted without explicit selection. No trust store or operator config is
changed, no registry image is pulled, and only the owned container is removed.

Separate instrumented HTTPS response fixtures prove untrusted certificates,
wrong-host certificates and invalid CA bytes fail before an HTTP request or
Basic credentials reach the service. A verified HTTPS peer's redirect is rejected
without contacting the other origin. These malicious endpoints are simulated;
successful publication and readback use the real registry. TLS verification is
never disabled. Explicit loopback-fixture mode permits HTTP or HTTPS on
`127.0.0.1` with an explicit port; hosted mode still rejects that loopback origin.
This is local certificate/transport acceptance, not hosted service ACL, managed
runner trust distribution, credential provisioning or production authorization.

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
  --kitmaker-checkout /private/job/reviewed-release-scripts \
  --conda-python /private/tools/conda-environment/bin/python \
  --pixi /private/tools/pixi
```

The optional Conda tool pair closes the channel/install part of the local
rehearsal. Supply both or neither; partial tool context fails before publication
clients run. The indexer Python must provide real `conda-index` and its Conda
runtime. Local acceptance used conda-index 0.13.0, Conda 26.9.2 and Pixi 0.81.0;
the receipt records actual versions rather than establishing a production lock.
The synthetic `.conda` contains a real `info/files`/`info/paths.json` payload.
Only hash-verified upload readback bytes are indexed. Real `conda-index` emits
repodata; its package SHA-256/size/identity must match the planned archive.

Pixi uses a fresh job-local manifest, prefix and cache, exactly one owned
authenticated loopback channel, `--no-config` and an explicit empty auth file.
It inherits no operator proxy/auth/config environment; HOME is not reassigned.
The only credentials are fixed synthetic Basic values in the disposable local
manifest, never in the report. The temporary workspace and auth file are removed
on completion/failure. Evidence binds repodata, authenticated archive response,
installed archive identity/hash, installed `payload.txt` hash and Pixi lock hash.
Anonymous/wrong credentials, missing indexed package and changed archive bytes
are tested through the actual client. No public fallback channel is configured.
This owned server emits no redirects; it is not an egress guard for arbitrary
Conda endpoints or proof of hosted private service access.

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

## Checks and opt-in runtime integration

The pre-commit hook always runs the plan/input/source-tamper, private-mode,
exclusive-write/symlink, body-bound and other local contract regressions.
The two cross-repository SDK tests and five real Conda runtime cases opt in
through their explicitly supplied tool/source inputs:

```text
PUB01_KITMAKER_SOURCE=/private/job/reviewed-release-scripts \
PUB01_CONDA_PYTHON=/private/tools/conda-environment/bin/python \
PUB01_PIXI=/private/tools/pixi pre-commit run --all-files
```

Without the explicit checkout the two SDK integration cases skip with a precise
reason; without both Conda tools those five runtime cases skip. Pure validation
contracts always run. Supplying just one Conda tool is a failure. Skips do not
claim an install or four-family replay occurred. A supplied wrong SHA,
dirty checkout, failed/nonterminal SDK status or bad package hash is a failure,
never a skip or fallback. The standalone `run` command always requires the
reviewed checkout. Its actual completion receipt, rather than a standalone CI
skip count, is the evidence for a complete local replay.

Hosted service selection, live credentials/ACLs, downstream delivery, retained
repository metadata, signatures, authorization and deployment acceptance remain
separate work. This branch changes no production workflow, proof ref, Vault/IAM
policy, hosted infrastructure or public publication destination.
