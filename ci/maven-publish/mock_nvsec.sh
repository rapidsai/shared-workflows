#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Offline test transport: produce real signatures with a disposable test key.
set -euo pipefail
[[ $1 == 3s ]]
operation=$2
shift 2
while (( $# )); do
  case $1 in
    --input_file) input=$2; shift 2 ;;
    --result_dir) result=$2; shift 2 ;;
    --request_id_file) request=$2; shift 2 ;;
    --output_path) status=$2; shift 2 ;;
    --multi_part_upload|--download) shift ;;
    *) shift 2 ;;
  esac
done
case ${operation} in
  submit)
    printf '{"requestId":123}\n' > "${request}"
    gpg --homedir "${MOCK_GPG_HOME}" --batch --yes --armor --detach-sign \
      --output "${result}/signature.asc" "${input}"
    if [[ ${MOCK_BAD_SIGNATURE:-} == true ]]; then
      printf 'corrupt\n' >> "${input}"
    fi
    ;;
  request-info) printf '%s\n' "${MOCK_3S_STATUS:-{\"statusCode\":40,\"statusName\":\"Completed\"}}" > "${status}" ;;
  *) exit 1 ;;
esac
