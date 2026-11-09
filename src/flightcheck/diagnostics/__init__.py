"""AI diagnostics for ``flightcheck explain`` (plan §19).

Downstream only: reads a finished report and returns a copy with ``ai_suggestions`` filled in.
Nothing here may add, remove or change a finding, check, status or severity (ADR-019).
Imports only ``core`` and ``providers`` (docs/ARCHITECTURE.md §3).
"""

from flightcheck.diagnostics.service import (
    ConsentInfo,
    DiagnosticService,
    ExplainOptions,
    ExplainPlan,
)

__all__ = ["ConsentInfo", "DiagnosticService", "ExplainOptions", "ExplainPlan"]
