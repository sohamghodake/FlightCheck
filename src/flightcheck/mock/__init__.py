"""Contract-aware BharatCode mock provider (plan §18).

Imports only ``flightcheck.core`` (docs/ARCHITECTURE.md §3). ``create_mock_app`` builds the
FastAPI app (JSON and SSE answers); ``MockServer`` serves it on 127.0.0.1 in a daemon thread.
"""

from flightcheck.mock.app import create_mock_app
from flightcheck.mock.faults import FaultScript, MockAction, MockStep
from flightcheck.mock.recorder import TrafficRecorder, canonical_sha256
from flightcheck.mock.server import MockServer, MockServerError

__all__ = [
    "FaultScript",
    "MockAction",
    "MockServer",
    "MockServerError",
    "MockStep",
    "TrafficRecorder",
    "canonical_sha256",
    "create_mock_app",
]
