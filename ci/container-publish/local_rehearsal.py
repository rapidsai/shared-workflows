# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real Docker publication roundtrip against an ephemeral authenticated registry."""

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess  # nosec B404 -- fixed Docker/htpasswd argv, no shell or operator credentials.
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
import uuid

import requests

REGISTRY_IMAGE = "dockerhub.nvidia.com/library/registry@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e"
ORAS_VERSION = "1.3.3"
MAX_OCI_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_SINGLE_PLATFORM_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_SINGLE_PLATFORM_CONTENT_BYTES = 4 * 1024 * 1024 * 1024
MAX_SINGLE_PLATFORM_MEMBERS = 100_000
MAX_OCI_JSON_BYTES = 4 * 1024 * 1024
AUTH_PAIR = ("fixture-user", "fixture-value")
MEDIA = "application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json"


def local_reference(reference):
    if not re.fullmatch(
        r"127\.0\.0\.1:[1-9][0-9]{0,4}/pub01-[a-f0-9]+/fixture(?:-import)?(?::[a-z0-9-]+|@sha256:[a-f0-9]{64})",
        reference,
    ):
        raise ValueError(
            "only explicitly named loopback fixture references are allowed"
        )
    if urlsplit("http://" + reference).port > 65535:
        raise ValueError("invalid fixture port")
    return reference


def command(argv, *, text=None, check=True, isolated_home=None):
    result = subprocess.run(  # nosec B603 -- fixed executables/argv constructed within this module.
        argv,
        input=text,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            **(
                {
                    "HOME": str(isolated_home),
                    "XDG_CONFIG_HOME": str(Path(isolated_home) / "xdg"),
                }
                if isolated_home
                else {}
            ),
        },
    )
    if check and result.returncode:
        raise RuntimeError("local Docker/tool operation failed: " + result.stderr)
    return result


def registry_request(origin, path, *, auth=AUTH_PAIR):
    if (
        not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", origin)
        or urlsplit(origin).port > 65535
    ):
        raise ValueError("registry origin must be loopback only")
    if not path.startswith("/v2/") or ".." in path or "?" in path or "#" in path:
        raise ValueError("invalid registry API path")
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            origin + path,
            auth=auth,
            headers={"Accept": MEDIA},
            timeout=5,
            allow_redirects=False,
        )
    if 300 <= response.status_code < 400:
        raise ValueError("registry API redirects are rejected")
    return response


