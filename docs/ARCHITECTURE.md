# FlightCheck architecture

This is the living description of how FlightCheck is structured. Together with
[DECISIONS.md](DECISIONS.md) it is the source of truth: if the two disagree, the newer ADR wins and
this file is corrected in the same commit.

**Current state (Phase 2):** `src/flightcheck/__init__.py`, `__main__.py`, `cli/app.py` (with
`--version`), the `core` package (`models`, `config`, `errors`, `logging`, `redact`, `paths`) and
the scanner foundation (`scanner/discovery`, `ast_utils`, `parser`, `detect`; no rules yet)
exist. Conventions settled while building `core` are in ADR-023; scanner resolution and detection
rules are in ADR-011 and ADR-024. Everything else below is the target design. Modules are created
in the phase that first gives them real code; there are no placeholder modules.

## 1. Shape of the system

A **modular monolith**: one installable package, one process. Only during `test` are there extra
moving parts: one mock-server thread and one consented child process running the user's app.

```mermaid
flowchart TB
  subgraph CLI["CLI layer (Typer + Rich)"]
    scan[scan] --- test[test] --- explain[explain] --- report[report]
  end
  CLI --> ORCH["Orchestrator (use-case functions)"]
  ORCH --> SCN["Scanner: discovery → AST index → provider detection → rule engine"]
  ORCH --> RT["Runtime: harness → mock provider (FastAPI thread) → traffic validators"]
  ORCH --> DIAG["Diagnostics: consent → sanitise → prompt → provider adapter → validate"]
  ORCH --> REP["Reporting: JSON / HTML / terminal"]
  DIAG --> PROV["Providers: adapter interface, BharatCode, OpenAI-compatible, capability registry"]
  RT --> MOCK["Mock provider: contract-aware FastAPI app + fault scenarios + recorder"]
  SCN & RT & DIAG & REP & PROV & MOCK --> CORE["Core: models, config, redaction, logging, errors, safe paths"]
  RT -. "--allow-exec only" .-> APP["User's app (child process)\nBASE_URL → 127.0.0.1 mock\nAPI key → fake"]
  APP -. HTTP .-> MOCK
  PROV -. "explain / probe only, with consent + key" .-> BC[("BharatCode API\nbharatcode.ai/api/model/v1")]
```

## 2. Data flow per command

| Command                            | Flow                                                                                                                                                 | Network                      | Executes repo code    |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- | --------------------- |
| `scan PATH`                        | discover → parse → index → detect → rules → `Report(kind=scan)` → terminal + optional JSON/HTML                                                      | none                         | no                    |
| `test PATH --allow-exec`           | load `flightcheck.toml` → per scenario: start mock with scenario script → run app command → collect traffic → validators → `Report(kind=test)`       | localhost only               | **yes, with consent** |
| `explain REPORT.json`              | load report → select findings → consent summary → sanitise → call BharatCode (or `--no-llm` / fake) → validate → attach `ai_suggestions` → new report | BharatCode only with consent | no                    |
| `report REPORT.json --format html` | load → render                                                                                                                                        | none                         | no                    |
| `probe` (P1)                       | adapter → documented probes → `runtime-live` checks → sanitised golden transcripts                                                                   | BharatCode                   | no                    |

## 3. Import boundaries

Enforced by an import test from Phase 11.

```text
core          → (stdlib, pydantic only)
scanner       → core
mock          → core
runtime       → core, mock
providers     → core
diagnostics   → core, providers
reporting     → core
orchestrator  → core, scanner, runtime, diagnostics, reporting, providers
cli           → orchestrator, reporting, core
```

No module may import `cli`. `scanner` must never import `providers` or `runtime`, nor
`subprocess`, `importlib` or `runpy`: `scan` stays network-free and execution-free by
construction.

## 4. Components

