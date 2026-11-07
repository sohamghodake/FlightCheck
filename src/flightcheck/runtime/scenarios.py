"""Scenario catalogue (plan §18.3): fault script + the checks each scenario feeds.

Phase 7A implements S01, S02 and S04. S03, S05 and S06 arrive in Phase 7B; S02b and S07–S10 are
P1. Those IDs are known (so a typo is a ``ConfigError`` but a later scenario is not), and the
orchestrator reports them with a ``Diagnostic`` instead of a fake result (ADR-029).

Every ``script_factory`` returns a **new** :class:`FaultScript`: a script keeps a cursor, and a
scenario must never see steps another run already consumed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from flightcheck.mock.faults import FaultScript, MockAction, MockStep

# Checks evaluated in every scenario (§17.4 "Applies in: all") and reported once per run
# (validators.report_once, ADR-029); new offending requests in a later scenario are still shown.
CONTRACT_CHECK_IDS: tuple[str, ...] = (
    "FC-RT-001",
    "FC-RT-002",
    "FC-RT-003",
    "FC-RT-004",
    "FC-RT-005",
    "FC-RT-006",
    "FC-RT-007",
)
EVERY_SCENARIO_CHECK_IDS: tuple[str, ...] = (*CONTRACT_CHECK_IDS, "FC-RT-050", "FC-RT-060")

BUSY_STEP_PREFIX = "busy_"
TOOL_STEP_ID = "tool_call"


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    script_factory: Callable[[], FaultScript]
    check_ids: tuple[str, ...]  # scenario-specific checks (plus EVERY_SCENARIO_CHECK_IDS)
    default_expectations: str
    priority: Literal["P0", "P1"]

    @property
    def all_check_ids(self) -> tuple[str, ...]:
        """Scenario checks first, then the every-scenario checks (no duplicates)."""
        extra = tuple(c for c in EVERY_SCENARIO_CHECK_IDS if c not in self.check_ids)
        return (*self.check_ids, *extra)


def _step(step_id: str, action: MockAction, scenario: str, **params: object) -> MockStep:
    return MockStep(id=step_id, action=action, params={"scenario": scenario, **params})


def _default(scenario: str) -> MockStep:
    return _step("default", MockAction.OK_TEXT, scenario)


def _s01_script() -> FaultScript:
    return FaultScript(
        steps=[_step("baseline", MockAction.OK_TEXT, "S01")], default_step=_default("S01")
    )


def _s02_script() -> FaultScript:
    # BharatCode documents 503 as "model busy: wait a few seconds and retry" (contract §15.1).
    return FaultScript(
        steps=[
            _step(f"{BUSY_STEP_PREFIX}1", MockAction.HTTP_ERROR, "S02", status=503),
            _step(f"{BUSY_STEP_PREFIX}2", MockAction.HTTP_ERROR, "S02", status=503),
            _step("recover", MockAction.OK_TEXT, "S02"),
        ],
        default_step=_default("S02"),
    )


def _s04_script() -> FaultScript:
    return FaultScript(
        steps=[
            _step(TOOL_STEP_ID, MockAction.TOOL_CALL, "S04"),
            _step("answer", MockAction.OK_TEXT, "S04"),
        ],
        default_step=_default("S04"),
    )


S01 = Scenario(
    id="S01",
    name="Baseline",
    script_factory=_s01_script,
    check_ids=("FC-RT-008",),
    default_expectations="app exits 0",
    priority="P0",
)
S02 = Scenario(
    id="S02",
    name="Busy then recover",
    script_factory=_s02_script,
    check_ids=("FC-RT-040", "FC-RT-041"),
    default_expectations="≥ 2 attempts, eventual exit 0",
    priority="P0",
)
S04 = Scenario(
    id="S04",
    name="Tool round trip",
    script_factory=_s04_script,
    check_ids=("FC-RT-011", "FC-RT-012", "FC-RT-013", "FC-RT-014"),
    default_expectations="correct continuation",
    priority="P0",
)

SCENARIOS: dict[str, Scenario] = {s.id: s for s in (S01, S02, S04)}

# Known but not implemented in this phase: ID -> (name, when).
PHASE_7B_SCENARIOS: dict[str, str] = {
    "S03": "Truncated output",
    "S05": "Truncated tool call",
    "S06": "Stream fails mid-way",
}
P1_SCENARIOS: dict[str, str] = {
    "S02b": "Busy forever",
    "S07": "Invalid key",
    "S08": "Multi-turn history",
    "S09": "Queue keepalives",
    "S10": "Slow response",
}
KNOWN_SCENARIO_IDS: tuple[str, ...] = (
    "S01",
    "S02",
    "S02b",
    "S03",
    "S04",
    "S05",
    "S06",
    "S07",
    "S08",
    "S09",
    "S10",
)


__all__ = [
    "CONTRACT_CHECK_IDS",
    "EVERY_SCENARIO_CHECK_IDS",
    "KNOWN_SCENARIO_IDS",
    "P1_SCENARIOS",
    "PHASE_7B_SCENARIOS",
    "S01",
    "S02",
    "S04",
    "SCENARIOS",
    "Scenario",
]
