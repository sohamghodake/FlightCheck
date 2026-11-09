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
- Re-check (Phase 5A/5B, 2026-11-07, PyPI JSON API): fastapi 0.162.0 (2026-10-23), uvicorn 0.54.0
  (2026-09-25) and httpx2 2.16.0 (2026-10-21) are still the latest releases, matching the
  installed versions; bounds unchanged (plan §27 Phase 5A item 9).

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
  8. **Slow streams** (added after the 5B commit, same phase): any action answering a stream
     request takes `interval_s`, the pause between consecutive chunks (none before the first),
     at most 60 s per gap and 600 s per stream, else a recorded 500. Recorded as
     `response_summary["interval_s"]`. This models read timeouts and long queue waits with
     keepalives; `DELAY` still delays the first byte.
  9. **Recorder failures** never cost the client its answer: the app logs the exception type,
     calls `TrafficRecorder.note_record_failure()` and still responds (with `x-request-id`).
     `TrafficRecorder.complete` is `False` when anything was dropped or failed to record.
- Consequences: The OpenAI SDK parses every stream, including its `responses.stream()`
  helper. When the Phase 10 probe records real BharatCode streams, golden transcripts replace
  these shapes wherever they differ, and this ADR is superseded.


## ADR-016 — Sync adapters; injected sleep and transport

- Date: 2026-11-07 · Phase: 6 · Status: accepted
- Context: Provider adapters must be testable with no sockets and no network
  (`FLIGHTCHECK_NO_NETWORK=1` in every test), retries must not slow tests down, and adapters
  must see raw SSE and headers (ADR-003). httpx2 2.16's `ASGITransport` is async-only
  (ADR-027 item 7), but `httpx2.MockTransport` accepts a sync handler.
- Decision:
  1. **ADR-003 confirmed.** Adapters use httpx2 directly; `src/flightcheck/providers` imports
     only `flightcheck.core` and itself (never `openai`, the mock, the scanner, the runtime,
     the CLI or the orchestrator), enforced by `tests/providers/test_capabilities.py`.
  2. **Synchronous** (ADR-002). `BharatCodeAdapter(config, transport=None, sleep=time.sleep,
     *, jitter=None)`; `build_adapter(config, *, transport, sleep)`. All clients come from
     `providers/http.build_client` (ADR-004), which raises `NetworkDisabledError` when
     `FLIGHTCHECK_NO_NETWORK` is set to anything but `""`, `0`, `false`, `no` or `off` (fail
     closed) and no transport is injected. Redirects are not followed; the key goes only into
     `Authorization`.
  3. **Test double:** `tests/providers/provider_bridge.py` wraps the in-process mock in an
     `httpx2.MockTransport` whose handler forwards to `fastapi.testclient.TestClient(
     create_mock_app(...))` and returns a fresh `httpx2.Response` with the body in 7-byte
     chunks (headers, body and SSE bytes verified intact). No private Starlette API.
  4. **Retries** (`send_with_retry(fn, policy, sleep, *, jitter, attempts)`): only a
     `ProviderError` that is `retryable` *and* whose kind is in `RetryPolicy.retry_on` (503,
     429, connection failures). Never other 4xx, 500, timeouts (a queued request may still be
     running; this includes connect timeouts), malformed answers or truncated results. A
     `Retry-After` header ([UNVERIFIED]) is a minimum wait; one longer than `max_delay_s` stops
     the retries. Streams are retried only while opening (before a 2xx answer); never once the
     answer has started. Attempts go to `NormalizedResponse.raw["_flightcheck_attempts"]` and
     each retry is logged as a warning.
  5. **Errors:** every error an adapter operation raises is a `ProviderError` with a redacted
     message naming the status, code and `x-request-id`. Local pre-flight refusals are
     `ProviderError(UNSUPPORTED_CAPABILITY)` (or `BAD_REQUEST` for a missing model or a budget
     over 32 000) with `http_status=None`, not the `UnsupportedCapabilityError` of plan §14.1,
     so callers handle one exception type. Construction raises `NetworkDisabledError` first,
     then `ConfigError` when the API key is missing. 1xx/3xx answers are `MALFORMED_RESPONSE`.
  6. **Streams:** pre-flight runs when `stream_*()` is called; the request is sent when
     iteration starts. A `failed` event (`response.failed`, EOF without a terminal event,
     Chat `[DONE]` without a finish_reason, an unrecognised finish_reason, an `error` chunk) is
     yielded and then raised as `ProviderError(STREAM_FAILED)`. A stream stops at its first
     terminal event; for Chat that is the chunk carrying `finish_reason`. Lines are split on
     CR/LF only (`iter_text()` + `sse.iter_sse_lines`), not with `iter_lines()`, whose
     `str.splitlines` also splits on U+2028/U+0085 that may appear unescaped in JSON.
  7. **`Endpoint`** is the core enum (`chat.completions` value), not a second provider enum.
- Consequences: Adapter tests are fast and socket-free; the same transport seam serves the
  Phase 10 probe recorder. A server that labels SSE with something other than
  `application/json` is parsed as SSE; only an explicit JSON answer to a stream request is
  refused.

