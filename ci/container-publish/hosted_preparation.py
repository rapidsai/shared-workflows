# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Credential-free destination-policy and exact OCI handoff preparation.

Preparation never contacts a registry. Expected policy/handoff digests and train
identity must come from an independently authenticated controller, not the files.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit

_SPEC = importlib.util.spec_from_file_location(
    "preparation_oci", Path(__file__).with_name("local_rehearsal.py")
)
OCI = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(OCI)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a UTC ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be a UTC ISO timestamp") from error
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return parsed


def validate_destination(policy, descriptor, *, policy_sha256, now, loopback_fixture):
    """Bind a current descriptor to the caller-approved destination policy."""
    policy_fields = {
        "schema_version",
        "origin",
        "namespace",
        "revision",
        "expires_at",
        "private_acl_evidence",
        "transport_boundary_evidence",
        "credential_reference",
    }
    descriptor_fields = {
        "schema_version",
        "policy_sha256",
        "issued_at",
        "expires_at",
        "repository",
        "tag",
        "handoff",
    }
    if not isinstance(policy, dict) or set(policy) != policy_fields:
        raise ValueError("destination policy has unsupported or missing fields")
    if not isinstance(descriptor, dict) or set(descriptor) != descriptor_fields:
        raise ValueError("destination descriptor has unsupported or missing fields")
    if (
        type(policy["schema_version"]) is not int
        or policy["schema_version"] != 1
        or type(descriptor["schema_version"]) is not int
        or descriptor["schema_version"] != 1
    ):
        raise ValueError("unsupported schema version")
    if not isinstance(policy_sha256, str) or not re.fullmatch(
        r"[a-f0-9]{64}", policy_sha256
    ):
        raise ValueError("caller must supply the approved policy digest")
    if digest(policy) != policy_sha256 or descriptor["policy_sha256"] != policy_sha256:
        raise ValueError("destination policy differs from caller-approved digest")
    for field in ("revision", "private_acl_evidence", "transport_boundary_evidence"):
        if not isinstance(policy[field], str) or not re.fullmatch(
            r"[A-Za-z0-9_.:/-]{1,256}", policy[field]
        ):
            raise ValueError("operator evidence reference is missing or invalid")
    reference = policy["credential_reference"]
    if not isinstance(reference, str) or not re.fullmatch(
        r"kv2/rapids-team/rapids/ci/rehearsal/container/[a-z0-9-]+", reference
    ):
        raise ValueError("credential reference must name an isolated rehearsal path")
    origin = policy["origin"]
    if not isinstance(origin, str):
        raise ValueError("registry origin is invalid")
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("registry origin port is invalid") from error
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or origin != f"{parsed.scheme}://{parsed.netloc}"
        or parsed.netloc != parsed.netloc.lower()
    ):
        raise ValueError(
            "registry origin must be canonical and contain no credentials/path"
        )
    if loopback_fixture:
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname != "127.0.0.1"
            or not port
        ):
            raise ValueError("fixture origin must be explicit IPv4 loopback")
    elif parsed.scheme != "https" or parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("hosted destination requires an approved HTTPS origin")
    namespace, repository = policy["namespace"], descriptor["repository"]
    pattern = r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    if (
        not isinstance(namespace, str)
        or not re.fullmatch(pattern, namespace)
        or not isinstance(repository, str)
        or not re.fullmatch(pattern, repository)
        or not repository.startswith(namespace + "/")
    ):
        raise ValueError("repository escapes the approved private namespace")
    if not isinstance(descriptor["tag"], str) or not re.fullmatch(
        r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", descriptor["tag"]
    ):
        raise ValueError("destination tag is invalid")
    issued, expires = _time(descriptor["issued_at"]), _time(descriptor["expires_at"])
    if (
        not issued <= now < expires
        or expires - issued > timedelta(hours=24)
        or expires > _time(policy["expires_at"])
    ):
        raise ValueError(
            "destination descriptor is stale, future, or outside policy lifetime"
        )
    return parsed.netloc + "/" + repository + ":" + descriptor["tag"]


