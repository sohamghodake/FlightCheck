"""Test doubles for the provider adapters (imported by the tests in this directory).

No sockets and no real network (ADR-016).

``mock_transport`` bridges the in-process mock to a sync ``httpx2.MockTransport``: the handler
forwards each request to ``fastapi.testclient.TestClient(create_mock_app(...))`` (a sync
``httpx2.Client``; ``ASGITransport`` is async-only) and returns a fresh ``httpx2.Response``
whose body arrives in small chunks, so SSE line buffering across chunk borders is exercised.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import httpx2
from fastapi.testclient import TestClient
from pydantic import SecretStr

from flightcheck.core.config import BHARATCODE_BASE_URL, ProviderConfig, RetryPolicy
from flightcheck.mock.app import create_mock_app
from flightcheck.mock.faults import FaultScript, MockAction, MockStep
from flightcheck.mock.recorder import TrafficRecorder
from flightcheck.providers.bharatcode import BharatCodeAdapter

FAKE_KEY = "fc-test-key-not-a-secret"
_SKIP_REQUEST_HEADERS = {"host", "content-length", "transfer-encoding"}


def chunked(body: bytes, size: int) -> Iterator[bytes]:
    for start in range(0, len(body), size):
        yield body[start : start + size]


@dataclass
class Bridge:
    """The mock behind a ``MockTransport``; ``recorder`` holds what the mock saw."""

    transport: httpx2.MockTransport
    recorder: TrafficRecorder
    requests: list[httpx2.Request] = field(default_factory=list)


def mock_transport(
    script: FaultScript | None = None,
    *,
    fake_key: str | None = FAKE_KEY,
    chunk_size: int = 7,
) -> Bridge:
    recorder = TrafficRecorder()
    app = create_mock_app(script or FaultScript(), recorder, fake_key=fake_key)
    client = TestClient(app)
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() not in _SKIP_REQUEST_HEADERS
        }
        answer = client.request(
            request.method,
            request.url.raw_path.decode("ascii"),
            content=request.content,
            headers=headers,
        )
        out_headers = [
            (name, value)
            for name, value in answer.headers.multi_items()
            if name.lower() not in {"content-length", "transfer-encoding"}
        ]
        return httpx2.Response(
            answer.status_code,
            headers=out_headers,
            content=chunked(answer.content, chunk_size),
        )

    return Bridge(httpx2.MockTransport(handler), recorder, seen)


def raw_transport(
    status: int = 200,
    body: bytes | Callable[[httpx2.Request], bytes] = b"{}",
    headers: dict[str, str] | None = None,
    chunk_size: int = 5,
) -> tuple[httpx2.MockTransport, list[httpx2.Request]]:
    """A transport that answers every request with a fixed (or computed) body."""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        content = body(request) if callable(body) else body
        return httpx2.Response(status, headers=headers or {}, content=chunked(content, chunk_size))

    return httpx2.MockTransport(handler), seen


def make_config(
    api_key: str | None = FAKE_KEY, *, max_attempts: int = 3, **overrides: object
) -> ProviderConfig:
    return ProviderConfig(
        name="bharatcode",
        kind="bharatcode",
        base_url=BHARATCODE_BASE_URL,
        api_key=SecretStr(api_key) if api_key is not None else None,
        retry=RetryPolicy(max_attempts=max_attempts),
        **overrides,  # type: ignore[arg-type]
    )


class Sleeps(list[float]):
    def __call__(self, seconds: float) -> None:
        self.append(seconds)


def make_adapter(
    transport: httpx2.BaseTransport, *, api_key: str | None = FAKE_KEY, max_attempts: int = 3
) -> tuple[BharatCodeAdapter, Sleeps]:
    sleeps = Sleeps()
    adapter = BharatCodeAdapter(
        make_config(api_key, max_attempts=max_attempts),
        transport=transport,
        sleep=sleeps,
        jitter=lambda: 1.0,
    )
    return adapter, sleeps


def steps(*actions: MockAction | tuple[MockAction, dict]) -> FaultScript:
    built = []
    for index, action in enumerate(actions):
        if isinstance(action, tuple):
            built.append(MockStep(id=f"s{index}", action=action[0], params=action[1]))
        else:
            built.append(MockStep(id=f"s{index}", action=action))
    return FaultScript(steps=built)
