# Quality gates

A phase is finished only when every gate that applies to it is green locally, and CI is expected
to be green on the resulting commit. Gates are cumulative: once a gate starts at some phase, every
later phase must pass it too.

## Run the gates

```bash
source .venv/bin/activate          # after: python -m venv .venv && pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -m "not live" -q
```

CI ([.github/workflows/ci.yml](../.github/workflows/ci.yml)) runs the same three commands on
Python 3.11, 3.12 and 3.13 (ubuntu-latest) for every push and pull request.

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
