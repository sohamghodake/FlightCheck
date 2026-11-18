# Phase G4 handoff — Groq mock, scenarios and runtime checks

- Date / session: 2026-11-18, Claude Code chat 3 of the Groq split (G4 only).
- Status: done (P0). P1: the `TOOL_VALIDATION_ERROR` fault is built and tested; FC-RT-045 and a
  scenario that serves the fault are deferred (see "Deviations").
- What was built (files, public functions):
  - `core/contract.py`: `groq_chat_contract_violations`, `groq_responses_contract_violations`,
    `groq_reasoning_violation`, `GROQ_REJECTED_CHAT_PARAMS`, `GROQ_UNSUPPORTED_RESPONSES_PARAMS`,
    `GROQ_BUILTIN_TOOL_TYPES`, `GROQ_DOCUMENTED_TOOL_TYPES` (equal to FC-GQ's, tested);
    `Violation.status_documented` (False for R4/R5: the mock labels those 400s [UNVERIFIED]).
  - `core/config.py`: `GROQ_TEST_BASE_URL_ENV`, `GROQ_TEST_API_KEY_ENV`, `GROQ_TEST_SCENARIOS`,
    `RuntimeProvider`, `TestConfig.for_provider()` (Groq defaults for unset fields only).
  - `mock/app.py`: Groq routes under `/openai/v1` (`GROQ_ROUTE_PREFIX`) beside the BharatCode
    ones, selected by route prefix; Groq errors without `code`, no `x-request-id`, Groq 400s,
    summary keys `provider`, `received_ms`, `retry_after`, `status_unverified` (Groq routes only).
    `mock/server.py`: `MockServer.groq_base_url`. `mock/payloads.py`: `groq_chat_body`,
    `groq_error_body`, `groq_http_error` (`headers` param; `retry-after` only with 429),
    `groq_stream_failure`, `groq_tool_validation_error`, `groq_models_list`, `groq_headers`,
    `groq_request`, `error_headers`; `chat_stream(..., groq=True)` (`x_groq` in first/final
    chunk, `include_usage`, `x_groq.error` failure chunk). `mock/faults.py`:
    `MockAction.TOOL_VALIDATION_ERROR`; labels for `TRUNCATE_TOOL_CALL` / `KEEPALIVE_STREAM`.
  - `runtime/harness.py`: `ALWAYS_SCRUBBED` (`GROQ_API_KEY`, `GROQ_BASE_URL` removed for every
    child); `build_child_env(..., mock_root=)` (`GROQ_BASE_URL` → mock root, K4).
  - `runtime/scenarios.py`: S11 (Groq only; 429 + `retry-after: 2` + six `x-ratelimit-*`, then
    OK), `Scenario.providers`, `Scenario.check_ids_for(provider)`, `GROQ_CONTRACT_CHECK_IDS`,
    `GROQ_EVERY_SCENARIO_CHECK_IDS`, `REPORTED_ONCE_CHECK_IDS`, `every_scenario_check_ids()`.
  - `runtime/validators.py`: FC-RT-009, FC-RT-015, FC-RT-044; Groq branches of FC-RT-002, 006,
    007 (Groq catalogue), 012, 013, 014, 022, 030, 040, 041; `CheckConfig.provider`;
    `CheckMeta.providers` / `groq_title` / `groq_remediation` / `groq_doc_url`; `meta_for()`;
    `to_finding(check, provider)`; provider-aware `evaluate`, `skip_all`, `error_all`.
  - `orchestrator.py`: `runtime_provider()`, `TestPlan.provider`, provider-aware `plan_test` /
    `run_test` / `_run_scenario`, `scenario_not_applicable` diagnostic, report option
    `mock_provider`; `ready_test_section` shows the variable lists as comments.
  - `reporting/html_report.py`: the mock-fidelity notice names Groq on Groq runs.
  - `scripts/eval_fixtures.py`: per-app `scenarios` in expected_runtime.json.
  - Fixture apps (`tests/fixtures/apps`): `groq_good_chat_stream` (OpenAI SDK, streamed tool
    round, x_groq.error check), `groq_good_responses_http` (httpx2, groq SDK URL layout, Groq
    defaults), `groq_bad_stateful` (FC-RT-009), `groq_bad_builtin_tools` (FC-RT-015),
    `groq_bad_stream` (FC-RT-030 with an expectation), `groq_bad_no_resilience` (FC-RT-041,
    FC-RT-044); each with `[test]`, bad ones marked "INTENTIONALLY FLAWED FIXTURE".
  - Docs: docs/RULES.md (Groq runtime section, gating table, S11, limitation), ADR-038,
    docs/ARCHITECTURE.md (§8 Groq payload fields, component row), docs/EVALUATION.md
    (regenerated), docs/IMPLEMENTATION_STATUS.md, this handoff.
- Tests added (files, count) and gate results:
  - New: `tests/mock/test_groq_mock.py` (62), `tests/runtime/test_groq_runtime.py` (93, incl.
    every gating cell: 23 checks × 2 providers), `tests/unit/test_groq_contract.py` (15).
    Extended: `tests/security/test_leak_matrix.py` (planted `GROQ_API_KEY`/`GROQ_BASE_URL` in
    the parent env; a Groq app run through the CLI; the BharatCode leak app proves it inherited
    neither), `tests/security/test_no_execution.py` (groq-SDK project with a planted key),
    `tests/scanner/test_fixture_suite.py`, `tests/runtime/test_scenarios_7a.py` / `_7b.py`,
    `tests/runtime/test_validators.py`, `tests/mock/test_faults.py`,
    `tests/diagnostics/test_context.py`, `tests/unit/test_html_report.py` (all extended for S11,
    the new action, the new checks and the Groq apps; no assertion weakened).
  - 1907 tests (+216). Green on Python 3.12 (macOS): `ruff check .`, `ruff format --check .`,
    `pytest -m "not live" -q` (1907 passed), `python scripts/eval_fixtures.py --check --runtime`
    (159/159, 0, 0; runtime 393/393, 0 mismatches), dogfood `flightcheck scan src --fail-on low`
    exit 0. Manifests are additive: compared with HEAD, every existing expected_runtime.json app
    entry (273 `(fixture, scenario, check_id)` keys) and every expected_findings.json entry is
    identical; 6 apps added to each (120 new runtime keys; static findings hand-checked:
    FC-GQ-001 L24 high/high, FC-GQ-002 L25 high/medium for a variable, FC-GEN-003 L32
    medium/medium).
  - Mutation spot-checks (each reverted, each killed): explicit scrub removed; `GROQ_BASE_URL`
    given the `/openai/v1` URL; FC-RT-044 measuring with `at_ms` instead of `received_ms`;
    `>=` → `>` in FC-RT-044; `declared == "groq"` dropped from `runtime_provider` (survived
    first; killed after adding the detection-conflict test); FC-RT-001 added to the Groq set;
    `code` added to Groq errors; `x-request-id` on Groq routes; `retry-after` allowed on 503;
    C4 `n` check loosened; FC-RT-009 flagging only `True`; built-in tools allowed on any model.
- Decisions made (ADR ids): ADR-038 (route-prefix selection; Groq 400s in core.contract, which
  are enforced and which deliberately are not; fault parameters; which runs are Groq runs; env
  scrubbing and provider-aware `[test]` defaults; full gating table; new IDs 009/015/044;
  FC-RT-006/007 on Groq; Groq texts and `meta_for`; per-app eval scenarios; FC-RT-045 deferral).
- Deviations from the plan and why:
  - FC-RT-045 (P1) not built: "retries or exits non-zero without crashing" needs a crash
    heuristic on output (ADR-030 forbids inferring outcomes from output) or a new
    `[test.expect]` key; either is its own decision. The `TOOL_VALIDATION_ERROR` fault is built,
    so a later phase only adds a scenario and the check.
  - FC-RT-007 on Groq fails a model outside the dated catalogue (the check's meaning, as on
    BharatCode) and says the catalogue is a snapshot with GET /models as the live list (M6);
    Enterprise-only Llama models are `needs_review` (info) and preview models pass with an M1
    note, matching FC-GQ-006's reading.
  - FC-RT-013 on Groq: a non-string `function_call_output` on /responses is `needs_review`
    (T10 is [UNVERIFIED]), where BharatCode fails it; a Chat dict content still fails (T5).
  - The mock does not reject built-in tools on non-GPT-OSS models (R9's error is
    [UNVERIFIED]); FC-RT-015 judges them from traffic instead.
- Out-of-scope changes (file, reason, test):
  - `orchestrator.ready_test_section`: the ready-to-paste `[test]` block listed BharatCode
    variable names explicitly; pasted into a Groq project it would override the new Groq
    defaults. The lists are now comments (test:
    `test_ready_test_section_leaves_the_provider_defaults_alone`; the existing CLI test still
    passes).
  - `reporting/html_report.py`: the mock-fidelity notice said "models BharatCode's public
    documentation" for every runtime report; Groq runs now name Groq (test extended in
    `tests/unit/test_html_report.py`; the BharatCode wording is unchanged and still pinned).
