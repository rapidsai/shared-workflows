# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

"""Tests for opt-in, pre-start CI-image matrix binding."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT_PATH = ROOT / "ci" / "prepare_candidate_image_matrix.py"
spec = importlib.util.spec_from_file_location("prepare_candidate_image_matrix", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
MATRIX_HELPER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MATRIX_HELPER)

SHARED_ACTIONS_SHA = "aa27f344e33b176945146e5a4218a182d72e1688"
SHARED_ACTIONS_ROOT = os.environ.get("SHARED_ACTIONS_IMAGE_INPUT_CHECKOUT")


def _actual_contract():
    if not SHARED_ACTIONS_ROOT:
        pytest.skip("set SHARED_ACTIONS_IMAGE_INPUT_CHECKOUT to the reviewed shared-actions checkout")
    actions_root = Path(SHARED_ACTIONS_ROOT)
    actual_sha = subprocess.check_output(["git", "-C", str(actions_root), "rev-parse", "HEAD"], text=True).strip()
    status = subprocess.check_output(["git", "-C", str(actions_root), "status", "--porcelain"], text=True)
    assert actual_sha == SHARED_ACTIONS_SHA
    assert not status, "the reviewed shared-actions checkout must have no tracked or untracked changes"
    script = actions_root / "release-candidate-dependencies" / "attach_image_preflight.py"
    action_spec = importlib.util.spec_from_file_location("attach_image_preflight", script)
    assert action_spec is not None and action_spec.loader is not None
    action_module = importlib.util.module_from_spec(action_spec)
    sys.modules[action_spec.name] = action_module
    action_spec.loader.exec_module(action_module)
    fixture_path = actions_root / "tests" / "test_image_preflight_build_input.py"
    fixture_spec = importlib.util.spec_from_file_location("image_preflight_fixture", fixture_path)
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

    with pytest.raises(MATRIX_HELPER.CandidateImageMatrixError, match="Linux identity"):
        _prepare(train, preflight, attach, {"include": [_row(LINUX_VER="ubuntu22.04")]})

    with pytest.raises(MATRIX_HELPER.CandidateImageMatrixError, match="duplicate row"):
        _prepare(train, preflight, attach, {"include": [_row(), _row()]})