def prepare(
    policy,
    descriptor,
    archive,
    output,
    *,
    policy_sha256,
    handoff_sha256,
    train_sha256,
    identity,
    loopback_fixture=False,
    now=None,
):
    """Seal a retryable handoff in a private directory with mode 0700.

    Never retrieve credentials. Same-user concurrent mutation is outside this
    job-local preparation contract; callers must provide exclusive ownership.
    """
    now = now or datetime.now(timezone.utc)
    target = validate_destination(
        policy,
        descriptor,
        policy_sha256=policy_sha256,
        now=now,
        loopback_fixture=loopback_fixture,
    )
    handoff = descriptor["handoff"]
    if (
        not isinstance(handoff_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", handoff_sha256)
        or digest(handoff) != handoff_sha256
    ):
        raise ValueError("handoff differs from authenticated caller digest")
    OCI.validate_ci_image_import_handoff(handoff, train_sha256, identity)
    archive, output = Path(archive), Path(output)
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("source archive must be a regular non-symlink file")
    if archive.stat().st_size > OCI.MAX_SINGLE_PLATFORM_ARCHIVE_BYTES:
        raise ValueError("source archive exceeds the importer size limit")
    if (
        output.is_symlink()
        or not output.is_dir()
        or output.resolve() != output.absolute()
        or output.stat().st_mode & 0o7777 != 0o700
    ):
        raise ValueError(
            "output must be a canonical non-symlink directory with mode 0700"
        )
    key = digest(descriptor)
    destination = output / key
    expected = {
        "schema_version": 1,
        "state": "prepared-not-published",
        "descriptor_sha256": key,
        "policy_sha256": policy_sha256,
        "handoff_sha256": handoff_sha256,
        "train_sha256": train_sha256,
        "target": target,
        "transport_sha256": handoff["archive"]["transport_sha256"],
        "runnable_manifest_sha256": handoff["archive"]["runnable_manifest_sha256"],
        "loopback_fixture": loopback_fixture,
    }
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=output) as temporary:
        stage = Path(temporary)
        copied = stage / "archive.oci"
        shutil.copyfile(archive, copied)
        inventory = OCI.inspect_single_platform_oci_archive(
            copied, expected["transport_sha256"], stage
        )
        if inventory["platform"] != identity["platform"] or any(
            inventory[field] != value for field, value in handoff["archive"].items()
        ):
            raise ValueError(
                "archive structural identity differs from authenticated handoff"
            )
        files = {
            "policy.json": canonical(policy),
            "descriptor.json": canonical(descriptor),
            "receipt.json": canonical(expected),
        }
        for name, content in files.items():
            (stage / name).write_bytes(content)
        if destination.exists() or destination.is_symlink():
            if (
                destination.is_symlink()
                or not destination.is_dir()
                or {p.name for p in destination.iterdir()} != {*files, "archive.oci"}
            ):
                raise ValueError("existing preparation has unexpected files")
            if destination.stat().st_mode & 0o7777 != 0o700:
                raise ValueError("existing preparation directory is not private")
            for path in destination.iterdir():
                if path.is_symlink() or not path.is_file():
                    raise ValueError("existing preparation contains unsafe files")
                if path.stat().st_mode & 0o7777 != 0o400:
                    raise ValueError("existing preparation file is not sealed")
                if path.name in files and path.read_bytes() != files[path.name]:
                    raise ValueError(
                        "existing preparation conflicts with immutable descriptor"
                    )
            with (destination / "archive.oci").open("rb") as stream:
                if (
                    hashlib.file_digest(stream, "sha256").hexdigest()
                    != expected["transport_sha256"]
                ):
                    raise ValueError("existing prepared archive bytes changed")
        else:
            # No replace-on-retry: directory rename refuses an existing nonempty receipt.
            for path in stage.iterdir():
                path.chmod(0o400)
            stage.rename(destination)
            stage.mkdir()  # TemporaryDirectory cleanup owns only its original path.
    return expected, destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "policy",
        "descriptor",
        "archive",
        "output",
        "policy-sha256",
        "handoff-sha256",
        "train-sha256",
        "identity",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--loopback-fixture", action="store_true")
    args = parser.parse_args()
    receipt, _destination = prepare(
        json.loads(Path(args.policy).read_text()),
        json.loads(Path(args.descriptor).read_text()),
        args.archive,
        args.output,
        policy_sha256=args.policy_sha256,
        handoff_sha256=args.handoff_sha256,
        train_sha256=args.train_sha256,
        identity=json.loads(Path(args.identity).read_text()),
        loopback_fixture=args.loopback_fixture,
    )
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
