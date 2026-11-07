"""Runtime validation: run the app against the mock, then check the recorded traffic.

``harness`` is the only module in FlightCheck that starts repository code, and only as a
consented child process (ADR-005, ADR-017). ``validators`` are pure functions over recorded
traffic; ``scenarios`` is the fault-script catalogue. Imports: ``core`` and ``mock`` only
(docs/ARCHITECTURE.md §3); the orchestrator passes in anything the scanner knows.
"""

from flightcheck.runtime.harness import (
    AppLaunchError,
    build_child_env,
    ensure_consent,
    make_fake_key,
    run_app,
)
from flightcheck.runtime.scenarios import SCENARIOS, Scenario
from flightcheck.runtime.validators import CheckConfig, evaluate, report_once, to_finding

__all__ = [
    "SCENARIOS",
    "AppLaunchError",
    "CheckConfig",
    "Scenario",
    "build_child_env",
    "ensure_consent",
    "evaluate",
    "make_fake_key",
    "report_once",
    "run_app",
    "to_finding",
]
