# Implementation status — updated 2026-11-09 (Phase 7B)

| Phase | Title                                                                     | Status      | Date       | Commit                                         | Notes                                                    |
| ----- | ------------------------------------------------------------------------- | ----------- | ---------- | ---------------------------------------------- | -------------------------------------------------------- |
| 0     | Scope freeze, repository bootstrap, continuity docs                       | done        | 2026-11-06 | `chore: bootstrap repository (phase 0)`        | Gates green on 3.11 and 3.12 locally; see PHASE_00.md     |
| 1     | Core foundation: models, config, errors, logging, redaction, paths        | done        | 2026-11-06 | `feat(core): models, config, redaction, logging, paths (phase 1)` | 99 tests; ADR-010, ADR-023; see PHASE_01.md |
| 2     | Discovery, AST index, provider detection                                  | done        | 2026-11-06 | `feat(scanner): discovery, AST index, provider detection (phase 2)` | 213 tests; ADR-011, ADR-024; see PHASE_02.md |
| 3     | Rule engine, BharatCode contract rules, `scan`, JSON + terminal output    | done        | 2026-11-07 | `feat: scan command with BharatCode contract rules (phase 3)` | 305 tests; ADR-012, ADR-025; see PHASE_03.md |
| 4     | Robustness and security rules, fixture suite, evaluation script           | done        | 2026-11-07 | `feat(rules): robustness and security rules, fixture suite, eval (phase 4)` | 412 tests; ADR-013, ADR-026; see PHASE_04.md |
| 5A    | Mock provider: payloads, faults, recorder, non-streaming endpoints        | done        | 2026-11-07 | `feat(mock): contract-aware mock, non-streaming (phase 5A)` | 544 tests; ADR-014, ADR-027; see PHASE_05A.md |
| 5B    | Mock provider: streaming, server thread, OpenAI-SDK compatibility         | done        | 2026-11-07 | `feat(mock): streaming, server runner, SDK compatibility (phase 5B)` | 625 tests; ADR-015; see PHASE_05B.md |
| 6     | Provider adapters and capability registry                                 | done        | 2026-11-07 | `feat(providers): BharatCode adapter and capability registry (phase 6)` | 982 tests; ADR-016, ADR-028; generic adapter (P1) not built; see PHASE_06.md |
| 7A    | Runtime harness, validators, scenarios S01/S02/S04                        | done        | 2026-11-07 | `feat(runtime): harness, validators, scenarios S01 S02 S04 (phase 7A)` | 1118 tests; ADR-017, ADR-018, ADR-029; `run_test` internal only; see PHASE_07A.md |
| 7B    | Scenarios S03/S05/S06, `test` command, runtime expectations               | done        | 2026-11-09 | `feat: test command with truncation, tool and stream-failure scenarios (phase 7B)` | 1188 tests; ADR-030; `--config` deferred (P1); see PHASE_07B.md |
| 8     | AI diagnostics and `explain`                                              | not started |            |                                                | Tool-using agent is P1                                   |
| 9     | HTML report, `report` command, CI workflows                               | not started |            |                                                | P0-lite; `ci` command is P1                              |
| 10    | Live BharatCode probe and golden transcripts                              | not started |            |                                                | P1 (P0 if a key arrives by Day 3); needs an API key      |
| 11    | Security hardening, architecture tests, QA pass                           | not started |            |                                                |                                                          |
| 12    | Documentation, demo, `BharatCode.txt`, submission                         | not started |            |                                                | Early run on Day 2, final run on Day 4                   |

The commit column names the phase commit by its message; find its hash with
`git log --oneline --grep "phase <N>"`.

## Current state

- Working commands: `flightcheck --version`; `flightcheck scan PATH` (FC-BC-001…008,
  FC-GEN-001…007, FC-SEC-001…002, terminal
  output, JSON report at `PATH/.flightcheck/flightcheck-report.json` or `--output DIR`;
  `--provider`, `--rule`, `--min-severity`, `--fail-on`, `--fail-on-hypotheses`, `--no-color`,
  `-v`; `--format html|all` exits 2 until Phase 9); `flightcheck test PATH --allow-exec`
  (Phase 7B: warning panel, scenarios S01–S06, per-scenario table, failed/needs-review details
  with traffic excerpts, merged scan unless `--no-scan`, `--scenario`, `--timeout`,
  `--fail-on`, `--keep-traffic`, `--format terminal|json`; `--config` exits 2, P1).
  `explain` and `report` come later.
