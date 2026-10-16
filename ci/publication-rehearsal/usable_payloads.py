# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real dependency-free Java and Linux payloads for the local publishing demo."""

import gzip
import hashlib
import importlib.util
import io
import json
import re
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import uuid

SPEC = importlib.util.spec_from_file_location(
    "usable_driver", Path(__file__).with_name("run_local.py")
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)
MARKER = "rapids-dummy-container-26.12"
MAVEN_MARKER = "rapids-dummy-maven-26.12.0"
PLUGIN = "org.apache.maven.plugins:maven-dependency-plugin:3.8.1"


def docker_client(directory):
    host = DRIVER.execute(
        [
            "docker",
            "context",
            "inspect",
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ]
    ).strip()
    if not host.startswith("unix://"):
        raise DRIVER.RehearsalError("demo requires a local Unix Docker daemon")
    config = directory / "docker-config"
    config.mkdir(mode=0o700)
    return ["docker", "--host", host, "--config", str(config)]


def java_home():
    configured = os.environ.get("JAVA_HOME")
    candidate = (
        Path(configured)
        if configured
        else Path(shutil.which("mvn")).absolute().parent.parent / "lib/jvm"
    )
    if not all(
        (candidate / "bin" / name).is_file() for name in ("java", "javac", "jar")
    ):
        java = Path(shutil.which("javac")).resolve()
        candidate = java.parent.parent
    if (
        not all(
            (candidate / "bin" / name).is_file() for name in ("java", "javac", "jar")
        )
        or str(candidate) == "/usr"
    ):
        raise DRIVER.RehearsalError(
            "a real JDK is required; use the supplied Pixi environment"
        )
    return candidate.resolve()


def java_environment():
    return {"JAVA_HOME": str(java_home()), "MAVEN_SKIP_RC": "true"}


def java_payload(root):
    source = Path(__file__).with_name("fixtures") / "Main.java"
    with tempfile.TemporaryDirectory(prefix="dummy-java-") as temporary:
        work = Path(temporary)
        classes = work / "classes"
        classes.mkdir()
        DRIVER.execute(
            [java_home() / "bin/javac", "--release", "8", "-d", classes, source]
        )
        artifact = root / "maven/signed/ai/rapids/example/26.12.0/example-26.12.0"
        DRIVER.execute(
            [
                java_home() / "bin/jar",
                "--create",
                "--file",
                str(artifact) + ".jar",
                "-C",
                classes,
                ".",
            ]
        )
        DRIVER.execute(
            [
                java_home() / "bin/jar",
                "--create",
                "--file",
                str(artifact) + "-sources.jar",
                "-C",
                source.parent,
                "Main.java",
            ]
        )
        (work / "README.txt").write_text(
            "Synthetic dependency-free Maven demo payload.\n"
        )
        DRIVER.execute(
            [
                java_home() / "bin/jar",
                "--create",
                "--file",
                str(artifact) + "-javadoc.jar",
                "-C",
                work,
                "README.txt",
            ]
        )
        Path(str(artifact) + ".pom").write_text(
            '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
            "<groupId>ai.rapids</groupId><artifactId>example</artifactId><version>26.12.0</version>"
            "<packaging>jar</packaging></project>\n"
        )


