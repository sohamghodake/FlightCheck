# Architecture decision records

Append-only log. Superseded ADRs are marked as such, never deleted. When an ADR conflicts with
[ARCHITECTURE.md](ARCHITECTURE.md), the newer ADR wins and ARCHITECTURE.md is corrected in the same
commit.

Format:

```markdown
## ADR-0NN — <title>

- Date: YYYY-MM-DD · Phase: N · Status: accepted | superseded by ADR-0MM
- Context:
- Decision:
- Consequences:
```

---

## ADR-000 — The implementation plan is local-only

- Date: 2026-11-06 · Phase: 0 · Status: accepted
- Context: Development follows a detailed phase plan. The developer wants that plan kept out of the
  public repository, but later sessions and readers of the repository still need to understand
  what was decided and why.
- Decision: The implementation plan is kept locally at
  `docs/FLIGHTCHECK_IMPLEMENTATION_PHASE_PLAN.md` and is intentionally git-ignored. Committed docs
  (`DECISIONS.md`, `ARCHITECTURE.md`, `BHARATCODE_CONTRACT.md`) must be self-sufficient for anyone
  reading the public repo.
- Consequences: CI, tests and tooling never read the plan file. Anything from the plan that the
  code relies on is copied into a committed doc or recorded here as an ADR. The developer keeps a
  backup of the plan outside the repository, because it is not on GitHub.

## ADR-001 — Modular monolith, CLI-first, no service

- Date: 2026-11-06 · Phase: 0 · Status: accepted
- Context: One developer, a four-day hackathon deadline (10 Nov 2026), and a tool that runs on a
  developer's machine or in CI.
- Decision: FlightCheck is one installable Python package with a Typer CLI. No hosted service,
  database, authentication, multi-user features or web dashboard in the MVP.
- Consequences: Simple to install (`pip install`), test and demo. Packages inside the monolith are
  separated by import rules (ARCHITECTURE.md §3) so they can be split later if needed.

## ADR-002 — Synchronous core; asyncio only inside the mock server

- Date: 2026-11-06 · Phase: 0 · Status: accepted
- Context: FlightCheck's workload is sequential: scan files, run a scenario, call the model once
  per finding group. Async code is harder to write and test.
- Decision: All of FlightCheck's own code is synchronous. asyncio appears only inside the mock
  provider, which runs on Uvicorn in a background thread. Async adapters are post-hackathon (P2).
- Consequences: No pytest-asyncio. Tests drive the mock in-process through a synchronous ASGI
  transport where possible.

## ADR-003 — Raw HTTP in adapters; OpenAI SDK only in fixture apps and tests

- Date: 2026-11-06 · Phase: 0 · Status: accepted
- Context: Adapters must see exact wire payloads, raw SSE (including queue keepalive comments) and
  the `x-request-id` header, and must control retries themselves. The OpenAI Python SDK retries
  twice by default and hides wire details.
- Decision: FlightCheck's provider adapters use HTTPX2 directly. The OpenAI SDK is a dev/test
  dependency only, used to write realistic fixture apps (what users actually write) and to check
  that the mock is SDK-parseable.
- Consequences: More code in adapters (request building, SSE parsing, error mapping), in exchange
  for wire-level control and testability.

## ADR-004 — HTTPX2 instead of HTTPX, isolated in `providers/http.py`

- Date: 2026-11-06 · Phase: 0 · Status: accepted
- Context: HTTPX's latest stable release is 0.28.1 (Dec 2024), with only 1.0 dev pre-releases
  since. `httpx2` is the Pydantic-maintained continuation (2.16.0, Production/Stable, Python ≥
  3.11, includes ASGI transport), and the current OpenAI SDK and FastAPI's `TestClient` use it.
- Decision: Use `httpx2`. All HTTP client creation goes through one module,
  `providers/http.py`.
- Consequences: Swapping back to `httpx` is a one-file change if needed. Whether httpx2 offers a
  sync `ASGITransport` / `MockTransport` must be confirmed in Phase 5A / 6.

## ADR-005 — Runtime validation = consented subprocess + traffic-recording contract mock

