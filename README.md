# shared-action-workflows

This repo contains reusable workflows and custom actions for RAPIDS GitHub Actions CI.

## Reusable workflows

TBD

## Custom actions

[Per the docs](https://docs.github.com/en/actions/creating-actions/about-custom-actions#choosing-a-location-for-your-action), if we want an action to be used by the public, we should publish it to its own repository.

In the beginning, our custom actions will be for internal use, so they can go in this repo. If we choose to release it the public, we can break them out case by case.

Available actions:
| Action name | Description | Example usage |
|-------------|---------|---------|
| cibuildwheel | Variant of the [pypa/cibuildwheel](https://github.com/pypa/cibuildwheel) action with opinionated RAPIDS settings | [RMM](https://github.com/rapidsai/rmm/pull/1070) |
| citestwheel | Custom action for installing and smoke-testing wheels in clean containers | [RMM](https://github.com/rapidsai/rmm/pull/1070) |
