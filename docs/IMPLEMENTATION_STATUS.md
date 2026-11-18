# Implementation status — updated 2026-11-18 (Phase G4)

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
| 8     | AI diagnostics and `explain`                                              | done        | 2026-11-09 | `feat(diagnostics): explain command with BharatCode backend (phase 8)` | 1282 tests; ADR-019, ADR-020, ADR-031; agent loop (P1) not built; no live run (no key); see PHASE_08.md |
| 9     | HTML report, `report` command, CI workflows                               | done        | 2026-11-09 | `feat(report): HTML report and CI integration (phase 9)` | 1322 tests; ADR-021, ADR-032; dogfood fix in the parser; `ci` command and Markdown (P1) not built; see PHASE_09.md |
| 10    | Live BharatCode probe and golden transcripts                              | dropped     | 2026-11-18 |                                                | Dropped (BharatCode abandoned, 2026-11-18); ADR-035; ADR-022 void |
| 11    | Security hardening, architecture tests, QA pass                           | done        | 2026-11-09 | `test: security and architecture hardening (phase 11)` | 1445 tests; ADR-033, ADR-034; 0 leaks; redact fix (JSON-escaped values); see PHASE_11.md |
| 12    | Documentation, demo, `BharatCode.txt`, submission                         | dropped     | 2026-11-18 |                                                | Dropped (BharatCode abandoned, 2026-11-18); ADR-035      |
| G1    | Groq contract and migration decision                                      | done        | 2026-11-18 | `docs: Groq contract and migration plan (phase G1)` | Docs only; docs/GROQ_CONTRACT.md; ADR-035, ADR-022 void; see PHASE_G1.md |
| G2    | Groq detection, provider profile, constants, redaction                    | done        | 2026-11-18 | `feat: Groq provider profile and detection (phase G2)` | 1520 tests; ADR-036; same chat as G1; see PHASE_G2.md |
| G3    | Groq static rules (`FC-GQ-*`), GEN/SEC adaptations                        | done        | 2026-11-18 | `feat(rules): Groq contract rules (phase G3)`  | 1691 tests; ADR-037; FC-GQ-016 held (G5), FC-GQ-020 reserved; see PHASE_G3.md |
| G4    | Groq mock, scenarios and runtime checks                                   | done        | 2026-11-18 | `feat(runtime): Groq mock, scenarios and runtime checks (phase G4)` | 1907 tests; ADR-038; FC-RT-045 (P1) deferred; see PHASE_G4.md |
| G5    | Groq adapter, `explain --llm groq`, live verification                     | not started |            |                                                | Needs a Groq key (developer's environment only)          |
| G6    | BharatCode removal and documentation cleanup                              | not started |            |                                                | Decides schema 1.x legacy value vs 2.0 (ADR-035 item 4)  |

The commit column names the phase commit by its message; find its hash with
`git log --oneline --grep "phase <N>"`.

## Current state

- Working commands: `flightcheck --version`; `flightcheck scan PATH` (FC-BC-001…008,
  FC-GQ-001…015/017…019 (G3), FC-GEN-001…007, FC-SEC-001…002, terminal
  output, JSON report at `PATH/.flightcheck/flightcheck-report.json` or `--output DIR`;
  `--provider`, `--rule`, `--min-severity`, `--fail-on`, `--fail-on-hypotheses`, `--no-color`,
  `-v`; `--provider auto|bharatcode|groq|openai_compatible`; `--format html|all` also writes `flightcheck-report.html`, ADR-032); `flightcheck test PATH --allow-exec`
  (Phase 7B: warning panel, scenarios S01–S06 (plus S11 on Groq runs, G4), per-scenario table, failed/needs-review details
  with traffic excerpts, merged scan unless `--no-scan`, `--scenario`, `--timeout`,
  `--fail-on`, `--keep-traffic`, `--format terminal|json|html|all`; `--config` exits 2, P1);
  `flightcheck explain REPORT.json` (Phase 8: `--llm none|fake|bharatcode`, `--context
  metadata|snippets`, `--repo`, `--max-findings`, `--model`, `--yes`, `--dry-run`, `--output`,
  `--format terminal|json|html|all`; consent panel for BharatCode, deterministic fallback with a
  reason, writes `<report>.explained.json` and, with html/all, `<report>.explained.html`);
  `flightcheck report REPORT.json` (Phase 9: `--format html|terminal`, default html to
  `<report-stem>.html` or `--output FILE`, never the input; `markdown` exits 2, P1). The
  `scan`/`test` exit lines name `flightcheck explain <report>` when there is something to
  explain. The `ci` command (P1) does not exist.
- Package: `src/flightcheck/{__init__,__main__}.py`, `cli/{__init__,app}.py`,
  `core/{models,config,errors,logging,redact,paths}.py` (stdlib + pydantic only, enforced by
  `tests/unit/test_core_imports.py`) and `scanner/{__init__,discovery,ast_utils,parser,detect}.py`
  (`discover()`, `build_index()`, `detect()` / `detect_providers()`), `scanner/engine.py` +
  `scanner/rules/bharatcode.py`, `scanner/rules/groq.py` (G3), `orchestrator.run_scan`, `reporting/{json_report,terminal}.py`
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
  `tests/fixtures/expected_runtime.json` freezes their S01–S06 results (Groq apps: S01–S06 and
  S11, listed in their own `scenarios`). Rules and
  limitations: `docs/RULES.md`; schema: `docs/report.schema.json` (drift-checked). Evaluation:
  `python scripts/eval_fixtures.py` writes `docs/EVALUATION.md`; `--check` runs the §26.2 gates;
  `--runtime` also runs every fixture app on the mock and gates runtime agreement (100 %). The scanner
  imports no `subprocess`/`importlib`/`runpy`/`providers`/`runtime` and calls no
  `exec`/`eval`/`compile` (enforced by `tests/scanner/test_detect.py`). The diagnostics layer
  `diagnostics/{schema,contract_excerpts,sanitize,context,prompt,fake_llm,service}.py`
  (Phase 8: `DiagnosticService.prepare/explain`, behind `orchestrator.plan_explain` /
  `run_explain`; imports only `core` and `providers`, enforced by
  `tests/diagnostics/test_context.py`); `AIPatch`/`AIExplanation`/`AISuggestion` live in
  `core.models` (ADR-031). The HTML report `reporting/html_report.py` (`build_view`,
  `render_html`, `write_html`) renders the packaged template
  `reporting/templates/report.html.j2` (Jinja2 `autoescape=True`, no JavaScript, no external
  assets; ADR-021, ADR-032).
- Tests: 1907 (`tests/cli`, `tests/unit`, `tests/scanner`, `tests/security`, `tests/mock`,
  `tests/providers`, `tests/runtime`, `tests/diagnostics`, `tests/architecture`; fixtures in
  `tests/fixtures/parse`, `tests/fixtures/detect_groq` (G2), `tests/fixtures/rules` and the 19
  apps in `tests/fixtures/apps` (6 `groq_*` apps since G4),
  expectations in `tests/fixtures/expected_findings.json`);
  `FLIGHTCHECK_NO_NETWORK=1` is set for every test. `slow` tests: the 200-file index test,
  the `MockServer` / OpenAI-SDK tests in `tests/mock` and the child-process tests in
  `tests/runtime` (127.0.0.1 sockets only; fixture `timeout_s` 20).
- Placeholder types (ADR-023 item 7): all tightened. `Report.ai_suggestions` is now
  `list[AISuggestion]` (Phase 8); `ScenarioResult.exchanges` is `list[RecordedExchange]` (Phase 5A);
  `Report.capability_profile` is `CapabilityProfile` and `ProviderConfig.declared_capabilities`
  is `dict[Capability, bool]` (Phase 6).
- CI: `.github/workflows/ci.yml` (ruff check, ruff format --check, pytest, then
  `python scripts/eval_fixtures.py --check --runtime` and the dogfood
  `flightcheck scan src --fail-on low` on 3.11/3.12/3.13);
  `.github/workflows/flightcheck-example.yml` is manual-only (`workflow_dispatch`; docs/CI.md).
  Everything through G4 is pushed and CI is green on GitHub (Python 3.11/3.12/3.13; confirmed
  by the developer, 2026-11-18).
- Security and architecture (Phase 11): `tests/security` (leak matrix over every output,
  no-execution, path traversal, keys in `flightcheck.toml`, scan redaction) and
  `tests/architecture/test_imports.py` (ADR-034; orchestrator → mock allowed). `core.redact`
  matches JSON-escaped `key=value` values (ADR-033); accepted redaction limits are listed in
  docs/RULES.md.
- Known broken: nothing.
- BharatCode key available: no; BharatCode is abandoned (ADR-035). Groq replaces it over G1–G6;
  the contract is docs/GROQ_CONTRACT.md (read 18 Nov 2026). Never paste a
  key.
- Plan: `docs/FLIGHTCHECK_IMPLEMENTATION_PHASE_PLAN.md`, local-only and git-ignored (ADR-000);
  BharatCode-era reference. G-phase prompts come from the developer in chat.
- Groq (G2, ADR-036): built-in `providers.groq` profile (`[project] provider = "groq"`,
  `FLIGHTCHECK_PROVIDER=groq`), dated model catalogue `core.config.GROQ_MODELS`,
  `GROQ_PROFILE` in `providers/capabilities.py`, detection of `groq` call sites (groq SDK
  clients, `api.groq.com`, `GROQ` env names, Groq models as supporting signals), `scan
  --provider groq`, and `gsk_` key redaction. `test` runs Groq apps as Groq since
  G4; `explain` is still BharatCode-only (G5).
- Groq rules (G3, ADR-037): `scanner/rules/groq.py` (FC-GQ-001…015, 017…019; category
  `groq-contract`; each cites a docs/GROQ_CONTRACT.md row, read 18 Nov 2026), gated per family:
  `auto` (call sites classified `groq`, low-confidence downgrade), `forced` (`--provider
  groq`), `disabled` (`--provider bharatcode`/`openai_compatible`); the full table is in
  docs/RULES.md and ADR-037. FC-GEN-001…007 cite Groq on `groq` call sites; FC-GEN-005 gives
  groq SDK clients an info note (60 s default); FC-SEC-001 detects `gsk_` keys and Groq
  placeholders and names `GROQ_API_KEY`. FC-GQ-016 is held until G5; FC-GQ-020 is reserved.
- Groq runtime (G4, ADR-038): the mock serves Groq routes under `/openai/v1` beside the
  BharatCode ones (the route prefix selects the behaviour; Groq body/stream/error shapes, Groq's
  documented 400s and [UNVERIFIED]-labelled R4/R5 400s via `core.contract`; `HTTP_ERROR`
  `headers`, the `x_groq.error` stream failure, `TOOL_VALIDATION_ERROR`). `flightcheck test`
  runs a repository as Groq when `[project] provider = "groq"` or the repository-level detection
  is `groq` (`orchestrator.runtime_provider`, `metadata.options.mock_provider`): fake key in
  `GROQ_API_KEY`/`OPENAI_API_KEY`, mock root in `GROQ_BASE_URL`, `<mock>/openai/v1` elsewhere,
  scenarios S01–S06 + S11 (provider-aware `[test]` defaults, `TestConfig.for_provider`).
  `GROQ_API_KEY`/`GROQ_BASE_URL` are never inherited by any child. Groq every-scenario checks:
  FC-RT-002, 006, 007 (Groq catalogue), 009, 015, 050, 060; S11 → FC-RT-044; Groq wording,
  remediation and links via `validators.meta_for`; the gating table is in docs/RULES.md and
  ADR-038. FC-RT-045 (P1) is not built.
- CI: the example workflow now pins `actions/upload-artifact@v7` (verified 2026-11-18,
  ADR-008/ADR-032 item 5).

## Next phase

- Phase G5 (new chat): Groq adapter, `explain --llm groq`, live verification (docs/GROQ_CONTRACT.md
  §4.8, items marked G5). Needs the developer's `GROQ_API_KEY` in their own environment (never
  pasted). Start from docs/handoffs/PHASE_G4.md. The BharatCode path must stay green (ADR-035
  item 2).
- Still open (P1): the `ci` command, Markdown output, the §19.8 tool-using diagnostic
  agent, the malformed-JSON repair attempt, FC-GEN-008/009, FC-SEC-003, FC-RT-010/031/042/043, FC-RT-045 (Groq invalid tool call; G4 P1),
  scenarios S02b/S07–S10, `use_current_python`, `--force-exec`, `flightcheck test --config
  FILE`, the generic OpenAI-compatible adapter and the `providers` command, FC-GQ-020. FC-BC-009
  retires with the BharatCode family in G6; FC-GQ-016 is decided in G5.

## Dropped / deferred (per plan §7.3)

- Dropped (developer, 2026-11-18): BharatCode as the target provider; phases 10 (live probe)
  and 12 (`BharatCode.txt`, Build League submission) are dropped. Groq replaces BharatCode
  (ADR-035); the Build League overlap check (ADR-009) no longer applies.
- Dropped (developer, 2026-11-09): the plan's 21:00 IST code freeze on 10 Nov (§27 Phase 12
  item 24, §4 budget). There is no code freeze; this overrides the plan.
- Deferred from Phase 0: a committed lock file (`uv.lock` or `requirements-lock.txt`); see
  ADR-008.
