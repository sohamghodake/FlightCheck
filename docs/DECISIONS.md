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
