# Publish One Doc Action

GitHub Action for publishing a single file to S3 and flushing the Akamai CDN cache.

The action uploads a local file to an S3 bucket, then submits an Akamai ECCU flush request so the updated content is served from `docs.nvidia.com`.

N.B. This action installs its dependencies with `apt-get`, using `sudo` unless it is already running as root (as in the RAPIDS CI images). `rapids-pip-retry` from [gha-tools](https://github.com/rapidsai/gha-tools) must be on the `PATH`.

## Inputs

| Input Name | Required | Default | Description |
| --- | --- | --- | --- |
| `akamai-access-token` | Yes | N/A | Akamai EdgeGrid access token |
| `akamai-client-secret` | Yes | N/A | Akamai EdgeGrid client secret |
| `akamai-client-token` | Yes | N/A | Akamai EdgeGrid client token |
| `akamai-emails-to-notify` | No | N/A | Comma-delimited email addresses to notify for Akamai flush request progress |
| `akamai-host` | Yes | N/A | Akamai API hostname |
| `akamai-request-name` | Yes | N/A | Name of the Akamai flush request |
| `dry-run` | No | `false` | If `true`, run without making any changes |
| `file-path` | Yes | N/A | Path to the local file to upload and flush from the Akamai CDN cache |
| `target-aws-access-key-id` | Yes | N/A | AWS access key ID |
| `target-aws-region` | Yes | N/A | AWS region |
| `target-aws-secret-access-key` | Yes | N/A | AWS secret access key |
| `target-s3-bucket` | Yes | N/A | The S3 bucket to upload files to |
| `target-s3-key` | Yes | N/A | The S3 key to upload files to |
| `target-s3-key-prefix` | No | `developer/docs` | Prefix prepended to `target-s3-key` |

### S3 destination path

The final S3 destination is constructed as:

```
s3://<target-s3-bucket>/<target-s3-key-prefix>/<target-s3-key>/<basename of file-path>
```

## Example

```yaml
- uses: rapidsai/shared-workflows/actions/publish-one-doc@main
  with:
    akamai-access-token: ${{ secrets.AKAMAI_ACCESS_TOKEN }}
    akamai-client-secret: ${{ secrets.AKAMAI_CLIENT_SECRET }}
    akamai-client-token: ${{ secrets.AKAMAI_CLIENT_TOKEN }}
    akamai-emails-to-notify: docs-notify@example.com
    akamai-host: ${{ secrets.AKAMAI_HOST }}
    akamai-request-name: flush-${{ github.event.repository.name }}-versions
    dry-run: false
    file-path: versions.json
    target-aws-access-key-id: ${{ secrets.DOCS_AWS_ACCESS_KEY_ID }}
    target-aws-region: us-east-1
    target-aws-secret-access-key: ${{ secrets.DOCS_AWS_SECRET_ACCESS_KEY }}
    target-s3-bucket: ${{ secrets.DOCS_S3_BUCKET }}
    target-s3-key: ${{ github.event.repository.name }}
```


## Secrets

Use this action with the following secrets:

- `NVIDIA_DOCS_AKAMAI_ACCESS_TOKEN`
- `NVIDIA_DOCS_AKAMAI_CLIENT_TOKEN`
- `NVIDIA_DOCS_AKAMAI_CLIENT_SECRET`
- `NVIDIA_DOCS_AKAMAI_HOST`
- `NVIDIA_DOCS_AWS_ACCESS_KEY_ID`
- `NVIDIA_DOCS_AWS_REGION`
- `NVIDIA_DOCS_AWS_SECRET_ACCESS_KEY`
- `NVIDIA_DOCS_S3_BUCKET`

The `AKAMAI` secrets are for a service account associated with NVIDIA's Akamai CDN. The service account must be able to request a flush of the docs.nvidia.com property.

The `AWS` and `S3` secrets are for the NVIDIA docs team's production account.