def inspect_oci_archive(
    archive_path, expected_sha256, expected_platforms, fixture_root
):
    """Validate the rehearsal's tagged two-platform OCI layout archive."""
    archive = Path(archive_path).resolve(strict=True)
    root = Path(fixture_root).resolve(strict=True)
    if not archive.is_relative_to(root) or not archive.is_file():
        raise ValueError("OCI archive must be a regular file inside the fixture")
    if archive.stat().st_size > MAX_OCI_ARCHIVE_BYTES:
        raise ValueError("synthetic OCI archive exceeds the rehearsal size limit")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("expected archive SHA-256 must be a hex digest")
    with archive.open("rb") as stream:
        archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    if archive_sha256 != expected_sha256:
        raise ValueError("OCI archive digest does not match the admitted input")

    members = {}
    total_size = 0
    seen_paths = set()
    with tarfile.open(archive, mode="r:") as bundle:
        for member in bundle.getmembers():
            name = PurePosixPath(member.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in member.name
                or name.as_posix() != member.name
                or not member.name
                or member.name in seen_paths
                or member.name in members
            ):
                raise ValueError("OCI archive contains an unsafe or duplicate path")
            seen_paths.add(member.name)
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("OCI archive may contain only regular files")
            total_size += member.size
            if total_size > MAX_OCI_ARCHIVE_BYTES:
                raise ValueError("OCI archive contents exceed the rehearsal size limit")
            data = bundle.extractfile(member)
            if data is None:
                raise ValueError("OCI archive file could not be read")
            members[member.name] = data.read()
    try:
        layout = json.loads(members["oci-layout"])
        layout_index = json.loads(members["index.json"])
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("OCI archive layout metadata is invalid") from error
    if (
        not isinstance(layout, dict)
        or layout.get("imageLayoutVersion") != "1.0.0"
        or not isinstance(layout_index, dict)
        or layout_index.get("schemaVersion") != 2
        or not isinstance(layout_index.get("manifests"), list)
    ):
        raise ValueError("OCI archive layout metadata is invalid")

    expected = set(expected_platforms)
    if expected != {"linux/amd64", "linux/arm64"}:
        raise ValueError("rehearsal expects exactly Linux amd64 and arm64")

    def read_blob(descriptor):
        digest = descriptor.get("digest") if isinstance(descriptor, dict) else None
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("urls")
            or not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
        ):
            raise ValueError("OCI descriptor is invalid or contains an external URL")
        content = members.get("blobs/sha256/" + digest[7:])
        size = descriptor.get("size")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or content is None
            or len(content) != size
            or hashlib.sha256(content).hexdigest() != digest[7:]
        ):
            raise ValueError("OCI descriptor blob is missing or has a bad digest")
        return digest, content

    top_descriptors = layout_index["manifests"]
    tagged = [
        item
        for item in top_descriptors
        if isinstance(item, dict)
        and isinstance(item.get("annotations"), dict)
        and item["annotations"].get("org.opencontainers.image.ref.name") == "multi"
    ]
    if len(tagged) != 1:
        raise ValueError("OCI archive must contain exactly one admitted multi tag")
    root_digest, root_bytes = read_blob(tagged[0])
    if tagged[0].get("mediaType") not in {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }:
        raise ValueError("admitted multi tag is not an OCI image index")
    try:
        image_index = json.loads(root_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("admitted image index is invalid JSON") from error
    children = image_index.get("manifests") if isinstance(image_index, dict) else None
    if (
        not isinstance(image_index, dict)
        or image_index.get("schemaVersion") != 2
        or not isinstance(children, list)
    ):
        raise ValueError("admitted image index is malformed")

    platforms = {}
    manifest_digests = {root_digest}
    blob_digests = set()
    for child in children:
        platform_data = child.get("platform") if isinstance(child, dict) else None
        if not isinstance(platform_data, dict):
            raise ValueError("image child descriptor lacks a local platform")
        if child.get("urls"):
            raise ValueError("image child descriptor contains an external URL")
        platform = f"{platform_data.get('os')}/{platform_data.get('architecture')}"
        if platform not in expected or platform in platforms.values():
            raise ValueError("OCI archive has an unexpected or duplicate platform")
        if child.get("mediaType") not in {
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        }:
            raise ValueError("OCI image index contains a non-image child")
        child_digest, child_bytes = read_blob(child)
        try:
            manifest = json.loads(child_bytes)
            config = manifest["config"]
            layers = manifest["layers"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError("OCI child image manifest is malformed") from error
        if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2:
            raise ValueError("OCI child image manifest is invalid")
        _, config_bytes = read_blob(config)
        try:
            image_config = json.loads(config_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("OCI image config is invalid JSON") from error
        if (
            not isinstance(image_config, dict)
            or image_config.get("os") != "linux"
            or image_config.get("architecture") != platform_data.get("architecture")
        ):
            raise ValueError("OCI platform descriptor does not match its image config")
        if not isinstance(layers, list) or not layers:
            raise ValueError("OCI image manifest has no layers")
        blob_digests.add(config["digest"])
        for layer in layers:
            layer_digest, _ = read_blob(layer)
            blob_digests.add(layer_digest)
        manifest_digests.add(child_digest)
        platforms[child_digest] = platform
    if set(platforms.values()) != expected:
        raise ValueError("OCI archive is missing an expected platform variant")

    extras = [item for item in top_descriptors if item is not tagged[0]]
    if (
        len(extras) != len(expected)
        or {item.get("digest") for item in extras if isinstance(item, dict)}
        != set(platforms)
        or any(
            not isinstance(item, dict)
            or item.get("urls")
            or not isinstance(item.get("platform"), dict)
            or platforms.get(item.get("digest"))
            != f"{item['platform'].get('os')}/{item['platform'].get('architecture')}"
            for item in extras
        )
    ):
        raise ValueError("OCI layout has entries outside its admitted image index")
    return {
        "archive_sha256": archive_sha256,
        "index_digest": root_digest,
        "platforms": sorted(platforms.values()),
        "manifest_digests": sorted(manifest_digests),
        "blob_digests": sorted(blob_digests),
        "index_bytes": root_bytes,
    }


def inspect_single_platform_oci_archive(archive_path, expected_sha256, fixture_root):
    """Validate one producer-shaped OCI image layout and report every digest.

    The archive is intentionally bounded for this local rehearsal. This checks
    transport integrity and OCI structure only; it does not authenticate the
    handoff or establish provenance/admission.
    """
    archive = Path(archive_path).resolve(strict=True)
    root = Path(fixture_root).resolve(strict=True)
    if not archive.is_relative_to(root) or not archive.is_file():
        raise ValueError("OCI archive must be a regular file inside the fixture")
    if archive.stat().st_size > MAX_SINGLE_PLATFORM_ARCHIVE_BYTES:
        raise ValueError("single-platform OCI archive exceeds the rehearsal size limit")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("expected archive SHA-256 must be a hex digest")
    documents = {}
    document_bytes = {}
    blob_sizes = {}
    total_size = 0
    metadata_size = 0
    seen_paths = set()
    index_bytes = None
    with archive.open("rb") as source:
        source_stat = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(source_stat.st_mode)
            or source_stat.st_size > MAX_SINGLE_PLATFORM_ARCHIVE_BYTES
        ):
            raise ValueError(
                "single-platform OCI archive exceeds the rehearsal size limit"
            )
        hashing_reader = _HashingReader(source)
        with tarfile.open(fileobj=hashing_reader, mode="r|*") as bundle:
            for count, member in enumerate(bundle, start=1):
                if count > MAX_SINGLE_PLATFORM_MEMBERS:
                    raise ValueError(
                        "single-platform OCI archive contains too many members"
                    )
                name = PurePosixPath(member.name)
                if (
                    name.is_absolute()
                    or ".." in name.parts
                    or "\\" in member.name
                    or name.as_posix() != member.name
                    or not member.name
                    or member.name in seen_paths
                ):
                    raise ValueError("OCI archive contains an unsafe or duplicate path")
                seen_paths.add(member.name)
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError("OCI archive may contain only regular files")
                total_size += member.size
                if total_size > MAX_SINGLE_PLATFORM_CONTENT_BYTES:
                    raise ValueError(
                        "OCI archive contents exceed the rehearsal size limit"
                    )
                content = bundle.extractfile(member)
                if content is None:
                    raise ValueError("OCI archive member could not be read")
                if member.name in {"oci-layout", "index.json"}:
                    if member.size > MAX_OCI_JSON_BYTES:
                        raise ValueError(
                            "OCI metadata exceeds the rehearsal size limit"
                        )
                    data = content.read()
                    metadata_size += len(data)
                    documents[member.name] = json.loads(data)
                    if member.name == "index.json":
                        index_bytes = data
                elif member.name.startswith("blobs/"):
                    if not re.fullmatch(r"blobs/sha256/[a-f0-9]{64}", member.name):
                        raise ValueError("OCI blob path is invalid")
                    digest = hashlib.sha256()
                    data = bytearray()
                    keep_json = member.size <= MAX_OCI_JSON_BYTES
                    while chunk := content.read(1024 * 1024):
                        digest.update(chunk)
                        if keep_json:
                            data.extend(chunk)
                    actual_digest = digest.hexdigest()
                    if actual_digest != member.name.rsplit("/", 1)[1]:
                        raise ValueError(
                            "OCI blob bytes do not match their content digest"
                        )
                    blob_sizes[member.name] = member.size
                    if keep_json and data.lstrip().startswith(b"{"):
                        metadata_size += len(data)
                        if metadata_size > MAX_OCI_JSON_BYTES * 4:
                            raise ValueError(
                                "OCI metadata exceeds the aggregate rehearsal limit"
                            )
                        try:
                            parsed = json.loads(data)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            # Image layers are arbitrary bytes, even if they begin with '{'.
                            pass
                        else:
                            if isinstance(parsed, dict):
                                documents[member.name] = parsed
                                document_bytes[member.name] = bytes(data)
        while hashing_reader.read(1024 * 1024):
            pass
        archive_sha256 = hashing_reader.digest.hexdigest()
    if archive_sha256 != expected_sha256:
        raise ValueError("OCI archive digest does not match the handoff")

    try:
        layout = documents["oci-layout"]
        index = documents["index.json"]
        if index_bytes is None:
            raise ValueError("OCI index cannot be read")
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("single-platform OCI layout metadata is invalid") from error
    if (
        not isinstance(layout, dict)
        or layout.get("imageLayoutVersion") != "1.0.0"
        or not isinstance(index, dict)
        or index.get("schemaVersion") != 2
        or index.get("mediaType")
        not in (None, "application/vnd.oci.image.index.v1+json")
        or not isinstance(index.get("manifests"), list)
        or len(index["manifests"]) != 1
    ):
        raise ValueError(
            "OCI layout must contain exactly one image manifest descriptor"
        )

    def read_blob(descriptor, expected_media_types):
        if not isinstance(descriptor, dict) or descriptor.get("urls"):
            raise ValueError("OCI descriptor is invalid or contains an external URL")
        digest = descriptor.get("digest")
        size = descriptor.get("size")
        media_type = descriptor.get("mediaType")
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
            or type(size) is not int
            or media_type not in expected_media_types
        ):
            raise ValueError("OCI descriptor digest, size, or media type is invalid")
        content_path = "blobs/sha256/" + digest[7:]
        content = documents.get(content_path)
        if blob_sizes.get(content_path) != size:
            raise ValueError("OCI descriptor blob is missing or has a bad digest")
        return digest, content, document_bytes.get(content_path)

    descriptor = index["manifests"][0]
    manifest_digest, manifest, manifest_bytes = read_blob(
        descriptor, {"application/vnd.oci.image.manifest.v1+json"}
    )
    platform = descriptor.get("platform")
    if (
        not isinstance(platform, dict)
        or platform.get("os") != "linux"
        or platform.get("architecture") not in {"amd64", "arm64"}
    ):
        raise ValueError("OCI image descriptor has no supported Linux platform")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schemaVersion") != 2
        or manifest.get("mediaType") != "application/vnd.oci.image.manifest.v1+json"
        or not isinstance(manifest.get("config"), dict)
        or not isinstance(manifest.get("layers"), list)
    ):
        raise ValueError("OCI image manifest is malformed")
    config_digest, config, config_bytes = read_blob(
        manifest["config"], {"application/vnd.oci.image.config.v1+json"}
    )
    if (
        not isinstance(config, dict)
        or config.get("os") != platform["os"]
        or config.get("architecture") != platform["architecture"]
    ):
        raise ValueError("OCI config platform does not match its image descriptor")
    layers = []
    layer_types = {
        "application/vnd.oci.image.layer.v1.tar",
        "application/vnd.oci.image.layer.v1.tar+gzip",
        "application/vnd.oci.image.layer.v1.tar+zstd",
        "application/vnd.oci.image.layer.nondistributable.v1.tar",
        "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip",
        "application/vnd.oci.image.layer.nondistributable.v1.tar+zstd",
    }
    for layer in manifest["layers"]:
        layer_digest, _content, _bytes = read_blob(layer, layer_types)
        layers.append(layer_digest[7:])

    return {
        "transport_sha256": archive_sha256,
        "oci_index_sha256": hashlib.sha256(index_bytes).hexdigest(),
        "runnable_manifest_sha256": manifest_digest[7:],
        "config_sha256": config_digest[7:],
        "layer_sha256s": layers,
        "platform": f"{platform['os']}/{platform['architecture']}",
        "manifest_bytes": manifest_bytes,
        "config_bytes": config_bytes,
    }


