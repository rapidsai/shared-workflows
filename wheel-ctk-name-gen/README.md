# wheel-ctk-name-gen

This composite action is intended to be used in pip wheel workflows, to massage a CTK version 'AA.B.C' into the following:
* `RAPIDS_PY_WHEEL_CUDA_SUFFIX=-cuAA`
* `RAPIDS_PY_WHEEL_NAME=${{ inputs.package-name }}+

It will take the input repo/branch/date/sha and pass it through if they are set (by an external repo) or use `github.` variables if they are unset (running from the source repo itself).
