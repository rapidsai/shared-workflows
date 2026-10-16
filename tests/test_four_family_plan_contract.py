# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Hash, immutability, and failure contracts for the local four-family plan."""

import hashlib
import importlib.util
from pathlib import Path
import subprocess  # nosec B404 -- only a disposable local Git fixture is created.

import pytest


RUN_LOCAL = Path(__file__).parents[1] / "ci/publication-rehearsal/run_local.py"


def _load_run_local():
    spec = importlib.util.spec_from_file_location("four_family_run_local", RUN_LOCAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _private_directory(path):
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def test_prepare_emits_the_exact_hash_bound_four_family_plan(tmp_path):
    run_local = _load_run_local()
    root = _private_directory(tmp_path / "plan")

    digest = run_local.prepare(root)
    plan = run_local.validate(root, digest)

    assert digest == hashlib.sha256(run_local.canonical(plan)).hexdigest()
    assert plan["families"] == ["conda", "python-kitmaker", "maven", "oci"]
    assert set(plan["inputs"]) == {
        "conda/conda-fixture-26.12.00-h0_0.conda",
        "python/fixture_one-26.12.0-py3-none-any.whl",
        "oci/input.tar.gz",
        *{
            "maven/signed/ai/rapids/example/26.12.0/example-26.12.0" + suffix
            for suffix in (
                ".pom",
                ".pom.asc",
                ".jar",
                ".jar.asc",
                "-sources.jar",
                "-sources.jar.asc",
                "-javadoc.jar",
                "-javadoc.jar.asc",
            )
        },
    }
    assert set(plan["clients"]) == set(run_local.CLIENTS)
    for relative, expected in plan["inputs"].items():
        path = root / relative
        assert path.stat().st_mode & 0o777 == 0o400
        assert run_local.sha(path) == expected
    assert (root / "plan.json").stat().st_mode & 0o777 == 0o400


def test_validate_rejects_wrong_plan_digest_and_does_not_complete_run(
    tmp_path, monkeypatch
):
    run_local = _load_run_local()
    root = _private_directory(tmp_path / "plan")
    digest = run_local.prepare(root)

    with pytest.raises(run_local.RehearsalError, match="caller-supplied digest"):
        run_local.validate(root, "0" * 64)

    monkeypatch.setattr(
        run_local, "verify_kitmaker", lambda _checkout: tmp_path / "sdk"
    )

    def fail_first_family(_root, _plan):
        raise run_local.RehearsalError("fixture family failed")

    monkeypatch.setattr(run_local, "conda_run", fail_first_family)
    with pytest.raises(run_local.RehearsalError, match="fixture family failed"):
        run_local.run(root, digest, tmp_path / "sdk")
    assert (root / "results").is_dir()
    assert not (root / "completion.json").exists()


def test_validate_rejects_changed_input_bytes(tmp_path):
    run_local = _load_run_local()
    root = _private_directory(tmp_path / "plan")
    digest = run_local.prepare(root)
    relative = next(iter(run_local.validate(root, digest)["inputs"]))
    changed = root / relative
    changed.chmod(0o600)
    changed.write_bytes(changed.read_bytes() + b"tampered")
    changed.chmod(0o400)

    with pytest.raises(
        run_local.RehearsalError, match="immutable rehearsal input changed"
    ):
        run_local.validate(root, digest)


def test_validate_rejects_writable_input_mode(tmp_path):
    run_local = _load_run_local()
    root = _private_directory(tmp_path / "plan")
    digest = run_local.prepare(root)
    relative = next(iter(run_local.validate(root, digest)["inputs"]))
    (root / relative).chmod(0o600)

    with pytest.raises(
        run_local.RehearsalError, match="immutable rehearsal input changed"
    ):
        run_local.validate(root, digest)


def test_validate_rejects_changed_client_source(tmp_path, monkeypatch):
    run_local = _load_run_local()
    client_source = tmp_path / "fixture-client.py"
    client_source.write_text("original source\n", encoding="utf-8")
    monkeypatch.setattr(run_local, "CLIENTS", (str(client_source),))
    root = _private_directory(tmp_path / "plan")
    digest = run_local.prepare(root)
    client_source.write_text("changed source\n", encoding="utf-8")

    with pytest.raises(run_local.RehearsalError, match="local client source changed"):
        run_local.validate(root, digest)


def test_validate_rejects_incomplete_family_input_set_even_with_recomputed_plan_hash(
    tmp_path,
):
    run_local = _load_run_local()
    root = _private_directory(tmp_path / "plan")
    digest = run_local.prepare(root)
    plan_path = root / "plan.json"
    plan = run_local.validate(root, digest)
    plan["inputs"].pop("oci/input.tar.gz")
    plan_path.chmod(0o600)
    plan_path.write_bytes(run_local.canonical(plan))
    plan_path.chmod(0o400)
    tampered_digest = run_local.sha(plan_path)

    with pytest.raises(run_local.RehearsalError, match="exact four-family input set"):
        run_local.validate(root, tampered_digest)


@pytest.mark.parametrize("mode", [0o750, 0o755])
def test_validate_rejects_nonprivate_plan_root(tmp_path, mode):
    run_local = _load_run_local()
    root = _private_directory(tmp_path / "plan")
    digest = run_local.prepare(root)
    root.chmod(mode)

    with pytest.raises(run_local.RehearsalError, match="mode0700"):
        run_local.validate(root, digest)


def test_validate_rejects_symlinked_plan_and_input(tmp_path):
    run_local = _load_run_local()
    root = _private_directory(tmp_path / "plan")
    digest = run_local.prepare(root)
    plan_path = root / "plan.json"
    saved_plan = root / "plan.saved"
    plan_path.rename(saved_plan)
    plan_path.symlink_to(saved_plan)
    with pytest.raises(run_local.RehearsalError, match="caller-supplied digest"):
        run_local.validate(root, digest)

    plan_path.unlink()
    saved_plan.rename(plan_path)
    relative = next(iter(run_local.validate(root, digest)["inputs"]))
    input_path = root / relative
    saved_input = input_path.with_suffix(input_path.suffix + ".saved")
    input_path.rename(saved_input)
    input_path.symlink_to(saved_input)
    with pytest.raises(
        run_local.RehearsalError, match="immutable rehearsal input changed"
    ):
        run_local.validate(root, digest)


def test_write_once_does_not_follow_or_replace_symlink(tmp_path):
    run_local = _load_run_local()
    target = tmp_path / "existing.json"
    target_data = tmp_path / "target.json"
    target_data.write_text("unchanged\n", encoding="utf-8")
    target.symlink_to(target_data)

    with pytest.raises(FileExistsError):
        run_local.write_once(target, {"replacement": True})
    assert target.is_symlink()
    assert target_data.read_text(encoding="utf-8") == "unchanged\n"


def test_verify_kitmaker_rejects_ignored_untracked_publisher_files(
    tmp_path, monkeypatch
):
    run_local = _load_run_local()
    checkout = tmp_path / "kitmaker"
    publisher = checkout / "k2-publisher"
    publisher.mkdir(parents=True)
    (publisher / "local_rehearsal.py").write_text(
        "# reviewed fixture\n", encoding="utf-8"
    )
    (checkout / ".gitignore").write_text("k2-publisher/ignored.py\n", encoding="utf-8")

    def git(*args):
        return subprocess.run(  # nosec B603, B607 -- fixed local Git arguments, never a network operation.
            ["git", "-C", str(checkout), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "--quiet")
    git("config", "user.name", "Contract Test")
    git("config", "user.email", "contract-test@example.invalid")
    git("add", ".gitignore", "k2-publisher/local_rehearsal.py")
    git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture")
    monkeypatch.setattr(run_local, "KITMAKER_REVISION", git("rev-parse", "HEAD"))

    assert run_local.verify_kitmaker(checkout) == checkout.resolve()
    (publisher / "ignored.py").write_text("unreviewed code\n", encoding="utf-8")
    assert git("check-ignore", "k2-publisher/ignored.py")

    with pytest.raises(
        run_local.RehearsalError, match="clean reviewed publisher files"
    ):
        run_local.verify_kitmaker(checkout)
