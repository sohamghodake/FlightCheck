"""MockServer: 127.0.0.1 only, free ports, repeatable start/stop, no thread leaks (5B)."""

from __future__ import annotations

import socket
import threading
import time

import httpx2
import pytest

from flightcheck.core.errors import FlightCheckError
from flightcheck.mock import FaultScript, MockAction, MockServer, MockServerError, MockStep

pytestmark = pytest.mark.slow

FAKE = "fc_fake"
AUTH = {"Authorization": f"Bearer {FAKE}"}


def client() -> httpx2.Client:
    # trust_env=False: a developer's HTTP(S)_PROXY must never capture localhost traffic.
    return httpx2.Client(trust_env=False, timeout=5)


def refused(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return False
    except OSError:
        return True


def test_binds_loopback_only_and_serves() -> None:
    with MockServer(fake_key=FAKE) as server, client() as http:
        assert server.host == "127.0.0.1"
        assert server._socket is not None
        assert server._socket.getsockname()[0] == "127.0.0.1"
        assert server.base_url == f"http://127.0.0.1:{server.port}/api/model/v1"
        resp = http.get(f"{server.base_url}/models", headers=AUTH)
        assert resp.status_code == 200 and resp.headers["x-request-id"] == "fc_mock_1"
        assert [e.endpoint for e in server.recorder.list()] == ["models"]


def test_start_stop_five_times_without_leaks() -> None:
    baseline = threading.active_count()
    ports = []
    for _ in range(5):
        with MockServer(fake_key=FAKE) as server, client() as http:
            port = server.port
            assert port is not None
            ports.append(port)
            assert http.get(f"{server.base_url}/models", headers=AUTH).status_code == 200
        assert refused(port)  # the listening socket is closed
    assert abs(threading.active_count() - baseline) <= 1
    assert not [t for t in threading.enumerate() if t.name.startswith("flightcheck-mock-")]


def test_two_servers_in_one_process() -> None:
    one = MockServer(FaultScript(steps=[MockStep(id="a", action=MockAction.TRUNCATE)]))
    two = MockServer(FaultScript(steps=[MockStep(id="b", action=MockAction.HTTP_ERROR)]))
    with one, two, client() as http:
        assert one.port != two.port
        body = {"model": "qwen-3.8-27b", "input": "hi", "store": False}
        r1 = http.post(f"{one.base_url}/responses", json=body, headers=AUTH)
        r2 = http.post(f"{two.base_url}/responses", json=body, headers=AUTH)
        assert (r1.status_code, r2.status_code) == (200, 503)
        assert r1.json()["status"] == "incomplete"
        assert [e.step_id for e in one.recorder.list()] == ["a"]
        assert [e.step_id for e in two.recorder.list()] == ["b"]


def test_streams_over_a_real_socket() -> None:
    script = FaultScript(steps=[MockStep(id="k", action=MockAction.KEEPALIVE_STREAM)])
    with MockServer(script, fake_key=FAKE) as server, client() as http:
        body = {"model": "qwen-3.8-27b", "input": "hi", "store": False, "stream": True}
        with http.stream("POST", f"{server.base_url}/responses", json=body, headers=AUTH) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            raw = b"".join(r.iter_bytes())
    assert raw.startswith(b": keepalive\n\n")
    assert b"event: response.completed\n" in raw
    (ex,) = server.recorder.list()
    assert ex.response_summary["finish"] == "completed" and ex.stream is True


def test_stop_with_open_stream_returns_quickly() -> None:
    long_text = " ".join(f"w{i}" for i in range(20000))
    script = FaultScript(
        steps=[MockStep(id="l", action=MockAction.OK_TEXT, params={"text": long_text})]
    )
    server = MockServer(script, fake_key=FAKE).start()
    http = client()
    body = {"model": "qwen-3.8-27b", "input": "hi", "store": False, "stream": True}
    with http.stream("POST", f"{server.base_url}/responses", json=body, headers=AUTH) as r:
        next(r.iter_bytes())
        started = time.monotonic()
        server.stop()
        assert time.monotonic() - started < 10
    http.close()
    assert server._thread is None
    (ex,) = server.recorder.list()  # the cut-off stream is still recorded, exactly once
    assert ex.response_summary["finish"] == "client_disconnected"
    assert ex.response_summary["chunks_sent"] < ex.response_summary["chunks_planned"]


def test_startup_timeout_names_the_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MockServer, "_run", lambda self: time.sleep(0.3))
    server = MockServer(startup_timeout_s=0.1)
    with pytest.raises(MockServerError) as exc:
        server.start()
    assert isinstance(exc.value, FlightCheckError) and exc.value.exit_code == 3
    assert "127.0.0.1:" in str(exc.value) and str(server.port) in str(exc.value)
    assert "did not start within 0.1 s" in str(exc.value)
    assert server._thread is None and server._socket is None


def test_startup_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: MockServer) -> None:
        self._error = OSError("boom")

    monkeypatch.setattr(MockServer, "_run", broken)
    with pytest.raises(MockServerError, match="failed to start \\(OSError\\)"):
        MockServer().start()


def test_double_start_and_unstarted_url() -> None:
    server = MockServer()
    with pytest.raises(RuntimeError):
        _ = server.base_url
    with server:
        with pytest.raises(RuntimeError):
            server.start()
    server.stop()  # idempotent