def validate_ci_image_import_handoff(handoff, expected_train_sha256, expected_identity):
    """Strictly compare structural handoff fields to authenticated caller inputs.

    The calling system remains responsible for authenticating the handoff and
    independently validating train/catalog admission; this function never
    treats JSON fields as an authorization decision.
    """
    if not isinstance(handoff, dict) or set(handoff) != {
        "schema_version",
        "train_sha256",
        "release_catalog_key",
        "source",
        "identity",
        "archive",
    }:
        raise ValueError("CI-image handoff has unsupported or missing fields")
    if type(handoff["schema_version"]) is not int or handoff["schema_version"] != 1:
        raise ValueError("CI-image handoff schema version is unsupported")
    if (
        not isinstance(expected_train_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected_train_sha256)
        or handoff["train_sha256"] != expected_train_sha256
    ):
        raise ValueError(
            "CI-image handoff does not match the authenticated release train"
        )
    if handoff["release_catalog_key"] != "container:ci-images":
        raise ValueError("CI-image handoff release unit is unsupported")
    if not isinstance(expected_identity, dict) or set(expected_identity) != {
        "ecosystem",
        "name",
        "version",
        "build",
        "platform",
    }:
        raise ValueError("expected CI-image identity is incomplete")
    identity = handoff["identity"]
    if (
        not isinstance(identity, dict)
        or set(identity) != set(expected_identity)
        or identity != expected_identity
    ):
        raise ValueError("CI-image handoff identity does not match the frozen request")
    if (
        identity.get("ecosystem") != "container"
        or not isinstance(identity.get("name"), str)
        or not re.fullmatch(r"docker\.io/rapidsai/[a-z0-9][a-z0-9-]*", identity["name"])
        or not isinstance(identity.get("version"), str)
        or not re.fullmatch(r"\d+\.\d+", identity["version"])
        or not isinstance(identity.get("platform"), str)
        or not re.fullmatch(r"linux/(amd64|arm64)", identity["platform"])
        or not isinstance(identity.get("build"), str)
        or not identity["build"]
    ):
        raise ValueError("CI-image handoff identity is malformed")

    source = handoff["source"]
    source_fields = {"repository", "sha", "run_id", "run_attempt", "workflow_artifact"}
    if not isinstance(source, dict) or set(source) != source_fields:
        raise ValueError("CI-image handoff source has unsupported or missing fields")
    if (
        not isinstance(source["repository"], str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/ci-imgs", source["repository"])
        or not re.fullmatch(
            r"[a-f0-9]{40}", source["sha"] if isinstance(source["sha"], str) else ""
        )
        or any(
            not isinstance(source[field], str)
            or not re.fullmatch(r"[1-9][0-9]*", source[field])
            for field in ("run_id", "run_attempt")
        )
        or not isinstance(source["workflow_artifact"], str)
        or not source["workflow_artifact"]
    ):
        raise ValueError("CI-image handoff producer source is malformed")

    archive = handoff["archive"]
    archive_fields = {
        "transport_sha256",
        "oci_index_sha256",
        "runnable_manifest_sha256",
        "config_sha256",
        "layer_sha256s",
    }
    if not isinstance(archive, dict) or set(archive) != archive_fields:
        raise ValueError("CI-image handoff archive has unsupported or missing fields")
    for field in archive_fields - {"layer_sha256s"}:
        if not isinstance(archive[field], str) or not re.fullmatch(
            r"[a-f0-9]{64}", archive[field]
        ):
            raise ValueError("CI-image handoff contains an invalid archive digest")
    layers = archive["layer_sha256s"]
    if not isinstance(layers, list) or any(
        not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
        for digest in layers
    ):
        raise ValueError("CI-image handoff layer digests are invalid")
    return handoff


class _HashingReader:
    """Hash source bytes as the bounded ORAS tar copy is prepared."""

    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        chunk = self.stream.read(size)
        self.digest.update(chunk)
        return chunk


def _prepare_oras_tar(archive, expected_sha256, fixture_root):
    """Copy or bounded-gunzip validated transport bytes for ORAS 1.3.3."""
    temporary = tempfile.NamedTemporaryFile(
        mode="wb", prefix="oci-import-", suffix=".tar", dir=fixture_root, delete=False
    )
    output_path = Path(temporary.name)
    try:
        with archive.open("rb") as raw:
            hashing_reader = _HashingReader(raw)
            magic = raw.read(2)
            raw.seek(0)
            hashing_reader = _HashingReader(raw)
            if magic == b"\x1f\x8b":
                source = gzip.GzipFile(fileobj=hashing_reader, mode="rb")
            else:
                source = hashing_reader
            total = 0
            with temporary:
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_SINGLE_PLATFORM_CONTENT_BYTES:
                        raise ValueError(
                            "decompressed OCI archive exceeds the rehearsal size limit"
                        )
                    temporary.write(chunk)
            while hashing_reader.read(1024 * 1024):
                pass
            if hashing_reader.digest.hexdigest() != expected_sha256:
                raise ValueError("OCI archive changed after handoff validation")
        return output_path
    except (OSError, EOFError, gzip.BadGzipFile) as error:
        output_path.unlink(missing_ok=True)
        raise ValueError("OCI archive could not be safely copied for ORAS") from error
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise


def import_single_platform_oci_archive(
    oras,
    archive_path,
    handoff,
    expected_train_sha256,
    expected_identity,
    fixture_root,
    origin,
    target,
    registry_config,
    oras_home,
):
    """Import one exact frozen image into the isolated registry and verify readback."""
    validate_ci_image_import_handoff(handoff, expected_train_sha256, expected_identity)
    target = local_reference(target)
    if "@" in target:
        raise ValueError("OCI rehearsal import destinations must use a tag")
    parsed = urlsplit("http://" + target)
    repository, separator, tag = parsed.path.rpartition(":")
    repository = repository.lstrip("/")
    if not separator or not repository or not tag:
        raise ValueError("OCI import target must use a tag")
    fixture_root = Path(fixture_root).resolve(strict=True)
    archive = Path(archive_path).resolve(strict=True)
    if not archive.is_relative_to(fixture_root):
        raise ValueError("OCI archive must be inside the owned fixture directory")
    inventory = inspect_single_platform_oci_archive(
        archive, handoff["archive"]["transport_sha256"], fixture_root
    )
    for field in (
        "transport_sha256",
        "oci_index_sha256",
        "runnable_manifest_sha256",
        "config_sha256",
        "layer_sha256s",
    ):
        if inventory[field] != handoff["archive"][field]:
            raise ValueError(f"OCI archive {field} does not match the handoff")
    if inventory["platform"] != expected_identity["platform"]:
        raise ValueError("OCI archive platform does not match the frozen identity")

    manifest_path = f"/v2/{repository}/manifests/{tag}"
    existing = registry_request(origin, manifest_path)
    expected_manifest = "sha256:" + inventory["runnable_manifest_sha256"]
    if existing.status_code == 200:
        current_digest = "sha256:" + hashlib.sha256(existing.content).hexdigest()
        if current_digest != expected_manifest:
            raise ValueError("destination tag already names a conflicting OCI manifest")
    elif existing.status_code != 404:
        raise RuntimeError("could not establish destination tag state")

    oras_archive = _prepare_oras_tar(
        archive, handoff["archive"]["transport_sha256"], fixture_root
    )
    try:
        command(
            [
                oras,
                "cp",
                "--from-oci-layout",
                "--from-registry-config",
                str(registry_config),
                "--to-registry-config",
                str(registry_config),
                "--to-username",
                AUTH_PAIR[0],
                "--to-password",
                AUTH_PAIR[1],
                "--to-plain-http",
                f"{oras_archive}@sha256:{inventory['runnable_manifest_sha256']}",
                target,
            ],
            isolated_home=oras_home,
        )
    finally:
        oras_archive.unlink(missing_ok=True)
    copied = registry_request(origin, manifest_path)
    copied.raise_for_status()
    copied_digest = "sha256:" + hashlib.sha256(copied.content).hexdigest()
    if (
        copied_digest != expected_manifest
        or copied.content != inventory["manifest_bytes"]
    ):
        raise ValueError(
            "OCI import changed the admitted runnable manifest bytes or digest"
        )
    manifest = copied.json()
    descriptors = [manifest["config"], *manifest["layers"]]
    actual_config = manifest["config"].get("digest", "").removeprefix("sha256:")
    actual_layers = [
        layer.get("digest", "").removeprefix("sha256:") for layer in manifest["layers"]
    ]
    if (
        actual_config != handoff["archive"]["config_sha256"]
        or actual_layers != handoff["archive"]["layer_sha256s"]
    ):
        raise ValueError("registry manifest descriptors do not match the handoff")
    for descriptor in descriptors:
        digest = descriptor["digest"]
        response = registry_request(origin, f"/v2/{repository}/blobs/{digest}")
        response.raise_for_status()
        if "sha256:" + hashlib.sha256(response.content).hexdigest() != digest:
            raise ValueError("OCI import changed a config or layer digest")
    config_response = registry_request(
        origin, f"/v2/{repository}/blobs/sha256:{actual_config}"
    )
    config_response.raise_for_status()
    if config_response.content != inventory["config_bytes"]:
        raise ValueError("OCI import changed the admitted image config bytes")
    return {
        key: value
        for key, value in inventory.items()
        if key not in {"manifest_bytes", "config_bytes"}
    }


def write_oci_archive(layout_path, archive_path):
    """Create a deterministic-path tar from the owned OCI layout fixture."""
    layout = Path(layout_path).resolve(strict=True)
    archive = Path(archive_path).resolve()
    if not archive.is_relative_to(layout.parent):
        raise ValueError("OCI archive output must remain inside the fixture")
    with tarfile.open(archive, mode="w") as bundle:
        for path in sorted(layout.rglob("*")):
            if path.is_file():
                bundle.add(path, arcname=path.relative_to(layout))


def write_producer_shaped_single_platform_archive(
    archive_path, *, architecture, image_tag, image_name, repeat_layer=False
):
    """Write a tiny OCI layout matching the CI-image producer's one-row index."""
    if architecture not in {"amd64", "arm64"}:
        raise ValueError("single-platform fixture architecture is unsupported")
    config = json.dumps(
        {"architecture": architecture, "os": "linux"}, separators=(",", ":")
    ).encode()
    layer = b"local publication rehearsal layer"
    second_layer = b"another local publication rehearsal layer"
    config_digest = hashlib.sha256(config).hexdigest()
    layer_digest = hashlib.sha256(layer).hexdigest()
    second_layer_digest = hashlib.sha256(second_layer).hexdigest()
    layer_sequence = (
        [
            max(layer_digest, second_layer_digest),
            min(layer_digest, second_layer_digest),
            max(layer_digest, second_layer_digest),
        ]
        if repeat_layer
        else [layer_digest]
    )
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + config_digest,
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": "sha256:" + digest,
                    "size": len(layer if digest == layer_digest else second_layer),
                }
                for digest in layer_sequence
            ],
        },
        separators=(",", ":"),
    ).encode()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    index = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + manifest_digest,
                    "size": len(manifest),
                    "annotations": {
                        "io.containerd.image.name": f"docker.io/{image_name}:{image_tag}",
                        "org.opencontainers.image.ref.name": image_tag,
                    },
                    "platform": {"architecture": architecture, "os": "linux"},
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    files = {
        "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
        "index.json": index,
        "blobs/sha256/" + config_digest: config,
        "blobs/sha256/" + layer_digest: layer,
        "blobs/sha256/" + second_layer_digest: second_layer,
        "blobs/sha256/" + manifest_digest: manifest,
    }
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="oci-layout-",
        suffix=".tar",
        dir=Path(archive_path).parent,
        delete=False,
    ) as plain:
        plain_path = Path(plain.name)
    try:
        with tarfile.open(plain_path, mode="w") as bundle:
            for name, content in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mode = 0o644
                bundle.addfile(info, io.BytesIO(content))
        with (
            plain_path.open("rb") as source,
            gzip.open(archive_path, "wb") as compressed,
        ):
            shutil.copyfileobj(source, compressed)
    finally:
        plain_path.unlink(missing_ok=True)


