# rapids-github-info

This composite action is intended to be used in workflows that are run from either the source repository (e.g., cudf, rmm) or an external repository (e.g., actions).

It will take the input repo/branch/date/sha and pass it through if they are set (by an external repo) or use `github.` variables if they are unset (running from the source repo itself).
