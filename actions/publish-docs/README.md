# Publish Docs Action

GitHub Action for publishing documentation to S3 and flushing the Akamai CDN cache.

The action syncs a local docs directory to an S3 bucket, then submits an Akamai ECCU flush request so the updated content is served from `docs.nvidia.com`.

N.B. This action installs its dependencies with `apt-get`, using `sudo` unless it is already running as root (as in the RAPIDS CI images). `rapids-pip-retry` from [gha-tools](https://github.com/rapidsai/gha-tools) must be on the `PATH`.

## Inputs

| Input Name | Required | Default | Description |
| --- | --- | --- | --- |
| `akamai-access-token` | Yes | N/A | Akamai EdgeGrid access token |
| `akamai-client-secret` | Yes | N/A | Akamai EdgeGrid client secret |
| `akamai-client-token` | Yes | N/A | Akamai EdgeGrid client token |
| `akamai-emails-to-notify` | Yes | N/A | Comma-delimited email addresses to notify for Akamai flush request progress |
| `akamai-host` | Yes | N/A | Akamai API hostname |
| `akamai-request-name` | Yes | N/A | Name of the Akamai flush request |
| `dry-run` | No | `false` | If `true`, run without making any changes |
| `source-path` | Yes | N/A | Local path to the docs to publish |
| `target-aws-access-key-id` | Yes | N/A | AWS access key ID |
| `target-aws-region` | Yes | N/A | AWS region |
| `target-aws-role-to-assume` | Yes | N/A | AWS role to assume |
| `target-aws-secret-access-key` | Yes | N/A | AWS secret access key |
| `target-s3-bucket` | Yes | N/A | The S3 bucket to upload files to |
| `target-s3-exclude` | No | `""` | Newline-separated glob patterns, relative to `source-path`, to exclude from the sync |
| `target-s3-key` | Yes | N/A | The S3 key to upload files to |
| `target-s3-key-prefix` | No | `developer/docs` | Prefix prepended to `target-s3-key` |
| `target-s3-key-suffix` | No | `""` | Suffix appended to `target-s3-key` |

### S3 destination path

The final S3 destination is constructed as:

```
s3://<target-s3-bucket>/<target-s3-key-prefix>/<target-s3-key>/<target-s3-key-suffix>
```

Leading and trailing slashes are stripped from each component before joining. Prefix and suffix are omitted from the path when empty.

### Excluding a subtree

The sync runs with `--delete`, so anything under the destination that is not in `source-path` is removed. Use `target-s3-exclude` when another workflow publishes a subtree below the same destination. Each pattern becomes an `aws s3 sync --exclude` flag, and the AWS CLI applies these filters to the destination as well, so excluded objects are neither uploaded nor deleted.

```yaml
    target-s3-key: datascience
    target-s3-exclude: deployment/*
```

The Akamai flush still covers the whole `target-s3-key` path, including excluded subtrees. That only revalidates their cache and does not change their content.

## Example

```yaml
- uses: rapidsai/shared-workflows/actions/publish-docs@main
  with:
    akamai-access-token: ${{ secrets.AKAMAI_ACCESS_TOKEN }}
    akamai-client-secret: ${{ secrets.AKAMAI_CLIENT_SECRET }}
    akamai-client-token: ${{ secrets.AKAMAI_CLIENT_TOKEN }}
    akamai-emails-to-notify: docs-notify@example.com
    akamai-host: ${{ secrets.AKAMAI_HOST }}
    akamai-request-name: flush-${{ github.event.repository.name }}-${{ github.ref_name }}
    dry-run: false
    source-path: docs/_build/html
    target-aws-access-key-id: ${{ secrets.DOCS_AWS_ACCESS_KEY_ID }}
    target-aws-region: us-east-1
    target-aws-role-to-assume: ${{ secrets.DOCS_AWS_ROLE_TO_ASSUME }}
    target-aws-secret-access-key: ${{ secrets.DOCS_AWS_SECRET_ACCESS_KEY }}
    target-s3-bucket: ${{ secrets.DOCS_S3_BUCKET }}
    target-s3-key: ${{ github.event.repository.name }}
    target-s3-key-suffix: nightly
```


## Secrets

Use this action with the following secrets:

- `NVIDIA_DOCS_AKAMAI_ACCESS_TOKEN`
- `NVIDIA_DOCS_AKAMAI_CLIENT_TOKEN`
- `NVIDIA_DOCS_AKAMAI_CLIENT_SECRET`
- `NVIDIA_DOCS_AKAMAI_HOST`
- `NVIDIA_DOCS_AWS_ACCESS_KEY_ID`
- `NVIDIA_DOCS_AWS_REGION`
- `NVIDIA_DOCS_AWS_ROLE_TO_ASSUME`
- `NVIDIA_DOCS_AWS_SECRET_ACCESS_KEY`
- `NVIDIA_DOCS_S3_BUCKET`

The `AKAMAI` secrets are for a service account associated with NVIDIA's Akamai CDN. The service account must be able to request a flush of the docs.nvidia.com property.

The `AWS` and `S3` secrets are for the NVIDIA docs team's production account.
