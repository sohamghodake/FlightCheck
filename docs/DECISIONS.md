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

## ADR-013 — Heuristic rules carry ≤ medium confidence and "not observed in this function" wording

- Date: 2026-11-07 · Phase: 4 · Status: accepted
- Context: FC-GEN-001/002/003/006 look only at the attributes and strings of the function that
  makes the call (ADR-011: lexical, no inter-procedural flow). A check performed in a helper,
  a wrapper library or a caller is invisible to them.
- Decision: These rules never use `high` confidence and their messages say "… not observed in
  `<function>`" (or "in module-level code"), never "missing". Calls at module level
  (`CallSite.function is None`) use `FileIndex.module_facts`: the attributes and strings of
  module-level code outside any function (a check inside a function does not count for them).
  A known false positive (finish_reason checked in a helper) is kept as the fixture app
  `known_fp_helper_check`, recorded in the manifest *with* its finding so changes are visible.
- Consequences: Users can tell heuristics from contract facts. `tests/scanner/test_rules_fixtures.py`
  asserts the confidence ceiling and the wording.

## ADR-026 — Secret masking in the rule context; GEN/SEC detection details

- Date: 2026-11-07 · Phase: 4 · Status: accepted
- Context: Every excerpt goes through `core.redact`, which misses short or dict-form literals
  (`api_key="abc"`, `{"api_key": "abc12345"}`), so any rule whose excerpt covers such a line
  (FC-GEN-005 on the client, FC-GEN-004 on the call) would print the key.
- Decision:
  1. **Masking hook on `RuleContext`, not the global registry.** Before rules run,
     `run_rules` calls `RuleContext.collect_secrets()`: every prefixed key (`bc_live_…`,
     `sk-…`) inside any string literal and every non-placeholder literal given to an
     API-key-like name (kwarg, dict key or assignment). `RuleContext.mask()` replaces them in
     every excerpt, finding message and `rule_crashed` diagnostic, then applies `redact`.
     Values shorter than 4 characters are replaced only in quoted form. Nothing global is
     changed, so no state leaks between scans or tests.
  2. **FC-SEC-001** detects prefixed literals (critical/high) and literals given to `api_key=`,
     a dict key or a name whose words end in "api key" (`OPENAI_API_KEY`, `apiKey`;
     high/medium). The plan names only `api_key=`; the dict and assignment forms are the same
     mistake and are needed for masking anyway. Placeholders (`""`, `EMPTY`, `dummy`, `test`,
     `your_key_here`, `bc_live_your_key_here`, case-insensitive) are `info`. Prefix patterns
     require a non-alphanumeric character before the prefix (`task-…` is not `sk-…`).
  3. **FC-SEC-002** matches credential-like names by whole word (`api`+`key`, `apikey`,
     `secret`, `token`, `authorization`), not by substring, so `usage.total_tokens` and
     `max_tokens` are not credentials. Receivers: `print`, `logging.<method>`, and any
     `log`/`logger` receiver with a logging method name.
  4. **Low provider confidence lowers only provider-specific rules.** The ADR-025 downgrade
     is skipped for rules with `providers={"*"}`: an unsure provider says nothing about
     whether a call has a `try` or a client has a timeout.
  5. **FC-GEN-007** judges only literal shapes: dict/list/tuple/set or a non-string constant →
     high/high; a bare name or a call other than `json.dumps`/`str`/`repr`/`"…".format`/
     `"…".join` → high/low; f-strings, concatenation, attributes and subscripts are not judged.
  6. **FC-GEN-005** checks `openai.OpenAI`/`AsyncOpenAI` and `httpx`/`httpx2` clients (not
     `requests.Session`, which has no client-level timeout); any `.with_options(timeout=…)` in
     the file suppresses it. `**kwargs` in the constructor is not seen (a possible false
     positive).
- Consequences: Planted secrets of every form are absent from JSON, terminal output and every
  finding (`tests/security/test_scan_redaction.py`). FlightCheck's own source avoids literals its
  FC-SEC-001 would match (the dogfood gate).

## ADR-014 — Mock enforces documented 400s so apps experience the real contract

- Date: 2026-11-07 · Phase: 5A · Status: accepted
- Context: BharatCode documents five `/responses` features as unsupported with HTTP 400
  (`store:true`, `previous_response_id`, hosted tools, background jobs, audio/video/file
  input; BHARATCODE_CONTRACT.md §15.1). An app that uses them works against OpenAI and fails
  only on BharatCode. A permissive mock would hide exactly the mistakes FlightCheck exists to
  find.