def image_payload(root, image):
    """Copy three ELF runtime files from an immutable local amd64 image ID."""
    with tempfile.TemporaryDirectory(prefix="dummy-image-") as temporary:
        work = Path(temporary)
        client = docker_client(work)
        inspect = json.loads(DRIVER.execute(client + ["image", "inspect", image]))[0]
        if inspect["Architecture"] != "amd64" or inspect["Os"] != "linux":
            raise DRIVER.RehearsalError("payload source image must be linux/amd64")
        identity = inspect["Id"]
        name = "dummy-input-" + uuid.uuid4().hex
        DRIVER.execute(
            client
            + [
                "create",
                "--name",
                name,
                "--platform=linux/amd64",
                identity,
                "/bin/true",
            ]
        )
        files = {}
        try:
            for index, path in enumerate(
                (
                    "/bin/echo",
                    "/lib/x86_64-linux-gnu/libc.so.6",
                    "/lib64/ld-linux-x86-64.so.2",
                )
            ):
                destination = work / str(index)
                DRIVER.execute(client + ["cp", "-L", name + ":" + path, destination])
                files[path.lstrip("/")] = destination.read_bytes()
        finally:
            DRIVER.execute(client + ["rm", name])
        layer_stream = io.BytesIO()
        with tarfile.open(fileobj=layer_stream, mode="w") as layer:
            for path, body in files.items():
                entry = tarfile.TarInfo(path)
                entry.mode, entry.size = 0o755, len(body)
                layer.addfile(entry, io.BytesIO(body))
        layer_bytes = layer_stream.getvalue()
        layer_sha = hashlib.sha256(layer_bytes).hexdigest()
        config = json.dumps(
            {
                "architecture": "amd64",
                "os": "linux",
                "rootfs": {"type": "layers", "diff_ids": ["sha256:" + layer_sha]},
                "config": {"Cmd": ["/bin/echo", MARKER]},
            },
            separators=(",", ":"),
        ).encode()
        config_sha = hashlib.sha256(config).hexdigest()
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + config_sha,
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": "sha256:" + layer_sha,
                    "size": len(layer_bytes),
                }
            ],
        }
        manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode()
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        index = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": manifest["mediaType"],
                    "digest": "sha256:" + manifest_sha,
                    "size": len(manifest_bytes),
                    "platform": {"os": "linux", "architecture": "amd64"},
                }
            ],
        }
        contents = {
            "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
            "index.json": json.dumps(index, separators=(",", ":")).encode(),
            "blobs/sha256/" + config_sha: config,
            "blobs/sha256/" + layer_sha: layer_bytes,
            "blobs/sha256/" + manifest_sha: manifest_bytes,
        }
        with gzip.open(root / "oci/input.tar.gz", "wb") as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for path, body in contents.items():
                    entry = tarfile.TarInfo(path)
                    entry.mode, entry.size = 0o644, len(body)
                    archive.addfile(entry, io.BytesIO(body))
        return {
            "source_image_id": identity,
            "payload_sha256s": {
                name: hashlib.sha256(body).hexdigest() for name, body in files.items()
            },
        }


def settings(path, origin):
    if not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}/repository/", origin):
        raise DRIVER.RehearsalError(
            "Maven consumer requires the owned loopback repository"
        )
    path.write_text(
        "<settings><mirrors><mirror><id>dummy-only</id><mirrorOf>*</mirrorOf><url>"
        + origin
        + "</url></mirror></mirrors><servers><server><id>dummy-only</id><username>fixture-user</username><password>fixture-value</password></server></servers></settings>"
    )
    path.chmod(0o600)


def maven_command(directory, config, repository):
    return [
        "mvn",
        "-B",
        "-ntp",
        "-s",
        config,
        "-gs",
        config,
        "-Dmaven.repo.local=" + str(repository),
        "-Duser.home=" + str(directory),
        PLUGIN + ":",
    ]


def bootstrap_maven(directory):
    directory.mkdir(mode=0o700)
    config = directory / "settings.xml"
    # Read-only tool bootstrap, never a publication endpoint or credential source.
    config.write_text(
        "<settings><mirrors><mirror><id>tool-bootstrap</id><mirrorOf>*</mirrorOf><url>https://repo.maven.apache.org/maven2</url></mirror></mirrors></settings>"
    )
    config.chmod(0o600)
    repository = directory / "repository"
    command = maven_command(directory, config, repository)
    command[-1] += "help"
    DRIVER.execute(command, environment=java_environment(), timeout=240)
    return repository


def resolve_maven(root, origin, cache, expected_sha):
    work = root / "maven/consumer"
    work.mkdir(mode=0o700)
    repository = work / "repository"
    shutil.copytree(cache, repository)
    if (repository / "ai/rapids").exists():
        raise DRIVER.RehearsalError("tool cache must not contain the dummy artifact")
    for path in repository.rglob("_remote.repositories"):
        path.unlink()
    config = work / "settings.xml"
    settings(config, origin + "/repository/")
    command = maven_command(work, config, repository)
    command[-1] += "get"
    command += ["-Dartifact=ai.rapids:example:26.12.0", "-Dtransitive=false"]
    DRIVER.execute(command, environment=java_environment(), timeout=120)
    jar = repository / "ai/rapids/example/26.12.0/example-26.12.0.jar"
    if DRIVER.sha(jar) != expected_sha:
        raise DRIVER.RehearsalError("Maven resolved different JAR bytes")
    observed = DRIVER.execute(
        [java_home() / "bin/java", "-cp", jar, "ai.rapids.example.Main"]
    ).strip()
    if observed != MAVEN_MARKER:
        raise DRIVER.RehearsalError("resolved Maven payload did not execute")
    return {
        "resolved_jar_sha256": DRIVER.sha(jar),
        "java_main_output": observed,
        "java_version": DRIVER.execute(
            [java_home() / "bin/java", "--version"]
        ).splitlines()[0],
        "maven_version": DRIVER.execute(
            ["mvn", "-version"], environment=java_environment()
        ).splitlines()[0],
        "repository": "authenticated-loopback-only",
        "plugin_bootstrap": "separate read-only Maven Central tool downloads; no dummy artifact cached",
    }
