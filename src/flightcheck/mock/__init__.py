"""Contract-aware BharatCode mock provider (plan §18).

Imports only ``flightcheck.core`` (docs/ARCHITECTURE.md §3). Phase 5A provides the non-streaming
endpoints; streaming and the threaded server runner arrive in Phase 5B.
"""

from flightcheck.mock.app import create_mock_app
from flightcheck.mock.faults import FaultScript, MockAction, MockStep
from flightcheck.mock.recorder import TrafficRecorder, canonical_sha256

__all__ = [
    "FaultScript",
    "MockAction",
    "MockStep",
    "TrafficRecorder",
    "canonical_sha256",
    "create_mock_app",
]