- Decision: `mock/app.py` rejects those requests on `/responses` with 400 before any scripted
  step runs, mirroring FC-BC-001…004/008: `store is True`; `previous_response_id` present and
  not null; any tool whose `type` is a string other than `"function"`; `background is True`;
  `input_audio`/`input_file` items or `file_id`/`file_data` keys anywhere in `input`.
  Look-alikes (`store:false`, omitted `store`, `previous_response_id:null`, function tools in
  either style, `background:false`, text/image input) are accepted. All violations are
  recorded in `response_summary["violations"]`; the first one is reported. Chat Completions
  has no documented 400s, so none are enforced there. Error bodies use OpenAI's shape
  `{"error": {"message", "type": "invalid_request_error", "code"}}`; that shape, the codes
  (`unsupported_parameter`, `unsupported_tool_type`, `unsupported_input_type`) and the
  treatment of a JSON null are **[UNVERIFIED]** until the Phase 10 probe.
- Consequences: Runtime scenarios observe the same failure the live service would produce, and
  a rejected request does not consume the fault script. If the probe shows different codes or
  shapes, only `responses_contract_violations` and `payloads.error_body` change.

## ADR-027 — Mock conventions settled in Phase 5A

- Date: 2026-11-07 · Phase: 5A · Status: accepted
- Context: The plan leaves the mock's guard order, recording details and test client open, and
  the mock must not import the scanner (docs/ARCHITECTURE.md §3).
- Decision:
  1. **Shared contract facts in core.** `BHARATCODE_DOCUMENTED_MODELS`,
     `BHARATCODE_PRO_ONLY_MODEL`, `UNSUPPORTED_INPUT_TYPES` and `FILE_INPUT_KEYS` moved to
     `core/config.py`; `scanner/detect.py` re-exports the model set and the BC rules alias the
     rest, so scanner behaviour and tests are unchanged.
  2. **`RecordedExchange` and `Endpoint` live in `core/models.py`** (core may import only
     stdlib and pydantic). `Endpoint` values match the scanner's call-site endpoints
     (`responses`, `chat.completions`) plus `models`. `ScenarioResult.exchanges` is now
     `list[RecordedExchange]` (additive under ADR-010; schema regenerated).
  3. **Guard order:** request limit (429 `flightcheck_request_limit`, counted over every
     request including rejected ones) → unknown route (404/405, recorded) → bearer token
     (401 when missing, blank or not `Bearer`) → `GET /models` → JSON object body (400
     `invalid_json`) → documented 400s (ADR-014) → `stream:true` (501
     `flightcheck_streaming_unavailable`, "streaming arrives in phase 5B") → next scripted
     step. Only the last consumes the script. Handler exceptions → recorded 500
     `flightcheck_mock_error` whose message names only the exception type.
  4. **Auth flag for FC-RT-050:** `response_summary["auth"]` is `missing`, `fake_key`,
     `other_key` or `unchecked` (no fake key configured); the header is stored as
     `[FAKE KEY]` only for the configured fake key, `[REDACTED]` otherwise. Other headers whose
     name contains key/token/secret/password/cookie/auth are stored as `[REDACTED]`; every other
     header value, the body, the path and the summary go through `core.redact`. Outgoing bodies
     are redacted too. `request_sha256` hashes the redacted body (or the redacted raw text when
     it is not a JSON object), so retries still hash identically.
  5. **Non-stream meaning of stream actions:** `STREAM_FAIL_AFTER` → 500 (§18.2),
     `KEEPALIVE_STREAM` → text, `DELAY` → `asyncio.sleep(seconds)` (0–600) then text.
     `HTTP_ERROR` defaults to the documented 503; `retry_after` adds a `Retry-After` header
     only when a script asks ([UNVERIFIED] for BharatCode).
  6. **Payload details:** fixed `created_at`/`created` (2026-11-06T00:00Z) for determinism; IDs
     `resp_fc_mock_<seq>`, `chatcmpl-fc_mock_<seq>`, `call_fc_<scenario>_<seq>`,
     `x-request-id: fc_mock_<seq>`. Responses bodies carry the SDK-required
     `parallel_tool_calls`, `tool_choice`, `tools: []` (request tools are not echoed) and usage
     detail objects; truncated tool calls carry a JSON prefix that never parses. Both tool
     styles are accepted on both endpoints (`response_summary["tool_style"]`).
  7. **Test client:** httpx2 2.16's `ASGITransport` is async-only (`AsyncBaseTransport`), so
     tests use FastAPI's `TestClient` (Starlette 1.10, a sync `httpx2.Client`); the OpenAI SDK
     accepts it as `http_client`, so SDK parsing is tested in-process without sockets.
  8. **Recorder bound:** `TrafficRecorder(max_records=1000)`; extra exchanges are counted in
     `dropped`.
