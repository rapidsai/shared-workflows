# Local authenticated registry contracts

This rehearsal runs a real Distribution registry and real Docker/Buildx clients.
It mirrors the publishing operations inspected in rapidsai/docker's
`build-rapids-image.yml`, `build-test-publish-images.yml` and
`create-rapids-multiarch-manifest.sh`: authentication, architecture image pushes,
multiarchitecture manifest creation/push and pulls. It creates tiny `FROM scratch`
fixtures for amd64 and arm64. These are synthetic protocol inputs, not rebuilt or
promoted admitted RAPIDS candidates, and do not exercise the full RAPIDS matrix.

Registry version 3.0.0 is pinned to
`dockerhub.nvidia.com/library/registry@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e`.
Before running tests, obtain that image through the approved mirror using an empty
temporary Docker auth configuration. The harness uses `--pull=never` and fails
if the pinned image is absent; tests do not silently pull another service image.
Docker, a local Unix daemon socket, the installed Buildx plugin and bcrypt-capable
`htpasswd`, and ORAS 1.3.3 are prerequisites. No daemon configuration or existing
builder is changed. On the reviewed host, Docker client/server 29.4.0 and Buildx v0.33.0
passed; each receipt records the actual versions used. ORAS 1.3.3 uses the
documented `oras cp --from-oci-layout` flow to import an OCI layout archive; the
binary must already be installed and the rehearsal fails closed on another version.

`local_rehearsal.py` creates a uniquely named bridge and registry container,
loopback-only ephemeral port and tmpfs storage. Authentication uses only the
fixed synthetic `fixture-user` / `fixture-value` pair. The only host bind mount is
that temporary bcrypt password file. Docker subprocesses inherit only PATH; their
temporary Docker configuration includes only the resolved existing Buildx
executable and synthetic registry authentication. ORAS subprocesses use an empty
temporary registry-auth config and isolated HOME/XDG directories. No host home, operator tokens,
AWS/Vault configuration, Docker credentials or builder settings enter containers.
The registry has no remote storage/proxy configuration; backend redirects and
tracing exports are disabled. Its ordinary bridge is not an egress firewall:
the reviewed Docker 29 internal bridge did not support the required published
port, so no outbound-denial guarantee is claimed. HTTP Basic auth is permitted
only for this loopback fixture with known synthetic credentials; approved live
services require TLS and independently reviewed access controls.

The actual roundtrip verifies anonymous and wrong-credential 401 responses,
rejected Docker login, two architecture pushes, real `docker manifest create`
and `push`, index/child-manifest/config/layer SHA-256 readback, unknown digest
rejection and identical manifest retry. Both architecture child digests are
pulled, plus the multiarchitecture index for the daemon's native architecture.
It then copies the synthetic two-platform index from the loopback registry into
an OCI layout tar archive and imports that exact archive into a second repository
on the same isolated registry. Before invoking ORAS, the harness verifies the
archive SHA-256, exact expected Linux amd64/arm64 inventory, descriptor sizes and
digests, and config platform fields; it rejects external URLs and nonregular or
unsafe archive entries without extracting them. After import, it verifies that
the index bytes/digest, child manifest digests and every config/layer digest are
unchanged. Wrong archive digests and missing variants are rejected before upload;
retrying the same archive preserves the destination, and an archive with a
different index cannot overwrite an existing tag. All OCI operations use the
fixed synthetic fixture credentials and loopback endpoint. ORAS uses the documented
[`cp` OCI-layout archive flow](https://oras.land/docs/commands/oras_cp/).
The conflict preflight is single-writer rehearsal behavior; it does not provide
an atomic tag immutability guarantee under concurrent writers.
The reviewed classic image store cannot retain both platforms under the same
index reference, so the harness does not claim that unsupported operation.
All fixture image references, its container and network are removed and checked
absent. The pinned registry image remains cached; small BuildKit cache entries
may remain because pruning an existing builder could destroy unrelated work.

References reject public registries, non-loopback hosts and non-reserved image
names. Independent HTTP readback rejects redirects and ignores environment
proxies. A real 307 test confirms the redirect target receives no request.
Docker's ordinary publication client is not modified: arbitrary registry-selected
redirect/token endpoints remain a production destination-policy prerequisite.
The local server's fixed Basic-auth/filesystem/no-redirect configuration establishes
the bounded test route, not a general Docker egress or repository ACL guarantee.

Run `pre-commit run --all-files`, or use an isolated pytest/Requests environment
to run `python -m pytest -q tests/test_container_upload_contract.py`. To retain
the metadata-only receipt, run `local_rehearsal.py --output NEW_RECEIPT.json`.
It refuses to overwrite an existing receipt and stores new receipts with mode
0600. Existing Ruff/Bandit/residual bugbear scopes include the two new Python
files; Shellcheck and all existing Maven/Conda checks remain active.

This is local authentication, protocol and digest evidence, not a verified live
private service, namespace authorization, public publication, candidate promotion,
complete variant inventory or runtime compatibility test. Scratch images do not
run a RAPIDS workload. The owner-approved private registry, TLS, scoped Vault
identity, production admission-to-registry binding, immutable tag/conflict policy,
partial-upload recovery, quotas and retention remain PUB-01-DOCKER prerequisites.
No public publishing workflow, protection rule or proof pin is changed here.