def rewrite_oci_archive_index(
    archive_path, output_path, *, drop_architecture=None, external_url=False
):
    """Create a fixture-only index variant for rejection-path tests."""
    with tarfile.open(archive_path, mode="r:") as source:
        files = {}
        for member in source.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("fixture OCI archive has an unexpected file type")
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError("fixture OCI archive could not be read")
            files[member.name] = stream.read()
    index = json.loads(files["index.json"])
    source = [
        descriptor
        for descriptor in index["manifests"]
        if descriptor.get("annotations", {}).get("org.opencontainers.image.ref.name")
        == "multi"
    ]
    if len(source) != 1:
        raise ValueError("fixture archive must have one tagged multi index")
    descriptor = source[0]
    old_path = "blobs/sha256/" + descriptor["digest"].split(":", 1)[1]
    nested = json.loads(files[old_path])
    if drop_architecture:
        nested["manifests"] = [
            descriptor
            for descriptor in nested["manifests"]
            if descriptor.get("platform", {}).get("architecture") != drop_architecture
        ]
        index["manifests"] = [
            descriptor
            for descriptor in index["manifests"]
            if descriptor.get("platform", {}).get("architecture") != drop_architecture
        ]
    elif external_url:
        nested["manifests"][0]["urls"] = ["https://example.invalid/blob"]
    else:
        nested.setdefault("annotations", {})["org.opencontainers.image.ref.name"] = (
            "conflict"
        )
    nested_bytes = json.dumps(nested, separators=(",", ":"), sort_keys=True).encode()
    nested_digest = "sha256:" + hashlib.sha256(nested_bytes).hexdigest()
    del files[old_path]
    files["blobs/sha256/" + nested_digest.split(":", 1)[1]] = nested_bytes
    descriptor["digest"] = nested_digest
    descriptor["size"] = len(nested_bytes)
    files["index.json"] = json.dumps(
        index, separators=(",", ":"), sort_keys=True
    ).encode()
    with tarfile.open(output_path, mode="w") as output:
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            output.addfile(info, io.BytesIO(content))