- Known issues / limitations:
  - Every Groq wire detail Groq does not document (the `x_groq.error` chunk's surroundings,
    Responses stream events, non-400 error types, the effect of R4 parameters, the
    `failed_generation` format, `GET /models` shape) is the mock's assumption, labelled
    [UNVERIFIED]; G5 verifies live.
  - `orchestrator.check_catalogue()` (used by `explain` for `needs_review` checks) still gives the
    BharatCode remediation texts; G5 makes it provider-aware with `explain --llm groq`.
  - The scanner does not resolve a model held in a module constant (`model=MODEL`), so
    `groq_bad_builtin_tools` has no static FC-GQ-003; the runtime check FC-RT-015 catches it
    (documented G3 limitation).
  - CLI help texts still describe FlightCheck as a BharatCode checker (G6 docs cleanup).
  - S11 costs about 2 s of real waiting per Groq app (the `retry-after` the scenario tests).
  - Not pushed: G4 (everything through G3 is pushed; CI green on 3.11/3.12/3.13).
- Exact next step for the next phase: G5 (new chat; needs the developer's `GROQ_API_KEY` in their
  own environment, never pasted) — `GroqAdapter` (raw HTTP, §3 settings, `retry-after`),
  `explain --llm groq` with Groq contract excerpts and a provider-aware check catalogue, and live
  verification of the [UNVERIFIED] rows the G4 mock relies on (ST3 wire format, R4/R5 effects,
  R15 events, E1 types, T6 `failed_generation`, A6), updating the mock where they differ.