- Date: 2026-11-06 · Phase: 0 · Status: accepted
- Context: Static analysis alone can only suspect problems. Runtime evidence about the
  developer's app requires running it, but instrumenting arbitrary user code is fragile and
  invasive.
- Decision: `flightcheck test` starts a local mock that follows BharatCode's documented contract,
  runs the developer's app in a child process only with explicit `--allow-exec` consent (scrubbed
  environment, fake API key, base URL pointed at `127.0.0.1`, timeouts), records every request,
  and validates the recorded traffic against the contract under scripted failure scenarios.
- Consequences: Findings can be marked `runtime_mock`-verified. Repository code is executed only
  inside `runtime/harness.py`. A mock is not the real service; live behaviour is checked separately
  by the P1 probe.

## ADR-006 — The LLM is downstream of deterministic results; default context is metadata

- Date: 2026-11-06 · Phase: 0 · Status: accepted
- Context: AI explanations are useful but non-deterministic, and sending source code to an
  external API is a privacy decision the user must make.
- Decision: `explain` consumes a finished JSON report. The model can add `ai_suggestions` but can
  never change a status, severity or verification level. The default context sent is finding
  metadata only; source snippets are sent only with `--context snippets`, after a consent step
  that states exactly what will be sent. `--no-llm` and a deterministic fake LLM always work.
- Consequences: Reports stay reproducible. AI output is always labelled as an unverified
  suggestion.

## ADR-007 — Python ≥ 3.11

- Date: 2026-11-06 · Phase: 0 · Status: accepted
- Context: `httpx2` requires Python ≥ 3.11; `tomllib` is in the standard library from 3.11;
  Python 3.10 reached end-of-life in October 2026.
- Decision: `requires-python = ">=3.11"`. CI tests 3.11, 3.12 and 3.13. Develop on 3.12 or 3.13.
- Consequences: No `tomli` backport needed. Newer syntax (`X | Y` unions, `typing.Self`) is
  allowed.

## ADR-008 — Verified dependency versions and GitHub Action majors

- Date: 2026-11-06 · Phase: 0 · Status: accepted
- Context: Version bounds must come from what is actually published, not from memory.
- Decision: Versions below were read on **2026-11-06** from the PyPI JSON API
  (`https://pypi.org/pypi/<name>/json`, latest release and upload date) and, for GitHub Actions,
  from each action's latest GitHub release (`https://api.github.com/repos/<owner>/<repo>/releases/latest`,
  the repositories behind the Marketplace listings).

  | Package / action        | Latest found | Released   | Requires-Python | Bound in `pyproject.toml` / CI |
  | ----------------------- | ------------ | ---------- | --------------- | ------------------------------ |
  | typer                   | 0.28.0       | 2026-11-06 | ≥ 3.10          | `>=0.28,<1`                    |
  | rich                    | 15.0.0       | 2026-04-12 | ≥ 3.9           | `>=15,<16`                     |
  | pydantic                | 2.14.3       | 2026-11-05 | ≥ 3.10          | `>=2.14,<3`                    |
  | httpx2                  | 2.16.0       | 2026-10-21 | ≥ 3.11          | `>=2.16,<3`                    |
  | fastapi                 | 0.162.0      | 2026-10-23 | ≥ 3.10          | `>=0.162,<1`                   |
  | uvicorn                 | 0.54.0       | 2026-09-25 | ≥ 3.10          | `>=0.54,<1`                    |
  | jinja2                  | 3.1.6        | 2025-03-05 | ≥ 3.7           | `>=3.1.6,<4`                   |
  | pytest (dev)            | 9.2.0        | 2026-10-27 | ≥ 3.10          | `>=9.2,<10`                    |
  | ruff (dev)              | 0.17.4       | 2026-11-05 | ≥ 3.7           | `>=0.17.4,<0.18`               |
  | openai (dev)            | 3.46.0       | 2026-11-05 | ≥ 3.10          | `>=3.46,<4`                    |
  | hatchling (build)       | 1.33.0       | 2026-10-28 | ≥ 3.10          | `>=1.33`                       |
  | `actions/checkout`      | v7.0.1       | 2026-07-20 | —               | `actions/checkout@v7`          |
  | `actions/setup-python`  | v7.1.0       | 2026-10-22 | —               | `actions/setup-python@v7`      |
  | `astral-sh/setup-uv`    | v10.3.3      | 2026-10-28 | —               | not used in CI yet             |
  | uv (local, informative) | 0.12.10      | —          | —               | documented in README only      |

