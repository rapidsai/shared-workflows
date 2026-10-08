# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

"""Run with python -m unittest discover -s ci -p 'test_compute_matrix.py'."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/actions/prepare-matrix/compute.sh"
MATRICES = yaml.safe_load(
    (ROOT / ".github/actions/prepare-matrix/matrix.yaml").read_text()
)


class ComputeMatrixTests(unittest.TestCase):
    def compute(self, name="wheels-test", **overrides):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            result = subprocess.run(
                ["bash", str(SCRIPT)],
                env={
                    **os.environ,
                    "BUILD_TYPE": "pull-request",
                    "MATRIX_NAME": name,
                    "MATRIX_TYPE": "auto",
                    "MATRIX": json.dumps(MATRICES),
                    "MATRIX_FILTER": ".",
                    "PURE_WHEEL": "false",
                    "PURE_CONDA": "false",
                    "RESOLVED_MATRIX": "",
                    "GITHUB_OUTPUT": str(output),
                    **overrides,
                },
                text=True,
                capture_output=True,
            )
            matrix = (
                json.loads(output.read_text().removeprefix("matrix="))
                if result.returncode == 0 else None
            )
            return result, matrix

    def test_existing_default_callers(self):
        for name, definitions in MATRICES.items():
            for build_type, matrix_type in (
                ("pull-request", "pull-request"),
                ("nightly", "nightly"),
                ("branch", "nightly"),
                ("release-candidate", "nightly"),
            ):
                if matrix_type not in definitions:
                    continue
                with self.subTest(name=name, build_type=build_type):
                    result, matrix = self.compute(name, BUILD_TYPE=build_type)
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    expected = []
                    for entry in definitions[matrix_type]:
                        if entry not in expected:
                            expected.append(entry)
                    self.assertEqual(matrix, {"include": expected})

    def test_snapshot_ignores_changed_definitions(self):
        snapshot = {"include": MATRICES["wheels-test"]["pull-request"]}
        result, matrix = self.compute(
            RESOLVED_MATRIX=json.dumps(snapshot), MATRIX="not JSON"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(matrix, snapshot)

    def test_snapshot_applies_filter(self):
        entries = MATRICES["wheels-test"]["pull-request"]
        result, matrix = self.compute(
            RESOLVED_MATRIX=json.dumps({"include": entries}),
            MATRIX_FILTER='map(select(.ARCH == "arm64"))',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(matrix["include"], [e for e in entries if e["ARCH"] == "arm64"])

    def test_multiple_types_deduplicate(self):
        result, matrix = self.compute(MATRIX_TYPE="pull-request,pull-request")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(matrix["include"], MATRICES["wheels-test"]["pull-request"])

    def test_pure_defaults_match_existing_filters(self):
        cases = (
            ("PURE_WHEEL", "true", 'map(select(.ARCH == "amd64")) | group_by(.CUDA_VER) | map(max_by(.PY_VER | split(".") | map(tonumber)))'),
            ("PURE_CONDA", "true", 'map(select(.ARCH == "amd64")) | sort_by(.CUDA_VER, .PY_VER) | [last]'),
            ("PURE_CONDA", "cuda_major", 'map(select(.ARCH == "amd64")) | group_by(.CUDA_VER|split(".")|map(tonumber)|.[0]) | map(max_by([(.PY_VER|split(".")|map(tonumber)), (.CUDA_VER|split(".")|map(tonumber))]))'),
        )
        for option, value, expression in cases:
            with self.subTest(option=option, value=value):
                default_result, default = self.compute("wheels-build", **{option: value})
                explicit_result, explicit = self.compute("wheels-build", MATRIX_FILTER=expression)
                self.assertEqual(default_result.returncode, 0, default_result.stderr)
                self.assertEqual(explicit_result.returncode, 0, explicit_result.stderr)
                self.assertEqual(default, explicit)

    def test_rejects_invalid_inputs(self):
        cases = [
            {"BUILD_TYPE": "invalid"},
            {"MATRIX_TYPE": "missing"},
            {"MATRIX_FILTER": "map(select(false))"},
            {"MATRIX_FILTER": "{}"},
        ]
        cases.extend({"RESOLVED_MATRIX": snapshot} for snapshot in (
            "not JSON", "[]", '{"include": []}', '{"include": [1]}',
            '{"include": [{}]}', '{"include": [{}], "exclude": []}',
        ))
        for overrides in cases:
            with self.subTest(overrides=overrides):
                result, matrix = self.compute(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(matrix)


if __name__ == "__main__":
    unittest.main()
