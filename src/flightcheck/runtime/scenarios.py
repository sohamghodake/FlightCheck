"""Scenario catalogue (plan §18.3): fault script + the checks each scenario feeds.

The P0 set is S01–S06 (S01, S02, S04 in Phase 7A; S03, S05, S06 in Phase 7B) plus S11 (Groq
only, phase G4). S02b and S07–S10 are P1: those IDs are known (so a typo is a ``ConfigError`` but
a later scenario is not), and the orchestrator reports them with a ``Diagnostic`` instead of a
fake result (ADR-029). A scenario selected on a run whose provider it does not apply to (S11 on
a BharatCode run) gets a ``Diagnostic`` too (ADR-038).

Every-scenario checks depend on the run provider (ADR-038 gating table): BharatCode runs keep
FC-RT-001…007; Groq runs use FC-RT-002, 006, 007, 009 (store is not true) and 015 (only tools
the model supports). FC-RT-050/060 run on both.

Every ``script_factory`` returns a **new** :class:`FaultScript`: a script keeps a cursor, and a
scenario must never see steps another run already consumed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from flightcheck.core.config import RuntimeProvider
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
# Groq runs (ADR-038): FC-RT-001 → 009 and FC-RT-003 → 015 (new meanings, new IDs, ADR-012);
# FC-RT-004/005 have no Groq basis (contract R8, R13) and are not evaluated.
GROQ_CONTRACT_CHECK_IDS: tuple[str, ...] = (
    "FC-RT-002",
    "FC-RT-006",
    "FC-RT-007",
    "FC-RT-009",
    "FC-RT-015",
)
GROQ_EVERY_SCENARIO_CHECK_IDS: tuple[str, ...] = (
    *GROQ_CONTRACT_CHECK_IDS,
    "FC-RT-050",
    "FC-RT-060",
)
# Every ID that is reported once per run, whichever the provider (validators.report_once).
REPORTED_ONCE_CHECK_IDS: frozenset[str] = frozenset(
    EVERY_SCENARIO_CHECK_IDS + GROQ_EVERY_SCENARIO_CHECK_IDS
)
BOTH_PROVIDERS: frozenset[str] = frozenset({"bharatcode", "groq"})


def every_scenario_check_ids(provider: RuntimeProvider) -> tuple[str, ...]:
    return GROQ_EVERY_SCENARIO_CHECK_IDS if provider == "groq" else EVERY_SCENARIO_CHECK_IDS


BUSY_STEP_PREFIX = "busy_"
TOOL_STEP_ID = "tool_call"
TRUNCATE_STEP_ID = "truncate"  # S03: the truncated text answer (FC-RT-020, FC-RT-023)
TRUNCATED_TOOL_STEP_ID = "truncated_tool_call"  # S05: the cut-off function call (RT-022/023)
STREAM_FAIL_STEP_ID = "stream_fail"  # S06: the failing stream / HTTP 500 (FC-RT-030)
TRUNCATION_STEP_IDS: tuple[str, ...] = (TRUNCATE_STEP_ID, TRUNCATED_TOOL_STEP_ID)
STREAM_FAIL_AFTER_N = 3  # §18.3: STREAM_FAIL_AFTER n=3
RATE_LIMIT_STEP_ID = "rate_limited"  # S11: the 429 (FC-RT-044)
RATE_LIMIT_RETRY_AFTER_S = 2  # contract §4.7: "429 with retry-after: 2"
# S11's x-ratelimit-* headers (contract E6: requests per day, tokens per minute; reset values in
# Groq's "2m59.56s" / "7.66s" style). The token budget is the one exhausted here.
RATE_LIMIT_HEADERS: dict[str, str] = {
    "retry-after": str(RATE_LIMIT_RETRY_AFTER_S),
    "x-ratelimit-limit-requests": "1000",
    "x-ratelimit-remaining-requests": "998",
    "x-ratelimit-reset-requests": "2m52.8s",
    "x-ratelimit-limit-tokens": "8000",
    "x-ratelimit-remaining-tokens": "0",
    "x-ratelimit-reset-tokens": "2s",
}


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    script_factory: Callable[[], FaultScript]
    check_ids: tuple[str, ...]  # scenario-specific checks (plus EVERY_SCENARIO_CHECK_IDS)
    default_expectations: str
    priority: Literal["P0", "P1"]
    providers: frozenset[str] = field(default=BOTH_PROVIDERS)  # runs it applies to (ADR-038)

    @property
    def all_check_ids(self) -> tuple[str, ...]:
        """Scenario checks first, then BharatCode's every-scenario checks (no duplicates)."""
        return self.check_ids_for("bharatcode")

    def check_ids_for(self, provider: RuntimeProvider) -> tuple[str, ...]:
        """Scenario checks first, then the provider's every-scenario checks (no duplicates)."""
        every = every_scenario_check_ids(provider)
        extra = tuple(c for c in every if c not in self.check_ids)
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


