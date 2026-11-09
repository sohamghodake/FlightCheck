# Quality gates

A phase is finished only when every gate that applies to it is green locally, and CI is expected
to be green on the resulting commit. Gates are cumulative: once a gate starts at some phase, every
later phase must pass it too.

## Run the gates

```bash
source .venv/bin/activate          # after: python -m venv .venv && pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -m "not live" -q
python scripts/eval_fixtures.py --runtime          # regenerates docs/EVALUATION.md
python scripts/eval_fixtures.py --check --runtime  # static + runtime evaluation gates
flightcheck scan src --fail-on low                 # dogfood: must exit 0
```

CI ([.github/workflows/ci.yml](../.github/workflows/ci.yml)) runs the same three commands on
Python 3.11, 3.12 and 3.13 (ubuntu-latest) for every push and pull request, then (from Phase 9)
`python scripts/eval_fixtures.py --check --runtime` and the dogfood
`flightcheck scan src --fail-on low` (report written to `$RUNNER_TEMP`). The example workflow
`flightcheck-example.yml` is `workflow_dispatch`-only and never runs on push (ADR-032).

## Gate table

| Gate           | Command                                              | From phase      | What it protects                                                       |
| -------------- | ---------------------------------------------------- | --------------- | ---------------------------------------------------------------------- |
| Lint           | `ruff check .`                                       | 0               | Errors, unused imports, import order, modern syntax (rules E F W I B UP) |
| Format         | `ruff format --check .`                              | 0               | One code style, line length 100                                         |
| Tests          | `pytest -m "not live" -q`                            | 0               | Behaviour; live BharatCode tests are excluded                           |
| Schema drift   | included in tests (`test_schema_export.py`)          | 3               | `docs/report.schema.json` matches the `Report` model                    |
| Static eval    | `python scripts/eval_fixtures.py --check`            | 4               | Rule detection metrics on fixture apps do not regress                   |
| Runtime eval   | `python scripts/eval_fixtures.py --check --runtime`  | 7B              | Scenario results on fixture apps do not regress                         |
| Dogfood        | `flightcheck scan src --fail-on low`                 | 9               | FlightCheck's own BharatCode integration passes its own rules           |
| Security suite | included in tests (`tests/security`)                 | 4 (grows in 11) | No secret leakage, exec policy, path traversal                          |
| Architecture   | included in tests (`tests/architecture`)             | 11              | Import boundaries (ARCHITECTURE §3/§5, ADR-034)                         |
| Fresh install  | new venv → `pip install .` → `flightcheck --version` | 11              | The package installs and runs outside editable mode                     |

## Standing test rules

- No real network in tests: `tests/conftest.py` sets `FLIGHTCHECK_NO_NETWORK=1` for every test.
- Tests that call the real BharatCode API are marked `@pytest.mark.live` and skip without
  `BHARATCODE_API_KEY`. They never run in CI.
- Slow tests (subprocesses, server threads) are marked `@pytest.mark.slow`.
- Markers are strict (`--strict-markers`): an unregistered marker fails the run.
- Tests are the contract between sessions: a later phase may refactor only if every existing test
  still passes unchanged. Changing an existing test requires a note in that phase's handoff.

## Phase 0 result

All three Phase 0 gates pass locally on Python 3.12.14 (see
[handoffs/PHASE_00.md](handoffs/PHASE_00.md)).

## Phase 9 result

The dogfood gate is active: `flightcheck scan src --fail-on low` exits 0 after the parser stopped
treating route decorators such as FastAPI's `@app.post("/responses")` as API calls (ADR-032
item 4). The runtime evaluation gate now runs in CI too.

## Phase 11 result

- **Leak matrix** (`tests/security/test_leak_matrix.py`): distinct fake values planted in a
  source literal, `BHARATCODE_API_KEY`, another secret-named environment variable, the app's
  stdout and stderr, a recorded request body and AI output (fake backend, and BharatCode
  through an injected transport). `scan`, `test --allow-exec --keep-traffic`, `explain`
  (fake, bharatcode, `--dry-run`) and `report` (html, terminal; run with the planted
  environment removed) all run through the CLI at `-vv` with `--format all`. Checked:
  stdout, stderr (logs included), the JSON report, the explained reports, every HTML file,
  `flightcheck-traffic.json`. Result: 0 leaks. The runtime half is marked `slow`.
- **No execution** (`test_no_execution.py`): module-level, `__main__`, package `__init__`,
  `setup.py`, `conftest.py`, `sitecustomize.py`, decorator and class-body sentinels; `scan`,
  `explain` and `report` create none.
- **Path traversal** (`test_path_traversal.py`): `--output` for scan, explain and report via
  symlinks pointing outside, a symlinked default `.flightcheck/`, `[test].cwd` escapes
  (`..`, absolute, symlink), snippet paths outside `--repo` (`..`, absolute, symlink) and AI
  patches for paths that were not sent: all exit 2 or are rejected; nothing outside changes.
- **Keys in flightcheck.toml** (`test_config_secrets.py`): `scan`, `test` and `explain` exit 2
  and never print the value.
- **Architecture** (`tests/architecture/test_imports.py`): §12.2 for every module, no module
  but `__main__.py` imports `cli`, scanner import bans, per-layer third-party allowlist.
- **Fresh install**: a new Python 3.11.14 venv, the wheel from `uv build --wheel`, run outside
  the repository: `flightcheck --version`, `scan` of a fixture app and `report` to HTML
  (the template ships in the wheel). Python 3.13 is covered by CI only.
