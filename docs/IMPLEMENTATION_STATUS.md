# Implementation status — updated 2026-11-07

| Phase | Title                                                                     | Status      | Date       | Commit                                         | Notes                                                    |
| ----- | ------------------------------------------------------------------------- | ----------- | ---------- | ---------------------------------------------- | -------------------------------------------------------- |
| 0     | Scope freeze, repository bootstrap, continuity docs                       | done        | 2026-11-06 | `chore: bootstrap repository (phase 0)`        | Gates green on 3.11 and 3.12 locally; see PHASE_00.md     |
| 1     | Core foundation: models, config, errors, logging, redaction, paths        | done        | 2026-11-06 | `feat(core): models, config, redaction, logging, paths (phase 1)` | 99 tests; ADR-010, ADR-023; see PHASE_01.md |
| 2     | Discovery, AST index, provider detection                                  | done        | 2026-11-06 | `feat(scanner): discovery, AST index, provider detection (phase 2)` | 213 tests; ADR-011, ADR-024; see PHASE_02.md |
| 3     | Rule engine, BharatCode contract rules, `scan`, JSON + terminal output    | done        | 2026-11-07 | `feat: scan command with BharatCode contract rules (phase 3)` | 305 tests; ADR-012, ADR-025; see PHASE_03.md |
| 4     | Robustness and security rules, fixture suite, evaluation script           | not started |            |                                                |                                                          |
| 5A    | Mock provider: payloads, faults, recorder, non-streaming endpoints        | not started |            |                                                | Can run before Phase 4 (depends on 1 only)               |
| 5B    | Mock provider: streaming, server thread, OpenAI-SDK compatibility         | not started |            |                                                |                                                          |
| 6     | Provider adapters and capability registry                                 | not started |            |                                                | Generic OpenAI-compatible adapter is P1                  |
| 7A    | Runtime harness, validators, scenarios S01/S02/S04                        | not started |            |                                                |                                                          |
| 7B    | Scenarios S03/S05/S06, `test` command, runtime expectations               | not started |            |                                                |                                                          |
| 8     | AI diagnostics and `explain`                                              | not started |            |                                                | Tool-using agent is P1                                   |
| 9     | HTML report, `report` command, CI workflows                               | not started |            |                                                | P0-lite; `ci` command is P1                              |
| 10    | Live BharatCode probe and golden transcripts                              | not started |            |                                                | P1 (P0 if a key arrives by Day 3); needs an API key      |
| 11    | Security hardening, architecture tests, QA pass                           | not started |            |                                                |                                                          |
| 12    | Documentation, demo, `BharatCode.txt`, submission                         | not started |            |                                                | Early run on Day 2, final run on Day 4                   |

The commit column names the phase commit by its message; find its hash with
`git log --oneline --grep "phase <N>"`.

## Current state

- Working commands: `flightcheck --version`; `flightcheck scan PATH` (FC-BC-001…008, terminal
  output, JSON report at `PATH/.flightcheck/flightcheck-report.json` or `--output DIR`;
  `--provider`, `--rule`, `--min-severity`, `--fail-on`, `--fail-on-hypotheses`, `--no-color`,
  `-v`; `--format html|all` exits 2 until Phase 9). `test`, `explain`, `report` come later.
- Package: `src/flightcheck/{__init__,__main__}.py`, `cli/{__init__,app}.py`,
  `core/{models,config,errors,logging,redact,paths}.py` (stdlib + pydantic only, enforced by
  `tests/unit/test_core_imports.py`) and `scanner/{__init__,discovery,ast_utils,parser,detect}.py`
  (`discover()`, `build_index()`, `detect()` / `detect_providers()`), `scanner/engine.py` +
  `scanner/rules/bharatcode.py`, `orchestrator.run_scan`, `reporting/{json_report,terminal}.py`
  and `cli/render.py`. Rules and limitations: `docs/RULES.md`; schema: `docs/report.schema.json`
  (drift-checked). The scanner
  imports no `subprocess`/`importlib`/`runpy`/`providers`/`runtime` and calls no
  `exec`/`eval`/`compile` (enforced by `tests/scanner/test_detect.py`).
- Tests: 305 (`tests/cli`, `tests/unit`, `tests/scanner`; fixtures in `tests/fixtures/parse` and
  `tests/fixtures/rules`, expectations in `tests/fixtures/expected_findings.json`);
  `FLIGHTCHECK_NO_NETWORK=1` is set for every test. One `slow` test (200 files indexed < 3 s).
- Placeholder types to tighten later (ADR-023 item 7): `ScenarioResult.exchanges` (Phase 5A),
  `Report.capability_profile` and `ProviderConfig.declared_capabilities` (Phase 6),
  `Report.ai_suggestions` (Phase 8).
- CI: `.github/workflows/ci.yml` (ruff check, ruff format --check, pytest on 3.11/3.12/3.13).
  Phases 0–2 are pushed and CI is green on GitHub (Python 3.11/3.12/3.13); Phase 3 is committed
  locally and not yet pushed.
- Known broken: nothing.
- BharatCode key available: no (never paste the key).
- Plan: `docs/FLIGHTCHECK_IMPLEMENTATION_PHASE_PLAN.md`, local-only and git-ignored (ADR-000).

## Next phase

- Phase 4: Robustness and security rules (FC-GEN/FC-SEC), fixture suite, evaluation script
  (`scripts/eval_fixtures.py --check`). Prompt in plan §28 ("Prompt — Phase 4"). Phase 5A can run
  before it.

## Dropped / deferred (per plan §7.3)

- Nothing dropped yet.
- Deferred from Phase 0: a committed lock file (`uv.lock` or `requirements-lock.txt`); see
  ADR-008.
- Manual, outside the code: Build League project-list overlap check, to be recorded as ADR-009.