def import_oci_archive(
    oras,
    archive_path,
    expected_sha256,
    expected_platforms,
    fixture_root,
    origin,
    target,
    registry_config,
    oras_home,
):
    """Copy an admitted OCI archive to the isolated fixture without rebuilding."""
    target = local_reference(target)
    if "@" in target:
        raise ValueError("OCI rehearsal import destinations must use a tag")
    parsed = urlsplit("http://" + target)
    repository, separator, tag = parsed.path.rpartition(":")
    repository = repository.lstrip("/")
    if not separator or not repository or not tag:
        raise ValueError("OCI import target must use a tag")
    fixture_root = Path(fixture_root).resolve(strict=True)
    archive = Path(archive_path).resolve(strict=True)
    if not archive.is_relative_to(fixture_root):
        raise ValueError("OCI archive must be inside the owned fixture directory")
    inventory = inspect_oci_archive(
        archive, expected_sha256, expected_platforms, fixture_root
    )
    manifest_path = f"/v2/{repository}/manifests/{tag}"
    existing = registry_request(origin, manifest_path)
    if existing.status_code == 200:
        current_digest = "sha256:" + hashlib.sha256(existing.content).hexdigest()
        if current_digest != inventory["index_digest"]:
            raise ValueError("destination tag already names a conflicting OCI index")
    elif existing.status_code != 404:
        raise RuntimeError("could not establish destination tag state")

    command(
        [
            oras,
            "cp",
            "--from-oci-layout",
            "--from-registry-config",
            str(registry_config),
            "--to-registry-config",
            str(registry_config),
            "--to-username",
            AUTH_PAIR[0],
            "--to-password",
            AUTH_PAIR[1],
            "--to-plain-http",
            f"{archive}:multi",
            target,
        ],
        isolated_home=oras_home,
    )
    copied_index = registry_request(origin, manifest_path)
    copied_index.raise_for_status()
    if (
        "sha256:" + hashlib.sha256(copied_index.content).hexdigest()
        != inventory["index_digest"]
        or copied_index.content != inventory["index_bytes"]
    ):
        raise ValueError("OCI import changed the admitted index bytes or digest")

    for digest in inventory["manifest_digests"]:
        child = registry_request(origin, f"/v2/{repository}/manifests/{digest}")
        child.raise_for_status()
        if "sha256:" + hashlib.sha256(child.content).hexdigest() != digest:
            raise ValueError("OCI import changed a child manifest digest")
    for digest in inventory["blob_digests"]:
        blob = registry_request(origin, f"/v2/{repository}/blobs/{digest}")
        blob.raise_for_status()
        if "sha256:" + hashlib.sha256(blob.content).hexdigest() != digest:
            raise ValueError("OCI import changed a config or layer digest")
    return inventory


