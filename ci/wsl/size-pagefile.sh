#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Raise the WSL runner's Windows pagefile maximum so the GPU can be fully allocated while the WSL VM is full.
set -euo pipefail
here="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
mem_total=$(awk '/^MemTotal:/ {print $2 * 1024}' /proc/meminfo)
{
  echo '& {'
  cat "${here}/size_pagefile.ps1"
  echo "} -Mode wsl -WslMemTotalBytes ${mem_total} -Apply"
} | python3 "${here}/winps.py"
