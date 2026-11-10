#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
sed -n '/# BEGIN BURNDOWN CLASSIFIER/,/# END BURNDOWN CLASSIFIER/p' \
    "$root/.github/workflows/burndown-pr.yaml" | sed 's/^          //' > "$work/classifier.sh"
mkdir "$work/bin"
cat > "$work/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == api && "$2" == repos/rapidsai/rmm/git/commits/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ]]
printf '%s\n' "$MOCK_MESSAGE"
EOF
chmod +x "$work/bin/gh"
export PATH="$work/bin:$PATH"
export CALLER_REPOSITORY=rapidsai/rmm
export MOCK_MESSAGE
MOCK_MESSAGE=$(printf 'Update to 27.02\n\nBurndown-Base: %040d\nBurndown-Release: %040d\n' 1 2)
candidate=$(jq -nc '{
  state:"open", base:{ref:"main",repo:{full_name:"rapidsai/rmm"}},
  head:{ref:"version-update-27.02",sha:("a" * 40),repo:{full_name:"rapidsai/rmm"}},
  labels:[{name:"non-breaking"},{name:"improvement"}],
  body:"This PR reviews the atomic burndown candidate that updates main to version 27.02.\nThe candidate history also contains the release-settings commit and its revert, so review both."
}')

check() {
    local expected=$1 info=$2
    : > "$work/output"
    PR_INFO="$info" GITHUB_OUTPUT="$work/output" bash "$work/classifier.sh"
    if ! grep -Fxq "skip_product_ci=$expected" "$work/output"; then
        echo "Unexpected classification: expected $expected for $info" >&2
        cat "$work/output" >&2
        exit 1
    fi
}

check true "$candidate"
concise=$(jq '.body="This PR updates the repository to version 27.02.\n\nThis is part of the 26.12 release burndown process.\n"' <<< "$candidate")
check true "$concise"
check false "$(jq '.body="This PR updates the repository to version 27.02."' <<< "$candidate")"
check false "$(jq '.head.ref="feature/work"' <<< "$candidate")"
check false "$(jq '.base.ref="release/26.12"' <<< "$candidate")"
check false "$(jq '.head.repo.full_name="someone/rmm"' <<< "$candidate")"
check false "$(jq '.labels=[]' <<< "$candidate")"
check false "$(jq '.labels=[{name:"non-breaking"}]' <<< "$candidate")"
check false "$(jq '.body="Ordinary version update"' <<< "$candidate")"
check false "$(jq '.head.sha="invalid"' <<< "$candidate")"
check false "$(jq '.state="closed"' <<< "$candidate")"
check false '{}'
MOCK_MESSAGE='Ordinary commit without candidate trailers'
check false "$candidate"
MOCK_MESSAGE=$(printf 'Burndown-Base: %040d\nBurndown-Base: %040d\nBurndown-Release: %040d\n' 1 1 2)
check false "$candidate"
echo 'Burndown PR classifier checks passed.'