- Package: `src/flightcheck/{__init__,__main__}.py`, `cli/{__init__,app}.py`,
  `core/{models,config,errors,logging,redact,paths}.py` (stdlib + pydantic only, enforced by
  `tests/unit/test_core_imports.py`) and `scanner/{__init__,discovery,ast_utils,parser,detect}.py`
  (`discover()`, `build_index()`, `detect()` / `detect_providers()`), `scanner/engine.py` +
  `scanner/rules/bharatcode.py`, `orchestrator.run_scan`, `reporting/{json_report,terminal}.py`
  and `cli/render.py`, `scanner/rules/{robustness,security}.py` (Phase 4), and the
  mock provider `mock/{faults,payloads,recorder,app,server}.py` (`create_mock_app`,
  `FaultScript`, `TrafficRecorder`, `MockServer` on 127.0.0.1; JSON and SSE; Phases 5A/5B;
  SDK-required payload fields in `docs/ARCHITECTURE.md` §8), `core/{capabilities,contract}.py`
  and the provider layer `providers/{capabilities,base,http,sse,bharatcode,registry}.py`
  (`BharatCodeAdapter`, `build_adapter`, `BHARATCODE_PROFILE`; Phase 6; sync httpx2 with an
  injected transport and sleep; `providers` imports only `core`), and the runtime layer
  `runtime/{harness,scenarios,validators}.py` (Phase 7A: consented child process, scrubbed env,
  egress guard (best effort, not a sandbox), scenarios S01–S06, checks FC-RT-001…008,
  011…014, 020, 022, 023, 030, 040, 041, 050, 060, `[test.expect]` parsed into
  `validators.Expectation`) behind `orchestrator.plan_test` / `run_test` and the `test` command;
  every fixture app has a `flightcheck.toml` `[test]` section and
  `tests/fixtures/expected_runtime.json` freezes their S01–S06 results. Rules and
  limitations: `docs/RULES.md`; schema: `docs/report.schema.json` (drift-checked). Evaluation:
  `python scripts/eval_fixtures.py` writes `docs/EVALUATION.md`; `--check` runs the §26.2 gates;
  `--runtime` also runs every fixture app on the mock and gates runtime agreement (100 %). The scanner
  imports no `subprocess`/`importlib`/`runpy`/`providers`/`runtime` and calls no
  `exec`/`eval`/`compile` (enforced by `tests/scanner/test_detect.py`).
- Tests: 1188 (`tests/cli`, `tests/unit`, `tests/scanner`, `tests/security`, `tests/mock`,
  `tests/providers`, `tests/runtime`; fixtures in
  `tests/fixtures/parse`, `tests/fixtures/rules` and the 13 apps in `tests/fixtures/apps`,
  expectations in `tests/fixtures/expected_findings.json`);
  `FLIGHTCHECK_NO_NETWORK=1` is set for every test. `slow` tests: the 200-file index test,
  the `MockServer` / OpenAI-SDK tests in `tests/mock` and the child-process tests in
  `tests/runtime` (127.0.0.1 sockets only; fixture `timeout_s` 20).
- Placeholder types to tighten later (ADR-023 item 7): `Report.ai_suggestions` (Phase 8).
  `ScenarioResult.exchanges` is now `list[RecordedExchange]` (Phase 5A);
  `Report.capability_profile` is `CapabilityProfile` and `ProviderConfig.declared_capabilities`
  is `dict[Capability, bool]` (Phase 6).
- CI: `.github/workflows/ci.yml` (ruff check, ruff format --check, pytest, then
  `python scripts/eval_fixtures.py --check` on 3.11/3.12/3.13).
  Phases 0–7A are pushed and CI is green on GitHub (Python 3.11/3.12/3.13); Phase 7B is
  committed locally and not yet pushed.
- Known broken: nothing.
- BharatCode key available: no (never paste the key).
- Plan: `docs/FLIGHTCHECK_IMPLEMENTATION_PHASE_PLAN.md`, local-only and git-ignored (ADR-000).

## Next phase

- Phase 8: AI diagnostics and `explain` (tool-using agent is P1). Start from
  docs/handoffs/PHASE_07B.md. Still open (P1): FC-BC-009, FC-GEN-008/009, FC-SEC-003,
  FC-RT-010/031/042/043, scenarios S02b/S07–S10, `use_current_python`, `--force-exec`,
  `flightcheck test --config FILE`, the generic OpenAI-compatible adapter and the `providers`
  command. CI still runs `eval_fixtures.py --check` without `--runtime` (Phase 9 CI work).

## Dropped / deferred (per plan §7.3)

- Nothing dropped yet.
- Deferred from Phase 0: a committed lock file (`uv.lock` or `requirements-lock.txt`); see
  ADR-008.
- Manual, outside the code: Build League project-list overlap check, to be recorded as ADR-009.