| #   | Component                  | Module(s)                                         | Responsibility                                                                    | Public interface (MVP)                                         | P   |
| --- | -------------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------- | -------------------------------------------------------------- | --- |
| 1   | CLI layer                  | `cli/app.py`, `cli/render.py`                     | Parse args, call orchestrator, render, map exceptions to exit codes. No logic.    | `main()`, Typer `app`                                          | P0  |
| 2   | Orchestrator               | `orchestrator.py`                                 | One function per use case; wires components; no I/O formatting.                   | `run_scan`, `run_test`, `run_explain` (request → `Report`)     | P0  |
| 3   | Repository discovery       | `scanner/discovery.py`                            | Safe file walk, exclusions, limits, repo metadata.                                | `discover(root, options) -> DiscoveryResult`                   | P0  |
| 4   | Provider detection         | `scanner/detect.py`                               | Classify client constructions and call sites.                                     | `detect_providers(index) -> list[ProviderDetection]`           | P0  |
| 5   | Provider adapter interface | `providers/base.py`                               | Abstract base + normalised types.                                                 | `ProviderAdapter`                                              | P0  |
| 6   | BharatCode adapter         | `providers/bharatcode.py`                         | Live calls with BharatCode pre-flight validation, stream handling, error mapping. | `BharatCodeAdapter`                                            | P0  |
| 7   | OpenAI-compatible adapter  | `providers/openai_compat.py`                      | Conservative pass-through; capabilities `unknown` unless probed or declared.      | `OpenAICompatibleAdapter`                                      | P1  |
| 8   | Capability registry        | `providers/capabilities.py`                       | Profiles: capability → support level + source.                                    | `get_profile(provider) -> CapabilityProfile`                   | P0  |
| 9   | Static rule engine         | `scanner/engine.py`, `scanner/rules/*.py`         | Rule registry, execution, ordering, filtering.                                    | `@rule(...)`, `run_rules(index, config) -> list[Finding]`      | P0  |
| 10  | Workflow validator         | `runtime/validators.py`                           | Assertions over recorded traffic.                                                 | `validate_traffic(traffic, scenario) -> list[CheckResult]`     | P0  |
| 11  | Mock provider              | `mock/app.py`, `payloads.py`, `recorder.py`, `server.py` | Contract-aware fake BharatCode API; recording; thread runner.              | `create_mock_app(script, recorder)`, `MockServer`              | P0  |
| 12  | Failure simulation         | `mock/faults.py`, `runtime/scenarios.py`          | Scripted per-request behaviours; scenario catalogue + expectations.               | `FaultScript`, `SCENARIOS`                                     | P0  |
| 13  | AI diagnostic service      | `diagnostics/*.py`                                | Consent, sanitisation, prompting, validation, fallback.                           | `DiagnosticService.explain(report, options)`                   | P0  |
| 14  | Report generation          | `reporting/*.py`                                  | JSON (schema-versioned), HTML, terminal.                                          | `write_json`, `render_html`, `render_terminal`                 | P0  |
| 15  | Configuration              | `core/config.py`                                  | Merge defaults ← `flightcheck.toml` ← env ← CLI flags; secrets never in `repr`.   | `load_config(root, overrides) -> FlightCheckConfig`            | P0  |
| 16  | Logging & errors           | `core/logging.py`, `core/errors.py`               | Redacting log filter; exception hierarchy with user-facing hints.                 | `get_logger`, `FlightCheckError` subclasses                    | P0  |
| 17  | CI integration             | `.github/workflows/*.yml`, `docs/CI.md`           | Gate builds on findings.                                                          | exit codes 0/1/2/3                                             | P0  |
| 18  | Live probe                 | `probe/live.py`                                   | Documented-behaviour probes against BharatCode.                                   | `run_probes(adapter) -> list[CheckResult]`                     | P1  |
| 19  | Runtime harness            | `runtime/harness.py`                              | Consent-gated subprocess runner with scrubbed env and timeouts.                   | `run_app(command, env, timeout) -> AppRun`                     | P0  |

## 5. Packages and layers

| Package           | Layer              | Responsibility                                    | Depends on                    |
| ----------------- | ------------------ | ------------------------------------------------- | ----------------------------- |
| `cli`             | CLI                | Argument parsing, rendering, exit codes           | orchestrator, reporting, core |
| `orchestrator.py` | application        | Use-case wiring                                   | all below                     |
| `core`            | core/configuration | Models, config, errors, logging, redaction, paths | stdlib, pydantic              |
| `scanner`         | scanner            | Discovery, AST index, detection, rules            | core                          |
| `providers`       | provider           | Adapters, capabilities, HTTP, SSE                 | core, httpx2                  |
| `mock`            | testing            | Contract mock, faults, recording                  | core, fastapi, uvicorn        |
| `runtime`         | testing            | Harness, scenarios, validators                    | core, mock                    |
| `diagnostics`     | provider/AI        | Explain pipeline                                  | core, providers               |
| `probe`           | provider           | Live probes                                       | core, providers               |
| `reporting`       | reporting          | JSON/HTML/terminal                                | core, jinja2, rich            |

## 6. Target repository layout (condensed)

```text
BharatCode.txt  README.md  CLAUDE.md  LICENSE  pyproject.toml  .gitignore
.github/workflows/ci.yml               ruff + pytest on 3.11/3.12/3.13 (dogfood scan from Phase 9)
docs/                                  ARCHITECTURE, DECISIONS, IMPLEMENTATION_STATUS, QUALITY_GATES,
                                       BHARATCODE_CONTRACT, RULES, CI, EVALUATION, report.schema.json,
                                       handoffs/PHASE_XX.md
scripts/                               eval_fixtures.py, demo.sh
examples/bharatcode-weather-agent/     demo app with deliberate mistakes
src/flightcheck/
  __init__.py  __main__.py  orchestrator.py
  cli/         app.py, render.py
  core/        models, config, errors, logging, redact, paths
  scanner/     discovery, parser, ast_utils, detect, engine, rules/{bharatcode,robustness,security}
  providers/   base, capabilities, http (all httpx2 client creation), sse, bharatcode,
               openai_compat, registry
  mock/        faults, payloads, recorder, app, server
  runtime/     harness, scenarios, validators
  diagnostics/ schema, sanitize, context, prompt, fake_llm, service
  probe/       live.py
  reporting/   json_report, terminal, html_report, templates/report.html.j2
tests/         unit, scanner, providers, mock, runtime, diagnostics, cli, security, live,
               architecture, fixtures/ (rule fixtures, fixture apps, expected results, golden)
```

## 7. CLI surface (planned)

P0 commands: `scan PATH`, `test PATH --allow-exec`, `explain REPORT.json`, `report REPORT.json`.
P1: `ci`, `probe`, `init`, `providers`, `mock-server`. Global options: `--version`,
`-v/--verbose`, `-q/--quiet`, `--no-color`.

Exit codes: `0` completed with nothing at or above `--fail-on`; `1` completed with at least one
finding or failed check at or above `--fail-on`; `2` usage, configuration or consent error; `3`
internal or infrastructure error.
