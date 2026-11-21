"""Low-frequency, process-local lookup of the published release."""
from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ..json_codec import decode_strict_json

_SUCCESS_TTL = 21600
_FAILURE_TTL = 300
_TIMEOUT = 3
_MAX_RESPONSE_BYTES = 512 * 1024
_URL = "https://pypi.org/pypi/hey-my-buddy/json"
_VERSION = re.compile(r"(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?"
                      r"(?:\.post[0-9]+)?(?:\.dev[0-9]+)?(?:\+[a-z0-9]+(?:\.[a-z0-9]+)*)?")
_UNAVAILABLE = {"publishedVersion": None, "checkedAt": None}


def _checks_disabled() -> bool:
    return (os.environ.get("HMB_DISABLE_PYPI_CHECK") == "1"
            or "HMB_CHECKS_TMPDIR" in os.environ or "PYTEST_CURRENT_TEST" in os.environ)


def _fetch_version() -> str | None:
    request = Request(_URL, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=_TIMEOUT) as response:
            if response.status != 200:
                return None
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            return None
        payload = decode_strict_json(raw)
        info = payload.get("info") if isinstance(payload, dict) else None
        version = info.get("version") if isinstance(info, dict) else None
        if isinstance(version, str) and len(version) <= 128 and _VERSION.fullmatch(version):
            return version
    except HTTPError as error:
        error.close()
        return None
    except (OSError, HTTPException, ValueError, RecursionError):
        return None
    return None


class _Cache:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._refreshing: threading.Event | None = None
        self._expiry = float("-inf")
        self._result = _UNAVAILABLE

    def _refresh(self, done: threading.Event) -> None:
        try:
            version = _fetch_version()
            with self._lock:
                if version is None:
                    self._result = _UNAVAILABLE
                    self._expiry = self._clock() + _FAILURE_TTL
                else:
                    checked_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
                    self._result = {"publishedVersion": version, "checkedAt": checked_at}
                    self._expiry = self._clock() + _SUCCESS_TTL
        finally:
            with self._lock:
                self._refreshing = None
                done.set()

    def read(self) -> dict[str, str | None]:
        if _checks_disabled():
            return _UNAVAILABLE.copy()
        with self._lock:
            if self._clock() < self._expiry:
                return self._result.copy()
            if self._refreshing is None:
                self._refreshing = threading.Event()
                threading.Thread(target=self._refresh, args=(self._refreshing,), daemon=True).start()
            done = self._refreshing
        # The HTTP timeout alone cannot bound a platform DNS lookup or a slow stream.
        if not done.wait(_TIMEOUT):
            with self._lock:
                if self._refreshing is done:
                    self._result = _UNAVAILABLE
                    self._expiry = self._clock() + _FAILURE_TTL
            return _UNAVAILABLE.copy()
        with self._lock:
            return self._result.copy() if self._clock() < self._expiry else _UNAVAILABLE.copy()


_cache = _Cache()


def published_version() -> dict[str, str | None]:
    return _cache.read()
