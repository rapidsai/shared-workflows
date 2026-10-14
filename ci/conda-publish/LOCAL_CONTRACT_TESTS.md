# Local Conda upload contracts

This test tranche exercises the real `anaconda-client==1.15.0` CLI, not a generic
S3 copy. The current production workflow installs `anaconda-client>=1.13.1` and
calls gha-tools' `rapids-upload-to-anaconda-github`; its upload command uses
`anaconda -t TOKEN upload --label LABEL --skip-existing --no-progress`. Production
workflow inputs, package selection, credentials, defaults and guards are unchanged.

The reviewed upstream [1.15.0 client](https://github.com/anaconda/anaconda-client/tree/1.15.0)
implements HEAD/user/distribution/package/release requests, a stage POST containing
labels and SHA-256, a separate multipart POST to the stage response's `post_url`,
and a commit POST. The fixture implements those API responses and receives the
actual client requests. Tests verify original `.conda` and `.tar.bz2` archive
bytes, the multipart Content-MD5/length, stage SHA-256, labels, commit identity and
authenticated content readback. The service is simulated; it is not a replacement
Anaconda service and does not establish real channel indexing or private ACLs.

`local_rehearsal.py` is an explicit local-only harness. It verifies the expected
input SHA-256 before the client runs and after upload, creates an isolated client
configuration and supplies only a fixed synthetic token. All actual Requests
traffic is confined to the exact `http://127.0.0.1:<port>` fixture origin with a
five-second request timeout; proxies and redirect following are disabled. The
guard also checks the storage origin selected by the stage response. Tests prove
that redirect and foreign-storage targets receive no requests, credentials or
package bytes, and that an environment proxy is unused. No operator environment
or publisher token is passed to client subprocesses by the test suite.

The upstream client otherwise follows Requests' redirect behavior and consumes
server-selected storage URLs. This harness's guard is **not** installed in the
production publisher. A private production adapter still needs an approved
allowlist for API/storage/readback origins, redirect and proxy policy, network
controls, TLS and independent service ACL evidence. The literal synthetic token
cannot authorize public publishing, and local mode does not accept a caller's
credential or service URL.

The real CLI's `--skip-existing` returns success based on distribution existence,
without a content comparison. This harness independently retrieves the stored
bytes and compares SHA-256 before reporting success. Tests distinguish an identical
retry from a changed archive at the same package identity; the latter fails
readback without replacing existing bytes. Failed authentication, failed commit,
changed local input and disallowed initial origins fail without a success receipt.
Incomplete commits remain fixture state; no deletion or production cleanup policy
is inferred. The receipt labels itself local-only and records the exact client
and installed dependency versions. Transitive dependencies are recorded rather
than represented as a fully locked environment.

Run `pre-commit run --all-files`, or install `anaconda-client==1.15.0` and
`pytest==8.4.2` in an isolated environment and run
`python -m pytest -q tests/test_conda_upload_contract.py`. Ruff's `B` rules cover
supported bugbear checks; only four unavailable leaf checks retain the separate
plugin. Bandit checks both new Python files, and existing Shellcheck coverage stays
in place. No multiline Python is embedded in shell scripts.

Remaining PUB-01-CONDA work requires a service owner: verify actual Anaconda upload
compatibility, private accounts/channels and labels, indexing/repodata updates,
authenticated installation, anonymous denial, quotas, recovery and cleanup.
The fixture's byte readback proves neither indexing nor installation. Use exact
admitted candidate bytes and inventory when adapting the publisher, retain public
publication guards, and obtain separate approval for the concrete service/access
activation. No service, Vault path, IAM role or public publisher is changed here.