- Consequences: Phase 5B adds streaming branches and `MockServer` without changing these
  rules; Phase 7 validators read `response_summary` keys (`finish`, `call_ids`, `no_tools`,
  `auth`, `violations`, `error_code`).

## ADR-015 — Mock streaming modelled on OpenAI event shapes until golden transcripts exist

- Date: 2026-11-07 · Phase: 5B · Status: accepted
- Context: BharatCode documents only the Responses event names that matter to a client
  (`response.output_text.delta`; `response.completed` / `response.incomplete` end a stream;
  `response.failed` is an error; keepalive comments are not output; a busy model that already
  started streaming ends with `response.failed`). Payload fields, the other event names and the
  whole Chat Completions stream format are not documented (BHARATCODE_CONTRACT.md §15.1).
- Decision:
  1. **Responses SSE** (`event: <name>\ndata: <json>\n\n`) follows OpenAI's sequence:
     `response.created`, `response.in_progress`, `response.output_item.added`,
     `response.content_part.added`, `response.output_text.delta` × n (word-sized pieces),
     `response.output_text.done`, `response.content_part.done`, `response.output_item.done`,
     then `response.completed` or `response.incomplete`. Tool calls use
     `response.function_call_arguments.delta` × n (8-character pieces) and `.done`; a truncated
     tool call has no `.done` and ends with `response.incomplete`. The terminal event's
     `response` is exactly the non-stream body for the same step. `sequence_number` counts from
     0. Everything except the documented names is **[UNVERIFIED]**.
  2. **`STREAM_FAIL_AFTER`**: Responses sends `n` text deltas (default 2) then `response.failed`
     with `error.code = "server_error"`. Chat sends a role chunk and `n` content chunks, then ends
     the body cleanly with no finish chunk and no `[DONE]` (Chat has no documented failure
     event; a clean end is deterministic and is what an abrupt close looks like to an SSE
     reader).
  3. **Chat SSE** (`data: <chunk>\n\n`, OpenAI convention, **[UNVERIFIED]**): a role chunk,
     content or tool-call argument chunks, a chunk with `finish_reason` (`stop`, `tool_calls`,
     `length`), then `data: [DONE]`. No usage chunk.
  4. **`KEEPALIVE_STREAM`** puts `: keepalive` before every chunk, so the first bytes a client
     sees are a comment (as when queued). The exact comment text is **[UNVERIFIED]**; FlightCheck's
     own parser treats any comment as a keepalive (plan §14.6).
  5. **Non-stream actions on a stream request:** `HTTP_ERROR` is a JSON error before the stream
     starts (BharatCode's documented busy 503); `DELAY` waits before the first byte.
  6. **Recording:** a stream is recorded once, when the response ends (a `StreamingResponse`
     subclass calls the recorder in `finally`), with `finish`, `call_ids`, `terminal_event` /
     `finish_reason` / `done_sent`, `keepalives`, `chunks_sent` and `chunks_planned`. A client
     that leaves early gives `finish: "client_disconnected"` plus `planned_finish`. Because the
     exchange is appended after the last byte, readers use the recorder after
     `MockServer.stop()`, which waits for in-flight requests.
  7. **`MockServer`** binds a socket to `127.0.0.1:0` itself and passes it to `uvicorn.Server`
     (no port race), runs it in a daemon thread with `http="h11"`, `lifespan="off"`,
     `timeout_graceful_shutdown=2`, waits ≤ 5 s for `started` (else `MockServerError`, exit 3,
     message names the port), and on stop sets `should_exit`, joins ≤ 5 s, then `force_exit`.
     Uvicorn 0.54 skips signal handlers off the main thread (`Server.capture_signals`), so
     nothing is disabled.
- Consequences: The OpenAI SDK parses every stream, including its `responses.stream()`
  helper. When the Phase 10 probe records real BharatCode streams, golden transcripts replace
  these shapes wherever they differ, and this ADR is superseded.

