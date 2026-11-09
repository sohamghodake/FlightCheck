# Phase 7B handoff — Truncation, incomplete-tool-call and stream-failure scenarios; `test` CLI

- Date / session: 2026-11-07 to 2026-11-09, one Claude Code chat (interrupted twice; resumed in
  the same chat).
- Status: done (P0). P1 scenarios (S02b/S07–S10) and checks (FC-RT-010/031/042/043) were not
  started, as scoped.
- What was built (files, public functions):
  - `runtime/scenarios.py`: `S03` (`truncate` → `after_truncate`), `S05`
    (`truncated_tool_call` → `answer`), `S06` (`stream_fail`, `STREAM_FAIL_AFTER n=3`);
    `SCENARIOS` is S01–S06; step-ID constants `TRUNCATE_STEP_ID`, `TRUNCATED_TOOL_STEP_ID`,
    `STREAM_FAIL_STEP_ID`, `TRUNCATION_STEP_IDS`. `PHASE_7B_SCENARIOS` removed.
  - `runtime/validators.py`: `ExpectKind`, `Expectation.parse`, `Expectations.from_config`,
    `CheckConfig.expectations` (parsed once), `expectation_met`; checks `check_rt020`
    (S03, medium), `check_rt022` (S05, high), `check_rt023` (S03/S05, medium), `check_rt030`
    (S06, medium; a retry → info + "discard partial output" unless failed).
  - `orchestrator.py`: `require_consent`, `plan_test` → `TestPlan` (consent, `[test]`,
    scenarios, cwd, pre-flight index), `run_test(..., plan=None)`, `ready_test_section`
    (the ready-to-paste `[test]` block in the missing-`[test]` hint).
  - `cli/app.py`: `flightcheck test PATH --allow-exec` with `--scenario` (repeatable),
    `--timeout` (1–600), `--format`, `--output`, `--fail-on`, `--no-scan`, `--keep-traffic`,
    `--no-color`, `-v`; `--config` exits 2 (P1). `cli/render.py`: `render_exec_warning`
    (panel on stderr), `render_exit_line(..., command=)`; scan's exit line now also names
    `flightcheck test`. `reporting/terminal.py`: `render_test`, `render_scenario_table`,
    `render_check_details` (errors once per scenario, failed, needs_review, with evidence).
    `reporting/json_report.py`: `write_traffic` → `flightcheck-traffic.json`.
  - `scripts/eval_fixtures.py --runtime`: `evaluate_runtime`, `runtime_gates`, runtime
    section in `docs/EVALUATION.md` (regenerated with `--runtime`).
  - `tests/fixtures/expected_runtime.json`: all 13 apps × S01–S06.
