# Explicit Bearer-token transport

`guarded_oci_transport.publish_prepared()` retains Basic authentication by
default. Optional Bearer mode accepts an independently reviewed policy through
`bearer_policy`, with separate `token_ca_file` and registry `ca_file` trust
inputs. No workflow, service, credential lookup or hosted access is activated.

```python
bearer_policy = {
    "endpoint": approved_https_token_endpoint,
    "service": approved_registry_service,
    "repository": descriptor["repository"],
    "actions": ["pull", "push"],
}
```

The caller must authenticate these inputs; their JSON shape is not approval.
The token endpoint must be exact canonical HTTPS, with an ASCII DNS/IPv4 host,
optional explicit port and simple absolute path. Credentials, queries,
fragments, encoded paths and traversal are rejected. The service is a nonempty
ASCII name using letters, digits, dots, underscores and hyphens. The repository
must exactly match the prepared publication repository. This narrow publisher
requires exactly `pull,push`; it cannot request delete, catalog, wildcard or
multiple-repository authority. IPv6 token endpoints are unsupported here.

The client proactively requests one token from that endpoint. It sends the
explicit Basic bootstrap credentials only to the token endpoint, and sends only
the returned Bearer token to the registry origin. Registry challenges never
choose a realm, service, scope or retry. Every unexpected authentication
challenge stops the operation. A token exchange is not repeated after failure;
an expired token is not refreshed, and no POST is automatically replayed.
The standard [Distribution token protocol](https://distribution.github.io/distribution/spec/auth/token/)
defines the service/scope request and token response fields. Proactive exact
policy selection is this adapter's stricter boundary, rather than discovery
from a server-supplied challenge.

Both HTTP sessions disable ambient netrc, proxy and CA environment settings
before preparing requests. HTTPS verification is never disabled. Token GET
redirects and any response Location are rejected. The token request explicitly
selects `Accept-Encoding: identity`; nonidentity responses are rejected before
streaming. Declared and streamed response bodies are capped at 64 KiB; tokens
at 16 KiB. Connect/read inactivity timeouts are five seconds. A POSIX main-thread alarm
also interrupts the complete token exchange after ten seconds, including slow
response headers and body, clamped to the descriptor deadline. Registry requests
have one thirty-second absolute budget shared by upload, headers and readback;
waiting before reading does not reset that budget. No raw token, token-service body/error,
bootstrap credential or token-endpoint URL is logged or added to a receipt.

This selected subset requires explicit UTC `issued_at` and integer `expires_in`
between 1 and 3600 seconds, rejecting stale issue times and issue times more
than five seconds in the future, plus duplicate or unsupported JSON fields. It accepts `token` or `access_token`; if both appear
they must be identical. Cached expiry cannot exceed the publication descriptor
deadline. Request counts include the token GET. Bearer receipts identify
`transport: guarded-bearer-oci` and the token-request count; Basic receipts retain
their existing transport name. All existing archive, upload location, digest
readback, conflict and expiry guards remain in force.

Tokens are opaque to this client. A JSON token or claimed scope does not grant
repository authority. The registry must verify its signature, trusted issuer,
audience, expiry and repository actions. The
[Distribution JWT implementation](https://distribution.github.io/distribution/spec/auth/jwt/)
and [registry token configuration](https://distribution.github.io/distribution/about/configuration/#token)
describe that server responsibility.

## Local evidence and remaining acceptance

The tests configure the existing digest-pinned real Distribution registry with
native HTTPS, a fixture issuer, service and CA trust. A controlled HTTPS issuer
returns real RS256 JWTs with an X.509 chain. The native registry accepts exact
publish/readback/retry and rejects wrong issuer/audience/repository, pull-only
grants and invalid signatures. Instrumented TLS endpoints test token redirects,
malicious realms, CA failures, body bounds, malformed/expired responses,
credential isolation and expiry before POST without refetch/replay.
Only the issuer and malicious endpoints are simulated; token crypto and registry
authorization are exercised by the real registry. JWT/cryptography packages
are pinned test dependencies, not new production transport dependencies.

This is one registry origin plus one independently configured HTTPS token
endpoint with Basic bootstrap authentication. It is not generic OAuth, challenge
discovery, device/code flow, refresh-token support, single-use-token management,
or support for arbitrary registry/token providers. Hosted issuer selection,
credential isolation, private ACLs, trust distribution, operator policy and
production authorization remain separate acceptance work.

## Deadline execution contract

The synchronous transport requires POSIX `SIGALRM`/`setitimer` support on the main
Python thread. It refuses an already-active real-time alarm before network I/O,
rather than taking ownership of another caller's timer. Each protected phase
restores the prior signal handler and disables only its own timer in `finally`,
on success and failure. Header timeout, streamed-body timeout and ambiguous POST
timeout close the connection and never cause retry or token refresh. Receipts
include the deadline helper's source SHA-256; rehearsal plans bind that helper
along with the other client sources.

This is real interruption of Python socket operations, including a peer that
keeps an inactivity timeout alive by dripping headers. It is not process
isolation or a universal watchdog for uninterruptible native extensions, system
DNS resolvers or arbitrary caller code. Non-POSIX and worker-thread use fail
closed; there is no weaker thread-only timeout fallback. Existing TLS, exact
origin, location, credential and digest boundaries remain mandatory.

The fixed thirty-second registry budget is tested with tiny fixture images and a
bounded stalled upload, not large RAPIDS archives. Hosted acceptance must size
and review that bounded budget against actual archive sizes and throughput;
this source change does not claim large-image transfer readiness. Readback
rejects responses missing their original request deadline instead of starting a
new budget.