- Consequences: ruff is pinned to one minor series because ruff minors can change lint and format
  output; bump it deliberately. Rich and Uvicorn have upper bounds at the next major/1.0. The plan
  had marked Rich, Uvicorn, Jinja2 and ruff as unverified; they are now verified as above. No
  lock file is committed in Phase 0; the plan's `uv.lock` / `requirements-lock.txt` is deferred to a
  later phase.

## ADR-010 — Report schema 1.0 is frozen for the MVP; additive changes only

- Date: 2026-11-06 · Phase: 1 · Status: accepted
- Context: Reports are consumed by CI, by `explain`, by the HTML renderer and by users' own
  tooling. Changing field meanings between phases would break saved reports and later sessions.
- Decision: `core/models.py` defines report schema `"1.0"` (`ReportMetadata.schema_version`).
  Until v0.1.0 ships, changes are additive only: new optional fields, new enum members, tighter
  types for the three placeholder fields listed in ADR-023. No field is renamed or removed and no
  status meaning changes. A breaking change needs a new ADR and schema `"2.0"`.
- Consequences: Phase 3 exports `docs/report.schema.json` from `Report.model_json_schema()` and a
  drift test keeps them in sync.

## ADR-023 — Core conventions settled in Phase 1

- Date: 2026-11-06 · Phase: 1 · Status: accepted
- Context: The plan leaves some details of configuration, redaction and the report models open.
  ADR numbers 011–022 are reserved by the plan for decisions in later phases, so this record uses
  the next free number.
- Decision:
  1. **Secret-looking config keys.** `load_config` rejects any key (in `flightcheck.toml` or CLI
     overrides) whose name matches `(?i)(api_?key|token|secret|password)` *and* whose value is a
     non-empty string. Keys ending in `_env` name an environment variable and are allowed
     (`api_key_env`); non-string values are allowed (`max_output_tokens = 4000`). The error
     message never includes the value.
  2. **API keys.** `ProviderConfig.api_key` is a `SecretStr` filled only from the environment
     variable named by `api_key_env`, excluded from `model_dump`/JSON and from `repr`. The value
     is also passed to `register_secret`, so redaction catches it even where no pattern matches.
  3. **Environment overrides.** Only these map onto config: `FLIGHTCHECK_PROVIDER` → `provider`,
     `FLIGHTCHECK_LLM` → `explain.llm`, `FLIGHTCHECK_MODEL` → `explain.model`,
     `FLIGHTCHECK_CONTEXT` → `explain.context`. Empty values are ignored. `FLIGHTCHECK_NO_NETWORK`
     and `FLIGHTCHECK_LIVE` are runtime switches, not config. New mappings are added here.
  4. **TOML layout.** `[project] provider` maps to `FlightCheckConfig.provider`.
     `[providers.<name>]` tables merge onto the built-in `bharatcode` profile (partial overrides
     allowed); other provider names must give `kind` and `base_url`. Unknown keys are errors
     (`extra="forbid"`).
  5. **Redaction order.** Registered secrets, then secret-looking environment values, then
     patterns, so a pattern never partially rewrites a known value first. Redaction is idempotent.
  6. **Logging.** The `RedactingFilter` sits on the handler (logger-level filters do not see
     records from child loggers) and redacts the formatted message, tracebacks and stack info.
  7. **Placeholder types** (to be tightened by the owning phase, additive under ADR-010):
     `ScenarioResult.exchanges: list[dict]` → `RecordedExchange` (Phase 5A);
     `Report.capability_profile: dict | None` → `CapabilityProfile` (Phase 6);
     `ProviderConfig.declared_capabilities: dict[str, bool]` → `dict[Capability, bool]` (Phase 6);
     `Report.ai_suggestions: list[dict]` → `AISuggestion` (Phase 8).
  8. **Model details.** `Evidence.excerpt` is truncated to 2 000 characters (with `…`) rather
     than rejected. `ReportMetadata.generated_at` must be timezone-aware. `Summary` lists only
     non-zero counts, in enum order; `highest_severity` covers all findings, hypotheses included
     (CI gating applies its own hypothesis rule). Scan size limits use binary units
     (1 MB = 1 MiB).
