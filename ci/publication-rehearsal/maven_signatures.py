# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ephemeral fixture signing and isolated verification of Maven readback bytes.

Only the existing signing step is reused on the host. This does not exercise
the production Maven/Docker wrapper, signer identity, access or approval.
"""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404
import tempfile


ROOT = Path(__file__).resolve().parents[2]
PASSPHRASE = "pub01-fixture-passphrase"  # nosec B105


class SignatureError(ValueError):
    """A sanitized signing/verification failure, with no key material or output."""


def command(argv, *, home, signing=False):
    try:
        result = subprocess.run(  # nosec B603
            [str(value) for value in argv],
            env={
                "PATH": os.environ["PATH"],
                "GNUPGHOME": str(home),
                **({"GPG_PASSPHRASE": PASSPHRASE} if signing else {}),
            },
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SignatureError("fixture signature operation could not complete") from None
    if result.returncode:
        raise SignatureError("fixture signature operation rejected")
    return result.stdout


def tool(name):
    executable = shutil.which(name)
    if not executable:
        raise SignatureError("required local signing/verification tool unavailable")
    return executable


@contextmanager
def keyring(*, signing=False):
    # Short socket path for GnuPG agents; works on macOS and Linux without
    # changing HOME or using any operator keyring/runtime directory.
    with tempfile.TemporaryDirectory(
        prefix="pub01-pgp-",
        dir=Path("/tmp").resolve(),  # nosec B108
    ) as directory:
        home = Path(directory)
        try:
            yield home
        finally:
            if signing:
                command(
                    [tool("gpgconf"), "--homedir", home, "--kill", "gpg-agent"],
                    home=home,
                )


def fingerprint(public_key, *, home):
    output = command(
        [
            tool("gpg"),
            "--no-options",
            "--batch",
            "--no-default-keyring",
            "--with-colons",
            "--show-keys",
            public_key,
        ],
        home=home,
    )
    public = [line for line in output.splitlines() if line.startswith("pub:")]
    fingerprints = [
        line.split(":")[9]
        for line in output.splitlines()
        if line.startswith("fpr:")  # codespell:ignore fpr
    ]
    if (
        len(public) != 1
        or len(fingerprints) != 1
        or not re.fullmatch(r"[A-F0-9]{40}", fingerprints[0])
    ):
        raise SignatureError("fixture must contain exactly one primary signing key")
    return fingerprints[0]


def prepare(directory):
    """Sign four existing originals once, export the only planned verification key."""
    directory = Path(directory)
    artifact = directory / "signed/ai/rapids/example/26.12.0"
    public = directory / "signer-public.asc"
    if public.exists() or public.is_symlink():
        raise SignatureError("fixture verification key already exists")
    with keyring(signing=True) as home:
        command(
            [
                tool("gpg"),
                "--no-options",
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                PASSPHRASE,
                "--quick-generate-key",
                "PUB01 Fixture <pub01-signing@example.invalid>",
                "rsa2048",
                "sign",
                "1d",
            ],
            home=home,
        )
        output = command(
            [tool("gpg"), "--no-options", "--batch", "--armor", "--export"], home=home
        )
        public.write_text(output)
        expected = fingerprint(public, home=home)
        command(
            [
                tool("bash"),
                ROOT / "ci/publication-rehearsal/sign_maven_fixture.sh",
                artifact,
                expected,
            ],
            home=home,
            signing=True,
        )
    return expected


def verify(public_key, artifacts):
    """Verify readback artifact/signature files against ONLY the planned public key.

    artifacts maps a coordinate to (artifact path, detached signature path).
    No private key, default trustedkeys, auto-fetch or trust database is used.
    """
    with keyring() as home:
        expected = fingerprint(public_key, home=home)
        trusted = home / "only-fixture-key.gpg"
        command(
            [
                tool("gpg"),
                "--no-options",
                "--batch",
                "--yes",
                "--output",
                trusted,
                "--dearmor",
                public_key,
            ],
            home=home,
        )
        verified = {}
        for coordinate, (artifact, signature) in artifacts.items():
            status = command(
                [
                    tool("gpgv"),
                    "--homedir",
                    home,
                    "--keyring",
                    trusted,
                    "--status-fd",
                    "1",
                    signature,
                    artifact,
                ],
                home=home,
            )
            valid = [
                line.split()[2]
                for line in status.splitlines()
                if line.startswith("[GNUPG:] VALIDSIG ")
            ]
            if valid != [expected]:
                raise SignatureError("readback signature has an unexpected signer")
            verified[coordinate] = {
                "artifact_sha256": hashlib.sha256(
                    Path(artifact).read_bytes()
                ).hexdigest(),
                "signature_sha256": hashlib.sha256(
                    Path(signature).read_bytes()
                ).hexdigest(),
                "primary_signing_fingerprint": expected,
            }
        if not verified:
            raise SignatureError("no readback signatures were verified")
        return {
            "verification": "gpgv-exact-planned-key",
            "primary_signing_fingerprint": expected,
            "public_key_sha256": hashlib.sha256(
                Path(public_key).read_bytes()
            ).hexdigest(),
            "artifacts": verified,
            "gpg_version": command([tool("gpg"), "--version"], home=home).splitlines()[
                0
            ],
            "gpgv_version": command(
                [tool("gpgv"), "--version"], home=home
            ).splitlines()[0],
            "limits": [
                "ephemeral host fixture signer; production signer/wrapper not exercised"
            ],
        }