def rehearse():
    docker = shutil.which("docker")
    htpasswd = shutil.which("htpasswd")
    oras = shutil.which("oras")
    if not docker or not htpasswd or not oras:
        raise RuntimeError(
            "Docker, ORAS 1.3.3, and bcrypt-capable htpasswd are required"
        )
    oras_version = command([oras, "version"]).stdout
    if not re.search(
        rf"(?m)^Version:\s+{re.escape(ORAS_VERSION)}(?:\+|\s|$)", oras_version
    ):
        raise RuntimeError("the local OCI import rehearsal requires ORAS 1.3.3")
    host = command(
        [
            docker,
            "context",
            "inspect",
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ]
    ).stdout.strip()
    if not host.startswith("unix://"):
        raise ValueError("only a local Unix Docker socket is allowed")
    plugins = json.loads(
        command(
            [docker, "--host", host, "info", "--format", "{{json .ClientInfo.Plugins}}"]
        ).stdout
    )
    buildx = next(item for item in plugins if item["Name"] == "buildx")
    buildx_path = Path(buildx["Path"]).resolve(strict=True)
    identifier = "pub01-" + uuid.uuid4().hex
    refs = []
    network_created = container_created = False
    receipt = None
    with tempfile.TemporaryDirectory(prefix="pub01-oci-") as directory:
        root = Path(directory)
        oras_home = root / "oras-home"
        oras_home.mkdir()
        (oras_home / "xdg").mkdir()
        registry_config = root / "oras-registry-config.json"
        registry_config.write_text('{"auths":{}}\n')
        registry_config.chmod(0o600)
        config = root / "client"
        config.mkdir()
        (config / "cli-plugins").mkdir()
        (config / "cli-plugins/docker-buildx").symlink_to(buildx_path)
        client = [docker, "--host", host, "--config", str(config)]
        command(
            client + ["image", "inspect", REGISTRY_IMAGE]
        )  # Fail instead of pulling an unreviewed image.
        try:
            command(client + ["network", "create", identifier])
            network_created = True
            auth_file = root / "htpasswd"
            auth_file.write_text(command([htpasswd, "-bnB", *AUTH_PAIR]).stdout)
            auth_file.chmod(0o600)
            command(
                client
                + [
                    "run",
                    "-d",
                    "--pull=never",
                    "--name",
                    identifier,
                    "--network",
                    identifier,
                    "--publish",
                    "127.0.0.1::5000",
                    "--tmpfs",
                    "/var/lib/registry:rw,size=16777216",
                    "--mount",
                    f"type=bind,src={auth_file},dst=/auth/htpasswd,readonly",
                    "-e",
                    "REGISTRY_AUTH=htpasswd",
                    "-e",
                    "REGISTRY_AUTH_HTPASSWD_REALM=LocalFixture",
                    "-e",
                    "REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd",
                    "-e",
                    "REGISTRY_STORAGE_REDIRECT_DISABLE=true",
                    "-e",
                    "OTEL_TRACES_EXPORTER=none",
                    REGISTRY_IMAGE,
                ]
            )
            container_created = True
            for _attempt in range(30):
                mapping = json.loads(
                    command(
                        client
                        + [
                            "inspect",
                            identifier,
                            "--format",
                            "{{json .NetworkSettings.Ports}}",
                        ]
                    ).stdout
                )
                if mapping.get("5000/tcp"):
                    binding = mapping["5000/tcp"][0]
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError(
                    "registry did not receive a loopback port: "
                    + command(client + ["logs", identifier]).stderr
                )
            if binding["HostIp"] != "127.0.0.1":
                raise ValueError("registry is not exclusively bound to loopback")
            endpoint = "127.0.0.1:" + binding["HostPort"]
            origin = "http://" + endpoint
            for _attempt in range(30):
                try:
                    ready = registry_request(origin, "/v2/", auth=None)
                    if ready.status_code == 401:
                        break
                except requests.ConnectionError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("authenticated registry did not become ready")
            if registry_request(origin, "/v2/", auth=None).status_code != 401:
                raise ValueError("anonymous API request unexpectedly allowed")
            if (
                registry_request(
                    origin, "/v2/", auth=("fixture-user", "invalid-fixture-value")
                ).status_code
                != 401
            ):
                raise ValueError("wrong registry authentication unexpectedly allowed")
            failed_login = command(
                client
                + ["login", endpoint, "--username", AUTH_PAIR[0], "--password-stdin"],
                text="invalid-fixture-value\n",
                check=False,
            )
            if failed_login.returncode == 0:
                raise ValueError("wrong registry credential unexpectedly authenticated")
            if not any(
                marker in failed_login.stderr.lower()
                for marker in ("unauthorized", "401")
            ):
                raise ValueError(
                    "wrong credential failed without an authentication rejection"
                )
            command(
                client
                + ["login", endpoint, "--username", AUTH_PAIR[0], "--password-stdin"],
                text=AUTH_PAIR[1] + "\n",
            )
            repo = identifier + "/fixture"
            target = local_reference(endpoint + "/" + repo + ":multi")
            context = root / "context"
            context.mkdir()
            (context / "Dockerfile").write_text(
                "FROM scratch\nCOPY payload.txt /payload.txt\n"
            )
            (context / "payload.txt").write_text(identifier)
            for arch in ("amd64", "arm64"):
                reference = local_reference(endpoint + "/" + repo + ":" + arch)
                refs.append(reference)
                command(
                    client
                    + [
                        "build",
                        "--network=none",
                        "--pull=false",
                        "--provenance=false",
                        "--sbom=false",
                        "--platform",
                        "linux/" + arch,
                        "-t",
                        reference,
                        str(context),
                    ]
                )
                command(client + ["push", reference])
            command(client + ["manifest", "create", "--insecure", target, *refs])
            command(client + ["manifest", "push", "--insecure", target])
            response = registry_request(origin, f"/v2/{repo}/manifests/multi")
            response.raise_for_status()
            digest = "sha256:" + hashlib.sha256(response.content).hexdigest()
            if response.headers["Docker-Content-Digest"] != digest:
                raise ValueError(
                    "registry index digest does not match the retrieved bytes"
                )
            index = response.json()
            platforms = sorted(
                item["platform"]["architecture"] for item in index["manifests"]
            )
            if platforms != ["amd64", "arm64"]:
                raise ValueError("multiarchitecture manifest is incomplete")
            blob_digests = set()
            for item in index["manifests"]:
                manifest = registry_request(
                    origin, f"/v2/{repo}/manifests/{item['digest']}"
                )
                manifest.raise_for_status()
                if (
                    "sha256:" + hashlib.sha256(manifest.content).hexdigest()
                    != item["digest"]
                ):
                    raise ValueError("child manifest digest mismatch")
                for blob in [manifest.json()["config"], *manifest.json()["layers"]]:
                    result = registry_request(
                        origin, f"/v2/{repo}/blobs/{blob['digest']}"
                    )
                    result.raise_for_status()
                    if (
                        "sha256:" + hashlib.sha256(result.content).hexdigest()
                        != blob["digest"]
                    ):
                        raise ValueError("registry blob digest mismatch")
                    blob_digests.add(blob["digest"])
            layout_path = root / "admitted-fixture"
            command(
                [
                    oras,
                    "cp",
                    "--from-username",
                    AUTH_PAIR[0],
                    "--from-password",
                    AUTH_PAIR[1],
                    "--from-registry-config",
                    str(registry_config),
                    "--from-plain-http",
                    endpoint + "/" + repo + ":multi",
                    "--to-oci-layout",
                    "--to-registry-config",
                    str(registry_config),
                    f"{layout_path}:multi",
                ],
                isolated_home=oras_home,
            )
            archive_path = root / "admitted-fixture.tar"
            write_oci_archive(layout_path, archive_path)
            archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            expected_platforms = ["linux/amd64", "linux/arm64"]
            archive_inventory = inspect_oci_archive(
                archive_path, archive_sha256, expected_platforms, root
            )
            if archive_inventory["index_digest"] != digest:
                raise ValueError("OCI archive export changed the source index digest")

            import_target = endpoint + "/" + identifier + "/fixture-import:multi"
            wrong_digest_target = (
                endpoint + "/" + identifier + "/fixture-import:wrong-digest"
            )
            try:
                import_oci_archive(
                    oras,
                    archive_path,
                    "0" * 64,
                    expected_platforms,
                    root,
                    origin,
                    wrong_digest_target,
                    registry_config,
                    oras_home,
                )
            except ValueError as error:
                if "does not match" not in str(error):
                    raise
            else:
                raise ValueError("OCI import accepted a wrong admitted archive digest")
            if (
                registry_request(
                    origin,
                    f"/v2/{identifier}/fixture-import/manifests/wrong-digest",
                ).status_code
                != 404
            ):
                raise ValueError("wrong-digest OCI import modified the registry")

            incomplete_archive = root / "missing-arm64.tar"
            rewrite_oci_archive_index(
                archive_path, incomplete_archive, drop_architecture="arm64"
            )
            incomplete_sha256 = hashlib.sha256(
                incomplete_archive.read_bytes()
            ).hexdigest()
            try:
                import_oci_archive(
                    oras,
                    incomplete_archive,
                    incomplete_sha256,
                    expected_platforms,
                    root,
                    origin,
                    wrong_digest_target,
                    registry_config,
                    oras_home,
                )
            except ValueError as error:
                if "missing an expected platform" not in str(error):
                    raise
            else:
                raise ValueError("OCI import accepted an incomplete platform inventory")
            if (
                registry_request(
                    origin,
                    f"/v2/{identifier}/fixture-import/manifests/wrong-digest",
                ).status_code
                != 404
            ):
                raise ValueError("incomplete OCI import modified the registry")

            external_url_archive = root / "external-url.tar"
            rewrite_oci_archive_index(
                archive_path, external_url_archive, external_url=True
            )
            external_url_sha256 = hashlib.sha256(
                external_url_archive.read_bytes()
            ).hexdigest()
            try:
                import_oci_archive(
                    oras,
                    external_url_archive,
                    external_url_sha256,
                    expected_platforms,
                    root,
                    origin,
                    wrong_digest_target,
                    registry_config,
                    oras_home,
                )
            except ValueError as error:
                if "external URL" not in str(error):
                    raise
            else:
                raise ValueError("OCI import accepted an external blob URL")
            if (
                registry_request(
                    origin,
                    f"/v2/{identifier}/fixture-import/manifests/wrong-digest",
                ).status_code
                != 404
            ):
                raise ValueError("external-URL OCI import modified the registry")

            imported = import_oci_archive(
                oras,
                archive_path,
                archive_sha256,
                expected_platforms,
                root,
                origin,
                import_target,
                registry_config,
                oras_home,
            )
            # Match the one-manifest BuildKit layout shape used by CI-image
            # producers. The destination remains this authenticated loopback
            # registry fixture; no external registry is contacted.
            single_archive = root / "single-platform.tar.gz"
            single_tag = "26.12-cuda12.9.2-rockylinux8-py3.12-amd64"
            write_producer_shaped_single_platform_archive(
                single_archive,
                architecture="amd64",
                image_tag=single_tag,
                image_name="rapidsai/ci-conda",
            )
            single_sha256 = hashlib.sha256(single_archive.read_bytes()).hexdigest()
            single_inventory = inspect_single_platform_oci_archive(
                single_archive, single_sha256, root
            )
            if single_inventory["platform"] != "linux/amd64":
                raise ValueError("ORAS single-image export lost its producer platform")
            single_identity = {
                "ecosystem": "container",
                "name": "docker.io/rapidsai/ci-conda",
                "version": "26.12",
                "build": single_tag,
                "platform": "linux/amd64",
            }
            single_handoff = {
                "schema_version": 1,
                "train_sha256": "a" * 64,
                "release_catalog_key": "container:ci-images",
                "source": {
                    "repository": "rapidsai/ci-imgs",
                    "sha": "b" * 40,
                    "run_id": "123",
                    "run_attempt": "1",
                    "workflow_artifact": "ci-imgs-123.1",
                },
                "identity": single_identity,
                "archive": {
                    key: single_inventory[key]
                    for key in (
                        "transport_sha256",
                        "oci_index_sha256",
                        "runnable_manifest_sha256",
                        "config_sha256",
                        "layer_sha256s",
                    )
                },
            }
            single_target = endpoint + "/" + identifier + "/fixture-import:single-amd64"
            bad_single_target = (
                endpoint + "/" + identifier + "/fixture-import:single-bad"
            )
            bad_handoff = json.loads(json.dumps(single_handoff))
            bad_handoff["archive"]["runnable_manifest_sha256"] = "0" * 64
            try:
                import_single_platform_oci_archive(
                    oras,
                    single_archive,
                    bad_handoff,
                    "a" * 64,
                    single_identity,
                    root,
                    origin,
                    bad_single_target,
                    registry_config,
                    oras_home,
                )
            except ValueError as error:
                if "runnable_manifest_sha256 does not match" not in str(error):
                    raise
            else:
                raise ValueError("single-platform import accepted a mismatched handoff")
            if (
                registry_request(
                    origin, f"/v2/{identifier}/fixture-import/manifests/single-bad"
                ).status_code
                != 404
            ):
                raise ValueError(
                    "invalid single-platform handoff modified the registry"
                )
            preparation_spec = importlib.util.spec_from_file_location(
                "hosted_preparation", Path(__file__).with_name("hosted_preparation.py")
            )
            preparation = importlib.util.module_from_spec(preparation_spec)
            preparation_spec.loader.exec_module(preparation)
            now = datetime.now(timezone.utc)
            policy = {
                "schema_version": 1,
                "origin": origin,
                "namespace": identifier,
                "revision": "synthetic-local-contract",
                "expires_at": (now + timedelta(hours=2)).isoformat(),
                "private_acl_evidence": "fixture:anonymous-deny-only",
                "transport_boundary_evidence": "fixture:loopback-only-not-hosted",
                "credential_reference": "kv2/rapids-team/rapids/ci/rehearsal/container/synthetic",
            }
            descriptor = {
                "schema_version": 1,
                "policy_sha256": preparation.digest(policy),
                "issued_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "repository": identifier + "/fixture-import",
                "tag": "single-amd64",
                "handoff": single_handoff,
            }
            prepared_receipt, prepared_directory = preparation.prepare(
                policy,
                descriptor,
                single_archive,
                root.resolve(),
                policy_sha256=preparation.digest(policy),
                handoff_sha256=preparation.digest(single_handoff),
                train_sha256="a" * 64,
                identity=single_identity,
                loopback_fixture=True,
                now=now,
            )
            if prepared_receipt["target"] != single_target:
                raise ValueError("prepared destination differs from the local registry")
            single_import = import_single_platform_oci_archive(
                oras,
                prepared_directory / "archive.oci",
                single_handoff,
                "a" * 64,
                single_identity,
                root,
                origin,
                single_target,
                registry_config,
                oras_home,
            )
            imported_retry = import_oci_archive(
                oras,
                archive_path,
                archive_sha256,
                expected_platforms,
                root,
                origin,
                import_target,
                registry_config,
                oras_home,
            )
            conflict_archive = root / "conflicting-index.tar"
            rewrite_oci_archive_index(archive_path, conflict_archive)
            conflict_sha256 = hashlib.sha256(conflict_archive.read_bytes()).hexdigest()
            try:
                import_oci_archive(
                    oras,
                    conflict_archive,
                    conflict_sha256,
                    expected_platforms,
                    root,
                    origin,
                    import_target,
                    registry_config,
                    oras_home,
                )
            except ValueError as error:
                if "conflicting OCI index" not in str(error):
                    raise
            else:
                raise ValueError("OCI import overwrote an existing conflicting tag")
            imported_readback = registry_request(
                origin,
                f"/v2/{identifier}/fixture-import/manifests/multi",
            )
            if imported_readback.content != archive_inventory["index_bytes"]:
                raise ValueError("conflict attempt changed the imported OCI index")
            anonymous = registry_request(
                origin, f"/v2/{repo}/manifests/multi", auth=None
            )
            if anonymous.status_code != 401:
                raise ValueError("anonymous manifest access unexpectedly allowed")
            missing = registry_request(
                origin, f"/v2/{repo}/manifests/sha256:" + "0" * 64
            )
            if (
                missing.status_code != 404
                or missing.json()["errors"][0]["code"] != "MANIFEST_UNKNOWN"
            ):
                raise ValueError("unknown digest did not receive MANIFEST_UNKNOWN/404")
            bad_digest = command(
                client + ["pull", endpoint + "/" + repo + "@sha256:" + "0" * 64],
                check=False,
            )
            if bad_digest.returncode == 0:
                raise ValueError("unknown digest unexpectedly pulled")
            for item in index["manifests"]:
                by_digest = local_reference(
                    endpoint + "/" + repo + "@" + item["digest"]
                )
                refs.append(by_digest)
                command(
                    client
                    + [
                        "pull",
                        "--platform",
                        "linux/" + item["platform"]["architecture"],
                        by_digest,
                    ]
                )
            versions = json.loads(
                command(client + ["version", "--format", "{{json .}}"]).stdout
            )
            native_arch = versions["Server"]["Arch"]
            by_index = local_reference(endpoint + "/" + repo + "@" + digest)
            refs.append(by_index)
            command(client + ["pull", "--platform", "linux/" + native_arch, by_index])
            command(client + ["manifest", "push", "--insecure", target])
            retry = registry_request(origin, f"/v2/{repo}/manifests/multi")
            if retry.content != response.content:
                raise ValueError("identical manifest retry changed registry bytes")
            receipt = {
                "local_contract_only": True,
                "synthetic_images_only": True,
                "registry_image": REGISTRY_IMAGE,
                "oras_version": oras_version.strip(),
                "docker_versions": versions,
                "index_pull_architecture": native_arch,
                "buildx_version": command(
                    client + ["buildx", "version"]
                ).stdout.strip(),
                "index_digest": digest,
                "platforms": platforms,
                "blob_digests": sorted(blob_digests),
                "anonymous_read": "denied401",
                "wrong_auth": "denied",
                "wrong_digest": "rejected",
                "wrong_digest_api": "MANIFEST_UNKNOWN/404",
                "digest_pulls": "passed",
                "identical_retry": "unchanged",
                "oci_archive_sha256": archive_sha256,
                "oci_source_index_digest": archive_inventory["index_digest"],
                "oci_archive_platforms": archive_inventory["platforms"],
                "oci_import_index_digest": imported["index_digest"],
                "oci_import_manifest_digests": imported["manifest_digests"],
                "oci_import_blob_digests": imported["blob_digests"],
                "oci_archive_import": "byte-identical",
                "oci_import_wrong_digest": "rejected-before-upload",
                "oci_import_missing_variant": "rejected-before-upload",
                "oci_import_external_url": "rejected-before-upload",
                "oci_import_conflict": "preserved-existing-tag",
                "oci_import_retry": (
                    "unchanged"
                    if imported_retry["index_digest"] == imported["index_digest"]
                    else "changed"
                ),
                "single_platform_import": (
                    "manifest-digest-byte-identical"
                    if single_import["runnable_manifest_sha256"]
                    == single_inventory["runnable_manifest_sha256"]
                    else "mismatch"
                ),
                "single_platform_handoff_mismatch": "rejected-before-upload",
                "destination_preparation": "descriptor-to-import-readback-passed",
            }
            return receipt
        finally:
            for reference in refs:
                command(client + ["image", "rm", reference], check=False)
            if container_created:
                command(client + ["rm", "-f", "-v", identifier], check=False)
            if network_created:
                command(client + ["network", "rm", identifier], check=False)
            if (
                container_created
                and command(client + ["inspect", identifier], check=False).returncode
                == 0
            ):
                raise RuntimeError("owned registry container cleanup failed")
            if (
                network_created
                and command(
                    client + ["network", "inspect", identifier], check=False
                ).returncode
                == 0
            ):
                raise RuntimeError("owned fixture network cleanup failed")
            if any(
                command(client + ["image", "inspect", ref], check=False).returncode == 0
                for ref in refs
            ):
                raise RuntimeError("owned fixture image reference cleanup failed")
            if receipt is not None:
                receipt["cleanup_verified"] = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():
        raise ValueError("receipt output already exists")
    receipt = rehearse()
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        args.output.chmod(0o600)
    else:
        print(json.dumps(receipt, indent=2, sort_keys=True))