- Consequences: Later phases build on these without re-deciding them. Changes require a new ADR
  that supersedes the relevant item.

## ADR-011 — One-hop resolution only; limitations are documented

- Date: 2026-11-06 · Phase: 2 · Status: accepted
- Context: Static rules need to know which client a call such as `client.responses.create(...)`
  uses, but FlightCheck never runs repository code and has no type inference.
- Decision: The AST index (`scanner/parser.py`) resolves a call's receiver one hop, lexically:
  1. an inline constructor (`OpenAI(...).responses.create`);
  2. a bare name bound by `=`, an annotated assignment or `with ... as name` in the current
     function, then in enclosing functions, then at module level (class bodies are skipped, as in
     Python's own name lookup). In the same scope the latest binding at or before the call line
     wins; from another scope the last binding in that scope is used;
  3. `self.<attr>` (the method's first parameter) bound anywhere in the same class, including a
     class-body assignment; bindings made in a later method still count.
  Client constructors are recognised only through the file's imports (`import openai as oa`,
  `from openai import OpenAI as C`); an unimported `OpenAI()` is not a client. One-hop lookups
  also give `base_url` constant fragments (`BASE = "https://…"`) and environment variable names
  (`url = os.getenv("X")`) for detection; `kwargs` themselves keep `Unknown` for non-literals.
- Not resolved (the call site is still indexed, with `binding=None`): clients passed as
  arguments, returned from factories, stored in containers, imported from other modules,
  captured by a closure inside a method (`self` of the outer method), produced by lambdas, or
  bound with the walrus operator; tuple unpacking; `global`/`nonlocal`; `getattr` dispatch;
  decorators are recorded by name, never evaluated. Such call sites take the repo-level provider
  at `low` confidence (ADR-024). Only literal values are inspected; `try` coverage is lexical
  (the body of a `try` in the same function; handlers, `else` and `finally` do not count, and a
  `def` inside a `try` is not protected).
- Consequences: Simple, fast and predictable (200 files index in well under a second). Some true
  problems are reported at lower confidence or missed. Phase 3 copies this list into
  `docs/RULES.md` and the report footer (plan §16.2).

## ADR-024 — Provider detection combination rules

- Date: 2026-11-06 · Phase: 2 · Status: accepted
- Context: The plan's signal table (§16.1 step 4) says to take the highest-confidence signal and
  to report `unknown` on conflicting high-confidence signals, but not how to break ties between
  equal medium or low signals, nor what to do for HTTP clients.
- Decision:
  1. Highest confidence wins. Two `high` signals naming different providers → `unknown` (`low`)
     plus an `info` diagnostic `provider_conflict`.
  2. A documented-model signal (`model="qwen-3.8-27b"` / `"deepseek-v4.1-flash"`, from
     BHARATCODE_CONTRACT.md) is *supporting*: it never outweighs a direct signal of the same
     confidence. So `OpenAI(base_url="https://other.example/v1")` with a BharatCode model stays
     `openai_compatible_unknown`, while `OpenAI()` with that model becomes `bharatcode`/`medium`.
  3. Remaining equal-confidence ties resolve in the order `bharatcode` >
     `openai_compatible_unknown` > `openai_or_env` > `unknown` (e.g. a BharatCode API-key env
     name plus an env-supplied `base_url` → `bharatcode`/`medium`).
  4. `httpx`/`httpx2`/`requests` clients without `base_url` are `unknown`/`low` (the
     `openai_or_env` default applies only to the OpenAI SDK constructors).
  5. A call site with a resolved client inherits the client's signals and adds its own (model,
     raw URL). An unresolved call site uses its own signals plus "repo-level provider at `low`".
     The repo-level detection combines every client and call-site signal (location path `"."`,
     line 0) and is absent when the repository has no clients or call sites.
  6. `flightcheck.toml` `provider = "bharatcode"` is passed in as `declared_provider` and adds a
     `high` signal everywhere. Signals are matched case-insensitively (`bharatcode.ai`,
     `BHARATCODE` in env var names). The `openai` classification is not produced in the MVP: the
     signal table has no row for it.
