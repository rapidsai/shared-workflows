# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""POSIX main-thread interruption for one absolute HTTP operation budget."""

from contextlib import contextmanager
from datetime import datetime, timezone
import math
import signal
import threading
import time


class DeadlineError(RuntimeError):
    """Sanitized deadline or unsupported execution-context failure."""


def expires_after(seconds, publication_deadline=None):
    if (
        not isinstance(seconds, (int, float))
        or not math.isfinite(seconds)
        or seconds <= 0
    ):
        raise DeadlineError("invalid HTTP request budget")
    if publication_deadline is not None:
        seconds = min(
            seconds,
            (publication_deadline - datetime.now(timezone.utc)).total_seconds(),
        )
    return time.monotonic() + seconds


@contextmanager
def interrupt_at(expires):
    """Never take ownership of another caller's alarm or run in a worker thread."""
    if threading.current_thread() is not threading.main_thread() or not all(
        hasattr(signal, name)
        for name in ("SIGALRM", "ITIMER_REAL", "setitimer", "getitimer")
    ):
        raise DeadlineError("HTTP deadline requires a POSIX main thread")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise DeadlineError("HTTP deadline refuses an existing real-time alarm")
    remaining = expires - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise DeadlineError("HTTP request deadline expired; no automatic retry")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(_signum, _frame):
        raise DeadlineError("HTTP request deadline expired; no automatic retry")

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, remaining)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
