# get-pr-info

This composite action is intended to be used in workflows that run on `pull-request/<PR_NUMBER>` branches.

It will extract the pull-request number from the branch name and use it to fetch information about the corresponding pull-request from GitHub's API.

The JSON object returned by this composite action is described here: [https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request).

## Example Usage

```yaml
name: Get PR Info Demo

on:
  push:
    branches:
      - "pull-request/[0-9]+"

jobs:
  main_job:
    runs-on: ubuntu-latest
    steps:
      - id: get-pr-info
        uses: rapidsai/shared-action-workflows/get-pr-info@main
      - run: echo "${RAPIDS_BASE_BRANCH}"
        env:
          RAPIDS_BASE_BRANCH: ${{ fromJSON(steps.get-pr-info.outputs.pr-info).base.ref }}
```
