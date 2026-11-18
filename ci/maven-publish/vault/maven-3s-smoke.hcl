# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Temporary cuDF identity selected for the signing-only smoke test.
path "services/libcudf-static/ssa/prod/issue/creds" {
  capabilities = ["read"]
}
path "kv2/rapids-team/data/rapids/gitlab/gitlab-master-rapids-svc-jenkins-pat" {
  capabilities = ["read"]
}
