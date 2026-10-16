# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Proactive token exchange for one independently configured HTTPS authority.

Registry challenges never choose URLs/scopes. The registry, not this opaque
token client, verifies the token signature and grants repository actions.
"""

from datetime import datetime, timedelta, timezone
import json
import importlib.util
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import requests

MAX_RESPONSE_BYTES = 64 * 1024
MAX_TOKEN_BYTES = 16 * 1024
MAX_TTL_SECONDS = 3600


_DEADLINE_SPEC = importlib.util.spec_from_file_location(
    "token_deadline", Path(__file__).with_name("http_deadline.py")
)
HTTP_DEADLINE = importlib.util.module_from_spec(_DEADLINE_SPEC)
_DEADLINE_SPEC.loader.exec_module(HTTP_DEADLINE)
REQUEST_SECONDS = 10


class BearerError(ValueError):
    """Sanitized failure; no server body, credential or token appears in errors."""


def validate_policy(policy, repository):
    if not isinstance(policy, dict) or set(policy) != {
        "endpoint",
        "service",
        "repository",
        "actions",
    }:
        raise BearerError("explicit complete token policy required")
    endpoint = policy["endpoint"]
    if not isinstance(endpoint, str) or not endpoint.isascii() or len(endpoint) > 2048:
        raise BearerError("token endpoint must be exact canonical HTTPS")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise BearerError("token endpoint must be exact canonical HTTPS") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in endpoint
        or "#" in endpoint
        or parsed.netloc != parsed.netloc.lower()
        or not re.fullmatch(
            r"[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[1-9][0-9]{0,4})?", parsed.netloc
        )
        or endpoint != "https://" + parsed.netloc + parsed.path
        or not re.fullmatch(r"/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*", parsed.path)
        or any(part in {".", ".."} for part in parsed.path.split("/"))
        or (port is not None and port == 0)
    ):
        raise BearerError("token endpoint must be exact canonical HTTPS")
    if (
        not isinstance(policy["service"], str)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", policy["service"])
        or policy["repository"] != repository
        or not isinstance(repository, str)
        or not re.fullmatch(
            r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*",
            repository,
        )
        or policy["actions"] != ["pull", "push"]
    ):
        raise BearerError(
            "token policy must bind the exact repository and pull/push actions"
        )
    return dict(policy)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise BearerError("token response repeats a field")
        value[key] = item
    return value


class Token:
    """Fetch once, cache until bounded expiry, never retry or refresh implicitly."""

    def __init__(self, policy, repository, credentials, *, ca_file=None, deadline=None):
        self.policy = validate_policy(policy, repository)
        if (
            not isinstance(deadline, datetime)
            or deadline.tzinfo is None
            or deadline.utcoffset() != timedelta(0)
        ):
            raise BearerError("explicit UTC publication deadline required")
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.auth = credentials
        self.session.verify = str(ca_file) if ca_file else True
        self.deadline = deadline
        self.value = None
        self.expires_at = None
        self.requests = 0

    def close(self):
        self.value = None
        self.session.close()

    def authorization(self):
        now = datetime.now(timezone.utc)
        if self.deadline is not None and now >= self.deadline:
            raise BearerError("token publication deadline expired")
        if self.value is None:
            if self.requests:
                raise BearerError(
                    "token exchange already attempted; no automatic retry"
                )
            self._fetch()
        if datetime.now(timezone.utc) >= self.expires_at:
            raise BearerError("token expired; no automatic refresh or replay")
        return "Bearer " + self.value

    def _fetch(self):
        self.requests += 1
        started = time.monotonic()
        try:
            expires = HTTP_DEADLINE.expires_after(REQUEST_SECONDS, self.deadline)
            with HTTP_DEADLINE.interrupt_at(expires):
                response = self.session.get(
                    self.policy["endpoint"],
                    headers={"Accept-Encoding": "identity"},
                    params={
                        "service": self.policy["service"],
                        "scope": "repository:"
                        + self.policy["repository"]
                        + ":pull,push",
                    },
                    timeout=(5, 5),
                    allow_redirects=False,
                    proxies={},
                    stream=True,
                )
                with response:
                    if response.status_code != 200 or response.headers.get("Location"):
                        raise BearerError("token HTTP response/redirect rejected")
                    if response.headers.get(
                        "Content-Encoding", ""
                    ).strip().lower() not in {
                        "",
                        "identity",
                    }:
                        raise BearerError("token response encoding rejected")
                    if (
                        response.headers.get("Content-Type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                        != "application/json"
                    ):
                        raise BearerError("token response must be JSON")
                    declared = response.headers.get("Content-Length")
                    if declared is not None and (
                        not declared.isdigit() or int(declared) > MAX_RESPONSE_BYTES
                    ):
                        raise BearerError("token response exceeds size bound")
                    body = bytearray()
                    # Byte-sized yields enforce the response-body time budget even on a
                    # slow peer that avoids inactivity timeouts by dripping bytes.
                    for chunk in response.iter_content(1):
                        body.extend(chunk)
                        if (
                            len(body) > MAX_RESPONSE_BYTES
                            or time.monotonic() - started > 10
                        ):
                            raise BearerError("token response exceeds size/time bound")
                        if (
                            self.deadline is not None
                            and datetime.now(timezone.utc) >= self.deadline
                        ):
                            raise BearerError("token publication deadline expired")
        except (requests.RequestException, OSError, HTTP_DEADLINE.DeadlineError):
            raise BearerError(
                "token request failed or deadline expired; no automatic retry"
            ) from None
        try:
            document = json.loads(
                body.decode("utf-8"), object_pairs_hook=_unique_object
            )
        except (ValueError, UnicodeDecodeError):
            raise BearerError("token response JSON rejected") from None
        if (
            not isinstance(document, dict)
            or not set(document).issubset(
                {"token", "access_token", "issued_at", "expires_in"}
            )
            or not {"issued_at", "expires_in"}.issubset(document)
            or not ({"token", "access_token"} & set(document))
        ):
            raise BearerError("token response has missing/unsupported fields")
        token = document.get("token", document.get("access_token"))
        if (
            not isinstance(token, str)
            or len(token) > MAX_TOKEN_BYTES
            or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token)
            or (
                "token" in document
                and "access_token" in document
                and document["token"] != document["access_token"]
            )
            or type(document["expires_in"]) is not int
            or not 1 <= document["expires_in"] <= MAX_TTL_SECONDS
            or not isinstance(document["issued_at"], str)
        ):
            raise BearerError("token value/expiry rejected")
        try:
            issued = datetime.fromisoformat(
                document["issued_at"].replace("Z", "+00:00")
            )
            if issued.tzinfo is None or issued.utcoffset() != timedelta(0):
                raise ValueError("not UTC")
        except ValueError:
            raise BearerError("token issue time rejected") from None
        now = datetime.now(timezone.utc)
        if issued > now + timedelta(seconds=5):
            raise BearerError("token is stale or future-issued")
        expiry = min(
            issued + timedelta(seconds=document["expires_in"]),
            now + timedelta(seconds=document["expires_in"]),
        )
        if expiry <= now:
            raise BearerError("token is stale or future-issued")
        if self.deadline is not None:
            expiry = min(expiry, self.deadline)
        self.value, self.expires_at = token, expiry
