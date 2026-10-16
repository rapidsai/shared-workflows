# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

"""Tests for opt-in, pre-start CI-image matrix binding."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess  # nosec B404 - fixed git queries inspect a caller-configured source checkout only.
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT_PATH = ROOT / "ci" / "prepare_candidate_image_matrix.py"
spec = importlib.util.spec_from_file_location(
    "prepare_candidate_image_matrix", SCRIPT_PATH
)
assert spec is not None and spec.loader is not None
MATRIX_HELPER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MATRIX_HELPER)

SHARED_ACTIONS_SHA = "aa27f344e33b176945146e5a4218a182d72e1688"
SHARED_ACTIONS_ROOT = os.environ.get("SHARED_ACTIONS_IMAGE_INPUT_CHECKOUT")


def _actual_contract():
    if not SHARED_ACTIONS_ROOT:
        pytest.skip(
            "set SHARED_ACTIONS_IMAGE_INPUT_CHECKOUT to the reviewed shared-actions checkout"
        )
    actions_root = Path(SHARED_ACTIONS_ROOT)
    git = shutil.which("git")
    assert git is not None
    actual_sha = subprocess.check_output(
        [git, "-C", str(actions_root), "rev-parse", "HEAD"], text=True
    ).strip()  # nosec B603 - argv is fixed and shell execution is disabled.
    status = subprocess.check_output(
        [git, "-C", str(actions_root), "status", "--porcelain"], text=True
    )  # nosec B603 - argv is fixed and shell execution is disabled.
    assert actual_sha == SHARED_ACTIONS_SHA
    assert not status, (
        "the reviewed shared-actions checkout must have no tracked or untracked changes"
    )
    script = (
        actions_root / "release-candidate-dependencies" / "attach_image_preflight.py"
    )
    action_spec = importlib.util.spec_from_file_location(
        "attach_image_preflight", script
    )
    assert action_spec is not None and action_spec.loader is not None
    action_module = importlib.util.module_from_spec(action_spec)
    sys.modules[action_spec.name] = action_module
    action_spec.loader.exec_module(action_module)
    fixture_path = actions_root / "tests" / "test_image_preflight_build_input.py"
    fixture_spec = importlib.util.spec_from_file_location(
        "image_preflight_fixture", fixture_path
    )
    assert fixture_spec is not None and fixture_spec.loader is not None
    fixture_module = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture_module)
    return action_module.attach_image_preflight, fixture_module._fixture


def _row(**overrides):
    return {
        "ARCH": "amd64",
        "CUDA_VER": "12.9.2",
        "PY_VER": "3.12",
        "LINUX_VER": "rockylinux8",
        **overrides,
    }


def _fake_attach(
    _base,
    train,
    preflight,
    *,
    runtime_image_reference,
    arch,
    cuda_version,
    python_version,
    **_kwargs,
):
    """Keep helper unit tests independent of a sibling shared-actions checkout."""
    consumer = preflight["consumers"][0]
    for image in consumer["images"]:
        identity = image["identity"]
        build = MATRIX_HELPER._image_build_parts(identity["build"])
        if (
            identity["version"] == train["release_version"]
            and build["arch"] == MATRIX_HELPER._arch(arch)
            and build["cuda"] == cuda_version
            and build["python"] == python_version
            and image["destination_reference"].rsplit(":", maxsplit=1)[0]
            + "@sha256:"
            + image["runnable_manifest_sha256"]
            == runtime_image_reference
        ):
            return {
                "ci_image_preflight": {
                    "runtime_image": {
                        "identity": identity,
                        "reference": runtime_image_reference,
                    }
                }
            }
    raise ValueError("no exact fixture image binding")


def _minimal_documents():
    import hashlib
    import json

    source_sha = "a" * 40
    identity = {
        "ecosystem": "container",
        "name": "docker.io/rapidsai/ci-conda",
        "version": "26.12",
        "build": "26.12-cuda12.9.2-rockylinux8-py3.12-amd64",
        "platform": "linux/amd64",
    }
    image = {
        "identity": identity,
        "destination_reference": "registry.example/private/ci-conda:26.12-cuda12.9.2-rockylinux8-py3.12-amd64",
        "runnable_manifest_sha256": "b" * 64,
    }
    train = {
        "release_version": "26.12",
        "release_units": [
            {
                "id": "conda:example",
                "artifact_family": "conda",
                "repository": "example",
                "source_revision": source_sha,
            }
        ],
        "source_revisions": {"example": source_sha},
        "artifact_expectations": {"container:ci-images": [identity]},
    }

    def canonical_sha(value):
        return hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()

    train["train_sha256"] = canonical_sha(train)
    extension = {
        "schema_version": 1,
        "train_sha256": train["train_sha256"],
        "request_sha256": "c" * 64,
        "consumption_receipt_sha256": "d" * 64,
        "consumer_id": "example-test",
        "release_unit_id": "conda:example",
        "source_sha": source_sha,
        "images": [image],
    }
    extension["sha256"] = canonical_sha(extension)
    consumer = {
        "consumer_id": "example-test",
        "release_unit_id": "conda:example",
        "source_sha": source_sha,
        "images": [image],
        "build_input_extension": extension,
    }
    preflight = {
        "schema_version": 1,
        "producer": "release-platform-ci-image-preflight",
        "train_sha256": train["train_sha256"],
        "request_sha256": extension["request_sha256"],
        "consumption_receipt_sha256": extension["consumption_receipt_sha256"],
        "selected_consumer_unit_ids": ["conda:example"],
        "consumers": [consumer],
    }
    preflight["preflight_sha256"] = canonical_sha(preflight)
    return train, preflight


def test_helper_maps_a_valid_matrix_row_to_immutable_reference():
    train, preflight = _minimal_documents()

    result = _prepare(train, preflight, _fake_attach)

    assert result["include"][0]["candidate_image_reference"] == (
        "registry.example/private/ci-conda@sha256:" + "b" * 64
    )


def test_invalid_preflight_binding_fails_before_attachment():
    train, preflight = _minimal_documents()

    with pytest.raises(
        MATRIX_HELPER.CandidateImageMatrixError, match="preflight does not match"
    ):
        _prepare(
            train,
            preflight,
            lambda *_args, **_kwargs: pytest.fail("attach must not run"),
            expected_preflight_sha256="f" * 64,
        )


def test_rejects_build_identity_version_inconsistent_with_frozen_version():
    train, preflight = _minimal_documents()
    preflight["consumers"][0]["images"][0]["identity"]["build"] = (
        "27.1-cuda12.9.2-rockylinux8-py3.12-amd64"
    )
    preflight.pop("preflight_sha256")
    preflight["preflight_sha256"] = hashlib.sha256(
        MATRIX_HELPER._canonical(preflight)
    ).hexdigest()

    with pytest.raises(
        MATRIX_HELPER.CandidateImageMatrixError, match="exactly one image"
    ):
        _prepare(train, preflight, _fake_attach)


def _cli_arguments(tmp_path, train, preflight, matrix):
    train_file = tmp_path / "train.json"
    preflight_file = tmp_path / "preflight.json"
    matrix_file = tmp_path / "matrix.json"
    for path, value in (
        (train_file, train),
        (preflight_file, preflight),
        (matrix_file, matrix),
    ):
        path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    return [
        str(SCRIPT_PATH),
        "--train",
        str(train_file),
        "--preflight",
        str(preflight_file),
        "--matrix",
        str(matrix_file),
        "--shared-actions",
        str(SHARED_ACTIONS_ROOT),
        "--expected-train-sha256",
        train["train_sha256"],
        "--expected-preflight-sha256",
        preflight["preflight_sha256"],
        "--consumer-id",
        "example-test",
        "--repository",
        "rapidsai/example",
        "--source-sha",
        "a" * 40,
    ]


def test_cli_emits_digest_matrix_and_exact_preflight_document_sha(
    tmp_path, monkeypatch
):
    attach, fixture = _actual_contract()
    train, preflight, _, image = fixture()
    build = MATRIX_HELPER._image_build_parts(image["identity"]["build"])
    matrix = {
        "include": [
            {
                "ARCH": build["arch"],
                "CUDA_VER": build["cuda"],
                "PY_VER": build["python"],
                "LINUX_VER": build["linux"],
            }
        ]
    }
    output = tmp_path / "github-output"
    output.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(sys, "argv", _cli_arguments(tmp_path, train, preflight, matrix))

    assert MATRIX_HELPER.main() == 0

    lines = output.read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", maxsplit=1) for line in lines)
    emitted = json.loads(values["matrix"])
    assert emitted["include"][0]["candidate_image_reference"].endswith(
        "@sha256:" + image["runnable_manifest_sha256"]
    )
    assert (
        values["preflight-document-sha256"]
        == hashlib.sha256(
            json.dumps(preflight, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )


def test_cli_rejects_tampered_preflight_without_emitting_outputs(tmp_path, monkeypatch):
    _attach, fixture = _actual_contract()
    train, preflight, _, image = fixture()
    image["runnable_manifest_sha256"] = "f" * 64
    matrix = {"include": [_row()]}
    output = tmp_path / "github-output"
    output.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(sys, "argv", _cli_arguments(tmp_path, train, preflight, matrix))

    assert MATRIX_HELPER.main() == 2
    assert output.read_text(encoding="utf-8") == ""


def test_prepared_output_size_is_bounded():
    train, preflight = _minimal_documents()
    row = _row()
    row["caller_metadata"] = "x" * (MATRIX_HELPER.MAX_MATRIX_BYTES - 200)
    matrix = {"include": [row]}
    assert len(MATRIX_HELPER._canonical(matrix)) <= MATRIX_HELPER.MAX_MATRIX_BYTES

    with pytest.raises(
        MATRIX_HELPER.CandidateImageMatrixError, match="prepared matrix exceeds"
    ):
        _prepare(train, preflight, _fake_attach, matrix=matrix)


def test_cli_rejects_oversized_preflight_before_json_parsing(tmp_path, monkeypatch):
    train, preflight = _minimal_documents()
    args = _cli_arguments(tmp_path, train, preflight, {"include": [_row()]})
    preflight_path = Path(args[args.index("--preflight") + 1])
    preflight_path.write_bytes(b" " * (MATRIX_HELPER.MAX_PREFLIGHT_BYTES + 1))
    output = tmp_path / "github-output"
    output.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(sys, "argv", args)

    assert MATRIX_HELPER.main() == 2
    assert output.read_text(encoding="utf-8") == ""


def _prepare(train, preflight, attach, matrix=None, **overrides):
    bindings = {
        "expected_train_sha256": train["train_sha256"],
        "expected_preflight_sha256": preflight["preflight_sha256"],
        "consumer_id": "example-test",
        "repository": "rapidsai/example",
        "source_sha": "a" * 40,
        **overrides,
    }
    return MATRIX_HELPER.prepare_matrix(
        matrix or {"include": [_row()]},
        train,
        preflight,
        attach=attach,
        **bindings,
    )


def test_binds_every_matrix_row_to_exact_frozen_digest_image():
    attach, fixture = _actual_contract()
    train, preflight, _, image = fixture()

    result = _prepare(train, preflight, attach)

    expected_repository = image["destination_reference"].rsplit(":", maxsplit=1)[0]
    assert result["include"][0]["candidate_image_reference"] == (
        f"{expected_repository}@sha256:{image['runnable_manifest_sha256']}"
    )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"expected_train_sha256": "f" * 64}, "train does not match"),
        ({"expected_preflight_sha256": "f" * 64}, "preflight does not match"),
        ({"source_sha": "b" * 40}, "does not bind"),
    ],
)
def test_rejects_wrong_independent_binding(override, message):
    attach, fixture = _actual_contract()
    train, preflight, _, _ = fixture()

    with pytest.raises(MATRIX_HELPER.CandidateImageMatrixError, match=message):
        _prepare(train, preflight, attach, **override)


def test_rejects_mismatched_linux_and_duplicate_matrix_rows():
    attach, fixture = _actual_contract()
    train, preflight, _, _ = fixture()

    with pytest.raises(
        MATRIX_HELPER.CandidateImageMatrixError, match="exactly one image"
    ):
        _prepare(train, preflight, attach, {"include": [_row(LINUX_VER="ubuntu22.04")]})

    with pytest.raises(MATRIX_HELPER.CandidateImageMatrixError, match="duplicate row"):
        _prepare(train, preflight, attach, {"include": [_row(), _row()]})