def _s03_script() -> FaultScript:
    # §18.3: [TRUNCATE] then OK_TEXT, so a retry or follow-up after the truncation is answered.
    return FaultScript(
        steps=[
            _step(TRUNCATE_STEP_ID, MockAction.TRUNCATE, "S03"),
            _step("after_truncate", MockAction.OK_TEXT, "S03"),
        ],
        default_step=_default("S03"),
    )


def _s05_script() -> FaultScript:
    return FaultScript(
        steps=[
            _step(TRUNCATED_TOOL_STEP_ID, MockAction.TRUNCATE_TOOL_CALL, "S05"),
            _step("answer", MockAction.OK_TEXT, "S05"),
        ],
        default_step=_default("S05"),
    )


def _s06_script() -> FaultScript:
    # §18.3: [STREAM_FAIL_AFTER n=3]; the mock answers a non-stream request with HTTP 500.
    return FaultScript(
        steps=[
            _step(STREAM_FAIL_STEP_ID, MockAction.STREAM_FAIL_AFTER, "S06", n=STREAM_FAIL_AFTER_N)
        ],
        default_step=_default("S06"),
    )


def _s11_script() -> FaultScript:
    # Contract §4.7 / E5, E6: a 429 with retry-after and the x-ratelimit-* headers, then OK.
    return FaultScript(
        steps=[
            _step(
                RATE_LIMIT_STEP_ID,
                MockAction.HTTP_ERROR,
                "S11",
                status=429,
                message="Rate limit reached: wait before retrying.",
                headers=dict(RATE_LIMIT_HEADERS),
            ),
            _step("after_rate_limit", MockAction.OK_TEXT, "S11"),
        ],
        default_step=_default("S11"),
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
S03 = Scenario(
    id="S03",
    name="Truncated output",
    script_factory=_s03_script,
    check_ids=("FC-RT-020", "FC-RT-023"),
    default_expectations="expect.on_truncation",
    priority="P0",
)
S05 = Scenario(
    id="S05",
    name="Truncated tool call",
    script_factory=_s05_script,
    check_ids=("FC-RT-022", "FC-RT-023"),
    default_expectations="tool not executed",
    priority="P0",
)
S06 = Scenario(
    id="S06",
    name="Stream fails mid-way",
    script_factory=_s06_script,
    check_ids=("FC-RT-030",),
    default_expectations="expect.on_stream_failure",
    priority="P0",
)

S11 = Scenario(
    id="S11",
    name="Rate limited (429)",
    script_factory=_s11_script,
    check_ids=("FC-RT-044",),
    default_expectations=f"next request ≥ {RATE_LIMIT_RETRY_AFTER_S} s after the 429",
    priority="P0",
    providers=frozenset({"groq"}),
)

SCENARIOS: dict[str, Scenario] = {s.id: s for s in (S01, S02, S03, S04, S05, S06, S11)}

# Known but not implemented (P1): ID -> name.
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
    "S11",
)


__all__ = [
    "CONTRACT_CHECK_IDS",
    "EVERY_SCENARIO_CHECK_IDS",
    "GROQ_CONTRACT_CHECK_IDS",
    "GROQ_EVERY_SCENARIO_CHECK_IDS",
    "RATE_LIMIT_HEADERS",
    "RATE_LIMIT_RETRY_AFTER_S",
    "RATE_LIMIT_STEP_ID",
    "REPORTED_ONCE_CHECK_IDS",
    "KNOWN_SCENARIO_IDS",
    "P1_SCENARIOS",
    "S01",
    "S02",
    "S03",
    "S04",
    "S05",
    "S06",
    "S11",
    "SCENARIOS",
    "STREAM_FAIL_STEP_ID",
    "TRUNCATED_TOOL_STEP_ID",
    "TRUNCATE_STEP_ID",
    "TRUNCATION_STEP_IDS",
    "Scenario",
    "every_scenario_check_ids",
]