## ADR-028 — Provider-layer conventions settled in Phase 6

- Date: 2026-11-07 · Phase: 6 · Status: accepted
- Context: The plan leaves where the capability types live, how `false` declarations are
  represented and several adapter details open; core may import only stdlib and pydantic.
- Decision:
  1. **Capability types in core.** `Capability`, `SupportLevel`, `CapabilityEntry` and
     `CapabilityProfile` (both frozen) live in `core/capabilities.py`;
     `providers/capabilities.py` re-exports them and holds `BHARATCODE_PROFILE`,
     `bharatcode_profile(base_url, declared)` and `generic_profile(declared, base_url,
     provider)`. ADR-023 item 7 placeholders tightened (additive, ADR-010):
     `Report.capability_profile: CapabilityProfile | None`,
     `ProviderConfig.declared_capabilities: dict[Capability, bool]` (unknown names are config
     errors). Schema regenerated.
  2. **Declarations.** `true` → `declared`, `false` → new level `declared_unsupported`. The TOML
     table is `[providers.<name>.declared_capabilities]` (the field name; the plan wrote
     `capabilities`). On the BharatCode profile declarations fill only `unknown` entries, so a
     documented fact cannot be declared away. Every BharatCode entry cites
     `https://bharatcode.ai/build`; `stream_chat` and `structured_output_response_format` are
     `unknown` (not documented).
  3. **One contract check.** The documented `/responses` 400s moved unchanged from
     `mock/app.py` to `core/contract.py` (`Violation`, `responses_contract_violations`); the
     mock and the adapter both call it. The scanner keeps its own AST-level checks.
  4. **Adapter pre-flight** = model present (documented as required) + the contract checks on
     `/responses` + output budget ≤ 32 000 on both endpoints; a model outside the documented
     catalogue is a logged warning once per adapter, never a refusal. Typed builders clamp the
     budget with a warning, always send `store` on `/responses` (false unless the caller set
     `store=True`, which pre-flight refuses), let typed fields win over `extra_body`, and set or
     remove `stream` themselves. Capabilities a payload uses whose level is `unsupported_*` or
     `declared_unsupported` are refused too.
  5. **Unverified tagging:** `NormalizedResponse.unverified_capabilities` lists capabilities the
     request used whose level is `unknown` or `declared` (plan §14.1);
     `BharatCodeAdapter.unverified_capabilities(payload, endpoint)` gives the same for streams.
  6. **Finish mapping details:** Chat `stop` with tool calls → `TOOL_CALLS` (OpenAI behaviour
     for forced tool choice, [UNVERIFIED]); `tool_calls` without calls → `UNKNOWN`; a Responses
     `function_call` item whose own `status` is not `completed` is never complete; a missing
     `status` → `UNKNOWN`.
  7. **`raw_post(path, json, allow_contract_violation=False) -> RawResponse`**: no rewriting,
     clamping or retries; HTTP errors are returned (status, `x-request-id`, redacted headers,
     body and text), not raised. The path must be relative (no scheme, `//`, `..` or `\`) and
     the built URL must start with the base URL, so the key never leaves the configured host.
- Consequences: Phase 8 (`explain`) and Phase 10 (probe) use `build_adapter` and these types
  without re-deciding them; the probe upgrades entries to `*_verified`.

## ADR-017 — Runtime checks run only via consented subprocess; no in-process import of user code

- Date: 2026-11-07 · Phase: 7A · Status: accepted
- Context: Runtime evidence needs the developer's app to run. Importing it into FlightCheck's
  process would give it FlightCheck's environment (real keys included), let it patch FlightCheck
  itself, and make crashes, hangs and `sys.exit` FlightCheck's problem.
- Decision: Repository code runs only through `runtime/harness.run_app`: the `[test].command`
  list, `shell=False`, `cwd` resolved inside the project root, a scrubbed allowlist environment
  (§20.7) with the mock URL, a fresh fake key `fc_fake_key_<random>` and a dead-proxy egress
  guard, stdin written then closed, a new session/process group killed as a whole on timeout
  (terminate → 3 s → kill) and after exit. `orchestrator.run_test` calls `ensure_consent` before
  anything else; without `allow_exec=True` it raises `ConsentRequiredError` (exit 2) with the
  ready-to-paste hint `flightcheck test <path> --allow-exec`. `subprocess` is imported nowhere
  in `src/` except `runtime/harness.py`, and `runtime` imports only `core` and `mock`; both are
  enforced by AST tests in `tests/runtime/test_harness.py`. The interpreter is whatever the
  command names (`use_current_python` is P1).
- Consequences: The egress guard is best effort, not a sandbox: it stops HTTP clients that
  honour the proxy variables (httpx2 and the OpenAI SDK, `trust_env=True`), not raw sockets or
  file access. Memory/CPU limits are not enforced (§20.7, P2). On Windows, grandchild cleanup
  relies on `taskkill /T` (untested).

## ADR-018 — Outcomes without explicit expectations are needs_review, never passed

- Date: 2026-11-07 · Phase: 7A · Status: accepted
- Context: Many runtime outcomes (does the app continue after a tool call? did it exit
  non-zero for a good reason?) are correct or not depending on the app's intent. Calling them
  `passed` would claim verification FlightCheck did not do (§20.14).
- Decision: A check is `passed` only when its criterion is crisp and was observed, and it
  carries at least one evidence item (the model enforces it). Otherwise:
  app-specific outcomes with no `[test.expect]` entry → `needs_review` with the observation
  (FC-RT-011 no follow-up, FC-RT-008 non-zero exit under S01, FC-RT-001 omitted `store`,
  FC-RT-041 SDK-only backoff); no recorded traffic → `skipped` with the §17.2 reason; an
  incomplete recording turns a would-be `passed` into `needs_review` (an absence cannot be
  confirmed) while an observed failure stays `failed`; an app with no tools under S04 →
  `unsupported`. Statuses are never collapsed; a scenario's status is `worst_status` of the
  checks it reports.
- Consequences: `needs_review` is common in reports and is not a failure for CI gating. Phase
  7B's `[test.expect]` handling (S03/S06) follows the same rule.

## ADR-029 — Runtime conventions settled in Phase 7A

- Date: 2026-11-07 · Phase: 7A · Status: accepted
- Context: The plan leaves open where the pre-flight lives, how "reported once" works, how to
  attribute retries to the OpenAI SDK, what S01's exit-code expectation is attached to, and what
  to do with scenario IDs this phase does not implement.
- Decision:
  1. **Pre-flight in the orchestrator.** `runtime` may not import the scanner, so
     `orchestrator.hardcoded_base_urls(index)` reads the scanner's index: a client whose
     `base_url` is a literal or one-hop constant with a non-local host (not read from an
     environment variable, even with a literal default), or a raw HTTP call to a literal
     absolute non-local URL. Any hit → no mock, no process; every reported check `skipped` with
     the `file:line` in the reason, plus a `test_preflight_refused` warning diagnostic.
     `DetectionResult` exposes no URL locations, so the scanner is unchanged.
  2. **Every-scenario checks reported once.** FC-RT-001…007, FC-RT-050 and FC-RT-060 are
     evaluated in every scenario; `validators.report_once` keeps a failed/needs_review result
     only if it names an offending request not yet reported for that check (key:
     `request_sha256`; for FC-RT-060, scenario + seq, because limited requests carry no body and
     a breach belongs to its scenario), always keeps a failure without offending requests, keeps
     a pass only if that check was not yet observed, and keeps a skip/unsupported/error only if
     nothing was kept for that check. So a scenario whose own checks were skipped (e.g. after a
     contract 400) is not shown as `passed` on the strength of a check another scenario showed.
  3. **SDK retry attribution.** The OpenAI SDK (3.46.0) retries 503/429 twice by default with
     0.5 s·2ⁿ backoff minus up to 25 % jitter (`openai/_constants.py`,
     `openai/_base_client.py:898-920`) and sends `x-stainless-retry-count` (kept by the
     recorder). FC-RT-040 is `passed` when ≥ 2 attempts are seen and says when the SDK did the
     retrying. FC-RT-041 is `needs_review` whenever every retry carries a retry count > 0,
     whatever the measured gap (the first SDK gap measured 440–510 ms, so a fixed 500 ms
     threshold would flap); `failed` (< 500 ms) / `passed` apply to the app's own retries.
  4. **FC-RT-008 "Baseline run exits 0" (new ID, S01 only, severity info).** §18.3 says S01
     with a non-zero exit is `needs_review` but names no check; a scenario's status must come
     from its checks, so this ID carries it: exit 0 after ≥ 1 model request → `passed`; non-zero,
     timeout, or exit 0 without traffic → `needs_review`. Never `failed`, so never mirrored.
  5. **Unimplemented scenarios.** S03/S05/S06 (Phase 7B) and S02b/S07–S10 (P1) are known IDs: a
     selected one yields an info `scenario_not_implemented` diagnostic, not a `ScenarioResult`.
     Any other ID is a `ConfigError`. `TestRequest.scenarios=None` uses `[test].scenarios`.
  6. **Request limit.** The harness polls the recorder and stops the app once it has more than
     `max_requests` exchanges (FC-RT-060 `failed`, "FlightCheck stopped the app"), instead of
     waiting for the timeout.
  7. **Other details.** One fresh fake key, mock and child per scenario; scripts use step IDs
     `baseline`, `busy_1`, `busy_2`, `recover`, `tool_call`, `answer` with `scenario` params, so
     call IDs are `call_fc_S04_<seq>`. FC-RT-007 with `deepseek-v4.1-flash` → `needs_review`
     (severity info: Pro only). Chat continuation checks (FC-RT-014) say [UNVERIFIED] and mirror
     at `medium` confidence; list content in a Chat tool message → `needs_review`. Failed checks
     mirror as `Finding(kind=runtime, verification=runtime_mock, location=None,
     check_ref=<CheckResult.id>, provider="bharatcode")`. Child output: invalid UTF-8 replaced,
     the project root and home directory shown as `<name>`/`~`, redacted, then cut to 8 KB;
     at most 1 MiB per stream is read into memory. Reports use `mock_fidelity =
     "documentation_model"`.
- Consequences: Phase 7B adds S03/S05/S06, `[test.expect]` and the CLI on top of these rules;
  `tests/fixtures/expected_runtime.json` freezes the S01/S02/S04 picture per fixture app.

## ADR-030 — Runtime expectations, S03/S05/S06 checks and the `test` command (Phase 7B)

- Date: 2026-11-09 · Phase: 7B · Status: accepted
- Context: §17.2/§17.4 say FC-RT-020/030 depend on `[test.expect]` but leave open what `any`
  and a timeout mean, how output matching works on a cut excerpt, how a retry after S06 is
  reported, and §23.2 lists `--config` and `--keep-traffic` without detail. Building the
  command also exposed two Phase 7A defects.
- Decision:
  1. **Expectation model.** `runtime/validators.Expectation` (`kind`, `text`, `raw`) is parsed
     once per run from core's validated `ExpectConfig` strings (`CheckConfig.expectations`);
     anything else raises `ValueError` (never guessed). `nonzero_exit`: exit code ≠ 0 (a
     signal exit counts). `stdout_contains:<t>` / `stderr_contains:<t>`: `<t>` occurs in the
     captured excerpt, which is redacted, path-aliased and cut to its first 8 KB (ADR-029 item
     7); not found in an excerpt FlightCheck had to cut → `needs_review` (the rest was not
     kept). `any`: the developer accepts any handling, so it is met by any finished run,
     whatever the exit code.
  2. **What never decides FC-RT-020/030.** A timeout, or FlightCheck stopping the app at
     `max_requests`, is `needs_review` whatever the expectation (timing alone never fails a
     check, §27 7B failure point 24). No expectation → `needs_review` with the exit code and
     the end of stdout/stderr as evidence (ADR-018); "surfaced" is never inferred from output
     heuristics. The fault step never served (contract 400, no traffic) → `skipped`. A pass
     carries the fault exchange and the process evidence.
  3. **FC-RT-030 retries.** Any model request after the failing exchange is a retry; the
     message adds the SDK attribution (ADR-029 item 3) and "discard any partial output". The
     result's severity drops to `info` unless it is `failed` (a configured expectation that
     was not met keeps `medium`). On a non-stream request S06's 500 is retried by the OpenAI
     SDK (`openai/_base_client.py:959`, status ≥ 500; `DEFAULT_MAX_RETRIES = 2`) and the
     default step answers, so the app recovers: `needs_review` at `info`, never `passed`
     without an expectation. On a stream, `response.failed` nests its error under `response`,
     and the SDK raises only for a top-level `error` (`openai/_streaming.py:95`), so an app
     that reads only text deltas exits 0 (bc_bad_stream).
  4. **FC-RT-022/023** are traffic checks. FC-RT-022 fails when a later request carries a tool
     result (Responses `function_call_output`, Chat `role: "tool"`) for a call_id issued in
     the `truncated_tool_call` answer; its pass says local execution without a result is not
     observable; an app without tools → `unsupported`. FC-RT-023 compares `request_sha256` of
     later requests with S03's or S05's truncated request. The four checks are
     scenario-specific (not in `EVERY_SCENARIO_CHECK_IDS`; `report_once` leaves them alone).
     Step IDs: `truncate`/`after_truncate` (S03), `truncated_tool_call`/`answer` (S05),
     `stream_fail` with `n = 3` (S06).
  5. **`flightcheck test`.** Order: `--format html|all` / `--config` → exit 2; consent
     (`orchestrator.require_consent`) before the configuration is even read; `plan_test`
     (`[test]`, scenario IDs, cwd, then the pre-flight on the scanner index); the warning
     panel on stderr (command, directory, scenarios, timeout, "your code will run", the
     egress-guard caveat; "will NOT run" when the pre-flight refuses); then
     `run_test(..., plan=plan)`, which checks consent again. `--allow-exec` is a flag only
     (config has no such key). `--timeout` goes through `load_config` overrides (core
     validates 1–600). `--fail-on` reuses scan's gating on `report.findings` (failed checks
     are mirrored as runtime findings; `needs_review` never fails; hypotheses are not
     counted). JSON is always written (ADR-025). Missing `[test]` → exit 2 with a
     ready-to-paste block (entry guessed from `app.py`/`main.py`). `MockServerError` → exit 3
     with a `-v` hint. `error` checks are shown once per scenario in the terminal details.
  6. **Deferred:** `--config FILE` exits 2 (P1): `load_config` takes a project root and core
     is not changed in this phase.
  7. **`--keep-traffic`.** The report already embeds every scenario's exchanges (unchanged,
     ADR-010). The flag also writes `flightcheck-traffic.json` next to it (schema version,
     root name, per scenario the `AppRun` and exchanges; `redact_obj`, `resolve_inside`), for
     diffing traffic without findings.
  8. **Evaluation.** `scripts/eval_fixtures.py --runtime` is the consent to run FlightCheck's
     own fixture apps through `run_test` (S01–S06, no `[test.expect]`); gates: 100 %
     `(fixture, scenario, check_id)` agreement with `expected_runtime.json`, and no scenario
     status or runtime-finding mismatch. CI keeps `--check` only until the CI workflow work
     in Phase 9. `scan`'s exit line now names `flightcheck test PATH --allow-exec` (ADR-025
     item 5).
  9. **Phase 7A fixes.** FC-RT-040/041 attempts end at the first non-503 answer (a request
     after the recovered 200 is not a retry and has no backoff to judge). `build_child_env`
     makes relative `PATH` entries absolute against FlightCheck's working directory, so
     `PATH=.venv/bin:$PATH flightcheck test APP` finds `python` although the child runs in
     `[test].cwd`.
- Consequences: FC-RT-020/030 are `needs_review` for every fixture app in
  `expected_runtime.json` (no expectations there); tests set expectations with
  `load_config(root, overrides={"test": {"expect": ...}})`. `on_busy_exhausted` is parsed but
  unused until S02b (P1). The pre-flight index is built once, in `plan_test`.

## ADR-019 — LLM output stored separately; never changes statuses

- Date: 2026-11-09 · Phase: 8 · Status: accepted
- Context: `explain` adds BharatCode-generated text to a finished report. An LLM must never be
  able to decide whether a finding is valid, change a status or severity, or move an exit code
  (plan §19.1, ADR-006).
- Decision: `DiagnosticService.explain(report, options)` returns a deep copy whose only
  differences are `Report.ai_suggestions` (a list of `AISuggestion`, one per selected finding
  group) and `summary.ai_suggestions` (the number of suggestions that carry a validated
  `explanation`; fallbacks are not counted). Findings, checks, scenarios, diagnostics,
  `metadata` and every other summary count are untouched; the input object is not mutated.
  AI text is redacted and validated (§19.6) before it is stored; patches become a diff made
  locally with `difflib`, labelled "AI suggestion — not verified, review before applying".
  `explain` never exits 1: success and fallback both exit 0.
- Consequences: Tests compare `model_dump_json()` of everything except the two AI fields byte
  for byte (service and CLI, scan and test reports). The HTML report (Phase 9) renders
  `ai_suggestions` as a separate section.

## ADR-020 — Default context = metadata

- Date: 2026-11-09 · Phase: 8 · Status: accepted
- Context: The cheapest safe default for data that leaves the machine (plan §19.3, ADR-006).
- Decision: `ExplainConfig.context` defaults to `metadata`: rule ID, title, remediation, the
  contract excerpt, the finding message, repo-relative path and lines, provider classification,
  and a check's expected/observed capped at 300 characters. No source lines and no traffic
  bodies. `--context snippets` adds ±8 lines per finding (≤ 40 lines, ≤ 12 000 characters in
  total; files read only through `resolve_inside(--repo, path)`) and redacted request bodies of
  a check's traffic evidence (≤ 2 000 characters per check, headers never included). Patches
  are accepted only at the `snippets` level.
- Consequences: The consent panel's "Files" line reads "none (no source code is sent)" by
  default. A wrong `--repo` omits the snippet with a note printed on stderr and in the panel.

## ADR-031 — `explain` conventions settled in Phase 8

- Date: 2026-11-09 · Phase: 8 · Status: accepted
- Context: The plan leaves open where the AI models live (core may not import diagnostics),
  how contract excerpts ship, where a `needs_review` check's title and remediation come from,
  which backends need consent, the fallback reason names and what a dry run prints.
- Decision:
  1. **Schema location.** `AIPatch`, `AIExplanation` and `AISuggestion` live in
     `core/models.py` (ADR-023 item 7 placeholder tightened, additive under ADR-010:
     `Report.ai_suggestions: list[AISuggestion]`); `diagnostics/schema.py` re-exports them and
     owns `AIExplanationBatch` plus the `text.format` JSON Schema (`$ref`s inlined, every
     property required, `additionalProperties: false`; sub-schema [UNVERIFIED], P-07).
     `AISuggestion` adds `rule_id`, `title`, `severity`, `finding_ids` and `remediation` to
     plan §21.3 so a fallback entry says which group it covers and carries the deterministic
     text. Schema regenerated.
  2. **Contract excerpts** are a Python dict (`diagnostics/contract_excerpts.py`): rows of
     `docs/BHARATCODE_CONTRACT.md` §15.1 copied verbatim, verified rows prefixed
     `[VERIFIED]`, rows of the "Not documented" table keeping `**[UNVERIFIED]**`, mapped to
     rule/check IDs per §15.2. A drift test requires every `(topic, text)` pair on one line of
     the document. FC-RT-008 and FC-RT-060 have no contract row.
  3. **`needs_review` remediation source.** The orchestrator (which may import runtime)
     injects `check_catalogue()` = `{check_id: (title, remediation)}` from
     `runtime.validators.CHECKS`; without it a check group's title is its ID and remediation
     is empty. A failed check is explained through its mirrored runtime finding
     (`Finding.check_ref`); a failed check without one is selected directly.
  4. **Selection.** Every finding (static, runtime, hypothesis) and every `needs_review`
     check; groups by rule ID ordered by severity, then instance count, then rule ID; the
     group's severity is its worst instance. `--max-findings` sets `max_groups` (default 5).
  5. **Consent per backend.** Consent is asked only when data would leave the process:
     backend `bharatcode`, a key present, at least one request planned — this includes the
     key-based default (`llm` unset with `BHARATCODE_API_KEY` exported). `fake` and `none`
     never ask (nothing is sent; `--yes` is accepted and ignored). `--dry-run` never asks and
     never sends. A missing consent callback is `ConsentRequiredError` (exit 2); in the CLI a
     non-interactive session (no TTY on stdin, or `CI=true|1|yes`) without `--yes` exits 2
     after showing the panel; an interactive "no" is the fallback `consent_declined`, exit 0,
     and the explained report is still written. The key is checked before consent (no key →
     `no_key`, never asked); `FLIGHTCHECK_NO_NETWORK` is checked after consent, when the
     adapter is built (→ `network_disabled`), so the consent path is the same with and without
     the switch.
  6. **Fallback reasons:** `no_key`, `llm_none`, `network_disabled`, `consent_declined`,
     `provider_busy` (503/429 after the adapter's retries), `auth_or_plan` (401/403),
     `truncated` (incomplete twice, or at the 32 000 cap), `invalid_output` (not a valid
     batch, or a finish state other than complete), `rejected` (with `rejected_reason`),
     `missing_explanation`, `call_limit`, `input_too_large`, `timeout`, `connection_error`,
     `provider_error`, `internal_error` (any unexpected exception in a call). The terminal
     prints a hint per reason.
  7. **Retries.** Per call at most one `text.format` retry (a 400, or a local
     `unsupported_capability` refusal → resend without `text.format`, with the schema spelled
     out in the instructions, `structured_output="prompt_only"`) and at most one budget retry
     (`TRUNCATED` → doubled `max_output_tokens`, capped at 32 000; never the same budget).
     503 retries stay in the adapter's `RetryPolicy`. One surrounding code fence is stripped;
     prose around JSON fails strict parsing (no repair, P1).
  8. **Splitting.** Groups are packed greedily into calls of ≤ 24 000 input characters
     ([ASSUMPTION]); at most `max_calls` (3) calls; a group that alone exceeds the cap is
     `input_too_large`.
  9. **Metadata invariant.** The explained report keeps the original `metadata` (command
     `scan` or `test`, options, timestamps); the AI provenance (`backend`, `model`,
     `request_id`, `latency_ms`, `structured_output`, `generated_at`) lives in each
     `AISuggestion`.
  10. **Sanitisation details.** Snippets are exempt from the 2 000-character string cap; the
      §19.3 snippet limits apply instead. Dictionary keys `headers`, `request_headers`,
      `response_headers`, `authorization`, `api_key`, `cookie`, `set-cookie` are dropped from
      anything sent. Absolute repo paths become relative, the home directory `~`.
  11. **CLI.** `explain REPORT.json` writes `<report-stem>.explained.json` next to the input
      unless `--output FILE` (refused if it resolves to the input, or is a directory); the
      destination is printed on stderr on every run; `--dry-run` prints the exact request
      bodies (redacted) that `--llm bharatcode` would send, whatever the backend, and writes
      nothing; zero selectable findings → "Nothing to explain", exit 0, nothing written.
      `--format html|all` exits 2 until Phase 9. `cli` reaches the diagnostic types through
      `orchestrator` (`plan_explain`, `run_explain`, `ExplainOptions`, `ConsentInfo`).
  12. **Phase 1 fix.** `core.redact`'s `key=value` pattern no longer lets a value swallow the
      backslash of an escaped quote: redacting JSON text holding an already-redacted
      `api_key=[REDACTED]\"` removed the backslash and broke the JSON (it corrupted the
      explain payload's `input`). A backslash not followed by a quote is still part of the
      value.
- Consequences: Phase 9 renders `ai_suggestions` in HTML. ADR-025 item 5 (an `explain` hint in
  `scan`/`test` exit lines) is not applied in this phase, so the other commands' output is
  unchanged; it can be added with Phase 9's CLI work.

## ADR-021 — The HTML report has no JavaScript

- Date: 2026-11-09 · Phase: 9 · Status: accepted
- Context: The HTML report is the file users share (CI artifacts, e-mail, PR links). It holds
  redacted source excerpts, traffic bodies and AI text from a model. Any script would be one
  more place where a crafted string could execute, and external assets (fonts, CDNs) would
  leak that a report was opened and break offline viewing (plan §24).
- Decision: `reporting/templates/report.html.j2` renders one self-contained file: inline CSS
  only, no `<script>`, no inline event handlers, no `<link>`, no `src=`, no `@import`, no
  `url(http…)`. Interaction uses native `<details>` elements and in-page anchors. Jinja2 runs
  with `autoescape=True` and `StrictUndefined`; no value is marked `|safe`. Only `http(s)`
  `doc_url`s become links (autoescape does not stop `javascript:` URLs). Light and dark colour
  schemes come from `prefers-color-scheme`; statuses and severities are shown as text in a
  badge, never by colour alone.
- Consequences: no client-side sorting or filtering. `tests/unit/test_html_report.py` asserts
  the forbidden tokens are absent and that `<script>` planted in a message, an excerpt, a path,
  traffic, an AI summary, a fix step, a diff and the options is escaped.

## ADR-032 — Phase 9 conventions: HTML output, `report` command, CI and the dogfood fix

- Date: 2026-11-09 · Phase: 9 · Status: accepted
- Context: The plan leaves open what `--format html|all` prints and writes (ADR-025 deferred
  it), the `report` command's defaults, how the template gets its data, how the dogfood gate
  is made to pass, how the example workflow avoids breaking this repository's CI, and the
  `explain` hint deferred by ADR-031.
- Decision:
  1. **Formats on `scan`/`test`/`explain`.** The JSON is always written. `terminal` prints the
     tables (unchanged); `json` prints only `Report written: <path>` (unchanged); `html`
     writes the JSON and the HTML and prints `Report written: …` then `HTML report written: …`
     (and `Traffic written: …` between them for `test --keep-traffic`), no tables; `all`
     prints the terminal output, then `HTML report written: …` before the exit line. File
     names: `flightcheck-report.html` next to `flightcheck-report.json` (in `--output DIR` or
     `PATH/.flightcheck/`); for `explain`, the explained JSON path with its suffix replaced by
     `.html` (`<stem>.explained.html` by default; `custom.html` as `--output` gives
     `custom.html.html`, never the JSON itself). An HTML target that resolves to the input
     report exits 2 before anything is written. `explain --dry-run` and "Nothing to explain"
     write no HTML. Exit codes never depend on the format.
  2. **`flightcheck report REPORT.json`.** `--format html` (default) | `terminal` |
     `markdown` (exits 2: "P1, not implemented"). `--output FILE` (default `<report-stem>.html`
     next to the input; a directory, or a path resolving to the input, exits 2; with
     `--format terminal` it is a usage error). `terminal` reuses `render_test` when
     `metadata.command == "test"` or the report has scenarios, otherwise `render_scan`, then
     `render_explain` when `ai_suggestions` exist; no exit line (it never gates). No key, no
     network, no execution. Exit 0 on success; 2 for an unreadable report, a non-report, an
     invalid report or a newer schema major (`read_report` raises `ConfigError`); 3 internal.
  3. **View model.** `html_report.build_view(report)` starts from
     `redact_obj(report.model_dump(mode="json"))` (the JSON report's redaction pass) and
     returns plain dicts: metadata (options as sorted `key=json` pairs), summary cards,
     providers, capability profile, findings grouped by file in first-seen order (no location
     → "(repository)"), scenarios (six status counts each, checks, exchanges), diagnostics, AI
     entries and the limitations. Derived text (pretty-printed request bodies and response
     summaries) is redacted again. Summary cards come from `report.summary`: all five
     severities, finding kinds static/runtime, hypotheses in their own card, all six check
     statuses (worst first), and findings/checks per verification level labelled "static",
     "observed with mock", "observed live" (hypotheses excluded from the findings column). AI
     suggestions are never counted in the cards. Request headers are not shown. Size guards:
     messages and remediations ≤ 4 000 characters, each traffic body ≤ 4 000, stdout/stderr
     ≤ 8 000. The footer shows `metadata.generated_at` (UTC), never the wall clock, so the same
     JSON renders the same bytes. AI provenance: `fake` → "fake LLM, offline; not generated by
     BharatCode"; `bharatcode` → "BharatCode <model>"; fallbacks show the reason, the
     terminal's hint for it (`FALLBACK_HINTS`) and the deterministic remediation.
  4. **Dogfood fix (TRAP 1).** `flightcheck scan src --fail-on low` reported FC-GEN-004 twice
     in `mock/app.py`: the parser's raw-HTTP detection treats any `<x>.post(url)` whose URL
     contains `/responses` or `/chat/completions` as an outgoing request, and FastAPI's
     `@app.post(f"{base}/responses")` route decorators matched. A decorator call registers a
     handler; it never sends a request. `scanner/parser.py` now records the `Call` nodes in a
     function's `decorator_list` and skips them in the raw `.post` branch only (SDK suffix
     calls are unchanged). No exclusion was added and `--fail-on` was not lowered.
     `eval_fixtures.py --check` stays 108/108 with `expected_findings.json` unchanged.
     Regression test: `tests/scanner/test_parser.py::test_route_decorator_post_is_not_an_api_call_phase9`.
  5. **CI.** `ci.yml` runs `python scripts/eval_fixtures.py --check --runtime` (was `--check`)
     and the dogfood step `flightcheck scan src --fail-on low --output "$RUNNER_TEMP/…"` (so
     the checkout stays clean). Action majors stay as in ADR-008.
     `.github/workflows/flightcheck-example.yml` is `workflow_dispatch`-only (input `path`,
     default `src`): a scan of `.` in this repository exits 1 because the fixture apps are
     deliberately broken. It runs `flightcheck scan "$PATH_TO_SCAN" --format all --fail-on
     high` and uploads `.flightcheck/` with `actions/upload-artifact@v6` (`if: always()`,
     `include-hidden-files: true` because the directory is a dot-directory). The
     upload-artifact major was **not verified** in this phase (no `gh` CLI, no web access per
     the phase prompt); confirm it on its releases page before the first dispatch and add it to
     ADR-008's table. docs/CI.md explains how users change the trigger, install step and path.
  6. **Exit-line hint (ADR-025 item 5, ADR-031).** `scan` and `test` exit lines add
     `flightcheck explain <report path>` (shell-quoted) when the report has something to
     explain (a finding of any kind, or a `needs_review` check); otherwise the hint is omitted
     because `explain` would print "Nothing to explain". `--format json|html` print no exit
     line (unchanged).
- Consequences: `report.html.j2` ships as package data with hatchling's
  `packages = ["src/flightcheck"]` (checked with `uv build --wheel` and `unzip -l`); no
  `pyproject.toml` change. ADR-025 item 1's "`--format html|all` exits 2" and ADR-031 item
  11's same sentence are superseded by item 1 here.

## ADR-033 — Redaction: JSON-escaped `key=value` values; accepted limits

- Date: 2026-11-09 · Phase: 11 · Status: accepted
- Context: Phase 8 recorded that `core.redact`'s `key=value` pattern did not match a value
  whose opening quote is JSON-escaped (`token=\"abcdefghij\"` inside JSON text): the optional
  quote group did not allow the backslash, so the value never started. Every output already
  redacts leaf strings before serialising, so no output leaked through this, but the
  defence-in-depth passes over JSON text (the dry-run's payload, the HTML's pretty-printed
  traffic bodies) were blind to it. Plan §27 Phase 11 item 15 asks that secrets split across
  lines be documented as a limitation.
- Decision:
  1. **Fixed in the shared path.** The pattern's opening quote may be escaped:
     `\s*[:=]\s*(?:\\?["'])?` before the value. The value still stops at a backslash that
     escapes a quote (the Phase 8 fix), so `token=\"value\"` becomes `token=[REDACTED]\"`
     and the surrounding JSON stays valid; redaction stays idempotent. Test:
     `tests/unit/test_redact.py::test_key_value_pattern_matches_a_json_escaped_value_phase11`;
     the Phase 8 regression test is unchanged and green.
  2. **Accepted limitations** (documented in docs/RULES.md "Explicit limitations"):
     redaction is pattern- and value-based; a secret split across lines or assembled at
     runtime from pieces is not recognised in source text (only its assembled value, when it
     reaches an output); unprefixed or short literals are masked only where a rule sees them
     assigned to a key-like name (ADR-026); environment values shorter than 8 characters are
     not redacted by value (they would mangle normal text). The JSON dict-key form inside JSON
     text (`{\"api_key\": \"value\"}`) is not matched by the `key=value` pattern; it is covered
     by leaf-string redaction before serialising.
- Consequences: The leak matrix (`tests/security/test_leak_matrix.py`) found no leak in any
  output; a test checks that RULES.md documents the split-across-lines limit. Report bytes do
  not change for existing fixtures (schema drift test and eval unchanged).

## ADR-034 — Import rules as enforced in Phase 11 (orchestrator → mock, entry point)

- Date: 2026-11-09 · Phase: 11 · Status: accepted
- Context: Plan §12.2 lists `orchestrator → core, scanner, runtime, diagnostics, reporting,
  providers`, but `orchestrator.py` imports `flightcheck.mock.server.MockServer`: it starts a
  fresh mock server per scenario for `test` (Phase 7A) and hands its URL to
  `runtime.harness.run_app`. `src/flightcheck/__main__.py` imports `cli` for
  `python -m flightcheck`, while §12.2 says no module may import `cli`.
- Decision:
  1. **orchestrator → mock is allowed.** The orchestrator is the use-case wiring layer that
     already depends on everything below it; starting the mock is wiring, and `runtime`
     already depends on `mock`. Moving the server start behind `runtime` would rewrite
     working, tested Phase 7A code for no behavioural gain. `docs/ARCHITECTURE.md` §3 is
     updated to match.
  2. **`__main__.py` is the one module allowed to import `cli`** (the console entry point);
     every other module, including all of `src/flightcheck/<package>/`, may not.
  3. **The root package** (`from flightcheck import __version__`) may be imported by any
     layer; `flightcheck/__init__.py` itself imports nothing internal.
  4. **Third-party allowlist per layer** (§5 table, made explicit): pydantic everywhere
     (models); scanner, runtime and orchestrator nothing else; mock fastapi, starlette,
     uvicorn; providers httpx2; diagnostics httpx2 (transport injection type only);
     reporting jinja2, rich; cli typer, rich, click. Relative imports are not used and are
     reported (the checks need absolute names). `scanner` may not import `subprocess`,
     `importlib`, `runpy`, `socket`, `http` or `urllib`, nor `providers`, `runtime`, `mock`
     or `diagnostics`.
  5. `tests/architecture/test_imports.py` parses every module with `ast` (including
     `TYPE_CHECKING` imports) and enforces items 1–4; a control test proves a forbidden
     import is reported. The narrower existing tests stay (core: `tests/unit/test_core_imports.py`;
     scanner exec/eval/compile: `tests/scanner/test_detect.py`; subprocess only in
     `runtime/harness.py`: `tests/runtime/test_harness.py`; diagnostics:
     `tests/diagnostics/test_context.py`).
- Consequences: A new dependency edge needs an ADR and an update to both the test tables and
  `docs/ARCHITECTURE.md` §3/§5.
