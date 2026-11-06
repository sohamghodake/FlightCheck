# Implementation status — updated 2026-11-06

| Phase | Title                                                                     | Status      | Date       | Commit                                         | Notes                                                    |
| ----- | ------------------------------------------------------------------------- | ----------- | ---------- | ---------------------------------------------- | -------------------------------------------------------- |
| 0     | Scope freeze, repository bootstrap, continuity docs                       | done        | 2026-11-06 | `chore: bootstrap repository (phase 0)`        | Gates green on 3.11 and 3.12 locally; see PHASE_00.md     |
| 1     | Core foundation: models, config, errors, logging, redaction, paths        | done        | 2026-11-06 | `feat(core): models, config, redaction, logging, paths (phase 1)` | 99 tests; ADR-010, ADR-023; see PHASE_01.md |
| 2     | Discovery, AST index, provider detection                                  | not started |            |                                                |                                                          |
| 3     | Rule engine, BharatCode contract rules, `scan`, JSON + terminal output    | not started |            |                                                | First vertical slice                                     |
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

- Working commands: `flightcheck --version` and `python -m flightcheck --version` (print
  `flightcheck 0.1.0`); `flightcheck --help` lists the four P0 commands as coming in later phases.
  No other commands exist yet.
- Package: `src/flightcheck/{__init__,__main__}.py`, `cli/{__init__,app}.py`, and
  `core/{models,config,errors,logging,redact,paths}.py` (stdlib + pydantic only, enforced by
  `tests/unit/test_core_imports.py`).
- Tests: 99 (`tests/cli`, `tests/unit`); `FLIGHTCHECK_NO_NETWORK=1` is set for every test.
- Placeholder types to tighten later (ADR-023 item 7): `ScenarioResult.exchanges` (Phase 5A),
  `Report.capability_profile` and `ProviderConfig.declared_capabilities` (Phase 6),
  `Report.ai_suggestions` (Phase 8).
- CI: `.github/workflows/ci.yml` (ruff check, ruff format --check, pytest, on 3.11/3.12/3.13).
  Not yet run on GitHub: the push is blocked (GitHub access for `sohamghodake/FlightCheck`).
- Known broken: nothing.
- BharatCode key available: no (never paste the key).
- Plan: `docs/FLIGHTCHECK_IMPLEMENTATION_PHASE_PLAN.md`, local-only and git-ignored (ADR-000).

## Next phase

- Phase 2: Discovery, AST index, provider detection. Prompt in plan §28 ("Prompt — Phase 2").

## Dropped / deferred (per plan §7.3)

- Nothing dropped yet.
- Deferred from Phase 0: a committed lock file (`uv.lock` or `requirements-lock.txt`); see
  ADR-008.
- Manual, outside the code: Build League project-list overlap check, to be recorded as ADR-009.
