#!/usr/bin/env python3
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

"""Bind a caller-supplied image preflight to every C++ build matrix row.

This is a consistency check for explicit reusable-workflow inputs. It does not
authenticate the caller's train or preflight document, authorize registry
access, or provide credentials for the runner's pre-job image pull.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

# JSON arrives through workflow-call strings and job outputs, so keep every
# transported JSON value below the runner's per-environment-string ceiling.
MAX_TRAIN_BYTES = 120 * 1024
MAX_PREFLIGHT_BYTES = 120 * 1024
MAX_MATRIX_BYTES = 120 * 1024
MAX_ROWS = 256
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_LINUX = re.compile(r"[a-z0-9][a-z0-9.]*\Z")


class CandidateImageMatrixError(ValueError):
    """Explicit candidate image inputs do not bind to this workflow matrix."""


def prepare_matrix(
    matrix: Mapping[str, Any],
    train: Mapping[str, Any],
    preflight: Mapping[str, Any],
    *,
    expected_train_sha256: str,
    expected_preflight_sha256: str,
    consumer_id: str,
    repository: str,
    source_sha: str,
    attach: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Return the matrix with a digest-pinned image on each exact row."""
    if not _SHA256.fullmatch(expected_train_sha256) or train.get("train_sha256") != expected_train_sha256:
        raise CandidateImageMatrixError("release train does not match its independently supplied SHA-256")
    if not _SHA256.fullmatch(expected_preflight_sha256) or preflight.get("preflight_sha256") != expected_preflight_sha256:
        raise CandidateImageMatrixError("image preflight does not match its independently supplied SHA-256")
    if not _SHA1.fullmatch(source_sha):
        raise CandidateImageMatrixError("candidate source SHA must be a full commit SHA")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise CandidateImageMatrixError("candidate repository must be owner/name")
    unit_id = f"conda:{repository.rsplit('/', maxsplit=1)[1]}"
    include = matrix.get("include")
    if not isinstance(include, list) or not include or len(include) > MAX_ROWS:
        raise CandidateImageMatrixError("computed build matrix must contain 1 to 512 rows")
    if set(matrix) != {"include"}:
        raise CandidateImageMatrixError("computed build matrix contains unsupported top-level fields")

    result = []
    seen_rows: set[bytes] = set()
    for row in include:
        if not isinstance(row, Mapping):
            raise CandidateImageMatrixError("computed matrix row must be an object")
        if "candidate_image_reference" in row:
            raise CandidateImageMatrixError("computed matrix unexpectedly contains a candidate image reference")
        arch = _required_text(row, "ARCH")
        cuda = _required_text(row, "CUDA_VER")
        python = _required_text(row, "PY_VER")
        linux = _required_text(row, "LINUX_VER")
        if not _LINUX.fullmatch(linux):
            raise CandidateImageMatrixError("matrix Linux version is malformed")
        canonical_row = _canonical(dict(row))
        if canonical_row in seen_rows:
            raise CandidateImageMatrixError("computed build matrix contains an exact duplicate row")
        seen_rows.add(canonical_row)
        try:
            bound = attach(
                {},
                train,
                preflight,
                consumer_id=consumer_id,
                runtime_image_reference=_digest_reference_for_row(
                    preflight,
                    release_version=str(train.get("release_version", "")),
                    consumer_id=consumer_id,
                    arch=arch,
                    cuda_version=cuda,
                    python_version=python,
                    source_sha=source_sha,
                    unit_id=unit_id,
                ),
                unit_id=unit_id,
                family="conda",
                repository=repository.rsplit("/", maxsplit=1)[1],
                source_sha=source_sha,
                arch=arch,
                cuda_version=cuda,
                python_version=python,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CandidateImageMatrixError(f"candidate image preflight does not bind to matrix row: {error}") from error
        image = bound.get("ci_image_preflight", {}).get("runtime_image", {})
        identity = image.get("identity") if isinstance(image, Mapping) else None
        if not isinstance(identity, Mapping):
            raise CandidateImageMatrixError("validated image binding has no frozen identity")
        build = identity.get("build")
        parts = _image_build_parts(build)
        if parts is None or parts["linux"] != linux:
            raise CandidateImageMatrixError("frozen image Linux identity does not match the matrix row")
        result.append({**dict(row), "candidate_image_reference": image["reference"]})
    prepared = {"include": result}
    if len(_canonical(prepared)) > MAX_MATRIX_BYTES:
        raise CandidateImageMatrixError(f"prepared matrix exceeds {MAX_MATRIX_BYTES} bytes")
    return prepared


def _digest_reference_for_row(
    preflight: Mapping[str, Any],
    *,
    release_version: str,
    consumer_id: str,
    arch: str,
    cuda_version: str,
    python_version: str,
    source_sha: str,
    unit_id: str,
) -> str:
    """Select a candidate reference by frozen matrix coordinates, never by tag alone."""
    consumers = preflight.get("consumers")
    if not isinstance(consumers, list):
        raise CandidateImageMatrixError("preflight consumers must be a list")
    matches = [
        item
        for item in consumers
        if isinstance(item, Mapping)
        and item.get("consumer_id") == consumer_id
        and item.get("release_unit_id") == unit_id
        and item.get("source_sha") == source_sha
    ]
    if len(matches) != 1:
        raise CandidateImageMatrixError("preflight must contain exactly one matching consumer/source/unit")
    images = matches[0].get("images")
    if not isinstance(images, list):
        raise CandidateImageMatrixError("preflight consumer images must be a list")
    selected = []
    for item in images:
        if not isinstance(item, Mapping):
            continue
        identity = item.get("identity")
        if not isinstance(identity, Mapping):
            continue
        parsed = _image_build_parts(identity.get("build"))
        if (
            identity.get("ecosystem") == "container"
            and identity.get("version") == release_version
            and parsed is not None
            and parsed["arch"] == _arch(arch)
            and parsed["cuda"] == cuda_version
            and parsed["python"] == python_version
        ):
            manifest = item.get("runnable_manifest_sha256")
            destination = item.get("destination_reference")
            if not isinstance(manifest, str) or not _SHA256.fullmatch(manifest):
                raise CandidateImageMatrixError("preflight runnable manifest digest is invalid")
            if not isinstance(destination, str) or "@" in destination or ":" not in destination:
                raise CandidateImageMatrixError("preflight destination must be an explicit tagged reference")
            repository, _, _tag = destination.rpartition(":")
            if not repository:
                raise CandidateImageMatrixError("preflight destination repository is empty")
            selected.append(f"{repository}@sha256:{manifest}")
    if len(selected) != 1:
        raise CandidateImageMatrixError("preflight must select exactly one image for each matrix row")
    return selected[0]


def _image_build_parts(build: Any) -> dict[str, str] | None:
    if not isinstance(build, str):
        return None
    match = re.fullmatch(
        r"(?P<version>\d+\.\d+)-cuda(?P<cuda>\d+\.\d+(?:\.\d+)?)-"
        r"(?P<linux>[a-z0-9][a-z0-9.]*)-py(?P<python>\d+\.\d+)-(?P<arch>amd64|arm64)",
        build,
    )
    return match.groupdict() if match else None


def _arch(value: str) -> str:
    if value in {"amd64", "x86_64"}:
        return "amd64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    raise CandidateImageMatrixError("matrix architecture is unsupported")


def _required_text(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value:
        raise CandidateImageMatrixError(f"matrix row requires nonempty {name}")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CandidateImageMatrixError(f"JSON repeats key {key!r}")
        result[key] = value
    return result


def _read_json(path: Path, max_bytes: int, label: str) -> tuple[Any, bytes]:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > max_bytes:
            raise CandidateImageMatrixError(f"{label} must contain 1 to {max_bytes} bytes")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except CandidateImageMatrixError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CandidateImageMatrixError(f"{label} must be valid UTF-8 JSON") from error
    if not isinstance(value, Mapping):
        raise CandidateImageMatrixError(f"{label} must be a JSON object")
    return value, raw


def _load_attach(shared_actions: Path) -> Callable[..., dict[str, Any]]:
    script = shared_actions / "release-candidate-dependencies" / "attach_image_preflight.py"
    if not script.is_file():
        raise CandidateImageMatrixError("pinned shared-actions image binding helper is missing")
    spec = importlib.util.spec_from_file_location("attach_image_preflight", script)
    if spec is None or spec.loader is None:
        raise CandidateImageMatrixError("unable to load pinned shared-actions image binding helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.attach_image_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--shared-actions", type=Path, required=True)
    parser.add_argument("--expected-train-sha256", required=True)
    parser.add_argument("--expected-preflight-sha256", required=True)
    parser.add_argument("--consumer-id", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    try:
        train, _ = _read_json(args.train, MAX_TRAIN_BYTES, "release train")
        preflight, preflight_raw = _read_json(args.preflight, MAX_PREFLIGHT_BYTES, "image preflight")
        matrix, _ = _read_json(args.matrix, MAX_MATRIX_BYTES, "build matrix")
        result = prepare_matrix(
            matrix,
            train,
            preflight,
            expected_train_sha256=args.expected_train_sha256,
            expected_preflight_sha256=args.expected_preflight_sha256,
            consumer_id=args.consumer_id,
            repository=args.repository,
            source_sha=args.source_sha,
            attach=_load_attach(args.shared_actions),
        )
        output = os.environ.get("GITHUB_OUTPUT")
        if not output:
            raise CandidateImageMatrixError("GITHUB_OUTPUT is required")
        matrix_json = json.dumps(result, separators=(",", ":"))
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"matrix={matrix_json}\n")
            stream.write(f"preflight-document-sha256={hashlib.sha256(preflight_raw).hexdigest()}\n")
    except (CandidateImageMatrixError, OSError, ValueError) as error:
        print(f"candidate image preflight rejected: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
