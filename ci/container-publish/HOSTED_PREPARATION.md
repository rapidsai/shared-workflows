# Private OCI rehearsal preparation

`hosted_preparation.py` prepares a destination-bound, byte-verified OCI handoff
before a publication job receives credentials. It makes no network requests,
retrieves no secrets, invokes no credential helpers, and cannot publish. Its
receipt says `prepared-not-published`; it is not approval or trusted provenance.
The independently authenticated controller must supply the expected policy and
handoff digests, train digest, and complete frozen package identity. Calculating
these expectations from untrusted input files is not authentication.

The current implementation reuses the reviewed single-platform CI-image importer
in `local_rehearsal.py`: SHA-256 over original transport bytes; bounded streaming
OCI validation; index, runnable manifest, config, ordered layer digests and
platform comparison. Limits remain 512 MiB compressed, 4 GiB expanded and 100,000
members. It supports the current `container:ci-images` handoff, not arbitrary
Docker publication manifests or a complete 208-image authorization decision.
A descriptor must expire within 24 hours and within the approved policy lifetime.
Expired descriptors are rejected even on identical retries.

Preparation seals `archive.oci`, `policy.json`, `descriptor.json` and
`receipt.json` under a canonical descriptor digest in an existing private local
output directory. Files have mode 0400 in a private temporary directory. Original
transport bytes are preserved; the importer performs bounded gzip normalization
later. An identical retry verifies the existing files and archive hash; changed
bytes, metadata, extra files and symlinks fail without repairing the receipt.
This is local retry protection, not a remotely atomic immutable registry tag.
Use a private job-local output directory with one writer. Source and output paths
must not be writable by an untrusted concurrent process.

## Operator-reviewed configuration

The following **non-executable template** intentionally has no invented service
URL or live credential path. Operators must replace nulls and authenticate the
complete policy digest before it is usable:

```json
{
  "schema_version": 1,
  "origin": null,
  "namespace": null,
  "revision": null,
  "expires_at": null,
  "private_acl_evidence": null,
  "transport_boundary_evidence": null,
  "credential_reference": null
}
```

`origin` must be one canonical HTTPS origin, with no credentials, path, query or
fragment. `namespace` is an exact repository-prefix boundary: `team/test` permits
`team/test/image`, but never `team/test-other/image`. The three review/evidence
fields are bounded references to externally reviewed records. They are not
validated attestations or evidence that a service is private. Credential paths
must be isolated proposed rehearsal paths under
`kv2/rapids-team/rapids/ci/rehearsal/container/<id>`; these paths have not been
provisioned. Credential values are forbidden in this schema. The preparation
process never opens the reference, ambient Docker configuration, netrc, proxies,
AWS credentials or Vault sessions.

A descriptor contains only `schema_version: 1`, the authenticated
`policy_sha256`, UTC `issued_at` and `expires_at`, destination `repository` and
`tag`, and the complete authenticated CI-image `handoff`. The existing handoff
contract binds source repository/SHA/run/attempt/artifact, train, package identity
and every structural archive digest. The caller must authenticate the handoff
as well as its SHA-256. The separate `--identity` JSON file must contain exactly
`ecosystem`, `name`, `version`, `build` and `platform` from the frozen request.

```text
python ci/container-publish/hosted_preparation.py \
  --policy reviewed-policy.json --policy-sha256 <approved-policy-sha256> \
  --descriptor destination-descriptor.json \
  --handoff-sha256 <authenticated-handoff-sha256> \
  --train-sha256 <authenticated-train-sha256> --identity frozen-identity.json \
  --archive downloaded-and-verified.oci.tar.gz --output /private/job/prepared
```

Hashes use sorted compact JSON plus a trailing newline, as `canonical()` defines.
The output directory must already exist with mode 0700 and a canonical
non-symlink path; preparation rejects shared permissions without repairing them.
Retries require the prepared directory to remain 0700 and every file 0400.
`--loopback-fixture` allows only HTTP `127.0.0.1` with an explicit port, is recorded
in the receipt, and does not permit a hosted operation. No hosted execution
command is emitted. Keep prepared artifacts and reports private.

## Executable local evidence and hosted activation boundary

The existing real Docker/ORAS roundtrip now creates a short-lived synthetic
policy/descriptor, prepares its producer-shaped gzip archive, imports the sealed
copy with ORAS 1.3.3 into its authenticated loopback registry, and independently
checks manifest/config/layer bytes. Existing anonymous/wrong-auth denial,
redirect-readback rejection, conflict/retry and owned-resource cleanup checks
remain in place. The test uses synthetic credentials only. Separate preparation
regressions cover policy/handoff substitution, stale descriptors, namespace
escapes, archive changes, immutable retry conflicts and ambient credential
isolation. This demonstrates local protocol mechanics, not live private ACLs,
real candidate admission/provenance, production authorization or hosted startup.

The hosted write path is deliberately not enabled: ORAS 1.3.3's CLI has no
redirect-deny switch. Checking the initial HTTPS origin cannot constrain upload
`Location`, bearer-token realms or subsequent redirects. The current local
readback client's no-redirect guard is not a guard around ORAS uploads. Do not
use this descriptor as permission to run arbitrary ORAS against a supplied host.
The ORAS-Go project has published upload-Location and authentication redirect
security fixes; any hosted client selection must review its actual embedded
library version and behavior, not assume the CLI version proves that boundary.
See the [upload-Location advisory](https://github.com/oras-project/oras-go/security/advisories/GHSA-jxpm-75mh-9fp7).

Before hosted activation, an operator must select and approve:

1. A real private OCI service/namespace, private read/write/anonymous-deny ACLs,
   no public replication, TLS/CA trust, retention and cleanup owner.
2. Separate narrowly scoped rehearsal uploader and read-only consumer identities;
   provision only their test credentials, with no production secrets inherited.
3. An enforced and tested registry/client transport boundary covering upload
   locations, token realms, redirects and destination namespaces. This may
   require a supported guarded transport or operator-managed network policy;
   neither exists in this source tranche. A JSON evidence reference alone does
   not enforce it.
4. A reviewed hosted execution adapter that revalidates this policy/handoff and
   sealed bytes immediately before upload, scopes credential files privately,
   verifies digest readback and rejects conflict/expiry without blind retries.
   Registry immutability/concurrent-writer behavior needs separate acceptance.

## Other publication families

This adapter does not pretend that all destinations share an OCI protocol:

| Family | Existing actual client evidence | Required operator choice before a hosted adapter |
| --- | --- | --- |
| Conda | Anaconda client 1.15.0 stage/object upload/commit, content readback, skip-existing and transient-commit recovery against local fixtures | Private compatible Anaconda API plus approved storage upload origins, scoped account, indexing/retrieval and cleanup semantics. S3 alone is not that API. |
| Python | Separate release-scripts Kitmaker SDK/CLI project discovery/release POST/UUID status contracts | Isolated Kitmaker project, downstream private index, credentials and supported fetch path. `dry-run` still POSTs; direct public PyPI remains disabled. |
| Maven | Central search/open/PUT/handoff/poll/drop local protocol fixtures | Private Maven snapshot/release service and credentials; distinguish Central protocol from actual snapshot deploy-plugin behavior. |
| OCI | ORAS archive import/readback plus descriptor preparation in this branch | The four requirements above; no existing private registry is assumed available. |

Live service setup, credential installation and hosted uploads require a separate
concrete review/authorization. No workflow, proof pin, IAM, Vault or service
configuration is changed here.
