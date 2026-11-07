"""Traffic recorder: every request the mock sees, redacted before it is stored (plan §17.3).

Nothing unredacted is stored or logged. ``Authorization`` becomes ``"[FAKE KEY]"`` when it
carried the configured fake key and ``"[REDACTED]"`` otherwise; other headers whose name looks
secret are replaced whole; every other string goes through ``core.redact``.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Mapping
from typing import Any

from flightcheck.core.models import Endpoint, RecordedExchange
from flightcheck.core.redact import REDACTED, redact, redact_obj

FAKE_KEY_MARK = "[FAKE KEY]"
DEFAULT_MAX_RECORDS = 1000
_SECRET_HEADER = re.compile(r"(?i)(key|token|secret|password|cookie|auth)")
_BEARER = re.compile(r"(?i)^\s*bearer\s+(.*?)\s*$")


def canonical_sha256(value: Any) -> str:
    """SHA-256 of canonical JSON (sorted keys, compact separators): stable across key order."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bearer_token(authorization: str | None) -> str | None:
    """The token of a ``Bearer`` header, or ``None`` when missing, blank or another scheme."""
    if authorization is None:
        return None
    match = _BEARER.match(authorization)
    if match is None or not match.group(1):
        return None
    return match.group(1)


def auth_status(authorization: str | None, fake_key: str | None) -> str:
    """``missing`` | ``fake_key`` | ``other_key`` | ``unchecked`` (no fake key configured)."""
    token = bearer_token(authorization)
    if token is None:
        return "missing"
    if fake_key is None:
        return "unchecked"
    return "fake_key" if token == fake_key else "other_key"


def redact_headers(headers: Mapping[str, str], fake_key: str | None) -> dict[str, str]:
    """Lower-cased header names; values safe to store."""
    safe: dict[str, str] = {}
    for name, value in headers.items():
        key = name.lower()
        if key == "authorization":
            token = bearer_token(value)
            fake = fake_key is not None and token == fake_key
            safe[key] = FAKE_KEY_MARK if fake else REDACTED
        elif _SECRET_HEADER.search(key):
            safe[key] = REDACTED
        else:
            safe[key] = redact(value)
    return safe


class TrafficRecorder:
    """Thread-safe, append-only log of :class:`RecordedExchange` items.

    Sequence numbers are handed out by :meth:`next_seq` when a request arrives, so they follow
    arrival order even if exchanges are appended later. After ``max_records`` exchanges, new
    ones are counted in :attr:`dropped` instead of stored (a runaway app cannot exhaust memory).
    """

    def __init__(self, *, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self._lock = threading.Lock()
        self._exchanges: list[RecordedExchange] = []
        self._seq = 0
        self._started = time.monotonic()
        self.max_records = max_records
        self.dropped = 0

    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    def append(self, exchange: RecordedExchange) -> None:
        with self._lock:
            if len(self._exchanges) >= self.max_records:
                self.dropped += 1
                return
            self._exchanges.append(exchange)

    def list(self) -> list[RecordedExchange]:
        """A snapshot copy, in append order."""
        with self._lock:
            return list(self._exchanges)

    def __len__(self) -> int:
        with self._lock:
            return len(self._exchanges)

    def record(
        self,
        *,
        seq: int,
        method: str,
        path: str,
        endpoint: Endpoint | None,
        headers: Mapping[str, str],
        fake_key: str | None,
        request_json: dict[str, Any] | None,
        raw_body: bytes,
        stream: bool,
        step_id: str,
        response_status: int,
        response_summary: dict[str, Any],
    ) -> RecordedExchange:
        """Redact, build and append one exchange. ``raw_body`` is hashed (redacted) only when
        the body was not a JSON object."""
        safe_json = redact_obj(request_json) if request_json is not None else None
        if safe_json is not None:
            digest = canonical_sha256(safe_json)
        else:
            digest = canonical_sha256(redact(raw_body.decode("utf-8", errors="replace")))
        exchange = RecordedExchange(
            seq=seq,
            at_ms=self.elapsed_ms(),
            method=method,
            path=redact(path),
            endpoint=endpoint,
            request_headers=redact_headers(headers, fake_key),
            request_json=safe_json,
            request_sha256=digest,
            stream=stream,
            step_id=step_id,
            response_status=response_status,
            response_summary=redact_obj(response_summary),
        )
        self.append(exchange)
        return exchange