- Tests added (files, count) and gate results: `tests/runtime/test_scenarios_7b.py` (50; 4
  slow), `tests/cli/test_test_cli.py` (14; 3 slow), +1 each in `test_harness.py` and
  `test_validators.py`; two 7A tests rewritten for S03/S05/S06 being real (the P1
  diagnostic is still asserted). 1188 total (+70). `ruff check .`, `ruff format --check .`,
  `pytest -m "not live" -q` (1188 passed, 87 s), `python scripts/eval_fixtures.py --check`
  (108/108, 0, 0) and `--runtime --check` (273/273 agreement, 0 mismatches, ~53 s) green on
  Python 3.12.14 (macOS). The full 13-app × 6-scenario run takes ~53 s, so all pairs are kept
  (no restriction to the plan's P0 pairs). Mutation spot-checks, each caught by a failing test
  then reverted: CLI consent bypass, warning panel skipped, FC-RT-022 never failing,
  FC-RT-023 never failing, FC-RT-030 retry not downgraded, FC-RT-020 passing without an
  expectation, relative PATH entries left relative.
- Decisions made (ADR ids): ADR-030 (expectation model, `any`/timeout, RT-030 retry severity,
  CLI order and conventions, `--config` deferred, `--keep-traffic`, eval `--runtime`, the two
  7A fixes).
- Deviations from the plan and why:
  - The pre-flight moved into `plan_test` so the panel follows it (§23.2 order) and can say
    "will NOT run" for a hard-coded base URL; `run_test` reuses the plan's index.
  - `--config FILE` exits 2 (P1): `load_config` takes a root and core was out of scope.
  - CI still runs `eval_fixtures.py --check` without `--runtime` (CI workflows are Phase 9).
  - `--fail-on` for `test` has no `--fail-on-hypotheses`; hypotheses never gate `test`.
- Out-of-scope changes (file, reason, test):
  - `runtime/validators.py` `_busy_attempts` (7A): attempts now end at the first non-503
    answer. Before, a request sent after the recovered 200 counted as a 503 retry, so the
    fixed `bc_bad_truncation` failed FC-RT-041 with a near-zero "backoff". Test:
    `test_validators.py::test_rt040_041_stop_at_the_recovered_answer`. No other fixture's
    expected result changed.
  - `runtime/harness.py` `build_child_env` (7A): relative `PATH` entries are made absolute
    against FlightCheck's working directory. The acceptance command `PATH=.venv/bin:$PATH
    flightcheck test …` failed every scenario with `'python' was not found`, because the
    child resolves PATH from `[test].cwd`. Test:
    `test_harness.py::test_child_env_makes_relative_path_entries_absolute`; the allowlist test
    now pins an absolute PATH (it compared the developer's shell PATH verbatim).
  - Fixture fix `tests/fixtures/apps/bc_bad_truncation/main.py` (plan item 14 could not be
    met: no tools, one request, no retry): it now defines `get_weather`, runs a function call
    even from an incomplete response (cut-off JSON arguments parsed inside `try`, defaulting to
    `{}`) and sends its `function_call_output`, and repeats the identical request once when
    the answer "looks unfinished" (no closing `.`/`!`/`?`), without reading `status`.
    Static result unchanged except the line: FC-GEN-002 L24 → L55 in `expected_findings.json`
    (108/108). Side effect: the heuristic also re-asks once in S01/S02/S04 (the mock's answers
    end with `]`), which changes no 7A check result except S04 becoming real (it was
    `unsupported`: no tools).
- Known issues / limitations:
  - `stdout_contains`/`stderr_contains` match the redacted, path-aliased first 8 KB; text that
    redaction masks can never match.
  - FC-RT-022 sees traffic only: an app that runs a cut-off tool call locally and never sends
    the result passes.
  - FC-RT-030 treats any later model request as a retry, including an unrelated second call.
  - Long check messages in the terminal details wrap without a hanging indent.
  - `flightcheck test` builds the scanner index once in `plan_test` and the static scan reuses
    it; a very large repository still pays for one full parse before anything runs.
  - The Windows process-group path and the egress-guard caveats of ADR-017 are unchanged.
  - Payload shapes beyond the documented facts stay [UNVERIFIED] until Phase 10.
- Manual verification (§27 item 19): `PATH=.venv/bin:$PATH flightcheck test
  tests/fixtures/apps/bc_bad_tool_pairing --allow-exec` (with `--output` in a temp dir) exits
  1. The panel lists the command, directory, six scenarios and timeout; the table reads well
  at 110 columns; the details show FAILED HIGH FC-RT-012 [S04] with `expected:
  call_fc_S04_1 · observed: "call_1"` and both traffic lines, FC-RT-013 with the dict output
  excerpt, then needs-review FC-RT-041 (SDK backoff), FC-RT-020 (exit 1, stderr "Answer
  truncated") and FC-RT-030 at info (SDK retry of the 500). Readable; the only roughness is
  the unindented wrap of long messages.
- Exact next step for the next phase: Phase 8 (AI diagnostics and `explain`). Read
  `docs/IMPLEMENTATION_STATUS.md`, ADR-030 and this handoff; `explain` reads reports with
  `reporting/json_report.read_report` (runtime findings carry `check_ref` into
  `report.scenarios[*].checks`). Do not change runtime statuses from AI output.
