# GitHub OIDC for the signing-only smoke test

GitHub issues a job token with audience `https://prod.vault.nvidia.com:443`.
Vault validates its signature and issuer against GitHub's discovery endpoint at
`auth/jwt/github-actions/rapids-package-signing`, then matches the exact claims
in `maven-3s-smoke-role.json`. Repository and owner IDs survive renaming and
prevent a new repository at an old name from inheriting access. The workflow
also needs `id-token: write`; no stored Vault login token is required.

Apply `github-oidc-config.json` only to a dedicated JWT auth mount, without
changing existing GitHub or Jenkins mounts. Install `maven-3s-smoke.hcl` as the
`maven-3s-smoke` policy. Before installing the role, replace
`REPLACE_WITH_REVIEWED_COMMIT` in an in-memory copy with the reviewed, pushed
smoke-test commit SHA. A subsequent code change requires updating that binding.
Read back the installed role, policy, and issuer settings before dispatching.

The role grants no default policy, write, list, mount-management, or credential
rotation rights. Its Vault session expires after ten minutes. It reads only:

- `services/libcudf-static/ssa/prod/issue/creds`, the temporary production client
  selected for this test;
- `kv2/rapids-team/data/rapids/gitlab/gitlab-master-rapids-svc-jenkins-pat`, used
  only to install the pinned nvsec package from NVIDIA GitLab.

SSA and registry credentials necessarily enter the runner while in use, but
are not configured as GitHub secrets or retained in test artifacts. The Vault
action masks them; output-to-environment export is disabled, and individual
steps receive only the outputs they need. The smoke workflow has no Sonatype
credentials or publication steps. Avoid raw credential/client logs in artifacts.

After the test, delete the temporary role and policy. The dedicated auth mount
may remain for the eventual reviewed publisher roles. Production enrollment
for the intended `rapids-pulse` identity is separate and still needs its scope
approval and roles restricted to the actual production caller identities.