- Consequences: Detection is deterministic and explainable (each `ProviderDetection.signals`
  lists the reasons). Changing a rule needs a new ADR.


## ADR-012 — Rule IDs are permanent; retired rules are never reused

- Date: 2026-11-07 · Phase: 3 · Status: accepted
- Context: Rule IDs (`FC-BC-001`…) appear in saved JSON reports, `--rule` flags, CI
  configuration, `tests/fixtures/expected_findings.json` and users' suppressions. Reusing an ID
  for a different check would silently change what those references mean.
- Decision: A rule ID, once released in a commit, always names the same check. A retired rule
  keeps its ID reserved (listed as retired in `docs/RULES.md`); new rules take the next free
  number in their family. `scanner.engine.rule` refuses duplicate IDs at import time.
  `Finding.id` hashes the rule ID with the location, so it is stable across runs.
- Consequences: Gaps in the numbering are expected (FC-BC-009 is reserved for the P1
  request-ID rule). Changing a rule's meaning needs a new ID.

## ADR-025 — `scan` output, provider gating and one-hop container literals

- Date: 2026-11-07 · Phase: 3 · Status: accepted
- Context: §23.2 and §23.3 leave open when the JSON report is written, how `--provider`
  combines with `flightcheck.toml`, and how FC-BC-003/008 see one-hop list literals.
- Decision:
  1. **JSON is always written**, to `--output DIR` or `PATH/.flightcheck/` (created with a
     `.gitignore` of `*`), so the terminal summary can name the report path. `--format terminal`
     prints the table; `--format json` prints only the report path. `--format html|all` exits 2
     ("HTML report arrives in Phase 9") until Phase 9. An `--output` that is an existing file
     exits 2. The report file name goes through `resolve_inside(out_dir, …)`.
  2. **Provider gating.** `--provider bharatcode` runs BharatCode rules on every call site and
     disables the low-confidence downgrade; `--provider openai_compatible` disables them;
     `auto` uses `flightcheck.toml` `provider` (a profile's `kind`) as `declared_provider`, then
     detection. Without an override, BharatCode rules run on call sites classified `bharatcode`
     at any confidence and the engine lowers a finding's confidence one level when the call
     site's provider confidence is `low`.
  3. **One-hop containers.** `CallSite.one_hop` (additive) maps a parameter whose value is a
     bare name to the list/dict/tuple literal bound to that name (same lookup order as string
     constants in ADR-011; bindings are those visited before the call). The nearest scope that
     binds the name wins even when its value is unusable (a parameter, `x = make()`, a loop or
     `with` target, `+=`), so an outer literal is never seen through a local rebinding; this also
     applies to the Phase 2 string-constant and env-name lookups. `kwargs`/`params` keep
     `Unknown`. Findings from one-hop values use `medium` confidence (FC-BC-003).
  4. **`--fail-on`** counts findings at or above the threshold; hypotheses only with
     `--fail-on-hypotheses`. Diagnostics (including `rule_crashed`) never change the exit code.
  5. **Next-step hints** name only commands that exist; `test`/`explain` hints are added by the
     phases that ship those commands.
  6. Messages wrap inside the findings table; paths, the summary, the exit line and errors are
     printed unwrapped so they can be copied.
- Consequences: Running `scan` creates `.flightcheck/` in the target unless `--output` is
  given. `docs/report.schema.json` is generated by
  `python -m flightcheck.reporting.json_report docs/report.schema.json` and kept in step by
  `tests/unit/test_schema_export.py`; the repository has no JSON Schema validator dependency,
  so reports are validated with `Report.model_validate` (the schema's source).
