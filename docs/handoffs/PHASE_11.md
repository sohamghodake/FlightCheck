# Phase 11 handoff — Security hardening, architecture tests, QA pass

- Date / session: 2026-11-09, the same Claude Code chat as Phase 9 (separate prompt).
- Status: done (P0). Zero leaks found; one shared-path redaction gap fixed (ADR-033).
- What was built (files, public functions):
  - `tests/security/test_leak_matrix.py`: distinct fake values (built by concatenation) in a
    source literal, `BHARATCODE_API_KEY`, `LEAK_SERVICE_TOKEN`, the app's stdout and stderr, a
    recorded request body and AI output (fake backend with a monkeypatched
    `FakeLLM.respond`; BharatCode backend through an `httpx2.MockTransport` injected by
    monkeypatching `orchestrator.explain_service`, with `--yes`). Runs `scan`,
    `test --allow-exec --keep-traffic`, `explain` (fake, bharatcode, `--dry-run`, all with
    `--context snippets`) and `report` (html, terminal; with the planted environment and the
    redaction registry cleared) through the CLI at `-vv`, `--format all`. Parametrised over
    every output (stdout, stderr incl. logs, JSON, explained JSON, HTML, traffic file); plus
    non-vacuity checks (FC-SEC-001 fired, `[REDACTED…]` markers present, poisoned AI
    explanations accepted, `INFO flightcheck` log lines on stderr) and a check that
    docs/RULES.md documents secrets split across lines. Module-scoped fixtures restore the
    redaction registry.
  - `tests/security/test_no_execution.py`: sentinels at module level, under `__main__`, in a
    package `__init__`, `setup.py`, `conftest.py`, `sitecustomize.py`, a decorator and a class
    body; `scan`, `explain` (fake, dry run with snippets) and `report` create none; a control
    proves the sentinel code does write when executed.
  - `tests/security/test_path_traversal.py`: symlinked `--output` files for scan (JSON and
    HTML), explain (JSON and HTML) and report; a symlinked default `.flightcheck/`;
    `[test].cwd` = `..`, `../outside`, `/tmp`, a symlink (exit 2, nothing starts); snippet
    paths outside `--repo` (`..`, absolute, symlink: "snippet omitted", never read); AI
    patches for paths not sent (`../…`, `other.py`, `/etc/hosts`: rejected, no diff, no file
    touched).
  - `tests/security/test_config_secrets.py`: four placements of a key value in
    `flightcheck.toml`; `scan`, `test` and `explain --repo` exit 2 with "must not contain
    secrets" and never print the value; `api_key_env` stays allowed.
  - `tests/architecture/test_imports.py`: AST import check of every module (§12.2 +
    ADR-034), no module but `__main__.py` imports `cli`, scanner bans, per-layer third-party
    allowlist, no relative imports, a control test with forbidden imports.
  - `core/redact.py`: the `key=value` pattern accepts an escaped opening quote.
  - Docs: `docs/RULES.md` (redaction limits), `docs/ARCHITECTURE.md` (§3 rules, current
    state), `docs/QUALITY_GATES.md` (commands, architecture gate, Phase 11 result),
    `docs/EVALUATION.md` regenerated with `--runtime` (metrics unchanged; dogfood line now
    "no findings").
- Tests added (files, count) and gate results: leak matrix 42 (21 marked `slow`), path
  traversal 16, no execution 2, config secrets 5, architecture 57 (one case per module),
  `tests/unit/test_redact.py` +1. 1445 total (+123). Green on Python 3.12.14 (macOS):
  `ruff check .`, `ruff format --check .`, `pytest -m "not live" -q` (1445 passed),
  `python scripts/eval_fixtures.py --runtime` then `--check --runtime` (108/108, 0, 0;
  273/273, 0 mismatches), dogfood `flightcheck scan src --fail-on low` exit 0. Python 3.11
  only through the fresh install below; 3.11/3.13 test runs are CI's job after the push.
  Mutation spot-checks: switching off environment-value redaction in `core.redact` fails 23
  matrix cases; adding `import importlib` + a runtime import to `scanner/engine.py`, or a
  `cli` import to `reporting/terminal.py`, fails the architecture test; all reverted.
  Removing a single later layer (the JSON writer's `redact_obj`, or the terminal's re-redact
  of an AI summary) is absorbed by the earlier redaction of the same data, so the matrix
  stays green: defence in depth, by design.
- Decisions made (ADR ids): ADR-033 (JSON-escaped `key=value` fix; accepted redaction
  limits), ADR-034 (orchestrator → mock allowed; `__main__.py` entry point; root package;
  third-party allowlist).
- Deviations from the plan and why:
  - `orchestrator → mock` is allowed (TRAP 1) instead of moving the server start behind
    `runtime`: the orchestrator is the wiring layer and rewriting working Phase 7A code
    would add risk without changing behaviour (ADR-034).
  - The fresh install used the wheel from `uv build --wheel` of the working tree in a new
    Python 3.11.14 venv (`python3.11 -m venv`, dependencies from PyPI; no interpreter was
    downloaded), and a fixture app, since `examples/` arrives in Phase 12.
- Out-of-scope changes (file, reason, test): `src/flightcheck/core/redact.py` (Phase 1/8):
  JSON-escaped values were missed by the `key=value` pattern (Phase 8 known issue). Test:
  `tests/unit/test_redact.py::test_key_value_pattern_matches_a_json_escaped_value_phase11`.
- Known issues / limitations (accepted, ADR-033/034):
  - Secrets split across lines or assembled at runtime are not recognised in source text;
    unprefixed short literals are masked only when a rule sees them assigned to a key-like
    name; environment values under 8 characters are not redacted by value; the JSON dict-key
    form inside JSON text is covered only by leaf-string redaction.
  - The egress guard is best effort, not a sandbox; no memory/CPU limits (ADR-017).
  - `--output` accepts any directory or file the user names (including outside the
    project); only symlinks that redirect the final file, and default paths, are refused.
  - `actions/upload-artifact@v6` (example workflow) is still unverified (ADR-032 item 5).
  - Python 3.13 and the Linux CI run of the new tests are pending until the push.
- Manual verification (§27 item 19): fresh venv outside the repository —
  `flightcheck --version` → `flightcheck 0.1.0`; `flightcheck scan
  <repo>/tests/fixtures/apps/bc_bad_tool_pairing --output <tmp>` → exit 1 (FC-GEN-007 high);
  `flightcheck report <tmp>/flightcheck-report.json --output <tmp>/r.html` → exit 0, a full
  HTML page from the installed package. Skimmed the JSON and HTML: no absolute home paths, no
  key-like strings.
- Exact next step for the next phase: Phase 12 FINAL (10 Nov, before the 21:00 IST freeze):
  documentation, demo, `BharatCode.txt`, submission. Push Phase 11 first and check that CI is
  green on 3.11/3.12/3.13. Phase 10 only once a BharatCode key exists.
