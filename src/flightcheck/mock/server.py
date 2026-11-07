"""``MockServer``: the mock app served by Uvicorn on 127.0.0.1 in a daemon thread (plan §18.1).

The socket is bound here (``127.0.0.1``, port 0 → a free port chosen by the OS) and handed to
Uvicorn, so there is no window in which another process can take the port. Uvicorn 0.54 installs
signal handlers only on the main thread (``Server.capture_signals``), so nothing has to be
disabled when it runs in this thread.
"""

from __future__ import annotations

import socket
import threading
import time
from types import TracebackType

import uvicorn

from flightcheck.core.errors import FlightCheckError
from flightcheck.mock.app import DEFAULT_BASE_PATH, DEFAULT_MAX_REQUESTS, create_mock_app
from flightcheck.mock.faults import FaultScript
from flightcheck.mock.recorder import TrafficRecorder

HOST = "127.0.0.1"
STARTUP_TIMEOUT_S = 5.0
SHUTDOWN_TIMEOUT_S = 5.0
_GRACEFUL_SHUTDOWN_S = 2  # open streams get this long before Uvicorn drops them
_POLL_S = 0.01


class MockServerError(FlightCheckError):
    """The mock server could not start (exit code 3)."""


class MockServer:
    """Context manager running the mock on ``http://127.0.0.1:<free port>``.

    ``base_url`` is ``http://127.0.0.1:<port>/api/model/v1``; ``recorder`` holds the traffic.
    A streamed answer is recorded just after its last byte is sent, so a fast client can finish
    reading before its exchange appears: read the recorder after :meth:`stop`, which waits for
    in-flight requests (Uvicorn graceful shutdown).
    """

    def __init__(
        self,
        script: FaultScript | None = None,
        *,
        fake_key: str | None = None,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        base_path: str = DEFAULT_BASE_PATH,
        recorder: TrafficRecorder | None = None,
        startup_timeout_s: float = STARTUP_TIMEOUT_S,
    ) -> None:
        self.script = script if script is not None else FaultScript()
        self.recorder = recorder if recorder is not None else TrafficRecorder()
        self.fake_key = fake_key
        self.host = HOST
        self.port: int | None = None
        self._base_path = "/" + base_path.strip("/") if base_path.strip("/") else ""
        self._startup_timeout_s = startup_timeout_s
        self._app = create_mock_app(
            self.script,
            self.recorder,
            base_path=base_path,
            fake_key=fake_key,
            max_requests=max_requests,
        )
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._error: BaseException | None = None

    @property
    def origin(self) -> str:
        if self.port is None:
            raise RuntimeError("MockServer is not running")
        return f"http://{self.host}:{self.port}"

    @property
    def base_url(self) -> str:
        return self.origin + self._base_path

    def start(self) -> MockServer:
        if self._thread is not None:
            raise RuntimeError("MockServer is already running")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((HOST, 0))
        self._socket = sock
        self.port = sock.getsockname()[1]
        config = uvicorn.Config(
            self._app,
            host=HOST,
            port=self.port,
            log_level="warning",
            access_log=False,
            http="h11",
            ws="none",
            lifespan="off",
            timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_S,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._run, name=f"flightcheck-mock-{self.port}", daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + self._startup_timeout_s
        while not self._server.started:
            if self._error is not None or not self._thread.is_alive():
                self.stop()
                raise MockServerError(
                    f"Mock server on {HOST}:{self.port} failed to start "
                    f"({type(self._error).__name__ if self._error else 'thread exited'})."
                )
            if time.monotonic() > deadline:
                port = self.port
                self.stop()
                raise MockServerError(
                    f"Mock server on {HOST}:{port} did not start within "
                    f"{self._startup_timeout_s:g} s."
                )
            time.sleep(_POLL_S)
        return self

    def _run(self) -> None:
        assert self._server is not None and self._socket is not None
        try:
            self._server.run(sockets=[self._socket])
        except BaseException as exc:  # noqa: BLE001 - reported by start()
            self._error = exc

    def stop(self) -> None:
        """Ask Uvicorn to exit and join the thread (≤ 5 s, then force)."""
        server, thread = self._server, self._thread
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(SHUTDOWN_TIMEOUT_S)
            if thread.is_alive() and server is not None:
                server.force_exit = True
                thread.join(SHUTDOWN_TIMEOUT_S)
        if self._socket is not None:
            self._socket.close()
        self._server = None
        self._thread = None
        self._socket = None

    def __enter__(self) -> MockServer:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()
