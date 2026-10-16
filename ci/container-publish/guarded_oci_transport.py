# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded Basic/Bearer OCI transport; no redirects or ambient auth.

This is a source-only publication adapter, not a deployment or approval gate.
All policy/handoff expectations must come from an authenticated caller.
"""

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tarfile
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests

_SPEC = importlib.util.spec_from_file_location(
    "transport_preparation", Path(__file__).with_name("hosted_preparation.py")
)
PREPARATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(PREPARATION)
_BEARER_SPEC = importlib.util.spec_from_file_location(
    "guarded_bearer", Path(__file__).with_name("guarded_oci_bearer.py")
)
BEARER = importlib.util.module_from_spec(_BEARER_SPEC)
_BEARER_SPEC.loader.exec_module(BEARER)
MAX_BLOBS = 128


class TransportError(ValueError):
    """A sanitized failure; response bodies/locations/credentials are never logged."""


class Registry:
    """Only exact repository API requests; server URLs never bypass this guard."""

    def __init__(
        self,
        origin,
        repository,
        credentials,
        *,
        ca_file=None,
        deadline=None,
        bearer_policy=None,
        token_ca_file=None,
    ):
        if (
            not isinstance(credentials, tuple)
            or len(credentials) != 2
            or any(
                not isinstance(value, str)
                or not value
                or not value.isascii()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
                for value in credentials
            )
            or ":" in credentials[0]
        ):
            raise TransportError("explicit valid Basic credentials are required")
        self.bearer = None
        if bearer_policy is not None:
            if not origin.startswith("https://"):
                raise TransportError("Bearer registry origin must use verified HTTPS")
            try:
                self.bearer = BEARER.Token(
                    bearer_policy,
                    repository,
                    credentials,
                    ca_file=token_ca_file,
                    deadline=deadline,
                )
            except BEARER.BearerError:
                raise TransportError("explicit Bearer token policy rejected") from None
        elif token_ca_file is not None:
            raise TransportError("token CA requires an explicit Bearer policy")
        self.origin = origin
        self.prefix = "/v2/" + repository + "/"
        self.session = requests.Session()
        # Disable implicit netrc/proxies before prepare_request runs.
        self.session.trust_env = False
        self.session.auth = None if self.bearer else credentials
        self.session.verify = str(ca_file) if ca_file else True
        self.requests = 0
        self.deadline = deadline

    def close(self):
        self.session.close()
        if self.bearer:
            self.bearer.close()

    def _url(self, value, *, uploading=False, finalizing=False):
        if (
            not isinstance(value, str)
            or len(value) > 8192
            or any(ord(char) < 33 or ord(char) > 126 for char in value)
        ):
            raise TransportError("registry URL is malformed")
        parsed = urlsplit(value)
        if value.startswith("/") and not value.startswith("//"):
            value = self.origin + value
            parsed = urlsplit(value)
        if (
            parsed.scheme + "://" + parsed.netloc != self.origin
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or "#" in value
            or "%" in parsed.path
            or "\\" in value
        ):
            raise TransportError("registry origin/redirect/location boundary rejected")
        escaped = re.escape(self.prefix)
        if uploading:
            pattern = escaped + r"blobs/uploads/[A-Za-z0-9_-]+"
        else:
            pattern = (
                escaped
                + r"(?:blobs/sha256:[a-f0-9]{64}|blobs/uploads/|manifests/(?:sha256:[a-f0-9]{64}|[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))"
            )
        if not re.fullmatch(pattern, parsed.path):
            raise TransportError("registry repository/path boundary rejected")
        try:
            query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            raise TransportError("registry upload query is malformed") from None
        if len({key for key, _value in query}) != len(query):
            raise TransportError("registry upload query has duplicate keys")
        for key, content in query:
            if not uploading or key not in (
                {"_state", "digest"} if finalizing else {"_state"}
            ):
                raise TransportError("unsupported registry upload query")
            if key == "digest":
                if not re.fullmatch(r"sha256:[a-f0-9]{64}", content):
                    raise TransportError("registry upload digest is malformed")
            elif not re.fullmatch(r"[A-Za-z0-9_.~=-]+", content):
                raise TransportError("registry upload state is malformed")
        return value

    def request(
        self, method, path, statuses, *, uploading=False, finalizing=False, **kwargs
    ):
        if self.deadline is not None and datetime.now(timezone.utc) >= self.deadline:
            raise TransportError(
                "destination descriptor expired before registry request"
            )
        url = self._url(path, uploading=uploading, finalizing=finalizing)
        if self.bearer:
            try:
                authorization = self.bearer.authorization()
            except BEARER.BearerError as error:
                raise TransportError(str(error)) from None
            kwargs["headers"] = {
                **kwargs.get("headers", {}),
                "Authorization": authorization,
            }
        try:
            response = self.session.request(
                method,
                url,
                timeout=(5, 30),
                allow_redirects=False,
                stream=True,
                proxies={},
                **kwargs,
            )
        except (requests.RequestException, OSError):
            raise TransportError(
                "registry request failed; no automatic retry"
            ) from None
        self.requests += 1
        if 300 <= response.status_code < 400:
            response.close()
            raise TransportError("registry redirect rejected")
        challenge = response.headers.get("WWW-Authenticate", "")
        if challenge and (
            self.bearer
            or not re.fullmatch(
                r'Basic(?:\s+realm="[^"\r\n]*")?', challenge, re.IGNORECASE
            )
        ):
            response.close()
            raise TransportError("unsupported registry authentication challenge")
        if response.status_code not in statuses:
            status = response.status_code
            response.close()
            raise TransportError(f"registry returned unexpected HTTP status {status}")
        location = response.headers.get("Location")
        if location:
            try:
                self._url(location, uploading=method == "POST")
            except ValueError:
                response.close()
                raise TransportError("registry response Location rejected") from None
        return response

    @staticmethod
    def read(response, expected_digest, size):
        digest = hashlib.sha256()
        count = 0
        try:
            with response:
                for chunk in response.iter_content(1024 * 1024):
                    count += len(chunk)
                    if count > size:
                        raise TransportError("registry readback exceeds expected size")
                    digest.update(chunk)
        except requests.RequestException:
            raise TransportError(
                "registry readback failed; no automatic retry"
            ) from None
        if count != size or digest.hexdigest() != expected_digest:
            raise TransportError("registry readback bytes differ from admitted digest")

    def manifest_exists(self, tag, content, digest):
        response = self.request(
            "GET",
            self.prefix + "manifests/" + tag,
            {200, 404},
            headers={"Accept": "application/vnd.oci.image.manifest.v1+json"},
        )
        if response.status_code == 404:
            response.close()
            return False
        self.read(response, digest, len(content))
        return True

    def blob(self, digest, path):
        with self.request(
            "HEAD", self.prefix + "blobs/sha256:" + digest, {200, 404}
        ) as response:
            exists = response.status_code == 200
        if not exists:
            with self.request(
                "POST",
                self.prefix + "blobs/uploads/",
                {202},
                headers={"Content-Length": "0"},
            ) as response:
                location = self._url(response.headers.get("Location"), uploading=True)
            separator = "&" if urlsplit(location).query else "?"
            location += separator + urlencode({"digest": "sha256:" + digest})
            with path.open("rb") as stream:
                with self.request(
                    "PUT",
                    location,
                    {201},
                    uploading=True,
                    finalizing=True,
                    data=stream,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(path.stat().st_size),
                    },
                ) as response:
                    if (
                        response.headers.get("Docker-Content-Digest")
                        != "sha256:" + digest
                    ):
                        raise TransportError("registry upload digest receipt differs")
        response = self.request("GET", self.prefix + "blobs/sha256:" + digest, {200})
        self.read(response, digest, path.stat().st_size)


def publish_prepared(
    policy,
    descriptor,
    archive,
    output,
    *,
    credentials,
    policy_sha256,
    handoff_sha256,
    train_sha256,
    identity,
    loopback_fixture=False,
    now=None,
    ca_file=None,
    bearer_policy=None,
    token_ca_file=None,
):
    """Revalidate a sealed handoff, then publish exact bytes using guarded HTTP.

    Basic is the default; optional Bearer uses only an independently configured
    HTTPS token authority. No refresh, credential lookup, POST retries, remote
    cleanup or concurrent-writer guarantee. Unexpected responses stop immediately.
    """
    receipt, directory = PREPARATION.prepare(
        policy,
        descriptor,
        archive,
        output,
        policy_sha256=policy_sha256,
        handoff_sha256=handoff_sha256,
        train_sha256=train_sha256,
        identity=identity,
        loopback_fixture=loopback_fixture,
        now=now,
    )
    sealed = directory / "archive.oci"
    inventory = PREPARATION.OCI.inspect_single_platform_oci_archive(
        sealed, receipt["transport_sha256"], directory
    )
    needed = {inventory["config_sha256"], *inventory["layer_sha256s"]}
    if len(needed) > MAX_BLOBS:
        raise TransportError("image exceeds the guarded transport blob limit")
    # Only verified blob members are copied, never tar paths/extractall.
    with tempfile.TemporaryDirectory(prefix="upload-", dir=output) as temporary:
        blobs = {}
        with tarfile.open(sealed, "r|*") as bundle:
            for member in bundle:
                digest = member.name.removeprefix("blobs/sha256/")
                if digest not in needed:
                    continue
                stream = bundle.extractfile(member)
                if stream is None:
                    raise TransportError("verified archive blob is unreadable")
                path = Path(temporary) / digest
                checksum = hashlib.sha256()
                count = 0
                with path.open("xb") as destination:
                    while chunk := stream.read(1024 * 1024):
                        count += len(chunk)
                        if count > member.size:
                            raise TransportError("archive blob exceeds verified size")
                        checksum.update(chunk)
                        destination.write(chunk)
                if checksum.hexdigest() != digest or count != member.size:
                    raise TransportError("archive blob changed after preparation")
                blobs[digest] = path
        if set(blobs) != needed:
            raise TransportError("archive is missing verified upload blobs")
        registry = Registry(
            policy["origin"],
            descriptor["repository"],
            credentials,
            ca_file=ca_file,
            deadline=PREPARATION._time(descriptor["expires_at"]),
            bearer_policy=bearer_policy,
            token_ca_file=token_ca_file,
        )
        try:
            content = inventory["manifest_bytes"]
            manifest_digest = inventory["runnable_manifest_sha256"]
            existed = registry.manifest_exists(
                descriptor["tag"], content, manifest_digest
            )
            for digest, path in sorted(blobs.items()):
                registry.blob(digest, path)
            if not existed and not registry.manifest_exists(
                descriptor["tag"], content, manifest_digest
            ):
                with registry.request(
                    "PUT",
                    registry.prefix + "manifests/" + descriptor["tag"],
                    {201},
                    data=content,
                    headers={"Content-Type": json.loads(content)["mediaType"]},
                ) as response:
                    if (
                        response.headers.get("Docker-Content-Digest")
                        != "sha256:" + manifest_digest
                    ):
                        raise TransportError("registry manifest digest receipt differs")
            if not registry.manifest_exists(
                descriptor["tag"], content, manifest_digest
            ):
                raise TransportError("published manifest cannot be read back")
            auth = (
                {}
                if registry.bearer is None
                else {
                    "authentication": {
                        "mode": "bearer",
                        "token_requests": registry.bearer.requests,
                    }
                }
            )
            return {
                **receipt,
                **auth,
                "state": "uploaded-and-read-back",
                "http_requests": registry.requests
                + (registry.bearer.requests if registry.bearer else 0),
                "transport": "guarded-bearer-oci"
                if registry.bearer
                else "guarded-basic-oci",
                "identical_retry": existed,
            }
        finally:
            registry.close()
