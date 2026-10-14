# Local Maven Central protocol contracts

`tests/test_maven_http_contract.py` runs the real Central publishing script and
curl against a temporary HTTP server on `127.0.0.1`. It verifies staging search,
24 file/checksum PUTs, upload bytes against the retained bundle, handoff, status
polling, optional Portal drop and cleanup of an owned partial staging upload.
Negative cases cover authentication rejection, malformed repository responses,
pre-existing staging, unexpected publication, failed validation, polling timeout,
and HTTP redirects. A second loopback server confirms that a 307 response causes
failure and receives no forwarded request or authorization header.

Run `pre-commit run --all-files`, or run the pytest file directly with the usual
shell tools (`bash`, `curl`, `jq`, `zip`, `md5sum`, `sha1sum`) available. No Docker
service, operator credentials or public publishing connection is needed.

The script's `--local-contract-base-url` option replaces both API origins only
with `http://127.0.0.1:<port>`. It rejects hostnames, paths, URL credentials,
non-loopback hosts and invalid ports before making requests. This mode accepts
only the synthetic `fixture-user` / `fixture-value` pair used by the fixture;
production credentials cannot accidentally be sent to the local server.
`--local-contract-poll-timeout` bounds loopback polling, with a one-second interval.
Without local mode, production origins, polling defaults and public workflow
guards are unchanged. These options are not an arbitrary private destination
configuration or an activated publication workflow.

The fixture reports simulated protocol evidence. Its `.asc` files are synthetic,
so it does not prove GPG signing, live Sonatype validation/finalization or public
publication. It never calls a publish endpoint. The private hosted Maven
snapshot/release service, its owner, ACLs, test signing identity, actual Maven
deploy/retrieval rehearsal and approved destination adapter remain prerequisites
of PUB-01-MAVEN. No private service or credentials are provisioned by these tests.

The new Python fixture has scoped Ruff, Bandit and residual bugbear checks in
pre-commit; existing shell code remains covered by Shellcheck. Ruff owns supported
`B` rules, with bugbear limited to `B001`, `B036`, `B040`, `B042`, which lack complete
equivalents in the pinned Ruff version. Existing workflow checks are unchanged.
